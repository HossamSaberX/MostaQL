"""
The single source of user-facing words: names, counting, project facts and links.

Email, Telegram, the bot and the API all take their wording from here, so a
phrase is written once. Digits are always Western (1, 2, 3).
"""
from typing import Any, Dict, List, Optional, Tuple

from backend.config import settings
from backend.utils.security import make_telegram_link_code

SERVICE_NAME = "تنبيهات مستقل"
ANNOUNCEMENT_TITLE = f"إعلان من {SERVICE_NAME}"
GENERIC_ERROR = "حدث خطأ غير متوقع، يرجى المحاولة لاحقاً"
UNAUTHORIZED = "غير مصرح"

VIEW_PROJECT = "راجع المشروع وتقدّم الآن"
MANAGE_ALERTS = "إدارة التنبيهات"
EMAIL_UNSUBSCRIBE = "إلغاء الاشتراك من البريد"

BOT_COMMANDS = (
    ("start", "ابدأ واربط حسابك"),
    ("help", "مساعدة وإدارة التنبيهات"),
    ("stop", "إيقاف تنبيهات تيليجرام"),
)

# One, two, 3-10, 11+ (the form after the number). Used for every count.
_PROJECTS = ("مشروع جديد واحد", "مشروعان جديدان", "مشاريع جديدة", "مشروعًا جديدًا")
_MINUTES = ("دقيقة واحدة", "دقيقتان", "دقائق", "دقيقة")
_HOURS = ("ساعة واحدة", "ساعتان", "ساعات", "ساعة")
_HOURS_AFTER_FOR = ("ساعة واحدة", "ساعتين", "ساعات", "ساعة")


def _count(n: int, forms: Tuple[str, str, str, str]) -> str:
    one, two, few, many = forms
    if n == 1:
        return one
    if n == 2:
        return two
    return f"{n} {few if 3 <= n <= 10 else many}"


def more_projects(n: int) -> str:
    """The "and N more" line; number first, so it needs no Arabic counting form."""
    return f"و{n} أخرى"


def count_projects(n: int) -> str:
    """1 -> مشروع جديد واحد, 2 -> مشروعان جديدان, 5 -> 5 مشاريع جديدة, 12 -> 12 مشروعًا جديدًا."""
    if n <= 0:
        return "لا مشاريع جديدة"
    return _count(n, _PROJECTS)


def valid_for_hours(hours: int) -> str:
    """The expiry sentence of the verification email."""
    return f"هذا الرابط صالح لمدة {_count(hours, _HOURS_AFTER_FOR)} فقط"


def _age(minutes: int) -> str:
    if minutes < 1:
        return "أقل من دقيقة"
    if minutes < 60:
        return _count(minutes, _MINUTES)
    return _count(minutes // 60, _HOURS)


def job_facts(payload: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Ordered (label, value) facts about one project, shared by email and Telegram."""
    facts: List[Tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value is not None and value != "":
            facts.append((label, str(value)))

    add("الميزانية", payload.get("budget"))
    add("معدل التوظيف", payload.get("hiring_rate"))
    add("مشاريع قيد التنفيذ", payload.get("projects_in_progress"))
    add("التواصلات الجارية", payload.get("ongoing_communications"))
    add("التوثيق", payload.get("verification"))
    if payload.get("project_age_minutes") is not None:
        add("عمر المشروع", _age(int(payload["project_age_minutes"])))
    return facts


def job_reason(payload: Dict[str, Any]) -> Optional[str]:
    """Why this person got this project, or None when we do not know."""
    if payload.get("followed_client"):
        return f"عميل سابق تتابعه: {payload['followed_client']}"
    if payload.get("category"):
        return f"تصنيف تتابعه: {payload['category']}"
    return None


def order_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Projects from past clients first; otherwise the order is kept."""
    return sorted(jobs, key=lambda job: not job.get("followed_client"))


def _site(path: str) -> str:
    return f"{settings.base_url.rstrip('/')}{path}"


def home_url() -> str:
    return _site("/")


def projects_url() -> str:
    """Mostaql's project list, where "and N more" leads."""
    return f"{settings.mostaql_base_url.rstrip('/')}/projects"


def manage_url(token: str) -> str:
    """Page where the owner of this token manages or stops alerts."""
    return _site(f"/api/unsubscribe/{token}")


def email_unsubscribe_url(token: str) -> str:
    """Email-only unsubscribe: one-click POST for mail clients, a confirm page for people."""
    return _site(f"/api/unsubscribe/{token}/email")


def unsubscribe_request_url() -> str:
    """Page that asks for an email address and mails a manage link (used when we have no token)."""
    return _site("/unsubscribe-request.html")


def manage_button(token: str) -> Tuple[str, str]:
    """(label, url) of the Telegram button under alerts."""
    return (f"⚙️ {MANAGE_ALERTS}", manage_url(token))


def telegram_link_url(user_id: int) -> Optional[str]:
    """Deep link that connects Telegram to this account; None when no bot is configured."""
    if not settings.telegram_bot_username:
        return None
    return f"https://t.me/{settings.telegram_bot_username}?start={make_telegram_link_code(user_id)}"
