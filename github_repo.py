import json
import urllib.error
import urllib.request

from exceptions import AppError


class GithubRepo:
    def __init__(self, owner_name: str, repo_name: str):
        url = f"https://api.github.com/repos/{owner_name}/{repo_name}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                self._raw_info = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise AppError(
                    "Repository not found or is private",
                    404,
                    {"owner_name": owner_name, "repo_name": repo_name},
                )
            raise AppError(
                "Cannot fetch repository info",
                502,
                {"owner_name": owner_name, "repo_name": repo_name, "url": url},
            )
        except Exception:
            raise AppError(
                "Cannot fetch repository info",
                502,
                {"owner_name": owner_name, "repo_name": repo_name, "url": url},
            )

        try:
            data = json.loads(self._raw_info)
        except json.JSONDecodeError:
            raise AppError(
                "Cannot fetch repository info",
                502,
                {"owner_name": owner_name, "repo_name": repo_name, "url": url},
            )

        self._full_name = data.get("full_name", "")
        self._description = data.get("description", "") or ""
        self._default_branch = data.get("default_branch", "")

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
