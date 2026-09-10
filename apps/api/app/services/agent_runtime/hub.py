import asyncio
from typing import Callable


class ChatStopped(Exception):
    """Raised when the user requests stop_chat mid-run."""


class ChatStreamHub:
    """In-process pub/sub event bus for WebSocket streaming.

    Subscribers (WebSocket handlers) are bound to the serving event loop, while
    publishers (the agent run) live in a separate ``asyncio.run`` loop on a
    background thread. Events are dispatched onto the subscriber's own loop so
    ``websocket.send_json`` never crosses loops.
    """

    def __init__(self):
        self._subs: dict[str, list[tuple[Callable, asyncio.AbstractEventLoop | None]]] = {}

    def subscribe(self, key: str, cb: Callable):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._subs.setdefault(key, []).append((cb, loop))

    def unsubscribe(self, key: str, cb: Callable):
        if key in self._subs:
            self._subs[key] = [(c, l) for (c, l) in self._subs[key] if c != cb]

    async def publish(self, key: str, event: dict):
        for cb, loop in list(self._subs.get(key, [])):
            try:
                if loop is None or loop.is_closed():
                    continue
                if asyncio.get_running_loop() is loop:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(event)
                    else:
                        cb(event)
                elif asyncio.iscoroutinefunction(cb):
                    fut = asyncio.run_coroutine_threadsafe(cb(event), loop)
                    await asyncio.wrap_future(fut)
                else:
                    loop.call_soon_threadsafe(cb, event)
            except Exception:
                pass


hub = ChatStreamHub()
_running: dict[str, bool] = {}
_auto_start_blocked: set[str] = set()


def chat_key(agent_id: str, session_id: str) -> str:
    return f"{agent_id}:{session_id}"


def inbox_key(agent_id: str) -> str:
    """Agent-scoped subscription key for cross-session inbound notifications."""
    return f"agent_inbox:{agent_id}"


def is_running(agent_id: str, session_id: str) -> bool:
    return _running.get(chat_key(agent_id, session_id), False)


def is_auto_start_blocked(agent_id: str, session_id: str) -> bool:
    return chat_key(agent_id, session_id) in _auto_start_blocked


def clear_auto_start_block(agent_id: str, session_id: str) -> None:
    _auto_start_blocked.discard(chat_key(agent_id, session_id))


def stop_chat(agent_id: str, session_id: str, *, block_auto_start: bool = True) -> bool:
    key = chat_key(agent_id, session_id)
    was_running = _running.get(key, False)
    _running[key] = False
    if block_auto_start:
        _auto_start_blocked.add(key)
    try:
        from app.services.code_agent.lifecycle import request_code_termination
        request_code_termination(key, "cancelled")
    except Exception:
        pass
    return was_running
