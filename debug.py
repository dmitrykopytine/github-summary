import json

from config import DEBUG


def debug(message: str, context: dict | None = None) -> None:
    if not DEBUG:
        return
    output = message
    if context:
        output += "\n" + json.dumps(context, indent=2, ensure_ascii=False)
    print(output)
