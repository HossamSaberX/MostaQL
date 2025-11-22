"""
Business logic for subscribing and updating user preferences.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import User, UserCategory, Category
from backend.utils.security import generate_token


class SubscriptionError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


@dataclass
class SubscriptionResult:
    user: User
    message: str
    status: str = "created"
    send_verification: bool = False
    token: Optional[str] = None


class SubscriptionService:
    def __init__(self, db: Session):
        self.db = db
        self.max_categories = settings.max_categories_per_user

    def subscribe(
        self, 
        email: str, 
        category_ids: List[int],
        receive_email: bool = True,
        receive_telegram: bool = True,
        min_hiring_rate: Optional[float] = None
    ) -> SubscriptionResult:
        normalized_ids = self._normalize_category_ids(category_ids)
        if not normalized_ids:
            raise SubscriptionError("اختر تخصصاً واحداً على الأقل")
        if len(normalized_ids) > self.max_categories:
            raise SubscriptionError(f"يمكنك اختيار {self.max_categories} تخصصات كحد أقصى")

        categories = self._fetch_categories(normalized_ids)
        if len(categories) != len(normalized_ids):
            raise SubscriptionError("معرفات التخصصات غير صالحة")

        user = self.db.query(User).filter(User.email == email).first()
        if user:
            return self._handle_existing_user(user, normalized_ids, receive_email, receive_telegram, min_hiring_rate)

        return self._create_new_user(email, normalized_ids, receive_email, receive_telegram, min_hiring_rate)

    def _normalize_category_ids(self, category_ids: List[int]) -> List[int]:
        return sorted({int(category_id) for category_id in category_ids})

    def _fetch_categories(self, category_ids: List[int]) -> List[Category]:
        return (
            self.db.query(Category)
            .filter(Category.id.in_(category_ids))
            .all()
        )

    def _replace_user_categories(self, user_id: int, category_ids: List[int]) -> None:
        self.db.query(UserCategory).filter(UserCategory.user_id == user_id).delete()
        for category_id in category_ids:
            self.db.add(
                UserCategory(user_id=user_id, category_id=category_id)
            )

    def _handle_existing_user(
        self,
        user: User,
        category_ids: List[int],
        receive_email: bool,
        receive_telegram: bool,
        min_hiring_rate: Optional[float] = None,
    ) -> SubscriptionResult:
        self._replace_user_categories(user.id, category_ids)
        user.receive_email = receive_email
        user.receive_telegram = receive_telegram
        user.min_hiring_rate = min_hiring_rate

        # Case 1: User is verified and active (just updating preferences)
        if user.verified and not user.unsubscribed:
            self.db.commit()
            return SubscriptionResult(
                user=user,
                message="تم تحديث تفضيلاتك بنجاح",
                status="updated",
                send_verification=False,
                token=user.token
            )

        # Case 2: User was unsubscribed, now resubscribing (Welcome back!)
        if user.verified and user.unsubscribed:
            user.unsubscribed = False
            self.db.commit()
            return SubscriptionResult(
                user=user,
                message="مرحباً بعودتك! تم إعادة تفعيل اشتراكك بنجاح.",
                status="reactivated",
                send_verification=False,
                token=user.token
            )

        # Case 3: User is NOT verified (Re-send verification)
        user.unsubscribed = False
        user.token = generate_token()
        user.token_issued_at = datetime.utcnow()

        self.db.commit()

        return SubscriptionResult(
            user=user,
            message="تم إرسال رسالة التفعيل. يرجى التحقق من بريدك الإلكتروني.",
            status="created",
            send_verification=True,
            token=user.token,
        )

    def _create_new_user(
        self,
        email: str,
        category_ids: List[int],
        receive_email: bool,
        receive_telegram: bool,
        min_hiring_rate: Optional[float] = None,
    ) -> SubscriptionResult:
        token = generate_token()
        user = User(
            email=email,
            token=token,
            token_issued_at=datetime.utcnow(),
            verified=False,
            unsubscribed=False,
            receive_email=receive_email,
            receive_telegram=receive_telegram,
            min_hiring_rate=min_hiring_rate,
        )
        self.db.add(user)
        self.db.flush()

        for category_id in category_ids:
            self.db.add(
                UserCategory(user_id=user.id, category_id=category_id)
            )

        self.db.commit()

        return SubscriptionResult(
            user=user,
            message="تم إرسال رسالة التفعيل. يرجى التحقق من بريدك الإلكتروني.",
            status="created",
            send_verification=True,
            token=token,
        )


