"""ContextManager coach-hint aggregation: multiple in-loop hints merge, none lost."""

from __future__ import annotations

from app.services.agent_runtime.context_manager import ContextManager


def _coach_hint_content(cm: ContextManager) -> str:
    """Return the current coach_hint layer content, or None if not present."""
    layer = cm._layers["coach_hint"]
    if not layer.present:
        return None
    return cm._messages[layer.start]["content"]


def test_multiple_hints_merge_without_loss():
    cm = ContextManager()
    # Establish the layer early (as the runtime does with the initial setup hint).
    cm.push_coach_hint("setup")

    cm.add_coach_hint("【卡死检测】尝试换个工具")
    cm.add_coach_hint("【完成度反思】还缺 view_map")
    cm.add_coach_hint("【预算提示】剩余轮次不多")
    cm.flush_coach_hints()

    content = _coach_hint_content(cm)
    assert content is not None
    assert "【卡死检测】尝试换个工具" in content
    assert "【完成度反思】还缺 view_map" in content
    assert "【预算提示】剩余轮次不多" in content


def test_duplicate_hints_are_deduped():
    cm = ContextManager()
    cm.push_coach_hint("setup")

    cm.add_coach_hint("【卡死检测】尝试换个工具")
    cm.add_coach_hint("【卡死检测】尝试换个工具")  # duplicate, dropped
    cm.flush_coach_hints()

    content = _coach_hint_content(cm)
    assert content.count("【卡死检测】尝试换个工具") == 1


def test_flush_with_empty_buffer_keeps_established_layer():
    cm = ContextManager()
    cm.push_coach_hint("setup")

    cm.flush_coach_hints()  # no-op, buffer empty

    assert _coach_hint_content(cm) == "setup"


def test_empty_hints_are_ignored():
    cm = ContextManager()
    cm.push_coach_hint("setup")

    cm.add_coach_hint("")
    cm.add_coach_hint("   ")
    cm.flush_coach_hints()

    assert _coach_hint_content(cm) == "setup"
