import json
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from config import DOWNLOAD_CONCURRENCY
from debug import debug
from exceptions import AppError
from github_url_fetcher import GithubUrlFetcher


_NOISE_DIRS = (
    ".github/",
    ".git/",
    "node_modules/",
    "__pycache__/",
    "vendor/",
    ".venv/",
    ".tox/",
    ".mypy_cache/",
    ".pytest_cache/",
)


def _is_noise_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _NOISE_DIRS)


_STRIP_INFO_EXACT_KEYS = {"url", "node_id", "security_and_analysis"}


def _strip_info_keys(obj):
    """Recursively remove noisy keys, empty/null values — makes JSON much more lightweight."""
    if isinstance(obj, dict):
        return {
            k: _strip_info_keys(v) for k, v in obj.items()
            if k not in _STRIP_INFO_EXACT_KEYS
            and not k.endswith("_url")
            and v != ""
            and v is not None
            and v != []
            and v != {}
        }
    if isinstance(obj, list):
        return [_strip_info_keys(item) for item in obj]
    return obj


class GithubRepo:
    def __init__(self, owner_name: str, repo_name: str):
        self._owner_name = owner_name
        self._repo_name = repo_name
        self._tree_as_text: str | None = None
        self._downloaded_files: OrderedDict[str, str] = OrderedDict()
        self._info_url = f"https://api.github.com/repos/{owner_name}/{repo_name}"
        self._readme_url = f"https://api.github.com/repos/{owner_name}/{repo_name}/readme"

        self._fetch_info()

        self._tree_url = (
            f"https://api.github.com/repos/{owner_name}/{repo_name}"
            f"/git/trees/{self._default_branch}?recursive=1"
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            readme_future = executor.submit(self._fetch_readme)
            tree_future = executor.submit(self._fetch_tree)
            readme_future.result()
            tree_future.result()

    def get_debug_context_repo(self) -> str:
        return f"{self._owner_name}/{self._repo_name}"

    def _fetch_info(self):
        debug(self.get_debug_context_repo(), "Fetching repo info", {"url": self._info_url})
        fetcher = GithubUrlFetcher(
            self._info_url,
            is_json=True,
            context_repo=self.get_debug_context_repo(),
        )
        if fetcher.is_error:
            if fetcher.http_code == 404:
                raise AppError(f"Repository not found or is private ({self._owner_name}/{self._repo_name})", 422)
            raise AppError(f"Cannot fetch repository info ({self._owner_name}/{self._repo_name})", 502)

        data = json.loads(fetcher.raw_response)
        self._full_name = data.get("full_name", "")
        self._description = data.get("description", "") or ""
        self._default_branch = data.get("default_branch", "")
        filtered_data = _strip_info_keys(data)
        self._raw_info = json.dumps(
            filtered_data,
            indent=2,
            ensure_ascii=False,
        )
        if not self._full_name:
            raise AppError(f"Incomplete repository info ({self._owner_name}/{self._repo_name}): Missing full_name. Attempted URL: {self._info_url}", 502)
        if not self._default_branch:
            raise AppError(f"Incomplete repository info ({self._owner_name}/{self._repo_name}): Missing default_branch. Attempted URL: {self._info_url}", 502)

    def _fetch_readme(self):
        debug(self.get_debug_context_repo(), "Fetching repo readme", {"url": self._readme_url})
        fetcher = GithubUrlFetcher(self._readme_url, context_repo=self.get_debug_context_repo())
        if fetcher.is_error:
            if fetcher.http_code == 404:
                debug(self.get_debug_context_repo(), "Readme is not available")
                self._readme = ""
                return
            debug(self.get_debug_context_repo(), "Failed to download README", {
                "error_message": fetcher.error_message,
                "error_code": fetcher.error_code,
                "http_code": fetcher.http_code,
                "url": self._readme_url,
            })
            raise AppError(f"Failed to download README ({self._owner_name}/{self._repo_name}). Attempted URL: {self._readme_url}", 502)

        self._readme = fetcher.raw_response

    def _fetch_tree(self):
        debug(self.get_debug_context_repo(), "Fetching repo tree", {"url": self._tree_url})
        fetcher = GithubUrlFetcher(
            self._tree_url,
            is_json=True,
            context_repo=self.get_debug_context_repo(),
        )
        if fetcher.is_error:
            if fetcher.http_code == 409:
                debug(self.get_debug_context_repo(), "Repository is empty, no tree available")
                self._tree: OrderedDict[str, dict] = OrderedDict()
                return
            debug(self.get_debug_context_repo(), "Failed to fetch project tree", {
                "error_message": fetcher.error_message,
                "error_code": fetcher.error_code,
                "http_code": fetcher.http_code,
                "url": self._tree_url,
            })
            raise AppError(f"Failed to fetch project tree ({self._owner_name}/{self._repo_name}). Attempted URL: {self._tree_url}", 502)

        data = json.loads(fetcher.raw_response)
        tree_items = data.get("tree", [])
        entries = []
        for item in tree_items:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            if _is_noise_path(path):
                continue
            entries.append((path, item.get("size", 0), item.get("url", "")))
        # Sort by depth so top-level files come first — important because
        # the tree may be truncated to fit the model context window.
        entries.sort(key=lambda e: e[0].count("/"))
        self._tree: OrderedDict[str, dict] = OrderedDict()
        for path, size, url in entries:
            self._tree[path] = {"size": size, "url": url}

    def download_files(self, file_paths: list[str], max_file_count: int, max_total_size_kb: float) -> None:
        max_total_size_bytes = int(max_total_size_kb * 1024)
        valid_paths = []
        total_size = 0
        for path in file_paths:
            if path not in self._tree:
                debug(self.get_debug_context_repo(), "Skipping file not in tree", {"path": path})
                continue
            file_size = self._tree[path]["size"]
            if len(valid_paths) >= max_file_count:
                debug(self.get_debug_context_repo(), "Reached max file count, skipping", {"path": path})
                break
            if total_size + file_size > max_total_size_bytes:
                debug(self.get_debug_context_repo(), "Would exceed max size, skipping", {
                    "path": path,
                    "size": file_size,
                })
                continue
            valid_paths.append(path)
            total_size += file_size

        self._downloaded_files: OrderedDict[str, str] = OrderedDict()
        lock = threading.Lock()

        def _download_one(path: str):
            url = self._tree[path]["url"]
            fetcher = GithubUrlFetcher(url, context_repo=self.get_debug_context_repo())
            if fetcher.is_error:
                debug(self.get_debug_context_repo(), "Failed to download file, skipping", {
                    "path": path,
                    "error": fetcher.error_message,
                })
                return
            with lock:
                self._downloaded_files[path] = fetcher.raw_response

        with ThreadPoolExecutor(max_workers=DOWNLOAD_CONCURRENCY) as executor:
            executor.map(_download_one, valid_paths)

        total_downloaded_bytes = sum(len(c.encode("utf-8")) for c in self._downloaded_files.values())
        debug(self.get_debug_context_repo(), "Downloaded files", {
            "count": len(self._downloaded_files),
            "size_kb": round(total_downloaded_bytes / 1024, 2),
        })

    def get_downloaded_files(self) -> list[dict[str, str]]:
        return [{"path": path, "content": content} for path, content in self._downloaded_files.items()]

    def get_tree_as_text(self) -> str:
        if self._tree_as_text is not None:
            return self._tree_as_text
        if not self._tree:
            self._tree_as_text = "(empty repository — no files)"
        else:
            lines = []
            for path, info in self._tree.items():
                lines.append(f"{path} ({info['size']})")
            self._tree_as_text = "\n".join(lines)
        return self._tree_as_text

    @property
    def info_url(self) -> str:
        return self._info_url

    @property
    def readme_url(self) -> str:
        return self._readme_url

    @property
    def tree_url(self) -> str:
        return self._tree_url

    @property
    def full_name(self) -> str:
        return self._full_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def default_branch(self) -> str:
        return self._default_branch

    @property
    def raw_info(self) -> str:
        return self._raw_info

    @property
    def readme(self) -> str:
        return self._readme

    @property
    def tree(self) -> OrderedDict[str, dict]:
        return self._tree
