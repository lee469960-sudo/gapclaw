import json
import re

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
    session_ttl_hours: int = 24
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3"
    # OpenAI-compatible Whisper ASR (录音转写)
    asr_api_key: str = ""
    asr_base_url: str = ""
    asr_model: str = "whisper-1"
    seed_dba_agent: bool = True
    default_sandbox_image: str = "myclaw-base:latest"
    code_repository_allowlist: str = "[]"
    code_repository_internal_cidrs: str = "[]"
    code_repository_ssh_known_hosts_file: str = ""
    code_repository_use_transport_proxy: bool = False
    code_repository_allow_public_http: bool = False
    code_repository_http_proxy: str = ""
    code_repository_https_proxy: str = ""
    code_local_repository_roots: str = "[]"
    code_trusted_image_digests: str = "[]"
    code_workspace_api_root: str = ""
    code_workspace_host_root: str = ""
    code_import_max_download_bytes: int = 536_870_912
    code_import_max_unpacked_bytes: int = 1_073_741_824
    code_import_max_files: int = 100_000
    code_import_max_file_bytes: int = 67_108_864
    code_import_timeout_seconds: int = 300
    code_snapshot_retention_hours: int = 720
    code_workspace_retention_hours: int = 168
    code_snapshot_capacity_bytes: int = 53_687_091_200
    code_workspace_capacity_bytes: int = 21_474_836_480
    code_storage_low_watermark_bytes: int = 1_073_741_824
    code_claude_code_runtime_enabled: bool = False
    seed_code_agent: bool = True
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
    # Release Agent control plane: one process-fixed deployment environment.
    # Target, Runner URL, and callback identity are derived from this value and
    # can never be selected by a browser, API request, or release manifest.
    release_environment: str = "production"
    release_runner_ca_file: str = ""
    release_runner_client_cert_file: str = ""
    release_runner_client_key_file: str = ""
    release_runner_timeout_seconds: float = 10.0

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
        if self.code_workspace_api_root:
            api_root = Path(self.code_workspace_api_root)
            if api_root.is_absolute():
                try:
                    api_root.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _string_list(raw: str, field: str, errors: dict[str, str]) -> list[str]:
        try:
            values = json.loads(raw or "[]")
        except json.JSONDecodeError:
            errors[field] = "code_config_invalid_json_list"
            return []
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            errors[field] = "code_config_invalid_string_list"
            return []
        return [value.strip() for value in values]

    @staticmethod
    def _valid_repository_proxy_url(value: str) -> bool:
        raw = str(value or "")
        if not raw:
            return True
        if raw != raw.strip() or re.search(r"[\x00-\x20\x7f]", raw):
            return False
        try:
            from urllib.parse import urlsplit

            parsed = urlsplit(raw)
        except ValueError:
            return False
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.query
            and not parsed.fragment
        )

    def code_agent_workspace_mapping_readiness(self) -> dict:
        """Read-only Workspace mount mapping probe (side-effect free).

        Validates that the API-visible Workspace root and the daemon-host root are
        well-formed and — when the data bind-mount host path is known — that both
        resolve to the same on-disk storage through that single bind mount, so the
        API can never hand the Docker daemon a path pointing at a different
        directory or a guessed sibling.
        """
        errors: dict[str, str] = {}

        api_root = Path(self.code_workspace_api_root) if self.code_workspace_api_root else None
        data_root = Path(self.data_dir).resolve()
        api_rel: tuple[str, ...] | None = None
        if api_root is None or not api_root.is_absolute():
            errors["workspace_api_root"] = "code_config_workspace_api_root_missing"
        else:
            try:
                resolved_api_root = api_root.resolve(strict=True)
                api_rel = resolved_api_root.relative_to(data_root).parts
            except (OSError, ValueError):
                errors["workspace_api_root"] = "code_config_workspace_api_root_invalid"

        host_root = Path(self.code_workspace_host_root) if self.code_workspace_host_root else None
        if host_root is None or not host_root.is_absolute() or ".." in host_root.parts:
            errors["workspace_host_root"] = "code_config_workspace_host_root_invalid"
        elif api_rel is not None:
            host_data_root = (
                Path(self.docker_data_host_path) if self.docker_data_host_path else None
            )
            if host_data_root is None:
                # Without the bind-mount host path the two roots cannot be proven
                # to share storage; the format validation above still applies.
                pass
            elif not host_data_root.is_absolute() or ".." in host_data_root.parts:
                errors["workspace_mapping"] = "code_config_workspace_data_host_path_invalid"
            else:
                try:
                    host_rel = host_root.relative_to(host_data_root).parts
                except ValueError:
                    errors["workspace_mapping"] = "code_config_workspace_host_root_not_same_storage"
                else:
                    if host_rel != api_rel:
                        errors["workspace_mapping"] = "code_config_workspace_roots_not_same_storage"

        return {
            "ready": not errors,
            "status": "ready" if not errors else "unavailable",
            "reason": "ready" if not errors else "code_agent_workspace_mapping_invalid",
            "errors": errors,
        }

    def code_agent_security_readiness(self) -> dict:
        """Validate deploy-time CodeAgent security settings without stopping the API."""
        from app.services.code_agent.source_policy import (
            RepositorySourcePolicyError,
            normalize_repository_allowlist,
        )
        from app.services.code_agent.network_policy import (
            RepositoryNetworkPolicyError,
            normalize_approved_internal_cidrs,
        )

        errors: dict[str, str] = {}
        allowlist = self._string_list(
            self.code_repository_allowlist, "repository_allowlist", errors
        )
        internal_cidrs = self._string_list(
            self.code_repository_internal_cidrs, "repository_internal_cidrs", errors
        )
        local_roots = self._string_list(
            self.code_local_repository_roots, "local_repository_roots", errors
        )
        image_digests = self._string_list(
            self.code_trusted_image_digests, "trusted_image_digests", errors
        )

        if allowlist:
            try:
                normalized_origins = normalize_repository_allowlist(allowlist)
            except RepositorySourcePolicyError:
                errors["repository_allowlist"] = "code_config_invalid_repository_allowlist"
                normalized_origins = frozenset()
        else:
            normalized_origins = frozenset()

        try:
            normalize_approved_internal_cidrs(internal_cidrs)
        except RepositoryNetworkPolicyError:
            errors["repository_internal_cidrs"] = "code_config_invalid_internal_cidrs"
        uses_proxy = bool(self.code_repository_use_transport_proxy)
        allows_public_http = bool(self.code_repository_allow_public_http)
        if uses_proxy:
            if not self._valid_repository_proxy_url(self.code_repository_http_proxy):
                errors["repository_http_proxy"] = "code_config_invalid_repository_proxy"
            if not self._valid_repository_proxy_url(self.code_repository_https_proxy):
                errors["repository_https_proxy"] = "code_config_invalid_repository_proxy"
            if any(origin.startswith("http://") for origin in normalized_origins) and not self.code_repository_http_proxy:
                errors["repository_http_proxy"] = "code_config_repository_http_proxy_missing"
            if (
                any(origin.startswith("https://") for origin in normalized_origins)
                and not self.code_repository_https_proxy
            ):
                errors["repository_https_proxy"] = "code_config_repository_https_proxy_missing"

        if (
            any(origin.startswith("http://") for origin in normalized_origins)
            and not internal_cidrs
            and not uses_proxy
            and not allows_public_http
        ):
            errors.setdefault(
                "repository_internal_cidrs",
                "code_config_internal_cidrs_required",
            )
        if any(origin.startswith("ssh://") for origin in normalized_origins):
            known_hosts_value = self.code_repository_ssh_known_hosts_file or ""
            known_hosts = Path(known_hosts_value)
            if not known_hosts_value:
                errors["repository_ssh_known_hosts_file"] = (
                    "code_config_ssh_known_hosts_file_unavailable"
                )
            else:
                try:
                    resolved_known_hosts = known_hosts.resolve(strict=True)
                except OSError:
                    errors["repository_ssh_known_hosts_file"] = (
                        "code_config_ssh_known_hosts_file_unavailable"
                    )
                else:
                    if (
                        not known_hosts.is_absolute()
                        or known_hosts.is_symlink()
                        or not resolved_known_hosts.is_file()
                    ):
                        errors["repository_ssh_known_hosts_file"] = (
                            "code_config_invalid_ssh_known_hosts_file"
                        )

        for value in local_roots:
            root = Path(value)
            try:
                resolved = root.resolve(strict=True)
            except OSError:
                errors["local_repository_roots"] = "code_config_local_root_unavailable"
                break
            if not root.is_absolute() or root.is_symlink() or not resolved.is_dir():
                errors["local_repository_roots"] = "code_config_invalid_local_root"
                break

        if not allowlist and not local_roots:
            errors.setdefault("repository_sources", "code_config_repository_sources_missing")

        errors.update(self.code_agent_workspace_mapping_readiness()["errors"])

        digest_pattern = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
        if not image_digests:
            errors.setdefault("trusted_image_digests", "code_config_trusted_images_missing")
        elif any(not digest_pattern.fullmatch(value) for value in image_digests):
            errors["trusted_image_digests"] = "code_config_invalid_image_digest"

        positive_limits = {
            "import_max_download_bytes": self.code_import_max_download_bytes,
            "import_max_unpacked_bytes": self.code_import_max_unpacked_bytes,
            "import_max_files": self.code_import_max_files,
            "import_max_file_bytes": self.code_import_max_file_bytes,
            "import_timeout_seconds": self.code_import_timeout_seconds,
            "snapshot_retention_hours": self.code_snapshot_retention_hours,
            "snapshot_capacity_bytes": self.code_snapshot_capacity_bytes,
            "workspace_capacity_bytes": self.code_workspace_capacity_bytes,
            "storage_low_watermark_bytes": self.code_storage_low_watermark_bytes,
        }
        for field, value in positive_limits.items():
            if value <= 0:
                errors[field] = "code_config_limit_must_be_positive"
        if self.code_workspace_retention_hours < 0:
            errors["workspace_retention_hours"] = "code_config_retention_must_be_non_negative"

        return {
            "ready": not errors,
            "status": "ready" if not errors else "unavailable",
            "reason": "ready" if not errors else "code_agent_security_config_invalid",
            "errors": errors,
        }

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
