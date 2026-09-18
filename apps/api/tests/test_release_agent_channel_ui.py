from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_channels_ui_exposes_dedicated_release_agent_feishu_binding():
    source = (ROOT / "apps/web/src/views/Channels.vue").read_text(encoding="utf-8")
    assert 'value="release-agent"' in source
    assert "release_chat_id" in source
    assert "仅发布通知" in source


def test_site_ui_exposes_version_override_for_release_sync():
    source = (ROOT / "apps/web/src/views/Site.vue").read_text(encoding="utf-8")
    assert "v-model=\"form.version\"" in source
    assert "Release Agent 自动同步" in source
