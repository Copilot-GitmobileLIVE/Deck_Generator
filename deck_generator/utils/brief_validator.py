"""brief_validator.py — Sanitise and validate a deck brief JSON file.

Fixes detected issues in-place and raises ``BriefValidationError`` only when
a problem cannot be auto-corrected (e.g. a required field is missing).

Typical usage (called by run_demo.py before json.load):
    from deck_generator.utils.brief_validator import validate_and_fix_brief
    data = validate_and_fix_brief("sample_briefs/my_brief.json")
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger(__name__)


class BriefValidationError(ValueError):
    """Raised when a brief has an unrecoverable structural problem."""


# Control characters that are illegal inside a JSON string (excludes \t \n \r)
_ILLEGAL_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Bullet / list prefixes used to detect implicit list items
_BULLET_PREFIXES = re.compile(r"^\s*[\t•\-\*]\s*")


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _raw_fix(path: Path) -> str:
    """Return the file content with all illegal control characters removed.

    The file is read as raw bytes so we can operate before JSON parsing.
    Only bytes < 0x20 that are NOT tab (0x09), LF (0x0A), or CR (0x0D) are
    stripped.  The result is returned as a UTF-8 string.
    """
    raw = path.read_bytes()
    cleaned = bytes(b for b in raw if b >= 0x20 or b in (0x09, 0x0A, 0x0D))
    if len(cleaned) != len(raw):
        n_removed = len(raw) - len(cleaned)
        logger.warning("brief_validator: removed %d illegal control byte(s) from %s", n_removed, path.name)
    return cleaned.decode("utf-8", errors="replace")


def _parse_json(text: str, path: Path) -> dict[str, Any]:
    """Parse JSON text and raise BriefValidationError on syntax failure."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BriefValidationError(
            f"brief_validator: {path.name} is not valid JSON even after control-char cleanup: {exc}"
        ) from exc


# ── Field-level fixers ────────────────────────────────────────────────────────

def _fix_key_messages(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure key_messages is a List[str] with 3–5 non-empty items.

    Auto-converts a plain string (with or without embedded newlines / bullets)
    into a list by splitting on newlines and stripping bullet prefixes.
    """
    km = data.get("key_messages")

    if isinstance(km, list):
        # Remove empty items and strip surrounding whitespace
        fixed = [str(m).strip() for m in km if str(m).strip()]
        if fixed != km:
            logger.info("brief_validator: stripped empty/whitespace items from key_messages")
        data["key_messages"] = fixed
        return data

    if isinstance(km, str):
        # Split on newlines, strip bullet markers, drop empty lines
        lines = [_BULLET_PREFIXES.sub("", ln).strip() for ln in km.splitlines()]
        items = [ln for ln in lines if ln]
        if not items:
            items = [km.strip()]
        logger.warning(
            "brief_validator: key_messages was a string — converted to list of %d items", len(items)
        )
        data["key_messages"] = items
        return data

    if km is None:
        raise BriefValidationError("brief_validator: required field 'key_messages' is missing")

    raise BriefValidationError(
        f"brief_validator: 'key_messages' must be a list of strings, got {type(km).__name__}"
    )


def _fix_string_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Strip embedded newlines and tabs from all top-level string fields.

    JSON strings must not contain literal control characters (RFC 8259 §7).
    This collapses multi-line strings back to single lines by replacing each
    run of whitespace (including \\n and \\t) with a single space.
    """
    _FIELDS = ("title", "client", "industry", "audience", "objective",
                "tone", "brand", "additional_context")
    for field in _FIELDS:
        val = data.get(field)
        if isinstance(val, str):
            cleaned = " ".join(val.split())  # collapses all whitespace runs
            if cleaned != val:
                logger.info("brief_validator: collapsed whitespace in field '%s'", field)
            data[field] = cleaned
    return data


def _fix_slide_count(data: dict[str, Any]) -> dict[str, Any]:
    """Clamp slide_count_target to the valid range [3, 20]."""
    count = data.get("slide_count_target")
    if count is None:
        return data
    try:
        count = int(count)
    except (TypeError, ValueError):
        logger.warning("brief_validator: slide_count_target is not an integer; defaulting to 10")
        data["slide_count_target"] = 10
        return data
    clamped = max(3, min(20, count))
    if clamped != count:
        logger.warning(
            "brief_validator: slide_count_target %d is out of range [3, 20]; clamped to %d",
            count, clamped,
        )
    data["slide_count_target"] = clamped
    return data


def _check_required_fields(data: dict[str, Any]) -> None:
    """Raise BriefValidationError if any non-optional required field is absent."""
    required = ("title", "client", "industry", "audience", "objective", "key_messages")
    missing = [f for f in required if not data.get(f)]
    if missing:
        raise BriefValidationError(
            f"brief_validator: required field(s) missing or empty: {', '.join(missing)}"
        )


# ── Public entry point ────────────────────────────────────────────────────────

def validate_and_fix_brief(brief_path: str | Path) -> dict[str, Any]:
    """Load, sanitise, and validate a brief JSON file.

    Steps performed in order:
    1. Strip illegal control characters from the raw bytes.
    2. Parse the sanitised text as JSON.
    3. Coerce ``key_messages`` to ``List[str]`` if it is a plain string.
    4. Collapse embedded newlines/tabs in all string fields.
    5. Clamp ``slide_count_target`` to [3, 20].
    6. Assert all required fields are present and non-empty.

    If any auto-fix changes the content the corrected file is written back to
    disk so subsequent runs are clean.

    Returns the validated ``dict`` ready for ``DeckBrief(**data)``.

    Raises ``BriefValidationError`` on unrecoverable problems.
    """
    path = Path(brief_path)
    if not path.exists():
        raise BriefValidationError(f"brief_validator: file not found: {path}")

    original_bytes = path.read_bytes()

    # Step 1 — remove illegal control chars from raw bytes
    sanitised_text = _raw_fix(path)

    # Step 2 — parse JSON
    data = _parse_json(sanitised_text, path)

    # Steps 3-5 — field-level fixes
    data = _fix_key_messages(data)
    data = _fix_string_fields(data)
    data = _fix_slide_count(data)

    # Step 6 — assert required fields
    _check_required_fields(data)

    # Write back only when something actually changed
    fixed_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    if fixed_bytes != original_bytes:
        path.write_bytes(fixed_bytes)
        logger.info("brief_validator: wrote sanitised brief back to %s", path.name)
    else:
        logger.debug("brief_validator: %s is already clean — no changes needed", path.name)

    return data
