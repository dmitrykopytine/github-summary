import json

from config import DEBUG


def debug(context_repo: str, message: str, context: dict | None = None) -> None:
    if not DEBUG:
        return
    output = f"[{context_repo}] {message}"
    if context:
        output += " " + json.dumps(context, indent=2, ensure_ascii=False)
    print(output)
