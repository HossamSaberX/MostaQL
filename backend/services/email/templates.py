"""
Email templates: one shared layout, and for every email an HTML body plus a plain-text twin.

Each renderer returns (subject, html, text). All user and scraped text is escaped here.
"""
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from backend.config import settings
from backend.services.messages import (
    ANNOUNCEMENT_TITLE,
    EMAIL_UNSUBSCRIBE,
    MANAGE_ALERTS,
    SERVICE_NAME,
    VIEW_PROJECT,
    count_projects,
    email_unsubscribe_url,
    job_facts,
    job_reason,
    manage_url,
    more_projects,
    order_jobs,
    projects_url,
    unsubscribe_request_url,
    valid_for_hours,
)

MAX_PROJECTS_IN_EMAIL = 20
SUBJECT_TITLE_LENGTH = 70

_BLUE = "#0A6FA0"  # white text on it has a contrast ratio of about 5.5:1
_TEXT = "#1f2933"
_MUTED = "#52606d"
_LINE = "#d9e2ec"
_PAGE = "#f4f6f8"
_FONT = "Tahoma, Arial, 'Noto Sans Arabic', sans-serif"
_TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" dir="rtl" lang="ar"'


def _href(url: str) -> str:
    return escape(str(url), quote=True)


class _Parts:
    """HTML and plain text of one section, written together so they cannot drift apart."""

    def __init__(self) -> None:
        self.html: List[str] = []
        self.text: List[str] = []

    def add(self, html: str, text: str) -> "_Parts":
        self.html.append(html)
        self.text.append(text)
        return self

    def paragraph(self, text: str, *, small: bool = False) -> "_Parts":
        size, color = ("13px", _MUTED) if small else ("16px", _TEXT)
        return self.add(
            f'<p style="margin:0 0 16px;font-size:{size};line-height:1.8;color:{color};">'
            f"{escape(text)}</p>",
            text,
        )

    def button(self, label: str, url: str) -> "_Parts":
        return self.add(
            f'<p style="margin:8px 0 20px;"><a href="{_href(url)}" style="display:inline-block;'
            f"padding:12px 24px;background-color:{_BLUE};color:#ffffff;text-decoration:none;"
            f'border-radius:6px;font-size:16px;font-weight:bold;">{escape(label)}</a></p>',
            f"{label}: {url}",
        )

    def link(self, label: str, url: str) -> "_Parts":
        return self.add(
            f'<p style="margin:0 0 16px;font-size:14px;line-height:1.8;">'
            f'<a href="{_href(url)}" style="color:{_BLUE};">{escape(label)}</a></p>',
            f"{label}: {url}",
        )


