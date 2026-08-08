"""Pure, conservative parser for Bosta package-description product lines.

Phase 7 may use package description as an inventory candidate only when every
line has an explicit title, positive quantity and deterministic embedded code.
No ORM, HTTP or product creation belongs here.
"""

import math
import re

from .bosta_product_helpers import canonical_business_code


_LINE_RE = re.compile(
    r"^\s*(?P<title>.+?)\s+x\s+(?P<qty>\d+(?:\.\d+)?)\s+"
    r"\((?P<token>[^()]+)\)\s*$",
    re.IGNORECASE,
)
# Real observed shape: 088.01-521.050.  Permit a conservative trailing Bosta
# packaging suffix after .050 while requiring exactly one code segment.
_TOKEN_RE = re.compile(
    r"^088\.01-(?P<code>[A-Za-z0-9][A-Za-z0-9_]*)\.050(?:-[A-Za-z0-9_-]+)*$",
    re.IGNORECASE,
)
_ALT_ITEM_RE = re.compile(
    r"\s*(?P<title>[^,\[\]\r\n]+?)\s+"
    r"\[(?P<token>[^\[\]]+)\]\s*"
    r"\((?P<qty>\d+(?:\.\d+)?)\)\s*(?:,\s*|$)",
    re.IGNORECASE,
)


def extract_business_code(token):
    if not isinstance(token, str):
        return None
    match = _TOKEN_RE.fullmatch(token.strip())
    if not match:
        return None
    return canonical_business_code(match.group("code"))


def _candidate(title, quantity_text, token):
    title = (title or "").strip()
    code = extract_business_code(token)
    try:
        quantity = float(quantity_text)
    except (TypeError, ValueError):
        return None
    if not title or not code or not math.isfinite(quantity) or quantity <= 0:
        return None
    if quantity.is_integer():
        quantity = int(quantity)
    return {
        "title": title,
        "quantity": quantity,
        "source_product_code": code,
    }


def _parse_alt_description_line(line):
    """Parse only a fully-consumed ``Title [token] (qty), ...`` line."""
    candidates = []
    position = 0
    while position < len(line):
        match = _ALT_ITEM_RE.match(line, position)
        if not match:
            return None
        candidate = _candidate(match.group("title"), match.group("qty"), match.group("token"))
        if not candidate:
            return None
        candidates.append(candidate)
        position = match.end()
    return candidates or None


def parse_package_line(line):
    if not isinstance(line, str):
        return None
    match = _LINE_RE.fullmatch(line)
    if not match:
        return None
    return _candidate(match.group("title"), match.group("qty"), match.group("token"))


def parse_package_description(description):
    """Return all candidates only when the full description is unambiguous."""
    if not isinstance(description, str) or not description.strip():
        return {"ambiguous": True, "candidates": []}
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    if not lines:
        return {"ambiguous": True, "candidates": []}
    candidates = []
    for line in lines:
        candidate = parse_package_line(line)
        if candidate:
            candidates.append(candidate)
            continue
        alternate = _parse_alt_description_line(line)
        if not alternate:
            return {"ambiguous": True, "candidates": []}
        candidates.extend(alternate)
    return {"ambiguous": False, "candidates": candidates}
