# Pretty-prints JSON in HTTP responses and prints debug messages to the server console
DEBUG = True

# FastAPI server bind address
BIND_HOST = "0.0.0.0"
BIND_PORT = 8000

# Anthropic LLM settings (API key is read from this env var)
ANTHROPIC_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
MODEL = "claude-sonnet-4-6"
# Maximum total tokens per call, input (80%) + output (20%).
MODEL_MAX_TOKENS_PER_CALL = 15000
# Cap on output tokens per call. If too many output tokens are requested,
# the Anthropic API may require streaming, which is not supported here.
MODEL_MAX_OUTPUT_TOKENS_PER_CALL = 8192
MODEL_CALL_RETRIES = 2
MODEL_CALL_RETRY_DELAY_MS = 800

# GitHub API token env var. The env var does not have to exist — the app works
# without it, but having it raises the rate limit from 60 to 5000 req/hr.
GITHUB_TOKEN_ENV_VAR = "GITHUB_TOKEN"

# Limits for the file download (model picks files to analyze)
DOWNLOAD_LIMIT_FILES = 12
DOWNLOAD_LIMIT_ONE_FILE_MAX_KB = 1024
# How many files to download in parallel from GitHub
DOWNLOAD_CONCURRENCY = 6
# Retry settings for GitHub API / file downloads
DOWNLOAD_RETRIES = 1
DOWNLOAD_RETRY_DELAY_MS = 800
# Timeout for individual socket operations (connect, read chunk)
DOWNLOAD_SOCKET_TIMEOUT_SEC = 30
# Total wall-clock timeout for downloading a single file
DOWNLOAD_ONE_FILE_TIMEOUT_SEC = 60