def _layout(title: str, preheader: str, body_html: str, footer_html: str) -> str:
    """The one HTML shell every email uses."""
    cell = f"font-family:{_FONT};text-align:right;"
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{escape(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:{_PAGE};">
<span style="display:none;visibility:hidden;opacity:0;color:transparent;height:0;width:0;max-height:0;max-width:0;overflow:hidden;mso-hide:all;">{escape(preheader)}</span>
<table {_TABLE} style="background-color:{_PAGE};">
<tr><td align="center" style="padding:24px 12px;">
<table {_TABLE} style="max-width:600px;background-color:#ffffff;border-radius:8px;">
<tr><td style="padding:24px 28px 0;{cell}font-size:14px;font-weight:bold;color:{_BLUE};">{escape(SERVICE_NAME)}</td></tr>
<tr><td style="padding:8px 28px 4px;{cell}"><h1 style="margin:0;font-size:22px;line-height:1.5;color:{_TEXT};">{escape(title)}</h1></td></tr>
<tr><td style="padding:12px 28px 8px;{cell}">{body_html}</td></tr>
<tr><td style="padding:16px 28px 24px;{cell}border-top:1px solid {_LINE};">{footer_html}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _finish(subject: str, title: str, preheader: str, body: _Parts, footer: _Parts) -> Tuple[str, str, str]:
    html = _layout(title, preheader, "\n".join(body.html), "\n".join(footer.html))
    text = "\n\n".join([f"{SERVICE_NAME}\n{title}", *body.text, "--", *footer.text])
    return subject, html, text


def _ignore_footer() -> _Parts:
    return _Parts().paragraph("إذا لم تطلب هذه الرسالة، يمكنك تجاهلها.", small=True)


def _subscriber_footer(token: Optional[str]) -> _Parts:
    """Why you got this, how to manage alerts, how to stop email. Used by every non-transactional email."""
    footer = _Parts().paragraph(f"وصلتك هذه الرسالة لأنك اشتركت في {SERVICE_NAME}.", small=True)
    if token:
        links = [(MANAGE_ALERTS, manage_url(token)), (EMAIL_UNSUBSCRIBE, email_unsubscribe_url(token))]
    else:
        links = [(EMAIL_UNSUBSCRIBE, unsubscribe_request_url())]
    html = " &nbsp;·&nbsp; ".join(
        f'<a href="{_href(url)}" style="color:{_BLUE};">{escape(label)}</a>' for label, url in links
    )
    return footer.add(
        f'<p style="margin:0;font-size:13px;line-height:1.8;">{html}</p>',
        "\n".join(f"{label}: {url}" for label, url in links),
    )


def render_verification_email(verify_url: str, expiry_hours: Optional[int] = None) -> Tuple[str, str, str]:
    hours = settings.verification_token_expiry_hours if expiry_hours is None else expiry_hours
    title = f"مرحباً بك في {SERVICE_NAME}"
    body = (
        _Parts()
        .paragraph("شكراً لاشتراكك. للمتابعة، أكّد بريدك الإلكتروني بالضغط على الزر أدناه.")
        .button("تأكيد البريد الإلكتروني", verify_url)
        .paragraph(valid_for_hours(hours))
    )
    return _finish(
        f"تأكيد البريد الإلكتروني - {SERVICE_NAME}",
        title,
        "أكّد بريدك لتبدأ باستلام التنبيهات.",
        body,
        _ignore_footer(),
    )


def render_unsubscribe_email(token: str) -> Tuple[str, str, str]:
    """The manage link. Sent when someone asks for it, and when a subscribed address signs up again."""
    title = "إدارة اشتراكك"
    body = (
        _Parts()
        .paragraph(
            f"وصلنا طلب لهذا البريد، وهو مشترك من قبل في {SERVICE_NAME}. "
            "من الزر أدناه تعدّل تنبيهاتك أو توقفها أو تعيد تشغيلها."
        )
        .button("إدارة الاشتراك أو إلغاؤه", manage_url(token))
    )
    return _finish(
        f"إدارة اشتراكك - {SERVICE_NAME}",
        title,
        "رابط تعديل تنبيهاتك أو إلغاء الاشتراك.",
        body,
        _ignore_footer(),
    )


def render_announcement_email(message: str, *, token: Optional[str] = None) -> Tuple[str, str, str]:
    title = ANNOUNCEMENT_TITLE
    safe_message = escape(message).replace("\n", "<br>")
    body = _Parts().add(f'<div style="font-size:16px;line-height:2;color:{_TEXT};">{safe_message}</div>', message)
    return _finish(f"تحديث جديد - {SERVICE_NAME}", title, message[:100], body, _subscriber_footer(token))


def _project_card(job: Dict[str, Any]) -> Tuple[str, str]:
    title = str(job["title"])
    facts = job_facts(job)
    reason = job_reason(job)

    rows = "".join(
        f'<tr><td style="padding:2px 0 2px 12px;font-size:14px;color:{_MUTED};white-space:nowrap;">{escape(label)}</td>'
        f'<td style="padding:2px 0;font-size:14px;color:{_TEXT};">{escape(value)}</td></tr>'
        for label, value in facts
    )
    html = (
        f'<table {_TABLE} style="margin:0 0 16px;border:1px solid {_LINE};border-radius:8px;">'
        f'<tr><td style="padding:16px;font-family:{_FONT};text-align:right;">'
        f'<a href="{_href(job["url"])}" style="font-size:18px;font-weight:bold;line-height:1.6;'
        f'color:{_BLUE};text-decoration:none;">{escape(title)}</a>'
        f'<table {_TABLE} style="margin:8px 0;">{rows}</table>'
        + (f'<p style="margin:0 0 12px;font-size:13px;color:{_MUTED};">{escape(reason)}</p>' if reason else "")
        + f'<a href="{_href(job["url"])}" style="display:inline-block;padding:10px 20px;'
        f"background-color:{_BLUE};color:#ffffff;text-decoration:none;border-radius:6px;"
        f'font-size:15px;font-weight:bold;">{escape(VIEW_PROJECT)}</a>'
        "</td></tr></table>"
    )
    text = "\n".join(
        [title, *(f"{label}: {value}" for label, value in facts), *([reason] if reason else []), str(job["url"])]
    )
    return html, text


def _shorten(title: str, limit: int = SUBJECT_TITLE_LENGTH) -> str:
    title = " ".join(title.split())
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def render_alert_email(jobs: List[Dict[str, Any]], *, token: Optional[str]) -> Tuple[str, str, str]:
    """
    One alert email for one person.

    Projects from past clients come first and at most 20 cards are drawn; the rest become one
    "and N more" link to Mostaql's project list. The subject counts every project.
    """
    if not jobs:
        raise ValueError("render_alert_email needs at least one project")

    ordered = order_jobs(jobs)
    shown = ordered[:MAX_PROJECTS_IN_EMAIL]
    total = len(ordered)
    hidden = total - len(shown)

    first = shown[0]
    subject = (
        f"عميل سابق نشر مشروعًا: {_shorten(str(first['title']))}"
        if first.get("followed_client")
        else f"{count_projects(total)}: {_shorten(str(first['title']))}"
    )

    body = _Parts()
    for job in shown:
        body.add(*_project_card(job))
    if hidden > 0:
        body.link(more_projects(hidden), projects_url())

    preheader = " · ".join(f"{label}: {value}" for label, value in job_facts(first)[:2]) or str(first["title"])
    return _finish(subject, count_projects(total), preheader, body, _subscriber_footer(token))
