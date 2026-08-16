"""Captcha must survive process restarts (file store) and wrong-code retries."""

from app.services import captcha as captcha_mod


def test_create_verify_once(tmp_path, monkeypatch):
    monkeypatch.setattr(
        captcha_mod,
        "get_settings",
        lambda: type("S", (), {"data_dir": str(tmp_path)})(),
    )
    cid, code = captcha_mod.create_captcha()
    assert captcha_mod.verify_captcha(cid, code)
    assert not captcha_mod.verify_captcha(cid, code)


def test_wrong_code_does_not_consume(tmp_path, monkeypatch):
    monkeypatch.setattr(
        captcha_mod,
        "get_settings",
        lambda: type("S", (), {"data_dir": str(tmp_path)})(),
    )
    cid, code = captcha_mod.create_captcha()
    assert not captcha_mod.verify_captcha(cid, "0000" if code != "0000" else "1111")
    assert captcha_mod.verify_captcha(cid, code)


def test_survives_after_reload_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        captcha_mod,
        "get_settings",
        lambda: type("S", (), {"data_dir": str(tmp_path)})(),
    )
    cid, code = captcha_mod.create_captcha()
    assert (tmp_path / "captcha_store.json").is_file()
    # Simulate new worker: only file remains (no in-memory cache in this impl)
    assert captcha_mod.verify_captcha(cid, code)
