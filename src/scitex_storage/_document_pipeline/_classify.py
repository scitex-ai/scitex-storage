#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, PURE classification of a document's extracted text.

Same philosophy as the package's ``reclaim`` verb: a ROUGH classifier is
safe because filing is reversible (archive-before-delete), so this is
keyword+regex rules, no LLM, no training. Every function here is pure --
text in, value out, nothing read from disk or env -- so each is testable on
a plain string. The two hard rules encoded here:

* NO SILENT WRONG GUESS. When nothing matches (or the match is weak), the
  category is ``misc`` -- the "not sure, a human should look" bucket -- never
  a confident-looking wrong category. ``confidence`` carries the honest
  strength of the guess.
* EXTENSIBLE TABLE. Rules live in small module-level dicts
  (:data:`CATEGORY_KEYWORDS`, :data:`KEEP_ORIGINAL_KEYWORDS`), so adding a
  vendor or a document type is a one-line edit, not a code change.

Dates are the one structured field worth parsing precisely: Japanese
documents date in the imperial era (令和/平成/昭和), the Western-year kanji
form (2026年7月18日), or ISO (2026-07-18). :func:`extract_date` normalises
all three to ``YYYY-MM-DD``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Classification result.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """The deterministic verdict for one document.

    ``category`` is one of the configured categories, or ``misc`` when the
    text matched nothing (or too weakly). ``issuer`` is a slug-safe best
    guess at who issued it (``unknown`` when none found). ``date`` is
    ``YYYY-MM-DD`` or ``None``. ``confidence`` in ``[0, 1]`` is the honest
    strength -- below :data:`CONFIDENCE_THRESHOLD` the category is forced to
    ``misc`` so a weak guess is never presented as a confident one.
    """

    category: str
    issuer: str
    date: str | None
    confidence: float


#: Below this confidence a document is filed to ``misc`` regardless of the
#: best keyword hit -- the "never a silent wrong guess" guard.
CONFIDENCE_THRESHOLD = 0.34

#: The safe fallback category. Always available even if a config omits it.
MISC_CATEGORY = "misc"

# --------------------------------------------------------------------------
# Rules tables (extensible: one line per vendor/keyword/type).
# --------------------------------------------------------------------------

#: category -> keywords (ja + en). Scored by how many DISTINCT keywords hit;
#: the highest-scoring category wins. ``misc`` is intentionally absent -- it
#: is the fallback, not something you match INTO.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "finance": (
        "請求書", "領収書", "明細", "給与", "振込", "口座", "銀行", "決済",
        "invoice", "receipt", "statement", "payment", "salary", "bank",
    ),
    "contracts": (
        "契約", "契約書", "同意書", "覚書", "約款", "誓約",
        "contract", "agreement", "terms", "consent",
    ),
    "admin": (
        "行政", "保険", "公共料金", "年金", "住民", "市役所", "区役所",
        "税", "納付", "証明書",
        "tax", "insurance", "pension", "utility", "certificate",
    ),
    "medical": (
        "医療", "健康", "処方", "診断", "病院", "診療", "薬",
        "medical", "clinic", "prescription", "diagnosis", "hospital",
    ),
    "academic": (
        "論文", "研究", "学会", "査読", "大学", "紀要",
        "abstract", "journal", "conference", "university", "doi", "research",
    ),
    "personal": (
        "手紙", "私信", "はがき", "葉書",
        "letter", "postcard",
    ),
    "manuals": (
        "取扱説明書", "取説", "保証書", "説明書",
        "manual", "warranty", "instructions", "guide",
    ),
}

#: keep-original TYPE -> keywords. If any of these hit, the physical original
#: must be kept (operator policy); the matched type is recorded. Keys mirror
#: the identifiers in ``config.document_sorter.keep_original``. Extend freely.
KEEP_ORIGINAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "passport": ("パスポート", "旅券", "passport"),
    "mynumber_card": ("マイナンバー", "個人番号", "my number", "mynumber"),
    "drivers_license": ("運転免許", "免許証", "driver", "driving licence"),
    "residence_card": ("在留カード", "residence card"),
    "pension_book": ("年金手帳", "pension book"),
    "seal_registration": ("印鑑登録", "印鑑証明", "seal registration"),
    "family_register": ("戸籍", "family register"),
    "property_deed": ("登記", "権利証", "property deed", "title deed"),
    "notarized_deed": ("公正証書", "notarized deed"),
}

#: Markers that flag the token/line likely naming the issuing entity.
_ISSUER_MARKERS: tuple[str, ...] = (
    "株式会社", "有限会社", "(株)", "（株）", "銀行", "大学", "病院",
    "市役所", "区役所", "町役場", "役場", "電力", "ガス", "保険",
    " Inc", " Inc.", " Corp", " Corp.", " Ltd", " LLC", " Co.",
    "University", "Hospital", "Bank",
)

# --------------------------------------------------------------------------
# Date extraction.
# --------------------------------------------------------------------------

#: First Gregorian year of each era MINUS 1 (era year N -> offset + N).
_ERA_OFFSET = {"令和": 2018, "平成": 1988, "昭和": 1925}

