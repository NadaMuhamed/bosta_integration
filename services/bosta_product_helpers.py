"""Pure helpers for Phase 7 Bosta product mapping."""

import re


_TESTER_SUFFIX_RE = re.compile(r"3\s*ML\s*$", re.IGNORECASE)


def canonical_business_code(value):
    """Return a conservative text business code without numeric coercion.

    Leading zeroes are significant.  This helper deliberately does not strip
    punctuation or otherwise guess equivalence between distinct codes.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    return text or None


def looks_like_tester_name(value):
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return False
    return bool(_TESTER_SUFFIX_RE.search(str(value).strip()))
