import json
from concurrent.futures import ThreadPoolExecutor

from debug import debug
from exceptions import AppError
from url_fetcher import UrlFetcher


class GithubRepo:
    def __init__(self, owner_name: str, repo_name: str):
        self._owner_name = owner_name
        self._repo_name = repo_name
        self._info_url = f"https://api.github.com/repos/{owner_name}/{repo_name}"
        self._readme_url = f"https://api.github.com/repos/{owner_name}/{repo_name}/readme"

        with ThreadPoolExecutor(max_workers=2) as executor:
            info_future = executor.submit(self._fetch_info)
            readme_future = executor.submit(self._fetch_readme)
            info_future.result()
            readme_future.result()

    def _fetch_info(self):
        debug("Fetching repo info", {"url": self._info_url})

        fetcher = UrlFetcher(self._info_url, is_json=True)

        if fetcher.is_error:
            if fetcher.http_code == 404:
                raise AppError(
                    "Repository not found or is private",
                    404,
                    {"owner_name": self._owner_name, "repo_name": self._repo_name},
                )
            raise AppError(
                f"Cannot fetch repository info: {fetcher.error_message}",
                502,
                {
                    "owner_name": self._owner_name,
                    "repo_name": self._repo_name,
                    "url": self._info_url,
                    "http_code": fetcher.http_code,
                    "error_code": fetcher.error_code,
                },
            )

        data = json.loads(fetcher.raw_response)

        self._full_name = data.get("full_name", "")
        self._description = data.get("description", "") or ""
        self._default_branch = data.get("default_branch", "")
        self._raw_info = fetcher.raw_response

        if not self._full_name:
            raise AppError(
                "Incomplete repository info: missing full_name",
                502,
                {"owner_name": self._owner_name, "repo_name": self._repo_name, "url": self._info_url},
            )
        if not self._default_branch:
            raise AppError(
                "Incomplete repository info: missing default_branch",
                502,
                {"owner_name": self._owner_name, "repo_name": self._repo_name, "url": self._info_url},
            )

    def _fetch_readme(self):
        debug("Fetching repo readme", {"url": self._readme_url})

        fetcher = UrlFetcher(self._readme_url)

        if fetcher.is_error:
            if fetcher.http_code == 404:
                self._readme = ""
                return
            raise AppError(
                f"Failed to download README: {fetcher.error_message}",
                502,
                {
                    "owner_name": self._owner_name,
                    "repo_name": self._repo_name,
                    "url": self._readme_url,
                    "http_code": fetcher.http_code,
                    "error_code": fetcher.error_code,
                },
            )

        self._readme = fetcher.raw_response

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
