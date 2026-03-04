import os
import time
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from config import (
    ANTHROPIC_API_KEY_ENV_VAR,
    MODEL,
    MODEL_CALL_RETRIES,
    MODEL_CALL_RETRY_DELAY_MS,
    MODEL_MAX_TOKENS,
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
        retry_number: int | None = None,
    ):
        self._context_repo = context_repo
        self._is_error: bool = False
        self._error_message: str | None = None
        self._parsed: T | None = None

        retries_left = retry_number if retry_number is not None else MODEL_CALL_RETRIES

        while True:
            self._attempt(request_content, output_schema)
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

    def _should_retry(self) -> bool:
        if self._error_http_code is not None and 400 <= self._error_http_code < 500:
            return False
        return True

    def _attempt(self, request_content: str, output_schema: type[T]):
        self._is_error = False
        self._error_message = None
        self._error_http_code = None
        self._parsed = None

        try:
            response = _client.beta.messages.parse(
                model=MODEL,
                max_tokens=MODEL_MAX_TOKENS,
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

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def parsed(self) -> T | None:
        return self._parsed
