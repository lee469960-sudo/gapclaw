"""OpenAI-compatible Whisper speech-to-text.

Note: MiniMax does not provide /audio/transcriptions — do not fall back to MiniMax for ASR.
Configure ASR_API_KEY + ASR_BASE_URL (Whisper-compatible) for server-side transcription.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ALLOWED_EXTS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mpeg", ".mpga"}
MAX_BYTES = 10 * 1024 * 1024
MINIMAX_HOST_MARKERS = ("minimax", "minimaxi")


class AsrError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _is_minimax_base(base: str) -> bool:
    lower = (base or "").lower()
    return any(m in lower for m in MINIMAX_HOST_MARKERS)


def _asr_credentials() -> tuple[str, str, str]:
    settings = get_settings()
    # 仅使用显式 ASR_*，避免把 MiniMax LLM key 误当成 Whisper
    api_key = (settings.asr_api_key or "").strip()
    base = (settings.asr_base_url or "").strip().rstrip("/")
    model = (settings.asr_model or "whisper-1").strip()

    if not api_key or not base:
        raise AsrError(
            "未配置服务端语音转写（ASR_API_KEY / ASR_BASE_URL）。"
            "MiniMax 仅支持 LLM/TTS，不支持 Whisper。"
            "请使用 Chrome/Edge 浏览器语音识别，或配置兼容 /audio/transcriptions 的地址。"
        )
    if _is_minimax_base(base):
        raise AsrError(
            "当前 ASR_BASE_URL 指向 MiniMax，该平台无 /audio/transcriptions 接口（会返回 404）。"
            "请改用支持 Whisper 的网关，或直接使用浏览器语音识别。"
        )
    return api_key, base, model


def validate_audio(filename: str, size: int) -> None:
    if size <= 0:
        raise AsrError("音频为空")
    if size > MAX_BYTES:
        raise AsrError(f"音频过大（上限 {MAX_BYTES // (1024 * 1024)}MB）")
    ext = Path(filename or "").suffix.lower()
    if ext and ext not in ALLOWED_EXTS:
        raise AsrError(f"不支持的音频格式: {ext}")


async def transcribe_audio(filename: str, content: bytes, content_type: str | None = None) -> str:
    validate_audio(filename, len(content))
    api_key, base, model = _asr_credentials()
    url = f"{base}/audio/transcriptions"
    mime = content_type or "application/octet-stream"
    name = filename or "recording.webm"
    files = {"file": (name, content, mime)}
    data = {"model": model}
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
    except httpx.HTTPError as e:
        logger.error("ASR request failed: %s", e)
        raise AsrError(f"转写服务请求失败: {e}") from e

    if resp.status_code >= 400:
        detail = resp.text[:300]
        logger.error("ASR HTTP %s: %s", resp.status_code, detail)
        if resp.status_code == 404:
            raise AsrError(
                "转写接口不存在（404）。请确认 ASR_BASE_URL 指向支持 "
                "POST /audio/transcriptions 的 Whisper 服务，而不是 MiniMax。"
            )
        raise AsrError(f"转写失败（HTTP {resp.status_code}）: {detail or '服务商错误'}")

    try:
        payload = resp.json()
    except Exception as e:
        raise AsrError("转写响应不是 JSON") from e

    text = (payload.get("text") or "").strip()
    if not text:
        raise AsrError("未识别到有效语音内容")
    return text
