import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from backend.api import webhook
from backend.config import settings
from backend.database import User
from backend.services import notification_queue
from backend.services.notification_queue import (
    TelegramTask,
    TelegramTaskQueue,
    register_bot_commands,
    send_telegram_message,
)
from backend.services.notifier import render_telegram_alert
from backend.utils.security import make_telegram_link_code

MANAGE = ("⚙️ إدارة التنبيهات", "https://alerts.test/api/unsubscribe/tok")


def job(n, **overrides):
    payload = {
        "title": f"مشروع {n} " + "طويل " * 20,
        "url": f"https://mostaql.com/project/{n}",
        "budget": "$100 - $250",
        "hiring_rate": "75.00%",
        "projects_in_progress": 1,
        "ongoing_communications": 2,
        "verification": "موثق (الهوية)",
        "project_age_minutes": 3,
        "category": "برمجة",
    }
    payload.update(overrides)
    return payload


# ---- the renderer --------------------------------------------------------------------


def test_one_message_holds_a_short_alert_with_card_facts_and_reason():
    (message,) = render_telegram_alert([job(1, followed_client="أحمد")])

    assert message.startswith("<b>مشروع جديد واحد</b>")
    assert '<b><a href="https://mostaql.com/project/1">' in message
    for fact in ("الميزانية: $100 - $250", "التوثيق: موثق (الهوية)", "عمر المشروع: 3 دقائق"):
        assert fact in message
    assert "<i>عميل سابق تتابعه: أحمد</i>" in message


def test_long_alerts_split_between_projects_under_the_limit():
    jobs = [job(n) for n in range(1, 61)]

    messages = render_telegram_alert(jobs, per_message=1000, max_messages=1000)

    assert len(messages) > 1
    assert all(len(message) <= 4096 for message in messages)
    for n in range(1, 61):
        owners = [m for m in messages if f"https://mostaql.com/project/{n}\"" in m]
        assert len(owners) == 1
    for message in messages:
        assert message.count("<a ") == message.count("</a>")
        assert message.count("<b>") == message.count("</b>")
    assert messages[0].startswith("<b>60 مشروعًا جديدًا</b>")
    assert not messages[1].startswith("<b>60")


def test_a_message_holds_ten_projects_and_three_messages_make_an_alert():
    messages = render_telegram_alert([job(n, title=f"م{n}") for n in range(1, 36)])

    assert len(messages) == 3
    assert messages[0].startswith("<b>35 مشروعًا جديدًا</b>")
    assert [message.count("https://mostaql.com/project/") for message in messages] == [10, 10, 10]
    for message, numbers in zip(messages, (range(1, 11), range(11, 21), range(21, 31))):
        assert all(f'project/{n}"' in message for n in numbers)
    assert 'project/31"' not in "".join(messages)
    assert messages[2].endswith('<a href="https://mostaql.com/projects">و5 أخرى</a>')
    assert "أخرى" not in messages[0] + messages[1]


def test_the_more_line_always_fits_and_counts_every_project_left_out():
    long_title = "ع" * 300
    jobs = [job(n, title=long_title, followed_client="أ" * 100) for n in range(1, 41)]

    messages = render_telegram_alert(jobs)

    assert len(messages) <= 3
    assert all(len(message) <= 4096 for message in messages)
    shown = sum(message.count("https://mostaql.com/project/") for message in messages)
    assert messages[-1].endswith(f"و{40 - shown} أخرى</a>")


def test_an_alert_that_fits_has_no_more_line():
    (message,) = render_telegram_alert([job(n) for n in range(1, 4)])

    assert "أخرى" not in message


def test_past_client_projects_are_first_even_when_they_arrive_last():
    jobs = [job(n) for n in range(1, 40)] + [job(99, followed_client="أحمد")]

    messages = render_telegram_alert(jobs)

    assert messages[0].index("project/99") < messages[0].index("project/1\"")


def test_telegram_text_is_escaped():
    (message,) = render_telegram_alert(
        [job(1, title="<b>x</b> & y", followed_client="<i>z</i>", url='https://a.test/?a=1&b="2"')]
    )

    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in message
    assert "<i>z</i>" not in message
    assert 'href="https://a.test/?a=1&amp;b=&quot;2&quot;"' in message


def test_nothing_to_render_gives_no_messages():
    assert render_telegram_alert([]) == []


# ---- sending --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"ok": status_code == 200}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture
def telegram(monkeypatch):
    """Record every Telegram call; tests queue the responses to return."""

    class Recorder:
        calls = []
        responses = []
        sleeps = []

    Recorder.calls, Recorder.responses, Recorder.sleeps = [], [], []

    def fake_post(url, json=None, timeout=None):
        Recorder.calls.append((url, json))
        return Recorder.responses.pop(0) if Recorder.responses else FakeResponse()

    monkeypatch.setattr(notification_queue.requests, "post", fake_post)
    monkeypatch.setattr(notification_queue.time, "sleep", Recorder.sleeps.append)
    return Recorder


