import sys
if sys.version_info < (3, 10):
    print("Python 3.10+ is required (current: %s)" % sys.version)
    sys.exit(1)

import asyncio
import contextvars
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
    MODEL_MAX_TOKENS_PER_CALL,
)
from exceptions import AppError
from github_repo import GithubRepo
from github_url_parser import GithubUrlParser
from model_call import ModelCall

app = FastAPI()

_debug_context_repo: contextvars.ContextVar[str] = contextvars.ContextVar(
    "debug_context_repo",
    default="",
)


class SummarizeRequest(BaseModel):
    github_url: str = Field(..., description="URL of a GitHub repository")


class FirstPassModelResponse(BaseModel):
    draft_summary: str
    draft_technologies: list[str]
    draft_structure: str
    notes: str
    files: list[str]


class SecondFinalPassModelResponse(BaseModel):
    summary: str
    technologies: list[str]
    structure: str


@app.post("/summarize")
async def summarize(request: SummarizeRequest):
    github_url = validate_github_url(request.github_url)
    parsed_github_url = GithubUrlParser(github_url)
    _debug_context_repo.set(parsed_github_url.get_debug_context_repo())
    debug(parsed_github_url.get_debug_context_repo(), "Processing request")
    github_repo = await asyncio.to_thread(
        GithubRepo,
        parsed_github_url.owner_name,
        parsed_github_url.repo_name,
    )

    first_pass_model = await asyncio.to_thread(model_first_pass, github_repo)
    first_pass = first_pass_model.parsed
    file_paths = first_pass.files
    debug(github_repo.get_debug_context_repo(), "Files to download", {
        "count": len(file_paths),
        "files": file_paths,
    })
    await asyncio.to_thread(
        github_repo.download_files,
        file_paths,
        DOWNLOAD_LIMIT_FILES,
        DOWNLOAD_LIMIT_KB,
    )

    summary_model = await asyncio.to_thread(
        model_summarize,
        github_repo,
        first_pass,
    )
    result = summary_model.parsed.model_dump()

    debug(github_repo.get_debug_context_repo(), "Responding with success")
    indent = 2 if DEBUG else None
    return Response(
        content=json.dumps(
            result,
            indent=indent,
            ensure_ascii=False,
        ),
        media_type="application/json",
    )


def model_first_pass(github_repo: GithubRepo) -> ModelCall:
    debug(github_repo.get_debug_context_repo(), "First pass: Analyzing repo and selecting files to download")
    request_content = f"""Analyze this GitHub repository. You are performing the first of two passes. Your output will be used by the same model (you) in a second pass, together with downloaded source files, to produce a final polished summary.

Your task now:
1. Write drafts of summary, technologies, and structure based on what you can see (repo info, README, file tree).
2. Mark uncertainties and things to verify — for example: "uses Redis (CHECK version in docker-compose.yml)", "src/main.py appears to be the entry point (CHECK what framework it uses)".
3. Select files to download that would help resolve these uncertainties and improve the final result.
4. Write free-form notes with cross-cutting observations, hypotheses, and instructions for the second pass.

Be generous with annotations and instructions. The second pass will have access to the downloaded files and your drafts, but NOT to the repo info, README, or file tree — so capture everything important in your drafts and notes.

JSON key "draft_summary":
A draft human-readable description of what the project does.
First sentence: what the project is and who maintains it. Start with repo name and owner, for example: "Repository 'user/repo' is ...".
Then: 2-3 key features or capabilities.
Then: licence name if found in repository info (e.g. "Licensed under MIT."). If not found, omit.
Keep under 5 sentences. Add (CHECK: ...) annotations where uncertain.

JSON key "draft_technologies":
Draft list of main technologies, languages, frameworks and libraries.
Up to 16 items. Include versions when visible (e.g. from file names or README).
Add "(CHECK: ...)" suffix to items where the version or usage is uncertain.

JSON key "draft_structure":
Draft description of the project structure.
List the 8-15 most important files and directories with a one-line description each. Format: "path - description".
Add (CHECK: ...) annotations for files you want to verify in the second pass.

JSON key "notes":
Free-form notes for the second pass. Include:
- Cross-cutting observations (e.g. "This is a monorepo", "The real project is in packages/core/").
- Hypotheses to verify.
- Specific things to look for in downloaded files.
- Anything that doesn't fit neatly into the three draft fields.

JSON key "files":
Array of file paths (strings) to download for the second pass.
Rules:
- Select at most {DOWNLOAD_LIMIT_FILES} files.
- Total size of selected files must not exceed {DOWNLOAD_LIMIT_KB} KB.
- Prioritize files that resolve your uncertainties: package manifests, config files, entry points, key source files.
- Do NOT include binary files, images, lock files, or generated files.
- Only select files that exist in the repository tree.
- Use all available output tokens — be thorough in your drafts and notes."""
    files = [
        {"description": "Repository info in JSON format", "content": github_repo.raw_info, "truncatable": True},
        {"description": "Repository tree, each line contains a file path and its size in bytes", "content": github_repo.get_tree_as_text(), "truncatable": True},
        {"description": "README.md file", "content": github_repo.readme, "truncatable": True},
    ]
    print(github_repo.get_tree_as_text());
    max_input_tokens = int(MODEL_MAX_TOKENS_PER_CALL * 0.8)
    max_output_tokens = int(MODEL_MAX_TOKENS_PER_CALL * 0.2)
    model = ModelCall(
        request_content,
        FirstPassModelResponse,
        max_input_tokens,
        max_output_tokens,
        files=files,
        debug_context_repo=github_repo.get_debug_context_repo(),
        debug_context_call_title="First pass",
    )
    if model.is_error:
        raise AppError("LLM call failed", 502)

    return model