# 令和6年7月18日 / 令和元年... ("元" == year 1).
_JP_ERA_RE = re.compile(
    r"(令和|平成|昭和)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
# 2026年7月18日
_JP_WESTERN_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 2026-07-18 or 2026/7/18
_ISO_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def _valid(year: int, month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2999


def _fmt(year: int, month: int, day: int) -> str | None:
    if not _valid(year, month, day):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_date(text: str) -> str | None:
    """Return the first date in ``text`` as ``YYYY-MM-DD``, or ``None``.

    Recognises the Japanese imperial era (令和/平成/昭和, with 元年 == year
    1), the Western-year kanji form (2026年7月18日), and ISO (2026-07-18 or
    2026/7/18), in that priority order. An implausible date (bad month/day)
    is skipped rather than emitted. Pure: no clock, no locale, no I/O.
    """
    m = _JP_ERA_RE.search(text)
    if m:
        era, yr, month, day = m.groups()
        n = 1 if yr == "元" else int(yr)
        out = _fmt(_ERA_OFFSET[era] + n, int(month), int(day))
        if out:
            return out

    m = _JP_WESTERN_RE.search(text)
    if m:
        out = _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if out:
            return out

    m = _ISO_RE.search(text)
    if m:
        out = _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if out:
            return out

    return None


# --------------------------------------------------------------------------
# Issuer heuristic.
# --------------------------------------------------------------------------


def extract_issuer(text: str) -> str:
    """Best-guess the issuing entity as a slug, or ``"unknown"``.

    Scans lines for an entity marker (株式会社/銀行/Inc/University/...) and
    slugs the line it appears on. First hit wins (issuers usually sit at the
    top of a document). Deliberately simple -- a wrong issuer is cosmetic
    (it only shapes the filename), and reversibility covers the rest.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for marker in _ISSUER_MARKERS:
            if marker in line:
                return slugify(line, max_len=24) or "unknown"
    return "unknown"


# --------------------------------------------------------------------------
# Category scoring.
# --------------------------------------------------------------------------


def _score_categories(
    text: str, allowed: Sequence[str]
) -> dict[str, int]:
    """Distinct-keyword hit count per allowed, table-known category."""
    low = text.lower()
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category not in allowed:
            continue
        hits = 0
        for kw in keywords:
            probe = kw.lower()
            if probe in low:
                hits += 1
        if hits:
            scores[category] = hits
    return scores


def classify(
    text: str, *, categories: Sequence[str] | None = None
) -> Classification:
    """Classify ``text`` into ``Classification(category, issuer, date, conf)``.

    ``categories`` restricts the allowed set (defaults to the full keyword
    table plus ``misc``); pass ``config.categories`` to honour a user's
    taxonomy. Scoring: the category with the most distinct keyword hits wins;
    ``confidence`` grows with the hit count and a small bonus when a date was
    found. A blank/short extraction, no keyword hit, or a confidence below
    :data:`CONFIDENCE_THRESHOLD` all yield ``misc`` -- the deliberate "not
    sure" bucket, never a confident wrong label.
    """
    allowed = tuple(categories) if categories is not None else tuple(
        CATEGORY_KEYWORDS
    ) + (MISC_CATEGORY,)

    date = extract_date(text)
    issuer = extract_issuer(text)

    scores = _score_categories(text, allowed)
    if not scores:
        return Classification(MISC_CATEGORY, issuer, date, 0.0)

    # Deterministic tie-break: highest hits, then the category order in the
    # table (stable across runs, never wall-clock or dict-hash dependent).
    best = max(
        scores,
        key=lambda c: (scores[c], -list(CATEGORY_KEYWORDS).index(c)),
    )
    hits = scores[best]
    # 1 hit -> 0.3, 2 -> 0.5, 3 -> 0.65, plus 0.1 if we also pinned a date.
    confidence = min(1.0, 0.15 + 0.18 * hits + (0.1 if date else 0.0))

    if confidence < CONFIDENCE_THRESHOLD:
        return Classification(MISC_CATEGORY, issuer, date, round(confidence, 3))
    return Classification(best, issuer, date, round(confidence, 3))


# --------------------------------------------------------------------------
# keep-original detection.
# --------------------------------------------------------------------------


def detect_keep_original(
    text: str, keep_original: Sequence[str]
) -> str | None:
    """Return the keep-original TYPE ``text`` matches, or ``None``.

    ``keep_original`` is ``config.keep_original`` (a list of type ids such as
    ``passport``). A type matches when any of its
    :data:`KEEP_ORIGINAL_KEYWORDS` appears in ``text``. Returns the FIRST
    matching type id (config order), so the caller can flag "do not discard
    the physical original" and record which type triggered it. A type with no
    keyword mapping simply never matches (documented, not an error).
    """
    low = text.lower()
    for type_id in keep_original:
        for kw in KEEP_ORIGINAL_KEYWORDS.get(type_id, ()):
            if kw.lower() in low:
                return type_id
    return None


# --------------------------------------------------------------------------
# Slug helper (shared by issuer + title in the filing step).
# --------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[\s/\\:*?\"<>|]+")
_SLUG_TRIM_RE = re.compile(r"[-_]{2,}")


def slugify(value: str, *, max_len: int = 40) -> str:
    """Filesystem-safe, deterministic slug of ``value``.

    Normalises unicode (NFKC), replaces path separators / whitespace / shell
    globs with ``-``, drops control chars, lowercases ASCII (Japanese is left
    as-is -- valid in POSIX filenames and worth keeping searchable), and caps
    the length. Returns ``""`` for empty/blank input so the caller can supply
    a fallback token.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).strip()
    text = _SLUG_STRIP_RE.sub("-", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = _SLUG_TRIM_RE.sub("-", text).strip("-_")
    # Lowercase only the ASCII letters; leave non-ASCII (Japanese) untouched.
    text = "".join(ch.lower() if ch.isascii() else ch for ch in text)
    return text[:max_len].strip("-_")


# EOF
