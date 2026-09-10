from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EI_BACKEND_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Enterprise Insight Backend"
    app_version: str = "0.1.0"
    build_id: str = "development"
    api_prefix: str = "/api/v3"
    data_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / ".local")
    frontend_dist_dir: Path | None = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )
    database_url: str | None = None
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8012"
    agent_worker_enabled: bool = True
    agent_worker_poll_seconds: float = Field(default=0.5, ge=0.1, le=30)
    agent_worker_lease_seconds: int = Field(default=120, ge=30, le=3600)
    agent_context_max_chars: int = Field(default=60_000, ge=5_000, le=500_000)
    agent_initial_graph_entities: int = Field(default=200, ge=1, le=5_000)
    agent_initial_graph_relations: int = Field(default=400, ge=1, le=10_000)
    agent_max_model_rounds: int = Field(default=12, ge=1, le=50)
    agent_max_tool_calls: int = Field(default=40, ge=1, le=200)
    agent_model_max_retries: int = Field(default=2, ge=0, le=5)
    agent_model_retry_base_seconds: float = Field(default=0.25, ge=0, le=10)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'enterprise_insight_v3.db').as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