def test_message_has_no_link_preview_and_a_manage_button(telegram):
    assert send_telegram_message("42", "<b>hi</b>", buttons=[MANAGE]) is True

    url, payload = telegram.calls[0]
    assert url.endswith("/sendMessage")
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert "disable_web_page_preview" not in payload
    assert "disable_notification" not in payload
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": MANAGE[0], "url": MANAGE[1]}]]
    }


def test_silent_messages_do_not_make_a_sound(telegram):
    send_telegram_message("42", "hi", silent=True)

    assert telegram.calls[0][1]["disable_notification"] is True


def test_nothing_is_sent_without_a_bot_token(telegram, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "")

    assert send_telegram_message("42", "hi") is False
    assert telegram.calls == []


def test_too_many_requests_waits_then_retries_once(telegram):
    telegram.responses = [
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 3}}),
        FakeResponse(200),
    ]

    assert send_telegram_message("42", "hi") is True

    assert telegram.sleeps == [3]
    assert len(telegram.calls) == 2


def test_the_wait_is_capped_and_a_second_429_gives_up(telegram):
    telegram.responses = [
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 900}}),
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 900}}),
    ]

    assert send_telegram_message("42", "hi") is False

    assert telegram.sleeps == [30]
    assert len(telegram.calls) == 2


