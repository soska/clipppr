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


def unique_slug(slug: str, taken: set[str]) -> str:
    """Return a slug not present in ``taken``, suffixing ``-2``, ``-3``, ...

    The returned slug is added to ``taken`` so repeated calls stay collision-free.
    """
    candidate = slug
    n = 2
    while candidate in taken:
        candidate = f"{slug}-{n}"
        n += 1
    taken.add(candidate)
    return candidate
