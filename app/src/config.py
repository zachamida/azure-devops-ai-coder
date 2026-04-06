from pydantic_settings import BaseSettings
from typing import Dict
from functools import lru_cache
from pathlib import Path
import json

# Resolve .env relative to this file's parent directory (app/)
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    azure_openai_endpoint: str
    azure_openai_key: str
    azure_openai_deployment: str = "gpt-4o"
    azure_devops_pat: str
    azure_devops_org: str
    storage_connection_string: str
    project_repo_map: str = "{}"
    webhook_secret: str = ""
    queue_name: str = "ai-coder-tasks"
    dead_letter_queue_name: str = "ai-coder-tasks-dlq"
    max_retries: int = 3

    @property
    def project_to_repo(self) -> Dict[str, str]:
        try:
            mapping = json.loads(self.project_repo_map)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"PROJECT_REPO_MAP contains invalid JSON: {e}"
            ) from e
        if not isinstance(mapping, dict):
            raise ValueError("PROJECT_REPO_MAP must be a JSON object (dict)")
        return mapping

    class Config:
        env_file = str(_ENV_FILE)


@lru_cache
def get_settings() -> Settings:
    return Settings()
