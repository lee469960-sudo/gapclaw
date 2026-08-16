from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/gap.db"
    secret_key: str = "change-me-in-production"
    data_dir: str = "./data"
    upload_dir: str = "./data/uploads"
    workplace_dir: str = "./data/workplaces"
    docker_socket: str = "unix:///var/run/docker.sock"
    docker_registry_mirror: str = ""
    gap_registry: str = ""
    gap_namespace: str = "tools_claw"
    gap_arch: str = "amd"
    # API 容器通过 docker.sock 创建沙箱时，卷挂载必须使用宿主机路径
    docker_data_host_path: str = ""
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    session_ttl_hours: int = 720
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3"
    # OpenAI-compatible Whisper ASR (录音转写)
    asr_api_key: str = ""
    asr_base_url: str = ""
    asr_model: str = "whisper-1"
    seed_dba_agent: bool = True
    default_sandbox_image: str = "myclaw-base:latest"
    tushare_mcp_url: str = ""
    seed_tushare: bool = True
    seed_system_logs: bool = True
    seed_system_postgres: bool = True
    system_pg_host: str = "127.0.0.1"
    system_pg_port: int = 15432
    system_pg_user: str = "gap"
    system_pg_password: str = "gap"
    system_pg_database: str = "gap"
    # Public HTTPS base for IM webhooks (e.g. https://xxxx.trycloudflare.com). No trailing slash.
    public_base_url: str = ""
    # RAG
    rag_max_upload_mb: int = 32
    rag_candidate_limit: int = 2000
    # OpenAI-compatible embeddings (optional; lexical fallback when empty)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def public_base_url_normalized(self) -> str:
        return (self.public_base_url or "").strip().rstrip("/")

    def ensure_dirs(self) -> None:
        for d in [self.data_dir, self.upload_dir, self.workplace_dir,
                  f"{self.data_dir}/skills", f"{self.data_dir}/engine"]:
            Path(d).mkdir(parents=True, exist_ok=True)

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
