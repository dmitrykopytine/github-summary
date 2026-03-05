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

_client = anthropic.Anthropic(
    api_key=os.environ.get(ANTHROPIC_API_KEY_ENV_VAR),
)

T = TypeVar("T", bound=BaseModel)


class ModelCall:
    SYSTEM_PROMPT = (
        "You are a helpful assistant that analyzes GitHub repositories. "
        "Return ONLY the JSON object, no markdown, no code blocks, no extra text."
    )

    def __init__(
        self,
        request_content: str,
        output_schema: type[T],
        context_repo: str,
        max_input_tokens: int,
        max_output_tokens: int,
        files: list[dict[str, str]] | None = None,
        retry_number: int | None = None,
    ):
        self._context_repo = context_repo
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
            debug(self._context_repo, "Model request failed, retrying", {
                "error": self._error_message,
                "retries_left": retries_left,
            })
            time.sleep(MODEL_CALL_RETRY_DELAY_MS / 1000)

    def _build_prompt(self, request_content: str, files: list[dict[str, str]]) -> str:
        file_parts = []
        for f in files:
            file_parts.append(f'\n\n<file description="{f["description"]}">\n{f["content"]}\n</file>')
        if DEBUG:
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
            debug(self._context_repo, "Model call prompt parts", {"parts": stats})
        return request_content + "".join(file_parts)

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

        try:
            response = _client.beta.messages.parse(
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
        except anthropic.APIStatusError as e:
            self._is_error = True
            self._error_message = f"Model call failed: {e.message}"
            self._error_http_code = e.status_code
        except Exception as e:
            self._is_error = True
            self._error_message = f"Model call failed: {e}"
            self._error_http_code = getattr(e, "status_code", None)

        if not self._is_error and self._parsed is None:
            self._is_error = True
            self._error_message = "Model returned empty or unparseable response"

        if self._is_error:
            debug(self._context_repo, "Model call failed", {
                "error_message": self._error_message,
                "error_http_code": self._error_http_code,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "raw_output_first_100_chars": self._raw_output[:100] if self._raw_output else None,
            })

    @staticmethod
    def count_tokens(request_content: str) -> int:
        result = _client.messages.count_tokens(
            model=MODEL,
            system=ModelCall.SYSTEM_PROMPT,
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
