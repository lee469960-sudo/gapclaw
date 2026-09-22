from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_agent_chat_renders_scheduled_terminal_result_cards_and_missed_poll_recovery():
    source = (ROOT / "apps/web/src/views/AgentChat.vue").read_text(encoding="utf-8")

    assert "isScheduledProgressVisible(scheduledProgress)" in source
    assert "scheduledProgressTitle(scheduledProgress)" in source
    assert "scheduledResultPreview(scheduledProgress)" in source
    assert "scheduledNotificationLabel(scheduledProgress)" in source
    assert "locateMessage(scheduledProgress.chat_message_id)" in source
    assert "return progress?.state === 'running'" in source
    assert "scheduledProgress.value = null" in source
    assert "await pollScheduledProgress()" in source
    assert "data-message-id" in source


def test_session_tick_dialog_shows_result_preview_notification_state_and_locate_action():
    source = (ROOT / "apps/web/src/components/SessionTickDialog.vue").read_text(encoding="utf-8")

    assert "runPreview(t.latestRun)" in source
    assert "runNotificationLabel(t.latestRun)" in source
    assert "locateRun(t.latestRun)" in source
    assert "'locate-message'" in source
    assert "content_preview" in source and "terminal_result" in source
