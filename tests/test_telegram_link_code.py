from datetime import datetime, timedelta
import re

from backend.config import settings
from backend.database import User
from backend.services.subscription_service import issue_telegram_link
from backend.utils.security import (
    make_telegram_link_code,
    new_link_nonce,
    telegram_link_code_user_id,
    verify_telegram_link_code,
)

NOW = datetime(2026, 11, 15, 10, 0)
NONCE = "n" * 22


def test_a_code_carries_the_user_id_and_is_safe_for_a_telegram_link():
    code = make_telegram_link_code(10**17, new_link_nonce(), now=NOW)

    assert telegram_link_code_user_id(code) == 10**17
    assert len(code) <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", code)
    assert len(new_link_nonce()) <= 32


def test_a_code_works_only_with_the_nonce_stored_on_the_account():
    code = make_telegram_link_code(7, NONCE, now=NOW)

    assert verify_telegram_link_code(code, 7, NONCE, now=NOW)
    assert not verify_telegram_link_code(code, 7, "another-nonce", now=NOW)  # a newer link was made
    assert not verify_telegram_link_code(code, 7, None, now=NOW)  # the link was used or closed


def test_a_code_works_for_a_day_and_not_after():
    code = make_telegram_link_code(7, NONCE, now=NOW)

    assert verify_telegram_link_code(code, 7, NONCE, now=NOW + timedelta(hours=23, minutes=59))
    assert not verify_telegram_link_code(code, 7, NONCE, now=NOW + timedelta(hours=24, seconds=1))


def test_a_changed_code_is_rejected():
    code = make_telegram_link_code(7, NONCE, now=NOW)
    user_id, expires, signature = code.split("_", 2)

    assert not verify_telegram_link_code(code, 8, NONCE, now=NOW)  # someone else's account
    assert not verify_telegram_link_code(f"8_{expires}_{signature}", 8, NONCE, now=NOW)
    assert not verify_telegram_link_code(f"{user_id}_{int(expires) + 86400}_{signature}", 7, NONCE, now=NOW)
    assert not verify_telegram_link_code(f"{code[:-1]}{'A' if code[-1] != 'A' else 'B'}", 7, NONCE, now=NOW)
    assert not verify_telegram_link_code(code + "x", 7, NONCE, now=NOW)


def test_a_code_signed_with_another_key_is_rejected(monkeypatch):
    code = make_telegram_link_code(7, NONCE, now=NOW)
    monkeypatch.setattr(settings, "secret_key", "another-key")

    assert not verify_telegram_link_code(code, 7, NONCE, now=NOW)


def test_garbage_is_rejected_without_an_error():
    for junk in ("", "  ", "x", "1_2", "a_b_c", "١٢_٣٤_x", "tok", "_" * 5, "9" * 400 + "_1_x", "1_" + "9" * 400 + "_x", None):
        assert telegram_link_code_user_id(junk) is None
        assert not verify_telegram_link_code(junk, 1, NONCE, now=NOW)
    assert not verify_telegram_link_code("1_2_3", 1, NONCE, now=NOW)


def test_the_account_token_is_not_a_link_code():
    # Management tokens are urlsafe random strings; none of them parses as a code.
    assert telegram_link_code_user_id("dGhpcy1pcy1hLXRva2Vu_cmFuZG9t-token_x") is None


def test_issuing_a_link_needs_a_bot_name_and_closes_the_previous_one(db_session, monkeypatch):
    db_session.add(User(id=5, email="me@example.com", token="tok"))
    db_session.commit()
    user = db_session.get(User, 5)

    monkeypatch.setattr(settings, "telegram_bot_username", "")
    assert issue_telegram_link(db_session, user) is None
    assert user.telegram_link_nonce is None

    monkeypatch.setattr(settings, "telegram_bot_username", "alerts_bot")
    first = issue_telegram_link(db_session, user).split("start=", 1)[1]
    second_url = issue_telegram_link(db_session, user)
    assert second_url.startswith("https://t.me/alerts_bot?start=")
    second = second_url.split("start=", 1)[1]

    db_session.rollback()  # the nonce was committed, not just set
    nonce = db_session.get(User, 5).telegram_link_nonce
    assert telegram_link_code_user_id(second) == 5
    assert verify_telegram_link_code(second, 5, nonce)
    assert not verify_telegram_link_code(first, 5, nonce)
