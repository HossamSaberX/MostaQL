from datetime import datetime, timedelta
import re

from backend.config import settings
from backend.services.messages import telegram_link_url
from backend.utils.security import make_telegram_link_code, read_telegram_link_code

NOW = datetime(2026, 11, 15, 10, 0)


def test_a_code_carries_the_user_id_and_is_safe_for_a_telegram_link():
    code = make_telegram_link_code(1234, now=NOW)

    assert read_telegram_link_code(code, now=NOW) == 1234
    assert len(code) <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", code)


def test_a_code_works_for_a_week_and_not_after():
    code = make_telegram_link_code(7, now=NOW)

    assert read_telegram_link_code(code, now=NOW + timedelta(days=6, hours=23)) == 7
    assert read_telegram_link_code(code, now=NOW + timedelta(days=7, seconds=1)) is None


def test_a_changed_code_is_rejected():
    code = make_telegram_link_code(7, now=NOW)
    user_id, expires, signature = code.split("_", 2)

    assert read_telegram_link_code(f"8_{expires}_{signature}", now=NOW) is None  # someone else's id
    assert read_telegram_link_code(f"{user_id}_{int(expires) + 86400}_{signature}", now=NOW) is None  # longer life
    assert read_telegram_link_code(f"{code[:-1]}{'A' if code[-1] != 'A' else 'B'}", now=NOW) is None
    assert read_telegram_link_code(code + "x", now=NOW) is None


def test_a_code_signed_with_another_key_is_rejected(monkeypatch):
    code = make_telegram_link_code(7, now=NOW)
    monkeypatch.setattr(settings, "secret_key", "another-key")

    assert read_telegram_link_code(code, now=NOW) is None


def test_garbage_is_rejected_without_an_error():
    for junk in ("", "  ", "x", "1_2", "a_b_c", "1_2_3", "١٢_٣٤_x", "tok", "_" * 5, "9" * 400 + "_1_x", None):
        assert read_telegram_link_code(junk, now=NOW) is None


def test_the_account_token_is_not_a_link_code():
    # Management tokens are urlsafe random strings; none of them parses as a code.
    assert read_telegram_link_code("dGhpcy1pcy1hLXRva2Vu_cmFuZG9t-token_x", now=NOW) is None


def test_the_deep_link_needs_a_bot_name(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "")
    assert telegram_link_url(5) is None

    monkeypatch.setattr(settings, "telegram_bot_username", "alerts_bot")
    url = telegram_link_url(5)
    assert url.startswith("https://t.me/alerts_bot?start=")
    assert read_telegram_link_code(url.split("start=", 1)[1]) == 5
