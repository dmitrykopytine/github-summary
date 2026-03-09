import os
import time
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from config import (
    ANTHROPIC_API_KEY_ENV_VAR,
    DEBUG,
    MODEL,
    MODEL_CALL_RETRIES,
    MODEL_CALL_RETRY_DELAY_MS,
)
from debug import debug

T = TypeVar("T", bound=BaseModel)

# Pessimism ratio for char-based token estimation — accounts for how
# inaccurate the chars-to-tokens conversion can be across files.
_TRUNCATION_TARGET_RATIO = 0.85


class ModelCall:
    SYSTEM_PROMPT = (
        "You are a helpful assistant that analyzes GitHub repositories. "
        "Return ONLY the JSON object, no markdown, no code blocks, no extra text."
    )

    @staticmethod
    def check_api_key() -> bool:
        key = os.environ.get(ANTHROPIC_API_KEY_ENV_VAR, "")
        return bool(key.strip())

    def __init__(
        self,
        request_content: str,
        output_schema: type[T],
        max_input_tokens: int,
        max_output_tokens: int,
        files: list[dict[str, str]] | None = None,
        retry_number: int | None = None,
        debug_context_repo: str = "",
        debug_context_call_title: str = "",
    ):
        self._client = anthropic.Anthropic(
            api_key=os.environ.get(ANTHROPIC_API_KEY_ENV_VAR),
        )
        self._debug_context_repo = debug_context_repo
        self._debug_context_call_title = debug_context_call_title
        self._max_input_tokens = max_input_tokens
        self._max_output_tokens = max_output_tokens
        self._is_error: bool = False
        self._error_message: str | None = None
        self._parsed: T | None = None
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._raw_output: str | None = None

        full_prompt = self._build_prompt(request_content, files or [])

        retries_left = retry_number if retry_number is not None else MODEL_CALL_RETRIES

        while True:
            self._attempt(full_prompt, output_schema)
            if not self._is_error:
                break
            if retries_left <= 0:
                break
            if not self._should_retry():
                break
            retries_left -= 1
            self._debug("Model request failed, retrying", {
                "error": self._error_message,
                "retries_left": retries_left,
            })
            time.sleep(MODEL_CALL_RETRY_DELAY_MS / 1000)

        self._debug_usage()

    def _debug(self, message: str, context: dict | None = None) -> None:
        prefix = f"{self._debug_context_call_title}: " if self._debug_context_call_title else ""
        debug(self._debug_context_repo, f"{prefix}{message}", context)

    def _debug_usage(self) -> None:
        self._debug("Model usage", {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
        })

    def _build_prompt(self, request_content: str, files: list[dict]) -> str:
        contents = [f["content"] for f in files]
        contents = self._truncate_files_if_needed(request_content, files, contents)
        prompt = self._assemble_prompt(request_content, files, contents)
        prompt = self._truncate_prompt_if_needed(prompt)
        if DEBUG:
            file_parts = self._assemble_file_parts(files, contents)
            def _stat(part: str) -> dict:
                chars = len(part)
                tokens = self.count_tokens(part)
                return {
                    "chars": chars,
                    "tokens": tokens,
                    "chars/token": round(chars / tokens, 2) if tokens else 0,
                }
            stats = [{"part": "request_content", **_stat(request_content)}]
            for i, f in enumerate(files):
                stats.append({"part": f["description"], **_stat(file_parts[i])})
        return prompt

    def _truncate_files_if_needed(
        self, request_content: str, files: list[dict], contents: list[str],
    ) -> list[str]:
        full_prompt = self._assemble_prompt(request_content, files, contents)
        token_count = self.count_tokens(full_prompt)
        if token_count <= self._max_input_tokens:
            self._debug("Input fits within token limit", {
                "input_tokens": token_count,
                "max": self._max_input_tokens,
            })
            return contents

        total_chars = len(full_prompt)
        chars_per_token = total_chars / token_count if token_count else 3
        target_tokens = int(self._max_input_tokens * _TRUNCATION_TARGET_RATIO)
        target_chars = int(target_tokens * chars_per_token)
        chars_to_remove = total_chars - target_chars
        self._debug("Input exceeds token limit, truncating files", {
            "input_tokens": token_count,
            "max": self._max_input_tokens,
            "total_chars": total_chars,
            "target_chars": target_chars,
            "chars_to_remove": chars_to_remove,
        })
        return self._level_truncate(files, contents, chars_to_remove)

    def _truncate_prompt_if_needed(self, prompt: str) -> str:
        token_count = self.count_tokens(prompt)
        if token_count <= self._max_input_tokens:
            return prompt

        total_chars = len(prompt)
        ratio = (self._max_input_tokens * _TRUNCATION_TARGET_RATIO) / token_count
        target_chars = int(total_chars * ratio)
        self._debug("Still over limit, truncating whole prompt", {
            "input_tokens": token_count,
            "max": self._max_input_tokens,
            "ratio": round(ratio, 3),
            "target_chars": target_chars,
        })
        return self._truncate_at(prompt, target_chars)

    def _level_truncate(
        self, files: list[dict], contents: list[str], chars_to_remove: int,
    ) -> list[str]:
        truncatable = [(i, len(contents[i])) for i, f in enumerate(files) if f.get("truncatable")]
        if not truncatable:
            self._debug("No truncatable files, cannot reduce input size")
            return contents

        truncatable.sort(key=lambda x: x[1], reverse=True)
        total_truncatable_chars = sum(length for _, length in truncatable)
        target_total = total_truncatable_chars - chars_to_remove
        if target_total < 0:
            target_total = 0
        waterline = self._find_waterline(truncatable, target_total)

        result = list(contents)
        for idx, length in truncatable:
            if length > waterline:
                result[idx] = self._truncate_at(contents[idx], waterline)
        return result

    def _truncate_at(self, text: str, max_chars: int) -> str:
        cut_pos = max_chars
        newline_pos = text.rfind("\n", max(0, cut_pos - 200), cut_pos)
        if newline_pos != -1:
            cut_pos = newline_pos
        return text[:cut_pos] + "\n... (truncated)"

    def _find_waterline(self, truncatable: list[tuple[int, int]], target_total: int) -> int:
        if target_total <= 0:
            return 0
        lengths = sorted(length for _, length in truncatable)
        n = len(lengths)
        cumulative = 0
        for i, length in enumerate(lengths):
            remaining = n - i
            space_if_level = cumulative + length * remaining
            if space_if_level >= target_total:
                return (target_total - cumulative) // remaining
            cumulative += length
        return lengths[-1]

    def _assemble_file_parts(self, files: list[dict], contents: list[str]) -> list[str]:
        return [
            f'\n\n<file description="{f["description"]}">\n{contents[i]}\n</file>'
            for i, f in enumerate(files)
        ]

    def _assemble_prompt(self, request_content: str, files: list[dict], contents: list[str]) -> str:
        return request_content + "".join(self._assemble_file_parts(files, contents))

    def _should_retry(self) -> bool:
        if self._error_http_code is not None and 400 <= self._error_http_code < 500:
            return False
        return True

    def _attempt(self, request_content: str, output_schema: type[T]):
        self._is_error = False
        self._error_message = None
        self._error_http_code = None
        self._parsed = None
        self._input_tokens = None
        self._output_tokens = None
        self._raw_output = None

        debug_detail: str | None = None

        try:
            response = self._client.beta.messages.parse(
                model=MODEL,
                max_tokens=self._max_output_tokens,
                betas=["structured-outputs-2025-11-13"],
                system=self.SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": request_content,
                    },
                ],
                output_format=output_schema,
            )
            self._parsed = response.parsed_output
            self._input_tokens = response.usage.input_tokens
            self._output_tokens = response.usage.output_tokens
            if response.content:
                self._raw_output = response.content[0].text
        except anthropic.AuthenticationError as e:
            self._is_error = True
            self._error_message = "Authentication error"
            self._error_http_code = e.status_code
            debug_detail = str(e)
        except anthropic.APIStatusError as e:
            self._is_error = True
            self._error_message = "Model call failed"
            self._error_http_code = e.status_code
            debug_detail = e.message
        except Exception as e:
            self._is_error = True
            self._error_message = "Model call failed"
            self._error_http_code = getattr(e, "status_code", None)
            debug_detail = str(e)

        if not self._is_error and self._parsed is None:
            self._is_error = True
            self._error_message = "Model returned empty or unparseable response"

        if self._is_error:
            self._debug("Model call failed", {
                "error_message": self._error_message,
                "debug_detail": debug_detail,
                "error_http_code": self._error_http_code,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "raw_output_first_100_chars": self._raw_output[:100] if self._raw_output else None,
            })

    def count_tokens(self, request_content: str) -> int:
        result = self._client.messages.count_tokens(
            model=MODEL,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": request_content}],
        )
        return result.input_tokens

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def parsed(self) -> T | None:
        return self._parsed

    @property
    def input_tokens(self) -> int | None:
        return self._input_tokens

    @property
    def output_tokens(self) -> int | None:
        return self._output_tokens

    @property
    def raw_output(self) -> str | None:
        return self._raw_output
