# GitHub Summary

A FastAPI service that summarizes GitHub repositories using the Anthropic Claude API.

## Requirements

- Python 3.10+
- Anthropic API key ([get one here](https://console.anthropic.com/))

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Anthropic API key for Claude model calls |
| `GITHUB_TOKEN` | no | GitHub personal access token — required for private repos, raises API rate limit from 60 to 5,000 req/hr for public repos |

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Set the Anthropic API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Optionally set a GitHub token:

```bash
export GITHUB_TOKEN="ghp_..."
```

## Start the server

```bash
python main.py
```

Or via uvicorn directly:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

## How it works

1. Receives a GitHub repository URL via the `/summarize` endpoint. Works with public repos by default; private repos require a `GITHUB_TOKEN`.
2. Parses the URL and fetches repository info, README, and file tree from the GitHub API (in parallel).
3. **First pass** — sends repo info, README, and file tree to Claude. The model produces draft summaries with uncertainty annotations (e.g. "uses Redis (CHECK version in docker-compose.yml)") and selects key files to download.
4. Downloads the selected files from GitHub (in parallel, with size/count limits).
5. **Second pass** — sends the first-pass drafts together with the downloaded files to Claude. The model resolves uncertainties and produces the final polished response.
6. Returns the result as JSON with `summary`, `technologies`, and `structure` fields.

Input is automatically truncated if it exceeds the configured token limit.

## Configuration

All constants are in `config.py`:

- `DEBUG` — enables pretty-printed JSON responses and debug messages in the server console.
- `BIND_HOST`, `BIND_PORT` — server bind address (default `0.0.0.0:8000`).
- `MODEL`, `MODEL_MAX_TOKENS_PER_CALL` — Anthropic model name and token budget per call.
- `MODEL_CALL_RETRIES`, `MODEL_CALL_RETRY_DELAY_MS` — retry settings for model calls.
- `DOWNLOAD_RETRIES`, `DOWNLOAD_RETRY_DELAY_MS` — retry settings for GitHub API requests.
- `DOWNLOAD_CONCURRENCY` — max parallel file downloads from GitHub.
- `DOWNLOAD_LIMIT_FILES`, `DOWNLOAD_LIMIT_KB` — limits for the file download stage.

## API

### POST /summarize

Summarize a GitHub repository (public or private with a `GITHUB_TOKEN`).

**Request body (JSON):**

| Field | Type | Required | Description |
|---|---|---|---|
| github_url | string | yes | URL of a GitHub repository |

**Example:**

```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/user/repo"}'
```

**Response:**

```json
{
  "summary": "Repository 'user/repo' is a tool that does X. It supports Y and Z. Licensed under MIT.",
  "technologies": ["Python 3.12", "FastAPI 0.110", "Redis 7"],
  "structure": "src/main.py - application entry point\nsrc/config.py - configuration\ntests/ - test suite"
}
```

## License

MIT — see [LICENSE](LICENSE).
