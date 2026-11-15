"""
Arabic-friendly text matching for the word filters.

Both the project title and the user's words go through `normalize`, so case, diacritics
(tashkeel, tatweel) and the usual letter variants never decide whether a word matches.
"""
import re
from typing import List, Optional

_DIACRITICS = re.compile("[ً-ٰٟـ]")  # tashkeel, superscript alef, tatweel
_LETTERS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه",
    "ى": "ي",
})
_SEPARATORS = re.compile(r"[\n\r,،;؛]+")


def normalize(text: str) -> str:
    return _DIACRITICS.sub("", text.lower()).translate(_LETTERS)


def split_entries(raw: Optional[str]) -> List[str]:
    """The entries of a newline- or comma-separated list as typed, trimmed, without empty ones."""
    if not raw:
        return []
    return [part.strip() for part in _SEPARATORS.split(raw) if part.strip()]


def split_words(raw: Optional[str]) -> List[str]:
    """The normalised words of a newline- or comma-separated list, without empty entries."""
    return [word for word in (normalize(entry).strip() for entry in split_entries(raw)) if word]


def passes_word_filters(title: str, include: Optional[str], exclude: Optional[str]) -> bool:
    """True when the title has one of the include words (if any) and none of the exclude words."""
    text = normalize(title or "")
    include_words = split_words(include)
    if include_words and not any(word in text for word in include_words):
        return False
    return not any(word in text for word in split_words(exclude))
