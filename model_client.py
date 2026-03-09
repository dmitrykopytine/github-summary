import os

import anthropic

from config import ANTHROPIC_API_KEY_ENV_VAR


class ModelClient:
    def __init__(self):
        self._client = anthropic.Anthropic(
            api_key=os.environ.get(ANTHROPIC_API_KEY_ENV_VAR),
        )

    @staticmethod
    def check_api_key() -> bool:
        key = os.environ.get(ANTHROPIC_API_KEY_ENV_VAR, "")
        return bool(key.strip())

    @property
    def client(self) -> anthropic.Anthropic:
        return self._client
