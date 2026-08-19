from project_link_fall_response.wechat import BindingStore, NotificationLedger, format_alert
from pathlib import Path


def test_binding_is_persisted_mode_0600(tmp_path):
    path = tmp_path / "binding.json"
    store = BindingStore(path)
    store.save("contact", "context")
    assert store.load()["user_id"] == "contact"
    assert path.stat().st_mode & 0o777 == 0o600


def test_notification_ledger_is_exactly_once(tmp_path):
    ledger = NotificationLedger(tmp_path / "notifications.sqlite3")
    first, claimed = ledger.claim("event-1")
    assert claimed is True
    ledger.finish("event-1", text_success=True, image_success=False, receipt="r", message="sent")
    second, claimed = ledger.claim("event-1")
    assert claimed is False
    assert second["text_success"] == 1


def test_alert_does_not_expose_demo_or_real_mode():
    text = format_alert("event", True, 0.5, "camera unavailable", 1787131200000)
    assert "视觉未确认" in text
    assert "demo" not in text
    assert "real" not in text


def test_wechat_sdk_private_context_contract_is_version_pinned():
    package_root = Path(__file__).resolve().parents[1]
    requirements = (package_root / "requirements-orin.txt").read_text(encoding="utf-8")
    source = (
        package_root / "project_link_fall_response" / "wechat.py"
    ).read_text(encoding="utf-8")
    assert "wechatbot-sdk==0.3.0" in requirements
    assert "_context_tokens" in source
    assert "message._context_token" in source
