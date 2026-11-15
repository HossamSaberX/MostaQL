import email
import re
from email import policy

from backend.config import settings
from backend.services import email as email_service
from backend.services.email import base, templates
from backend.services.email.base import SMTPEmailService, build_message
from backend.services.notification_queue import EmailTask, EmailTaskQueue
from backend.services.email.templates import (
    render_alert_email,
    render_announcement_email,
    render_unsubscribe_email,
    render_verification_email,
)


def job(n=1, **overrides):
    payload = {
        "title": f"مشروع {n}",
        "url": f"https://mostaql.com/project/{n}",
        "budget": "$100 - $250",
        "verification": "موثق (الهوية)",
        "category": "برمجة",
    }
    payload.update(overrides)
    return payload


def test_alert_subject_counts_projects_and_names_the_first():
    subject, html, text = render_alert_email([job(1), job(2), job(3)], token="tok")

    assert subject == "3 مشاريع جديدة: مشروع 1"
    assert "3 مشاريع جديدة" in html and "3 مشاريع جديدة" in text


def test_single_project_subject_uses_the_singular_form():
    subject, _html, _text = render_alert_email([job(1)], token="tok")

    assert subject == "مشروع جديد واحد: مشروع 1"


def test_subject_with_a_past_client_project_puts_it_first_with_its_title():
    subject, html, _text = render_alert_email(
        [job(1), job(2, followed_client="أحمد")], token="tok"
    )

    assert subject == "عميل سابق نشر مشروعًا: مشروع 2"
    assert html.index("مشروع 2") < html.index("مشروع 1")
    assert "عميل سابق تتابعه: أحمد" in html


def test_long_title_is_shortened_in_the_subject_only():
    long_title = "كلمة " * 40
    subject, html, _text = render_alert_email([job(1, title=long_title)], token="tok")

    assert subject.endswith("…")
    assert len(subject) < 100
    assert long_title.strip() in html


def test_html_and_text_carry_the_same_facts_and_link():
    _subject, html, text = render_alert_email([job(1)], token="tok")

    for part in (html, text):
        assert "الميزانية" in part
        assert "$100 - $250" in part
        assert "https://mostaql.com/project/1" in part
        assert "تصنيف تتابعه: برمجة" in part


def test_only_twenty_projects_are_drawn_and_the_rest_become_one_link():
    jobs = [job(n) for n in range(1, 26)]
    subject, html, text = render_alert_email(jobs, token="tok")

    assert subject.startswith("25 مشروعًا جديدًا")
    assert html.count("راجع المشروع وتقدّم الآن") == 20
    assert "مشروع 20<" in html and "مشروع 21<" not in html
    assert 'href="https://mostaql.com/projects"' in html
    assert "و5 مشاريع أخرى" in html
    assert "و5 مشاريع أخرى: https://mostaql.com/projects" in text


def test_no_more_link_when_everything_fits():
    _subject, html, _text = render_alert_email([job(1), job(2)], token="tok")

    assert "أخرى" not in html


def test_user_and_scraped_text_is_escaped():
    evil = job(1, title='<script>alert("x")</script>', url='https://a.test/?q="><b>', followed_client="<i>hi</i>")
    _subject, html, text = render_alert_email([evil], token="tok")

    assert "<script>" not in html and "<i>hi</i>" not in html and '"><b>' not in html
    assert "&lt;script&gt;" in html
    assert "<script>" in text  # plain text is not HTML, so nothing to escape


def test_every_alert_has_the_footer_with_reason_manage_and_email_unsubscribe():
    _subject, html, text = render_alert_email([job(1)], token="tok")

    for part in (html, text):
        assert "وصلتك هذه الرسالة لأنك اشتركت في تنبيهات مستقل" in part
        assert "إدارة التنبيهات" in part
        assert "إلغاء الاشتراك من البريد" in part
        assert "https://alerts.test/api/unsubscribe/tok" in part
        assert "https://alerts.test/api/unsubscribe/tok/email" in part


def test_layout_is_accessible_rtl_tables_with_a_hidden_preheader():
    _subject, html, _text = render_alert_email([job(1)], token="tok")

    assert html.startswith("<!DOCTYPE html>")
    assert '<html dir="rtl" lang="ar">' in html
    assert 'dir="rtl" lang="ar" style="max-width:600px' in html
    assert html.count('role="presentation"') >= 3
    assert "max-width:600px" in html
    assert "display:none" in html
    assert "Tahoma" in html


def contrast_with_white(hex_color):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return 1.05 / (luminance + 0.05)


def test_button_colour_has_enough_contrast_with_white_text():
    assert contrast_with_white(templates._BLUE) >= 4.5


def test_verification_email_uses_the_configured_expiry(monkeypatch):
    monkeypatch.setattr(settings, "verification_token_expiry_hours", 48)

    subject, html, text = render_verification_email("https://alerts.test/api/verify/t")

    assert "48 ساعة" in html and "48 ساعة" in text
    assert "24 ساعة" not in html
    assert "https://alerts.test/api/verify/t" in text
    assert "تنبيهات مستقل" in subject


def test_unsubscribe_request_email_button_says_what_the_page_does():
    subject, html, text = render_unsubscribe_email("tok")

    assert "إدارة الاشتراك أو إلغاؤه" in html and "إدارة الاشتراك أو إلغاؤه" in text
    assert "تأكيد إلغاء الاشتراك" not in html
    assert "https://alerts.test/api/unsubscribe/tok" in html


