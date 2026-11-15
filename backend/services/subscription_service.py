"""
Business logic for subscribing and updating user preferences.

Subscribing and the preferences page share one path: the same category rules, the same
settings and the same Arabic errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import User, UserCategory, Category
from backend.enums import EmailDelivery
from backend.services.text import normalize, split_entries
from backend.utils.security import generate_token

MAX_WORDS = 20
MAX_WORD_LENGTH = 40

# Every alert setting a person can choose, with its value when they choose nothing.
DEFAULT_SETTINGS: Dict[str, Any] = {
    "receive_email": True,
    "receive_telegram": True,
    "email_delivery": EmailDelivery.INSTANT.value,
    "min_hiring_rate": None,
    "require_projects_in_progress": False,
    "require_ongoing_communications": False,
    "min_budget_usd": None,
    "require_verified_client": False,
    "max_project_age_minutes": None,
    "keywords_include": None,
    "keywords_exclude": None,
}

_WORD_LIST_NAMES = {
    "keywords_include": "كلمات يجب أن يذكرها العنوان",
    "keywords_exclude": "كلمات تستبعد المشروع",
}


class SubscriptionError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


# The one reply to every signup, new address or not, so the form cannot reveal who has an account.
CHECK_INBOX = "تحقق من بريدك: أرسلنا إليه رابطًا لإكمال الاشتراك."


@dataclass
class SubscriptionResult:
    user: User
    message: str
    send_verification: bool = False  # a new or never-confirmed account: send the confirmation link
    send_manage_link: bool = False   # a confirmed account: mail its owner the manage link, change nothing
    token: Optional[str] = None


def clean_word_list(raw: Optional[str], list_name: str) -> Optional[str]:
    """
    One word or phrase per line, as the person typed it: trimmed, duplicates removed
    (ignoring case and Arabic letter variants). None when the list is empty.
    """
    words: List[str] = []
    seen = set()
    for word in split_entries(raw):
        key = normalize(word)
        if key in seen:
            continue
        if len(word) > MAX_WORD_LENGTH:
            raise SubscriptionError(
                f"اختصر «{word[:MAX_WORD_LENGTH]}…» في قائمة {list_name}: الحد {MAX_WORD_LENGTH} حرفًا."
            )
        seen.add(key)
        words.append(word)
    if len(words) > MAX_WORDS:
        raise SubscriptionError(f"قائمة {list_name} تقبل {MAX_WORDS} كلمة على الأكثر. احذف بعضها.")
    return "\n".join(words) or None


def clean_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    """Defaults filled in, choices checked; raises SubscriptionError with what to fix."""
    unknown = set(values) - set(DEFAULT_SETTINGS)
    if unknown:
        raise TypeError(f"unknown alert settings: {sorted(unknown)}")
    cleaned = {**DEFAULT_SETTINGS, **values}
    if not cleaned["receive_email"] and not cleaned["receive_telegram"]:
        raise SubscriptionError("اختر طريقة واحدة على الأقل لاستلام التنبيهات.")
    try:
        cleaned["email_delivery"] = EmailDelivery(cleaned["email_delivery"]).value
    except ValueError:
        raise SubscriptionError("اختر متى تصلك رسائل البريد.")
    for field, list_name in _WORD_LIST_NAMES.items():
        cleaned[field] = clean_word_list(cleaned[field], list_name)
    return cleaned


class SubscriptionService:
    def __init__(self, db: Session):
        self.db = db
        self.max_categories = settings.max_categories_per_user

    def subscribe(self, email: str, category_ids: List[int], **alert_settings: Any) -> SubscriptionResult:
        normalized_ids = self.validate_categories(category_ids)
        cleaned = clean_settings(alert_settings)

        user = self.db.query(User).filter(User.email == email).first()
        if user:
            return self._handle_existing_user(user, normalized_ids, cleaned)
        return self._create_new_user(email, normalized_ids, cleaned)

    def update_preferences(
        self, user: User, category_ids: Optional[List[int]], **alert_settings: Any
    ) -> None:
        """Save the preferences page. category_ids=None keeps the current categories."""
        normalized_ids = self.validate_categories(category_ids) if category_ids is not None else None
        cleaned = clean_settings(alert_settings)
        if normalized_ids is not None:
            self._replace_user_categories(user.id, normalized_ids)
        self._apply_settings(user, cleaned)
        self.db.commit()

    def validate_categories(self, category_ids: List[int]) -> List[int]:
        """Sorted, unique ids of existing categories, 1 to the maximum; raises with what to fix."""
        normalized_ids = sorted({int(category_id) for category_id in category_ids})
        if not normalized_ids:
            raise SubscriptionError("اختر تصنيفًا واحدًا على الأقل.")
        if len(normalized_ids) > self.max_categories:
            raise SubscriptionError(
                f"اختر {self.max_categories} تصنيفات على الأكثر، ثم أعد المحاولة."
            )
        found = self.db.query(Category.id).filter(Category.id.in_(normalized_ids)).count()
        if found != len(normalized_ids):
            raise SubscriptionError("بعض التصنيفات لم تعد متاحة. حدّث الصفحة واختر من جديد.")
        return normalized_ids

    def _replace_user_categories(self, user_id: int, category_ids: List[int]) -> None:
        self.db.query(UserCategory).filter(UserCategory.user_id == user_id).delete()
        for category_id in category_ids:
            self.db.add(UserCategory(user_id=user_id, category_id=category_id))

    @staticmethod
    def _apply_settings(user: User, cleaned: Dict[str, Any]) -> None:
        for field, value in cleaned.items():
            setattr(user, field, value)

    def _handle_existing_user(
        self, user: User, category_ids: List[int], cleaned: Dict[str, Any]
    ) -> SubscriptionResult:
        # Anyone can type any address, so a confirmed account is never changed from here: its
        # owner gets a manage link by email instead, and the reply matches a new signup's.
        if user.verified:
            return SubscriptionResult(user=user, message=CHECK_INBOX, send_manage_link=True)

        # Never confirmed: take the new choices and send a fresh confirmation link.
        self._replace_user_categories(user.id, category_ids)
        self._apply_settings(user, cleaned)
        user.unsubscribed = False
        user.token = generate_token()
        user.token_issued_at = datetime.utcnow()
        self.db.commit()
        return SubscriptionResult(user=user, message=CHECK_INBOX, send_verification=True, token=user.token)

    def _create_new_user(
        self, email: str, category_ids: List[int], cleaned: Dict[str, Any]
    ) -> SubscriptionResult:
        token = generate_token()
        user = User(
            email=email,
            token=token,
            token_issued_at=datetime.utcnow(),
            verified=False,
            unsubscribed=False,
            **cleaned,
        )
        self.db.add(user)
        self.db.flush()
        for category_id in category_ids:
            self.db.add(UserCategory(user_id=user.id, category_id=category_id))
        self.db.commit()
        return SubscriptionResult(user=user, message=CHECK_INBOX, send_verification=True, token=token)
