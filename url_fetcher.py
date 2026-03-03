import json
import socket
import time
import urllib.error
import urllib.request

from config import EXTERNAL_CALL_RETRIES, EXTERNAL_CALL_RETRY_DELAY_MS


class UrlFetcher:
    def __init__(self, url: str, is_json: bool = False, retry_number: int | None = None):
        self._url = url
        self._is_json = is_json

        self._raw_response: str | None = None
        self._http_code: int | None = None
        self._error_code: str | None = None
        self._is_error: bool = False
        self._error_message: str | None = None

        retries_left = retry_number if retry_number is not None else EXTERNAL_CALL_RETRIES

        while True:
            self._attempt()
            if not self._is_error:
                break
            if retries_left <= 0:
                break
            if not self._should_retry():
                break
            retries_left -= 1
            time.sleep(EXTERNAL_CALL_RETRY_DELAY_MS / 1000)

    @property
    def raw_response(self) -> str | None:
        return self._raw_response

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
        self._http_code = None
        self._error_code = None
        self._is_error = False
        self._error_message = None

        req = urllib.request.Request(self._url)
        if self._is_json:
            req.add_header("Accept", "application/json")
        else:
            req.add_header("Accept", "application/vnd.github.raw+json")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._http_code = resp.status
                self._raw_response = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            self._http_code = e.code
            self._is_error = True
            self._error_code = "http"
            self._read_http_error(e)
            return
        except urllib.error.URLError as e:
            self._is_error = True
            self._classify_url_error(e)
            return
        except socket.timeout:
            self._is_error = True
            self._error_code = "timeout"
            self._error_message = "Network error (timeout)"
            return
        except Exception as e:
            self._is_error = True
            self._error_code = "network"
            self._error_message = f"Network error ({e})"
            return

        if self._is_json:
            if not self._raw_response:
                self._is_error = True
                self._error_code = "empty_response"
                self._error_message = "Empty response"
                return
            try:
                json.loads(self._raw_response)
            except json.JSONDecodeError:
                self._is_error = True
                self._error_code = "json_parse"
                self._error_message = "Cannot parse JSON"

    def _read_http_error(self, e: urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8")
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

        self._error_message = f"HTTP error ({e.code})"
        if api_message:
            self._error_message += f": {api_message}"

    def _classify_url_error(self, e: urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, socket.timeout):
            self._error_code = "timeout"
            self._error_message = "Network error (timeout)"
        elif isinstance(reason, socket.gaierror):
            self._error_code = "dns"
            self._error_message = f"Network error (DNS lookup failed: {reason})"
        elif isinstance(reason, ConnectionRefusedError):
            self._error_code = "connection_refused"
            self._error_message = "Network error (connection refused)"
        elif isinstance(reason, ConnectionResetError):
            self._error_code = "connection_reset"
            self._error_message = "Network error (connection reset)"
        elif isinstance(reason, OSError):
            self._error_code = "network"
            self._error_message = f"Network error ({reason})"
        else:
            self._error_code = "network"
            self._error_message = f"Network error ({reason})"
