"""Validation and persistence helpers for followed Mostaql clients."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import FollowedClient


class FollowedClientError(ValueError):
    """A user-facing error while adding or removing a followed client."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def normalize_client_profile_url(value: str) -> str:
    """Return a canonical Mostaql profile URL or raise a validation error.

    Projects expose profile links in more than one form (absolute, relative,
    and occasionally with a trailing slash). Storing one canonical URL keeps
    matching reliable across categories and prevents duplicate watch entries.
    """

    raw = (value or "").strip()
    if not raw:
        raise FollowedClientError("أدخل رابط ملف العميل في مستقل")

    # Project pages can expose absolute links, site-relative links, or a
    # profile sub-page such as ``/u/name/projects``.  Resolve the relative
    # forms before validating the host so a copied link from the page works
    # just like the absolute URL shown in the browser.
    if raw.startswith("//"):
        candidate = f"https:{raw}"
    elif raw.startswith("/"):
        candidate = urljoin(settings.mostaql_base_url, raw)
    elif "://" in raw:
        candidate = raw
    else:
        candidate = f"https://{raw}"

    parsed = urlparse(candidate)
    expected_host = (urlparse(settings.mostaql_base_url).hostname or "mostaql.com").lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_hosts = {expected_host, f"www.{expected_host}"}
    try:
        has_port = parsed.port is not None
    except ValueError:
        has_port = True
    if parsed.scheme not in {"http", "https"} or host not in allowed_hosts or has_port:
        raise FollowedClientError("يجب إدخال رابط ملف عميل صحيح من Mostaql مثل https://mostaql.com/u/name")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "u" or not parts[1]:
        raise FollowedClientError("رابط العميل يجب أن يكون بصيغة https://mostaql.com/u/اسم-العميل")

    return f"https://{expected_host}/u/{parts[1]}"


def client_profile_key(value: Optional[str]) -> Optional[str]:
    """Return the stable Mostaql account key used for alert matching.

    The profile page itself may be private, deleted, or return a 403 to the
    current viewer.  A followed-client alert must not depend on fetching that
    page: the project page still exposes the owner's ``/u/<username>`` link.
    Canonicalising both values at match time also protects existing rows that
    were stored before URL normalisation was added.
    """

    if not value:
        return None
    try:
        canonical_url = normalize_client_profile_url(value)
    except FollowedClientError:
        return None

    path_parts = [part for part in urlparse(canonical_url).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].lower() != "u":
        return None
    # Mostaql usernames are account identifiers rather than display names;
    # compare case-insensitively so copied links with different casing still
    # identify the same owner.
    return path_parts[1].casefold()


def normalize_client_label(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    label = " ".join(value.split()).strip()
    return label[:120] or None


def add_followed_client(
    db: Session,
    user_id: int,
    profile_url: str,
    label: Optional[str] = None,
    max_clients: int = 50,
) -> FollowedClient:
    canonical_url = normalize_client_profile_url(profile_url)
    normalized_label = normalize_client_label(label)
    existing = (
        db.query(FollowedClient)
        .filter(
            FollowedClient.user_id == user_id,
            FollowedClient.profile_url == canonical_url,
        )
        .first()
    )
    if existing:
        existing.label = normalized_label
        db.commit()
        db.refresh(existing)
        return existing

    if db.query(FollowedClient).filter(FollowedClient.user_id == user_id).count() >= max_clients:
        raise FollowedClientError(f"يمكنك متابعة {max_clients} عميلاً كحد أقصى")

    followed_client = FollowedClient(
        user_id=user_id,
        profile_url=canonical_url,
        label=normalized_label,
    )
    db.add(followed_client)
    db.commit()
    db.refresh(followed_client)
    return followed_client


def remove_followed_client(db: Session, user_id: int, followed_client_id: int) -> None:
    followed_client = (
        db.query(FollowedClient)
        .filter(
            FollowedClient.id == followed_client_id,
            FollowedClient.user_id == user_id,
        )
        .first()
    )
    if not followed_client:
        raise FollowedClientError("العميل غير موجود في قائمتك", status_code=404)
    db.delete(followed_client)
    db.commit()
