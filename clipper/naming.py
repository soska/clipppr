"""Kebab-case slug generation from clip titles."""

from __future__ import annotations

import re
import unicodedata

MAX_SLUG_LEN = 60


def slugify(title: str) -> str:
    """Convert a title to a kebab-case ASCII slug, capped at ~60 chars.

    Accented characters are normalized rather than dropped, so
    ``"año"`` becomes ``ano`` and ``"qué"`` becomes ``que``.
    """
    normalized = unicodedata.normalize("NFKD", title)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    ascii_text = stripped.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > MAX_SLUG_LEN:
        slug = slug[:MAX_SLUG_LEN].rstrip("-")
    return slug or "clip"
