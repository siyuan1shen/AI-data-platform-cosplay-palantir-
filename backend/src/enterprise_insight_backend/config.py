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
    # The platform now uses one physical database by default.  The explicit
    # per-domain URLs remain as a compatibility escape hatch for restoring an
    # old installation or running an isolated migration test.
    unified_storage: bool = True
    observation_database_url: str | None = None
    potential_database_url: str | None = None
    control_database_url: str | None = None
    control_index_poll_seconds: float = Field(default=30, ge=5, le=3600)
    action_recovery_poll_seconds: float = Field(default=15, ge=5, le=3600)
    action_recovery_grace_seconds: int = Field(default=60, ge=10, le=86400)
    action_recovery_batch_size: int = Field(default=25, ge=1, le=500)
    local_actor_id: str = "local-owner"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8012"
    agent_worker_enabled: bool = True
    agent_worker_poll_seconds: float = Field(default=0.5, ge=0.1, le=30)
    agent_worker_lease_seconds: int = Field(default=120, ge=30, le=3600)
    # Complex management reads may combine a formal summary, work paths and
    # materialized ERP observations. Keep enough room for the next model round
    # while retaining the hard upper bound as a local safety valve.
    agent_context_max_chars: int = Field(default=100_000, ge=5_000, le=500_000)
    agent_initial_graph_entities: int = Field(default=200, ge=1, le=5_000)
    agent_initial_graph_relations: int = Field(default=400, ge=1, le=10_000)
    # Every non-graph collection placed in an Agent context has an explicit
    # ceiling as well.  Tools can still narrow or page through the source;
    # the initial prompt must never become a full project dump.
    agent_context_ontology_types: int = Field(default=500, ge=1, le=10_000)
    agent_context_source_systems: int = Field(default=100, ge=1, le=2_000)
    agent_context_semantic_mappings: int = Field(default=500, ge=1, le=20_000)
    agent_context_action_definitions: int = Field(default=100, ge=1, le=2_000)
    agent_context_metric_definitions: int = Field(default=500, ge=1, le=10_000)
    agent_context_design_tradeoffs: int = Field(default=100, ge=1, le=5_000)
    agent_route_identity_candidates: int = Field(default=500, ge=10, le=10_000)
    agent_max_model_rounds: int = Field(default=12, ge=1, le=50)
    agent_max_tool_calls: int = Field(default=40, ge=1, le=200)
    agent_model_max_retries: int = Field(default=2, ge=0, le=5)
    agent_model_retry_base_seconds: float = Field(default=0.25, ge=0, le=10)
    # Lifecycle cleanup only applies to temporary previews and generated files.
    # Formal enterprise facts, management observations, potential records and
    # confirmed work observations are never included in this automatic path.
    lifecycle_cleanup_enabled: bool = True
    lifecycle_cleanup_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    lifecycle_work_observation_preview_days: int = Field(default=7, ge=1, le=3650)
    lifecycle_export_days: int = Field(default=7, ge=1, le=3650)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'enterprise_insight_v3.db').as_posix()}"

    @property
    def resolved_observation_database_url(self) -> str:
        if self.observation_database_url:
            return self.observation_database_url
        if self.unified_storage:
            return self.resolved_database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'observations_v1.db').as_posix()}"

    @property
    def resolved_potential_database_url(self) -> str:
        if self.potential_database_url:
            return self.potential_database_url
        if self.unified_storage:
            return self.resolved_database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'potential_v1.db').as_posix()}"

    @property
    def resolved_control_database_url(self) -> str:
        if self.control_database_url:
            return self.control_database_url
        if self.unified_storage:
            return self.resolved_database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'control_v1.db').as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