def model_summarize(github_repo: GithubRepo, first_pass: FirstPassModelResponse) -> ModelCall:
    debug(github_repo.get_debug_context_repo(), "Second final pass: Refining with downloaded files")
    request_content = """You are performing the second pass of a GitHub repository analysis. In the first pass, you analyzed the repo info, README, and file tree and produced drafts with annotations. Now you have access to downloaded source files to verify and improve those drafts.

Produce the final polished response. Resolve all (CHECK: ...) annotations using the downloaded files. Remove speculation — if you cannot confirm something from the available data, omit it rather than guess.

JSON key "summary":
A human-readable description of what the project does.
First sentence: what the project is and who maintains it. Start with repo name and owner, for example: "Repository 'user/repo' is ...".
Then: 2-3 key features or capabilities.
Then: licence name if found (e.g. "Licensed under MIT."). If not found, omit.
Keep under 5 sentences. No (CHECK: ...) annotations — this is the final output.

JSON key "technologies":
List of main technologies, languages, frameworks and libraries used.
Up to 16 items. Include exact versions from package manifests when available.
License is NOT a technology and should not be included.

JSON key "structure":
Brief description of the project structure.
List the 8-15 most important files and directories with a one-line description each. Format: "path - description". This key must be a string, NOT an array.
Focus on entry points, config files, key source directories, and test directories.

Do not:
- Include (CHECK: ...) annotations in the final output.
- Include generic filler like "this is a well-structured project".
- Include markdown formatting in any of the values."""
    files = [
        {"description": "First pass: draft summary", "content": first_pass.draft_summary, "truncatable": False},
        {"description": "First pass: draft technologies", "content": "\n".join(first_pass.draft_technologies), "truncatable": False},
        {"description": "First pass: draft structure", "content": first_pass.draft_structure, "truncatable": False},
        {"description": "First pass: notes and instructions", "content": first_pass.notes, "truncatable": False},
    ]
    downloaded = github_repo.get_downloaded_files()
    if downloaded:
        for f in downloaded:
            files.append({"description": f'Downloaded file: {f["path"]}', "content": f["content"], "truncatable": True})
    max_input_tokens = int(MODEL_MAX_TOKENS_PER_CALL * 0.8)
    max_output_tokens = int(MODEL_MAX_TOKENS_PER_CALL * 0.2)
    model = ModelCall(
        request_content,
        SecondFinalPassModelResponse,
        max_input_tokens,
        max_output_tokens,
        files=files,
        debug_context_repo=github_repo.get_debug_context_repo(),
        debug_context_call_title="Second final pass",
    )
    if model.is_error:
        raise AppError("LLM call failed", 502)

    return model


def validate_github_url(github_url: str) -> str:
    if not github_url or not isinstance(github_url, str):
        raise AppError("github_url is required and must be a string", 422)
    github_url = github_url.strip()
    if not github_url:
        raise AppError("github_url cannot be empty", 422)
    return github_url


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    debug(_debug_context_repo.get(), f"Responding with failure ({exc.http_code})", {"message": exc.message})
    indent = 2 if DEBUG else None
    return Response(
        content=json.dumps(
            {"status": "error", "message": exc.message},
            indent=indent,
            ensure_ascii=False,
        ),
        status_code=exc.http_code,
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=BIND_HOST,
        port=BIND_PORT,
    )
