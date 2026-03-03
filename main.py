import asyncio
import json
import os
import urllib.error
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import Response
from openai import OpenAI
from pydantic import BaseModel, Field

from debug import debug
from config import (
    BIND_HOST,
    BIND_PORT,
    DEBUG,
    EXTERNAL_CALL_RETRIES,
    EXTERNAL_CALL_RETRY_DELAY_MS,
    MODEL,
    OPENAI_API_BASE_URL,
    OPENAI_API_KEY_ENV_VAR,
)
from exceptions import AppError
from github_repo import GithubRepo
from github_url_parser import GithubUrlParser

app = FastAPI()


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
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

client = OpenAI(
    base_url=OPENAI_API_BASE_URL,
    api_key=os.environ.get(OPENAI_API_KEY_ENV_VAR),
)


def download_readme(project_path: str) -> str:
    """
    Download README content for a GitHub project.
    Uses GitHub API with raw content Accept header.
    """
    url = f"https://api.github.com/repos/{project_path}/readme"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github.raw+json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise AppError(f"Failed to download README: {e.code} {e.reason}", 502)
    except Exception as e:
        raise AppError(f"Failed to download README: {str(e)}", 502)


class SummarizeRequest(BaseModel):
    github_url: str = Field(..., description="URL of a public GitHub repository")


@app.post("/summarize")
async def summarize(request: SummarizeRequest):
    github_url = request.github_url
    if not github_url or not isinstance(github_url, str):
        raise AppError("github_url is required and must be a string", 422)
    github_url = github_url.strip()
    if not github_url:
        raise AppError("github_url cannot be empty", 422)

    parsed = GithubUrlParser(github_url)
    debug("Received request", {"repo": f"{parsed.owner_name}/{parsed.repo_name}"})

    repo = await asyncio.to_thread(GithubRepo, parsed.owner_name, parsed.repo_name)
    debug("Fetched repo info", {
        "full_name": repo.full_name,
        "description": repo.description,
        "default_branch": repo.default_branch,
    })

    readme = await asyncio.to_thread(download_readme, f"{parsed.owner_name}/{parsed.repo_name}")
    user_content = f"""Respond with a JSON object.
Key "summary": provide a short summary of this project. Start with repo name and owner, for example: "Repository 'user/repo'".
Key "technologies": provide a list of used technologies as an array of strings, for example: ["Python", "urllib3", "certifi"].
Key "structure": provide a brief description of the project structure.
Important: Return ONLY the JSON object, no markdown, no code blocks, no extra text.
{readme}"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "",
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        )
        content = response.choices[0].message.content
    except Exception as e:
        raise AppError(f"Model call failed: {str(e)}", 502)

    if not content:
        raise AppError("Model returned empty response", 502)

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        raise AppError(f"Model response is not valid JSON: {str(e)}", 502)

    indent = 2 if DEBUG else None
    return Response(
        content=json.dumps(result, indent=indent, ensure_ascii=False),
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=BIND_HOST, port=BIND_PORT)