def test_blocked_bot_unlinks_the_chat(telegram, db_session, monkeypatch):
    db_session.add_all(
        [
            User(id=1, email="blocked@example.com", token="a", telegram_chat_id="42"),
            User(id=2, email="other@example.com", token="b", telegram_chat_id="43"),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        notification_queue, "SessionLocal", sessionmaker(bind=db_session.get_bind())
    )
    telegram.responses = [
        FakeResponse(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
    ]

    assert send_telegram_message("42", "hi") is False

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id is None
    assert db_session.get(User, 2).telegram_chat_id == "43"
    assert len(telegram.calls) == 1


def test_parse_error_is_retried_once_as_plain_text_without_tags(telegram):
    telegram.responses = [
        FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities"}),
        FakeResponse(200),
    ]
    html_text = '<b>عنوان &amp; آخر</b>\n<a href="https://mostaql.com/p/1?a=1&amp;b=2">اضغط</a>'

    assert send_telegram_message("42", html_text, buttons=[MANAGE]) is True

    retry = telegram.calls[1][1]
    assert "parse_mode" not in retry
    assert retry["text"] == "عنوان & آخر\nاضغط: https://mostaql.com/p/1?a=1&b=2"
    assert "<" not in retry["text"]
    assert retry["reply_markup"]["inline_keyboard"][0][0]["url"] == MANAGE[1]


def test_other_errors_are_not_retried(telegram):
    telegram.responses = [FakeResponse(500, {"ok": False, "description": "oops"})]

    assert send_telegram_message("42", "hi") is False
    assert len(telegram.calls) == 1


def test_a_task_sends_every_part_first_loud_and_button_on_the_last(monkeypatch):
    sent = []
    monkeypatch.setattr(
        notification_queue,
        "send_telegram_message",
        lambda chat_id, text, *, buttons=None, silent=False: sent.append((text, buttons, silent)) or True,
    )
    task = TelegramTask([1], [1], "42", ["one", "two", "three"], [MANAGE])

    assert TelegramTaskQueue()._process_task(task) is True

    assert sent == [("one", None, False), ("two", None, True), ("three", [MANAGE], True)]


def test_a_task_stops_at_the_first_failed_part(monkeypatch):
    sent = []

    def fake(chat_id, text, *, buttons=None, silent=False):
        sent.append(text)
        return False

    monkeypatch.setattr(notification_queue, "send_telegram_message", fake)

    assert TelegramTaskQueue()._process_task(TelegramTask([1], [1], "42", ["a", "b"])) is False
    assert sent == ["a"]


def test_bot_menu_is_registered_with_arabic_commands(telegram):
    assert register_bot_commands() is True

    url, payload = telegram.calls[0]
    assert url.endswith("/setMyCommands")
    assert [c["command"] for c in payload["commands"]] == ["start", "help", "stop"]
    assert all(c["description"] for c in payload["commands"])


def test_bot_menu_failure_is_only_reported(telegram):
    telegram.responses = [FakeResponse(401, {"ok": False, "description": "Unauthorized"})]

    assert register_bot_commands() is False


# ---- the webhook ------------------------------------------------------------------------


@pytest.fixture
def bot(monkeypatch):
    """Replies the webhook would send: (chat_id, text, buttons)."""
    replies = []
    monkeypatch.setattr(
        webhook,
        "send_telegram_message",
        lambda chat_id, text, *, buttons=None, silent=False: replies.append((chat_id, text, buttons)) or True,
    )
    return replies


def update(text, chat_id=42):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_webhook_without_a_configured_secret_accepts_calls(client, bot):
    assert client.post("/api/webhook/telegram", json=update("/help")).status_code == 200


def test_webhook_with_a_secret_rejects_missing_or_wrong_headers(client, bot, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")

    assert client.post("/api/webhook/telegram", json=update("/help")).status_code == 403
    wrong = {"X-Telegram-Bot-Api-Secret-Token": "nope"}
    assert client.post("/api/webhook/telegram", json=update("/help"), headers=wrong).status_code == 403
    assert bot == []

    right = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
    assert client.post("/api/webhook/telegram", json=update("/help"), headers=right).status_code == 200
    assert len(bot) == 1


def test_invalid_json_gets_an_arabic_error(client, bot):
    response = client.post("/api/webhook/telegram", content=b"not json")

    assert response.json() == {"status": "error", "message": "بيانات غير صالحة"}


def test_linking_turns_telegram_alerts_on_and_offers_the_manage_button(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok", receive_telegram=False))
    db_session.commit()

    client.post("/api/webhook/telegram", json=update(f"/start {make_telegram_link_code(1)}"))

    db_session.expire_all()
    user = db_session.get(User, 1)
    assert user.telegram_chat_id == "42"
    assert user.receive_telegram is True
    (chat_id, text, buttons), = bot
    assert chat_id == "42"
    assert "me@example.com" in text and "تم الربط" in text
    assert "متوقفة" not in text
    assert buttons == [MANAGE]


def test_linking_a_paused_account_says_alerts_are_paused(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok", unsubscribed=True))
    db_session.commit()

    client.post("/api/webhook/telegram", json=update(f"/start {make_telegram_link_code(1)}"))

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id == "42"
    (_chat, text, buttons), = bot
    assert "متوقفة" in text
    assert "إدارة التنبيهات" in text
    assert buttons == [MANAGE]


def test_linking_with_a_wrong_code_changes_nothing(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok"))
    db_session.commit()

    client.post("/api/webhook/telegram", json=update("/start wrong"))

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id is None
    assert "الرمز غير صحيح" in bot[0][1]


def test_the_management_token_no_longer_links_a_chat(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok"))
    db_session.commit()

    client.post("/api/webhook/telegram", json=update("/start tok"))

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id is None
    assert "الرمز غير صحيح" in bot[0][1]


def test_an_expired_or_forged_link_code_changes_nothing(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok"))
    db_session.commit()
    week_ago = datetime.utcnow() - timedelta(days=8)
    forged = make_telegram_link_code(1).replace("1_", "2_", 1)

    client.post("/api/webhook/telegram", json=update(f"/start {make_telegram_link_code(1, now=week_ago)}"))
    client.post("/api/webhook/telegram", json=update(f"/start {forged}"))

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id is None
    assert all("الرمز غير صحيح" in text for _chat, text, _buttons in bot)
    assert len(bot) == 2


def test_a_chat_linked_to_another_account_is_not_stolen(client, bot, db_session):
    db_session.add_all(
        [
            User(id=1, email="first@example.com", token="a", telegram_chat_id="42"),
            User(id=2, email="second@example.com", token="b"),
        ]
    )
    db_session.commit()

    client.post("/api/webhook/telegram", json=update(f"/start {make_telegram_link_code(2)}"))

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id == "42"
    assert db_session.get(User, 2).telegram_chat_id is None
    assert "first@example.com" in bot[0][1]


def test_help_shows_the_linked_email_and_the_manage_link(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok", telegram_chat_id="42"))
    db_session.commit()

    client.post("/api/webhook/telegram", json=update("/help"))

    (_chat, text, buttons), = bot
    assert "me@example.com" in text
    assert "/stop" in text
    assert buttons == [MANAGE]


def test_help_for_an_unlinked_chat_points_to_the_site(client, bot):
    client.post("/api/webhook/telegram", json=update("/start"))

    (_chat, text, buttons), = bot
    assert "https://alerts.test/" in text
    assert buttons is None


def test_stop_unlinks_the_chat(client, bot, db_session):
    db_session.add(User(id=1, email="me@example.com", token="tok", telegram_chat_id="42"))
    db_session.commit()

    client.post("/api/webhook/telegram", json=update("/stop"))

    db_session.expire_all()
    assert db_session.get(User, 1).telegram_chat_id is None
    assert "تم فصل" in bot[0][1]
