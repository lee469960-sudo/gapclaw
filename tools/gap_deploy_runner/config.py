"""Host-only configuration and fixed Compose inputs for Deploy Runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tools.gap_deploy_runner.release_manifest import ReleaseManifest


class RunnerConfigError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _read_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RunnerConfigError("runner_host_env_unavailable") from exc
    values: dict[str, str] = {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[7:].lstrip()
        if "=" not in raw:
            raise RunnerConfigError("runner_host_env_invalid")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key or key.startswith("GITHUB_"):
            raise RunnerConfigError("runner_github_credential_forbidden")
        values[key] = value.strip().strip('"').strip("'")
    return values


def _web_repository(image: str) -> str:
    return f"{image[:-4]}-web" if image.endswith("-api") else f"{image}-web"


@dataclass(frozen=True)
class RunnerConfig:
    target_id: str
    env_file: Path
    values: Mapping[str, str]
    allowed_images: Mapping[str, str]

    @classmethod
    def from_env_file(cls, path: Path, *, target_id: str) -> "RunnerConfig":
        values = _read_env(path)
        required = ("ALIYUN_REGISTRY", "ALIYUN_REGISTRY_USERNAME", "ALIYUN_REGISTRY_PASSWORD", "IMAGE_DB")
        if any(not values.get(key) for key in required):
            raise RunnerConfigError("runner_host_acr_config_missing")
        api = values.get("IMAGE_API")
        web = values.get("IMAGE_WEB")
        if not api or not web:
            image = values.get("ALIYUN_IMAGE", "")
            if not image:
                raise RunnerConfigError("runner_host_image_repository_missing")
            api = api or f"{values['ALIYUN_REGISTRY']}/{image}"
            web = web or f"{values['ALIYUN_REGISTRY']}/{_web_repository(image)}"
        return cls(target_id=target_id, env_file=path, values=values, allowed_images={"api": api, "web": web})

    def compose_environment(self, manifest: ReleaseManifest) -> dict[str, str]:
        if manifest.target_id != self.target_id:
            raise RunnerConfigError("runner_target_not_allowed")
        result = dict(self.values)
        result.update({
            "GAP_RELEASE_API_IMAGE": manifest.api_image,
            "GAP_RELEASE_WEB_IMAGE": manifest.web_image,
            "GAP_VERSION": manifest.version,
        })
        return result
