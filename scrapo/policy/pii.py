"""PII classifier — regex tier (cheap, default) and optional LLM tier (opt-in).

Default behavior is *flag* not redact, so callers can choose how to react.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

PiiKind = Literal["email", "phone", "ssn", "credit_card", "ipv4", "iban", "passport"]

# Email: local and domain labels may not start/end with a dot or contain
# consecutive dots, and the TLD must be alphabetic (rejects ``user@domain.123``
# and ``a..b@x.com``).
_EMAIL_LABEL = r"(?<![A-Za-z0-9_%+.\-])[A-Za-z0-9_%+\-]+(?:\.[A-Za-z0-9_%+\-]+)*"
_EMAIL_DOMAIN = r"(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,}"

# Phone: optional ``+``/country code then digit groups separated by space, dot
# or hyphen (optionally parenthesised area code), with a plausible 7-15 total
# digit count enforced in ``scan``. The structure requires at least one
# separator so it does not match bare long integer/ID runs.
_PHONE_RE = re.compile(
    r"\+?\d{0,3}[ .\-]?(?:\(\d{1,4}\)[ .\-]?)?\d{2,4}(?:[ .\-]\d{2,4}){1,4}"
)

_PATTERNS: list[tuple[PiiKind, re.Pattern[str]]] = [
    ("email", re.compile(rf"{_EMAIL_LABEL}@{_EMAIL_DOMAIN}", re.I)),
    ("phone", _PHONE_RE),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # IBAN total length is 15-34 chars: 2 letters + 2 check digits + 11-30 BBAN.
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("passport", re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")),
]

# A plausible phone number has 7-15 digits total (ITU E.164 caps at 15).
_PHONE_MIN_DIGITS = 7
_PHONE_MAX_DIGITS = 15


@dataclass(slots=True)
class PiiHit:
    kind: PiiKind
    value: str
    start: int
    end: int


class PiiClassifier:
    def __init__(self, enabled: bool = True, kinds: Iterable[PiiKind] | None = None) -> None:
        self.enabled = enabled
        self.kinds = set(kinds) if kinds else None

    def scan(self, text: str) -> list[PiiHit]:
        if not self.enabled or not text:
            return []
        hits: list[PiiHit] = []
        for kind, pattern in _PATTERNS:
            if self.kinds and kind not in self.kinds:
                continue
            for m in pattern.finditer(text):
                if kind == "credit_card" and not _luhn(m.group()):
                    continue
                if kind == "phone":
                    n_digits = sum(c.isdigit() for c in m.group())
                    if not _PHONE_MIN_DIGITS <= n_digits <= _PHONE_MAX_DIGITS:
                        continue
                hits.append(PiiHit(kind=kind, value=m.group(), start=m.start(), end=m.end()))
        return hits

    def has_pii(self, text: str) -> bool:
        return bool(self.scan(text))


def redact(text: str, hits: list[PiiHit] | None = None, replacement: str = "[REDACTED]") -> str:
    if hits is None:
        hits = PiiClassifier().scan(text)
    if not hits:
        return text
    out = []
    cursor = 0
    for h in sorted(hits, key=lambda x: x.start):
        out.append(text[cursor : h.start])
        out.append(replacement)
        cursor = h.end
    out.append(text[cursor:])
    return "".join(out)


def _luhn(card: str) -> bool:
    digits = [int(c) for c in card if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    s = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0
