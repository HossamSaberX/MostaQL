from datetime import datetime, timedelta
from types import SimpleNamespace
import re

from backend.config import settings
from backend.services.messages import telegram_link_url
from backend.utils.security import (
    make_telegram_link_code,
    telegram_link_code_user_id,
    verify_telegram_link_code,
)

NOW = datetime(2026, 11, 15, 10, 0)


def test_a_code_carries_the_user_id_and_is_safe_for_a_telegram_link():
    code = make_telegram_link_code(1234, now=NOW)

    assert telegram_link_code_user_id(code) == 1234
    assert verify_telegram_link_code(code, 1234, None, now=NOW)
    assert len(code) <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", code)


def test_a_code_works_once_and_stops_after_a_chat_is_linked():
    code = make_telegram_link_code(7, now=NOW)

    assert verify_telegram_link_code(code, 7, None, now=NOW)
    assert not verify_telegram_link_code(code, 7, "42", now=NOW)  # the account now has a chat
    newer = make_telegram_link_code(7, "42", now=NOW)
    assert verify_telegram_link_code(newer, 7, "42", now=NOW)
    assert not verify_telegram_link_code(newer, 7, "99", now=NOW)


def test_a_code_works_for_a_day_and_not_after():
    code = make_telegram_link_code(7, now=NOW)

    assert verify_telegram_link_code(code, 7, None, now=NOW + timedelta(hours=23, minutes=59))
    assert not verify_telegram_link_code(code, 7, None, now=NOW + timedelta(hours=24, seconds=1))


def test_a_changed_code_is_rejected():
    code = make_telegram_link_code(7, now=NOW)
    user_id, expires, signature = code.split("_", 2)

    assert not verify_telegram_link_code(code, 8, None, now=NOW)  # someone else's account
    assert not verify_telegram_link_code(f"8_{expires}_{signature}", 8, None, now=NOW)
    assert not verify_telegram_link_code(f"{user_id}_{int(expires) + 86400}_{signature}", 7, None, now=NOW)
    assert not verify_telegram_link_code(f"{code[:-1]}{'A' if code[-1] != 'A' else 'B'}", 7, None, now=NOW)
    assert not verify_telegram_link_code(code + "x", 7, None, now=NOW)


def test_a_code_signed_with_another_key_is_rejected(monkeypatch):
    code = make_telegram_link_code(7, now=NOW)
    monkeypatch.setattr(settings, "secret_key", "another-key")

    assert not verify_telegram_link_code(code, 7, None, now=NOW)


def test_garbage_is_rejected_without_an_error():
    for junk in ("", "  ", "x", "1_2", "a_b_c", "١٢_٣٤_x", "tok", "_" * 5, "9" * 400 + "_1_x", "1_" + "9" * 400 + "_x", None):
        assert telegram_link_code_user_id(junk) is None
        assert not verify_telegram_link_code(junk, 1, None, now=NOW)
    assert not verify_telegram_link_code("1_2_3", 1, None, now=NOW)


def test_the_account_token_is_not_a_link_code():
    # Management tokens are urlsafe random strings; none of them parses as a code.
    assert telegram_link_code_user_id("dGhpcy1pcy1hLXRva2Vu_cmFuZG9t-token_x") is None


def test_the_deep_link_needs_a_bot_name(monkeypatch):
    user = SimpleNamespace(id=5, telegram_chat_id=None)
    monkeypatch.setattr(settings, "telegram_bot_username", "")
    assert telegram_link_url(user) is None

    monkeypatch.setattr(settings, "telegram_bot_username", "alerts_bot")
    url = telegram_link_url(user)
    assert url.startswith("https://t.me/alerts_bot?start=")
    code = url.split("start=", 1)[1]
    assert telegram_link_code_user_id(code) == 5
    assert verify_telegram_link_code(code, 5, None)
