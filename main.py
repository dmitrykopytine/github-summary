import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from debug import debug
from config import (
    BIND_HOST,
    BIND_PORT,
    DEBUG,
    DOWNLOAD_LIMIT_FILES,
    DOWNLOAD_LIMIT_KB,
)
from exceptions import AppError
from github_repo import GithubRepo
from github_url_parser import GithubUrlParser
from model_call import ModelCall

app = FastAPI()


class SummarizeRequest(BaseModel):
    github_url: str = Field(..., description="URL of a public GitHub repository")


class FilesToDownloadModelResponse(BaseModel):
    files: list[str]


class SummaryModelResponse(BaseModel):
    summary: str
    technologies: list[str]
    structure: str


@app.post("/summarize")
async def summarize(request: SummarizeRequest):
    github_url = validate_github_url(request.github_url)
    parsed_github_url = GithubUrlParser(github_url)
    debug(parsed_github_url.get_debug_context_repo(), "Processing request")
    github_repo = await asyncio.to_thread(GithubRepo, parsed_github_url.owner_name, parsed_github_url.repo_name)

    files_model = await asyncio.to_thread(model_get_files_to_download, github_repo)
    file_paths = files_model.parsed.files
    debug(github_repo.get_debug_context_repo(), "Files to download", {"count": len(file_paths), "files": file_paths})
    await asyncio.to_thread(
        github_repo.download_files, file_paths, DOWNLOAD_LIMIT_FILES, DOWNLOAD_LIMIT_KB,
    )

    summary_model = await asyncio.to_thread(model_summarize, github_repo)
    result = summary_model.parsed.model_dump()

    debug(github_repo.get_debug_context_repo(), "Responding with success")
    indent = 2 if DEBUG else None
    return Response(
        content=json.dumps(result, indent=indent, ensure_ascii=False),
        media_type="application/json",
    )


def model_get_files_to_download(github_repo: GithubRepo) -> ModelCall:
    debug(github_repo.get_debug_context_repo(), "Asking model for files to download")
    request_content = f"""I want to summarize this repository. To improve the quality of the summary, I want to download and analyze some key files from the repository.

Respond with a JSON object containing a single key "files" which is an array of file paths (strings) from the repository tree that would be most useful to download for understanding what this project does, what technologies it uses, and how it is structured.

Rules:
- Select at most {DOWNLOAD_LIMIT_FILES} files.
- Total size of selected files must not exceed {DOWNLOAD_LIMIT_KB} KB.
- Prioritize: package manifests (package.json, composer.json, requirements.txt, Cargo.toml, etc.), configuration files, entry points, and key source files.
- Do NOT include binary files, images, lock files, or generated files.
- Only select files that exist in the repository tree below.

<file description="Repository info in JSON format">
{github_repo.raw_info}
</file>

<file description="Repository tree, each line contains a file path and its size in bytes">
{github_repo.get_tree_as_text()}
</file>

<file description="README.md file">
{github_repo.readme}
</file>
"""
    token_count = ModelCall.count_tokens(request_content)
    debug(github_repo.get_debug_context_repo(), "Token count for files-to-download request", {"tokens": token_count})
    token_count = ModelCall.count_tokens(github_repo.raw_info)
    debug(github_repo.get_debug_context_repo(), "Token count for github_repo.raw_info", {"tokens": token_count})
    token_count = ModelCall.count_tokens(github_repo.get_tree_as_text())
    debug(github_repo.get_debug_context_repo(), "Token count for github_repo.get_tree_as_text()", {"tokens": token_count})
    token_count = ModelCall.count_tokens(github_repo.readme)
    debug(github_repo.get_debug_context_repo(), "Token count for github_repo.readme", {"tokens": token_count})

    model = ModelCall(request_content, FilesToDownloadModelResponse, github_repo.get_debug_context_repo())
    if model.is_error:
        raise AppError("LLM call failed", 502)
    _debug_model_usage(github_repo, model, "files-to-download")

    return model


def model_summarize(github_repo: GithubRepo) -> ModelCall:
    debug(github_repo.get_debug_context_repo(), "Asking model to summarize")
    request_content = f"""Summarize this GitHub repository.
Respond with a JSON object containing the following keys: "summary" (string), "technologies" (array of strings), "structure" (string).

JSON key "summary":
A human-readable description of what the project does.
First sentence: what the project is and who maintains it. Start with repo name and owner, for example: "Repository 'user/repo' is ...".
Then: 2-3 key features or capabilities.
Then: licence name if found in repository info (e.g. "Licensed under MIT."). If not found, omit.
Keep the summary under 5 sentences.

JSON key "technologies":
List of main technologies, languages, frameworks and libraries used.
List up to 16 most important technologies.
Include versions when available (e.g. from package manifests like package.json, composer.json, requirements.txt, Cargo.toml).
License is NOT a technology and should not be included.

JSON key "structure":
Brief description of the project structure.
List the 8-15 most important files and directories with a one-line description each. Format: "path - description". This key must be a string, NOT an array.
Do not list every file. Focus on entry points, config files, key source directories, and test directories.

Do not:
- Repeat the README content verbatim.
- List every file in the tree.
- Include generic filler like "this is a well-structured project".
- Include markdown formatting in any of the values.


Below is relevant information about the repository, enclosed in <file> tags.

<file description="Repository info in JSON format">
{github_repo.raw_info}
</file>

<file description="Repository tree, each line contains a file path and its size in bytes">
{github_repo.get_tree_as_text()}
</file>

<file description="README.md file">
{github_repo.readme}
</file>

{_format_downloaded_files(github_repo)}"""
    model = ModelCall(request_content, SummaryModelResponse, github_repo.get_debug_context_repo())
    if model.is_error:
        raise AppError("LLM call failed", 502)
    _debug_model_usage(github_repo, model, "summarize")

    return model


def _debug_model_usage(github_repo: GithubRepo, model: ModelCall, label: str) -> None:
    ctx = github_repo.get_debug_context_repo()
    output_tokens_counted = None
    if model.raw_output:
        output_tokens_counted = ModelCall.count_tokens(model.raw_output)
    debug(ctx, f"Model usage ({label})", {
        "input_tokens": model.input_tokens,
        "output_tokens": model.output_tokens,
        "output_tokens_counted": output_tokens_counted,
    })


def _format_downloaded_files(github_repo: GithubRepo) -> str:
    files = github_repo.get_downloaded_files()
    if not files:
        return ""
    parts = [
        "Below are selected source files downloaded from the repository (partial, not all files)."
    ]
    for f in files:
        parts.append(f'\n<file path="{f["path"]}">\n{f["content"]}\n</file>')
    return "\n".join(parts)


def validate_github_url(github_url: str) -> str:
    if not github_url or not isinstance(github_url, str):
        raise AppError("github_url is required and must be a string", 422)
    github_url = github_url.strip()
    if not github_url:
        raise AppError("github_url cannot be empty", 422)
    return github_url


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    debug("", f"Responding with failure ({exc.http_code})", {"message": exc.message})
    message = exc.message
    if exc.context:
        message += "\n" + json.dumps(exc.context, indent=2, ensure_ascii=False)
    indent = 2 if DEBUG else None
    return Response(
        content=json.dumps(
            {"status": "error", "message": message},
            indent=indent,
            ensure_ascii=False,
        ),
        status_code=exc.http_code,
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=BIND_HOST, port=BIND_PORT)
