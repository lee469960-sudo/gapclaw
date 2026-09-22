from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_release_management_view_renders_current_release_history_and_reconciliation_state():
    source = (ROOT / "apps/web/src/views/ReleaseManagement.vue").read_text(encoding="utf-8")

    assert "/api/release-management/status" in source
    assert "/api/release-management/history" in source
    assert "reconciliation_required" in source
    assert "API digest" in source and "Web digest" in source
    assert "发布历史" in source and "健康结果" in source and "自动回滚" in source


def test_release_management_rollback_control_is_admin_only_and_requires_exact_confirmation():
    source = (ROOT / "apps/web/src/views/ReleaseManagement.vue").read_text(encoding="utf-8")

    assert 'v-if="isAdmin"' in source
    assert "/api/release-management/rollback-target" in source
    assert "/api/release-management/rollback" in source
    assert 'v-loading="rollbackLoading"' in source
    assert "loadRollbackTarget()" in source
    assert "await loadRollbackTarget()" not in source
    assert "rollbackConfirmation !== 'ROLLBACK'" in source
    assert "displayed_release_id: rollbackTarget.value.release_id" in source
    assert "displayed_target_id: rollbackTarget.value.target_id" in source
    assert "镜像、摘要或其他版本" in source


def test_release_management_view_redacts_configuration_and_guides_runner_reconciliation():
    source = (ROOT / "apps/web/src/views/ReleaseManagement.vue").read_text(encoding="utf-8")

    assert "release-management/config" not in source
    assert "client_key" not in source and "ca_file" not in source
    assert "请稍后刷新" in source
    assert "完成发布状态对账" in source
    assert "不会显示虚假的发布成功" in source


def test_release_management_route_and_permission_menu_are_registered():
    router = (ROOT / "apps/web/src/router.js").read_text(encoding="utf-8")
    menu = (ROOT / "apps/api/app/menu_config.py").read_text(encoding="utf-8")
    roles = (ROOT / "apps/web/src/views/Roles.vue").read_text(encoding="utf-8")

    assert "release-management" in router
    assert "/pages/page_release_management.cgi" in menu
    assert "/pages/page_release_management.cgi" in roles
