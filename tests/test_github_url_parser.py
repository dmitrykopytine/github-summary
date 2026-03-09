import pytest

from exceptions import AppError
from github_url_parser import GithubUrlParser


class TestGithubUrlParserValid:
    """URLs that should parse successfully."""

    @pytest.mark.parametrize(
        "url, expected_owner, expected_repo",
        [
            # github.com — basic
            ("https://github.com/intminds/gps", "intminds", "gps"),
            ("http://github.com/intminds/gps", "intminds", "gps"),
            ("https://www.github.com/intminds/gps", "intminds", "gps"),
            # github.com — .git suffix
            ("https://github.com/intminds/gps.git", "intminds", "gps"),
            ("https://github.com/intminds/gps.git/", "intminds", "gps"),
            # github.com — trailing path
            ("https://github.com/intminds/gps/", "intminds", "gps"),
            ("https://github.com/intminds/gps/tree/main", "intminds", "gps"),
            ("https://github.com/intminds/gps/blob/main/README.md", "intminds", "gps"),
            # api.github.com
            ("https://api.github.com/repos/intminds/gps", "intminds", "gps"),
            ("https://api.github.com/repos/intminds/gps/contents", "intminds", "gps"),
            ("https://api.github.com/repos/intminds/gps/readme", "intminds", "gps"),
            # raw.githubusercontent.com
            ("https://raw.githubusercontent.com/intminds/gps", "intminds", "gps"),
            ("https://raw.githubusercontent.com/intminds/gps/main/file.py", "intminds", "gps"),
            # single-char owner and repo
            ("https://github.com/a/b", "a", "b"),
            # owner with hyphens
            ("https://github.com/my-org/repo", "my-org", "repo"),
            ("https://github.com/a-b-c/repo", "a-b-c", "repo"),
            # repo with dots, underscores, hyphens
            ("https://github.com/owner/my.repo", "owner", "my.repo"),
            ("https://github.com/owner/my_repo", "owner", "my_repo"),
            ("https://github.com/owner/my-repo", "owner", "my-repo"),
            ("https://github.com/owner/my.repo-name_v2", "owner", "my.repo-name_v2"),
            # query parameters
            ("https://github.com/intminds/gps?tab=readme-ov-file", "intminds", "gps"),
            ("https://github.com/intminds/gps/?tab=readme-ov-file", "intminds", "gps"),
            # whitespace around URL
            ("  https://github.com/intminds/gps  ", "intminds", "gps"),
            # .git in API/raw URLs is absorbed into repo name (no stripping)
            ("https://api.github.com/repos/owner/repo.git", "owner", "repo.git"),
            ("https://raw.githubusercontent.com/owner/repo.git", "owner", "repo.git"),
        ],
    )
    def test_parses_valid_url(self, url, expected_owner, expected_repo):
        parsed = GithubUrlParser(url)
        assert parsed.owner_name == expected_owner
        assert parsed.repo_name == expected_repo


class TestGithubUrlParserInvalid:
    """URLs that should raise AppError."""

    @pytest.mark.parametrize(
        "url",
        [
            # wrong scheme
            "ftp://github.com/owner/repo",
            # not a URL
            "not-a-url",
            "owner/repo",
            # empty / whitespace
            "",
            "   ",
            # missing parts
            "https://github.com/",
            "https://github.com/owner/",
            "https://github.com//repo",
            # consecutive hyphens in owner
            "https://github.com/my--org/repo",
            # unsupported hosts
            "https://gitlab.com/owner/repo",
            "https://bitbucket.org/owner/repo",
        ],
    )
    def test_rejects_invalid_url(self, url):
        with pytest.raises(AppError) as exc_info:
            GithubUrlParser(url)
        assert exc_info.value.http_code == 422


class TestGithubUrlParserOwnerLimits:
    """Owner name length constraints (max 39 chars)."""

    def test_owner_at_max_length(self):
        owner = "a" * 39
        parsed = GithubUrlParser(f"https://github.com/{owner}/repo")
        assert parsed.owner_name == owner

    def test_owner_exceeding_max_length(self):
        owner = "a" * 40
        with pytest.raises(AppError):
            GithubUrlParser(f"https://github.com/{owner}/repo")


class TestGithubUrlParserRepoLimits:
    """Repo name length constraints (max 100 chars)."""

    def test_repo_at_max_length(self):
        repo = "a" * 100
        parsed = GithubUrlParser(f"https://github.com/owner/{repo}")
        assert parsed.repo_name == repo

    def test_repo_exceeding_max_length(self):
        repo = "a" * 101
        with pytest.raises(AppError):
            GithubUrlParser(f"https://github.com/owner/{repo}")
