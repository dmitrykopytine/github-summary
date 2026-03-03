import re

from exceptions import AppError

_OWNER_PART = r"""
    (?P<owner>
        (?![^/]*--)                     # no consecutive hyphens
        [A-Za-z0-9]                     # first char
        (?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?
                                        # rest, max total length 39
    )
"""

_REPO_PART = r"""
    (?P<repo>
        [A-Za-z0-9._-]{1,100}?         # repo chars, max length 100
    )
    (?=\.git(?:/|$)|/|$)               # stop before optional .git or path end
"""

_GITHUB_URL_RE = re.compile(
    rf"""
    ^
    https?://
    (?:
        (?P<web>(?:www\.)?github\.com/) # normal GitHub repo URL
      | api\.github\.com/repos/         # GitHub API repo URL
      | raw\.githubusercontent\.com/    # raw content URL
    )
    {_OWNER_PART}
    /
    {_REPO_PART}
    (?(web)(?:\.git)?)                  # only github.com URLs may have optional .git suffix
    (?:/|$)
    """,
    re.VERBOSE,
)


class GithubUrlParser:
    def __init__(self, url: str):
        match = _GITHUB_URL_RE.match(url.strip())
        if not match:
            raise AppError("Could not parse github_url", 422, {"url": url})
        self._owner_name = match.group("owner")
        self._repo_name = match.group("repo")

    @property
    def owner_name(self) -> str:
        return self._owner_name

    @property
    def repo_name(self) -> str:
        return self._repo_name
