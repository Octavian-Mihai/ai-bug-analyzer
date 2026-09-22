from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_max_tokens: int = 4096
    llm_max_retries: int = 1

    github_token: str | None = None
    allowed_git_hosts: list[str] = ["github.com"]

    clone_timeout_seconds: int = 60
    max_repo_size_mb: int = 500

    pip_install_timeout_seconds: int = 60
    pytest_discovery_timeout_seconds: int = 120
    pytest_timeout_seconds: int = 120
    pytest_memory_limit_mb: int = 1024

    max_context_files: int = 5
    max_context_lines_per_file: int = 40
    max_context_chars: int = 12000

    max_concurrent_analyses: int = 2
    workdir_root: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