def test_announcement_has_the_same_footer_and_escapes_the_message():
    _subject, html, text = render_announcement_email("سطر 1\n<b>سطر 2</b>", token="tok")

    assert "سطر 1<br>&lt;b&gt;سطر 2&lt;/b&gt;" in html
    assert "<b>سطر 2</b>" in text
    assert "إدارة التنبيهات" in html and "إلغاء الاشتراك من البريد" in html


def test_names_are_unified_across_all_emails():
    emails = [
        render_alert_email([job(1)], token="t"),
        render_verification_email("https://x"),
        render_unsubscribe_email("t"),
        render_announcement_email("hi", token="t"),
    ]

    for subject, html, text in emails:
        combined = subject + html + text
        assert "تنبيهات مستقل" in combined
        assert "خدمة إشعارات" not in combined and "خدمة تنبيهات" not in combined
        assert "وظائف" not in combined


def parse(msg):
    return email.message_from_string(msg.as_string(), policy=policy.default)


def test_message_has_text_then_html_and_the_standard_headers():
    msg = build_message(
        sender_email="alerts@mail.example.com",
        to_email="user@example.com",
        subject="3 مشاريع جديدة: مشروع 1",
        html_body="<p>مرحبا</p>",
        text_body="مرحبا",
        unsubscribe_url="https://alerts.test/api/unsubscribe/tok/email",
    )
    parsed = parse(msg)

    assert parsed["Subject"] == "3 مشاريع جديدة: مشروع 1"
    assert parsed["To"] == "user@example.com"
    assert "alerts@mail.example.com" in parsed["From"]
    assert parsed["Date"]
    assert re.fullmatch(r"<.+@mail\.example\.com>", parsed["Message-ID"])
    assert parsed["List-Unsubscribe"] == "<https://alerts.test/api/unsubscribe/tok/email>"
    assert parsed["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert [part.get_content_type() for part in parsed.iter_parts()] == ["text/plain", "text/html"]
    assert parsed.get_body(("plain",)).get_content().strip() == "مرحبا"


def test_message_without_unsubscribe_url_has_no_list_headers():
    parsed = parse(
        build_message(
            sender_email="a@example.com",
            to_email="u@example.com",
            subject="s",
            html_body="<p>x</p>",
            text_body="x",
        )
    )

    assert parsed["List-Unsubscribe"] is None
    assert parsed["List-Unsubscribe-Post"] is None


class FakeSMTP:
    sent = []

    def __init__(self, *args, **kwargs):
        pass

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def sendmail(self, sender, recipients, text):
        FakeSMTP.sent.append((sender, recipients, text))
        return {}

    def quit(self):
        pass


class FakeProvider(SMTPEmailService):
    def _get_smtp_config(self):
        return ("smtp.test", 587, "login", "secret")

    def _get_provider_name(self):
        return "Fake"

    def _credentials_valid(self):
        return True

    def _get_sender_email(self):
        return "alerts@mail.example.com"


def test_alert_goes_to_one_recipient_with_one_click_unsubscribe(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(base.smtplib, "SMTP", FakeSMTP)

    assert FakeProvider().send_job_notifications("user@example.com", [job(1)], "tok") is True

    (sender, recipients, text), = FakeSMTP.sent
    assert recipients == ["user@example.com"]
    parsed = email.message_from_string(text, policy=policy.default)
    assert parsed["To"] == "user@example.com"
    assert parsed["List-Unsubscribe"] == "<https://alerts.test/api/unsubscribe/tok/email>"
    assert parsed["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert parsed.get_body(("plain",)) is not None


def test_verification_email_is_sent_without_list_unsubscribe(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(base.smtplib, "SMTP", FakeSMTP)

    assert FakeProvider().send_verification_email("user@example.com", "tok") is True

    parsed = email.message_from_string(FakeSMTP.sent[0][2], policy=policy.default)
    assert parsed["List-Unsubscribe"] is None
    assert parsed.get_body(("plain",)) is not None


def test_a_failed_goodbye_after_a_sent_email_still_counts_as_sent(monkeypatch):
    # Reporting failure here would make the alternate provider send the same email again.
    class QuitFails(FakeSMTP):
        def quit(self):
            raise base.smtplib.SMTPServerDisconnected("gone")

    FakeSMTP.sent = []
    monkeypatch.setattr(base.smtplib, "SMTP", QuitFails)

    assert FakeProvider().send_job_notifications("user@example.com", [job(1)], "tok") is True
    assert len(FakeSMTP.sent) == 1


def test_an_email_task_goes_through_the_queue_to_one_recipient(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(base.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_service, "get_email_service", FakeProvider)
    task = EmailTask(
        notification_ids=[1, 2],
        user_ids=[7],
        email="user@example.com",
        jobs=[job(1), job(2)],
        unsubscribe_token="tok",
    )

    assert EmailTaskQueue()._process_task(task) is True

    (sender, recipients, text), = FakeSMTP.sent
    assert recipients == ["user@example.com"]
    parsed = email.message_from_string(text, policy=policy.default)
    assert parsed["Subject"] == "مشروعان جديدان: مشروع 1"
    assert parsed["List-Unsubscribe"] == "<https://alerts.test/api/unsubscribe/tok/email>"
    html = parsed.get_body(("html",)).get_content()
    assert "مشروع 2<" in html
