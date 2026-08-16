import asyncio
from typing import Callable


class ChatStopped(Exception):
    """Raised when the user requests stop_chat mid-run."""


class ChatStreamHub:
    """Simple in-process pub/sub event bus for WebSocket streaming."""

    def __init__(self):
        self._subs: dict[str, list[Callable]] = {}

    def subscribe(self, key: str, cb: Callable):
        self._subs.setdefault(key, []).append(cb)

    def unsubscribe(self, key: str, cb: Callable):
        if key in self._subs:
            self._subs[key] = [c for c in self._subs[key] if c != cb]

    async def publish(self, key: str, event: dict):
        for cb in self._subs.get(key, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception:
                pass


hub = ChatStreamHub()
_running: dict[str, bool] = {}


def chat_key(agent_id: str, session_id: str) -> str:
    return f"{agent_id}:{session_id}"


def is_running(agent_id: str, session_id: str) -> bool:
    return _running.get(chat_key(agent_id, session_id), False)


def is_stop_requested(key: str) -> bool:
    """True when stop_chat flipped the run flag (or never started)."""
    return not _running.get(key, False)


def stop_chat(agent_id: str, session_id: str):
    _running[chat_key(agent_id, session_id)] = False
