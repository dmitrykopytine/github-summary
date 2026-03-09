import json
import os
import socket
import time
import urllib.error
import urllib.request

from config import (
    DOWNLOAD_RETRIES,
    DOWNLOAD_RETRY_DELAY_MS,
    DOWNLOAD_SOCKET_TIMEOUT_SEC,
    DOWNLOAD_ONE_FILE_TIMEOUT_SEC,
    GITHUB_TOKEN_ENV_VAR,
)
from debug import debug


class _DownloadTimeoutError(Exception):
    pass


def _get_github_token() -> str | None:
    return os.environ.get(GITHUB_TOKEN_ENV_VAR) or None


_READ_CHUNK_SIZE = 8192


class GithubUrlFetcher:
    def __init__(
        self,
        url: str,
        is_json: bool = False,
        download_max_size_bytes: int | None = None,
        debug_context_repo: str = "",
        debug_context_call_title: str = "",
    ):
        self._url = url
        self._is_json = is_json
        self._download_max_size_bytes = download_max_size_bytes
        self._debug_context_repo = debug_context_repo
        self._debug_context_call_title = debug_context_call_title

        self._raw_response: str | None = None
        self._parsed_json: dict | list | None = None
        self._http_code: int | None = None
        self._error_code: str | None = None
        self._is_error: bool = False
        self._error_message: str | None = None
        self._is_truncated_response: bool = False

        retries_left = DOWNLOAD_RETRIES

        while True:
            self._attempt()
            if not self._is_error:
                break
            if retries_left <= 0:
                break
            if not self._should_retry():
                break
            retries_left -= 1
            self._debug("Fetch failed, retrying", {
                "url": self._url,
                "error_message": self._error_message,
                "error_code": self._error_code,
                "debug_detail": self._debug_detail,
                "http_code": self._http_code,
                "retries_left": retries_left,
            })
            time.sleep(DOWNLOAD_RETRY_DELAY_MS / 1000)

        if self._is_error:
            self._debug("Fetch failed", {
                "url": self._url,
                "error_message": self._error_message,
                "error_code": self._error_code,
                "debug_detail": self._debug_detail,
                "http_code": self._http_code,
            })

        if self._is_truncated_response:
            self._debug("Response truncated", {
                "url": self._url,
                "download_max_size_bytes": self._download_max_size_bytes,
            })

    def _debug(self, message: str, context: dict | None = None) -> None:
        prefix = f"{self._debug_context_call_title}: " if self._debug_context_call_title else ""
        debug(self._debug_context_repo, f"{prefix}{message}", context)

    @property
    def raw_response(self) -> str | None:
        return self._raw_response

    @property
    def parsed_json(self) -> dict | list | None:
        return self._parsed_json

    @property
    def http_code(self) -> int | None:
        return self._http_code

    @property
    def error_code(self) -> str | None:
        return self._error_code

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def is_truncated_response(self) -> bool:
        return self._is_truncated_response

    def _should_retry(self) -> bool:
        if self._error_code in ("timeout", "dns", "connection_refused", "network"):
            return True
        if self._http_code is not None and (self._http_code >= 500 or self._http_code == 429):
            return True
        if self._error_code in ("empty_response", "json_parse"):
            return True
        return False

    def _attempt(self):
        self._raw_response = None
        self._parsed_json = None
        self._http_code = None
        self._error_code = None
        self._is_error = False
        self._error_message = None
        self._is_truncated_response = False
        self._debug_detail: str | None = None

        req = urllib.request.Request(self._url)
        if self._is_json:
            req.add_header("Accept", "application/json")
        else:
            req.add_header("Accept", "application/vnd.github.raw+json")

        token = _get_github_token()
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_SOCKET_TIMEOUT_SEC) as resp:
                self._http_code = resp.status
                content_type = resp.headers.get("Content-Type", "")
                if not self._is_json and not self._is_text_content_type(content_type):
                    self._is_error = True
                    self._error_code = "binary"
                    self._error_message = "Binary file skipped"
                    self._debug_detail = "Content-Type: " + content_type
                    return
                if self._download_max_size_bytes is not None:
                    self._raw_response, self._is_truncated_response = self._read_limited(resp)
                else:
                    self._raw_response = resp.read().decode("utf-8", errors="ignore")
        except _DownloadTimeoutError:
            self._is_error = True
            self._error_code = "download_timeout"
            self._error_message = "Download timeout"
            self._debug_detail = f"exceeded {DOWNLOAD_ONE_FILE_TIMEOUT_SEC}s"
            return
        except urllib.error.HTTPError as e:
            self._http_code = e.code
            self._is_error = True
            self._error_code = "http"
            self._error_message = "HTTP error"
            self._read_http_error(e)
            return
        except urllib.error.URLError as e:
            self._is_error = True
            self._classify_url_error(e)
            return
        except socket.timeout:
            self._is_error = True
            self._error_code = "timeout"
            self._error_message = "Timeout"
            self._debug_detail = "timeout"
            return
        except Exception as e:
            self._is_error = True
            self._error_code = "network"
            self._error_message = "Network error"
            self._debug_detail = str(e)
            return

        if self._is_truncated_response and self._is_json:
            self._is_error = True
            self._error_code = "too_large"
            self._error_message = "JSON response is too large"
            self._debug_detail = f"exceeded {self._download_max_size_bytes} bytes"
            self._raw_response = None
            return

        if self._is_json:
            if not self._raw_response:
                self._is_error = True
                self._error_code = "empty_response"
                self._error_message = "Empty response"
                return
            try:
                self._parsed_json = json.loads(self._raw_response)
            except json.JSONDecodeError:
                self._is_error = True
                self._error_code = "json_parse"
                self._error_message = "Invalid response format"
                self._debug_detail = "JSON parse error"

    def _read_limited(self, resp) -> tuple[str, bool]:
        chunks = []
        total = 0
        truncated = False
        start = time.monotonic()
        while True:
            if time.monotonic() - start > DOWNLOAD_ONE_FILE_TIMEOUT_SEC:
                resp.close()
                raise _DownloadTimeoutError()
            chunk = resp.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > self._download_max_size_bytes:
                remainder = self._download_max_size_bytes - (total - len(chunk))
                if remainder > 0:
                    chunks.append(chunk[:remainder])
                truncated = True
                resp.close()
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="ignore"), truncated

    @staticmethod
    def _is_text_content_type(content_type: str) -> bool:
        return (
            content_type.startswith("text/")
            or "json" in content_type
            or "xml" in content_type
            or "javascript" in content_type
            or "yaml" in content_type
            or "charset=" in content_type
        )

    def _read_http_error(self, e: urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
            self._raw_response = body
        except Exception:
            pass

        api_message = ""
        if body:
            try:
                data = json.loads(body)
                api_message = str(data.get("message", ""))[:1024]
            except (json.JSONDecodeError, AttributeError):
                pass

        self._debug_detail = f"HTTP {e.code}"
        if api_message:
            self._debug_detail += f": {api_message}"

    def _classify_url_error(self, e: urllib.error.URLError):
        reason = e.reason
        self._error_message = "Network error"
        if isinstance(reason, socket.timeout):
            self._error_message = "Timeout"
            self._error_code = "timeout"
            self._debug_detail = "timeout"
        elif isinstance(reason, socket.gaierror):
            self._error_code = "dns"
            self._debug_detail = f"DNS lookup failed: {reason}"
        elif isinstance(reason, ConnectionRefusedError):
            self._error_code = "connection_refused"
            self._debug_detail = "connection refused"
        elif isinstance(reason, ConnectionResetError):
            self._error_code = "connection_reset"
            self._debug_detail = "connection reset"
        elif isinstance(reason, OSError):
            self._error_code = "network"
            self._debug_detail = str(reason)
        else:
            self._error_code = "network"
            self._debug_detail = str(reason)
