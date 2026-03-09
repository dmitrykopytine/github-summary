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
        self._debug_context_repo = debug_context_repo
        self._debug_context_call_title = debug_context_call_title
        self._token_count: int = 0
        self._is_error: bool = False
        self._error_message: str | None = None

        retries_left = MODEL_CALL_RETRIES

        while True:
            debug_detail: str | None = None

            try:
                result = client.messages.count_tokens(
                    model=MODEL,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                )
                self._token_count = result.input_tokens
                self._is_error = False
                self._error_message = None
                break
            except anthropic.AuthenticationError as e:
                self._is_error = True
                self._error_message = "Token counting failed: Authentication error"
                debug_detail = str(e)
                break
            except anthropic.APIStatusError as e:
                self._is_error = True
                self._error_message = "Token counting failed"
                debug_detail = e.message
                if 400 <= e.status_code < 500:
                    break
            except Exception as e:
                self._is_error = True
                self._error_message = "Token counting failed"
                debug_detail = str(e)

            if retries_left <= 0:
                break
            retries_left -= 1
            self._debug("Token counting failed, retrying", {
                "error": self._error_message,
                "debug_detail": debug_detail,
                "retries_left": retries_left,
            })
            time.sleep(MODEL_CALL_RETRY_DELAY_MS / 1000)

        if self._is_error:
            self._debug("Token counting failed", {
                "error": self._error_message,
                "debug_detail": debug_detail,
            })

    def _debug(self, message: str, context: dict | None = None) -> None:
        prefix = f"{self._debug_context_call_title}: " if self._debug_context_call_title else ""
        debug(self._debug_context_repo, f"{prefix}{message}", context)

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def error_message(self) -> str | None:
        return self._error_message
