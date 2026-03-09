import time

import anthropic

from config import (
    MODEL,
    MODEL_CALL_RETRIES,
    MODEL_CALL_RETRY_DELAY_MS,
)
from debug import debug


class ModelCountTokens:
    def __init__(
        self,
        client: anthropic.Anthropic,
        content: str,
        system: str,
        debug_context_repo: str = "",
        debug_context_call_title: str = "",
    ):
        self._client = client
        self._content = content
        self._system = system
        self._debug_context_repo = debug_context_repo
        self._debug_context_call_title = debug_context_call_title
        self._token_count: int = 0
        self._is_error: bool = False
        self._error_message: str | None = None

        retries_left = MODEL_CALL_RETRIES

        while True:
            self._attempt()
            if not self._is_error:
                break
            if not self._should_retry():
                break
            if retries_left <= 0:
                break
            retries_left -= 1
            self._debug("Token counting failed, retrying", {
                "error": self._error_message,
                "retries_left": retries_left,
            })
            time.sleep(MODEL_CALL_RETRY_DELAY_MS / 1000)

        if self._is_error:
            self._debug("Token counting failed", {
                "error": self._error_message,
            })

    def _attempt(self):
        self._is_error = False
        self._error_message = None
        self._error_http_code: int | None = None
        self._token_count = 0

        debug_detail: str | None = None

        try:
            result = self._client.messages.count_tokens(
                model=MODEL,
                system=self._system,
                messages=[{"role": "user", "content": self._content}],
            )
            self._token_count = result.input_tokens
        except anthropic.AuthenticationError as e:
            self._is_error = True
            self._error_message = "Authentication error"
            self._error_http_code = e.status_code
            debug_detail = str(e)
        except anthropic.APIStatusError as e:
            self._is_error = True
            self._error_message = "API error"
            self._error_http_code = e.status_code
            debug_detail = e.message
        except Exception as e:
            self._is_error = True
            self._error_message = "Generic error"
            self._error_http_code = getattr(e, "status_code", None)
            debug_detail = str(e)

        if self._is_error:
            self._debug("Token counting attempt failed", {
                "error_message": self._error_message,
                "debug_detail": debug_detail,
                "error_http_code": self._error_http_code,
            })

    def _should_retry(self) -> bool:
        if self._error_http_code is not None and 400 <= self._error_http_code < 500:
            return False
        return True

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def error_message(self) -> str | None:
        return self._error_message

    def _debug(self, message: str, context: dict | None = None) -> None:
        prefix = f"{self._debug_context_call_title}: " if self._debug_context_call_title else ""
        debug(self._debug_context_repo, f"{prefix}{message}", context)
