"""Deterministic input policy: normalization and prompt-injection detection.

These checks are an extra layer, not the security boundary. Authorization, SQL validation and
redaction are enforced separately and hold even if a phrasing gets past these patterns.
"""

import re
import unicodedata

_INJECTION_PATTERNS = (
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.?!]{0,40}\b(?:instructions?|prompts?|guardrails?|polic(?:y|ies)|rules?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|show|print|repeat|display)\b[^.?!]{0,30}\b(?:system|developer|hidden)\s+(?:prompt|message|instructions?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be)\b[^.?!]{0,30}\b(?:admin|administrator|dba|root|superuser)\b", re.IGNORECASE),
    re.compile(r"\b(?:disable|bypass|turn\s+off|skip)\b[^.?!]{0,30}\b(?:guardrails?|validation|security|restrictions?|redaction|masking)\b", re.IGNORECASE),
    re.compile(r"\b(?:make|grant|give)\s+me\b[^.?!]{0,30}\b(?:admin|administrator|superuser)\b", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:system|assistant)\s*:", re.IGNORECASE),
)
# Naming another domain's data source, not a topic: "insurance claims" is valid healthcare wording.
_DOMAIN_SOURCE = re.compile(
    r"\b(banking|finance|healthcare|insurance|retail)(?:['’]s)?\s+(?:catalogs?|domains?|schemas?|databases?|tables?|data)\b",
    re.IGNORECASE,
)


def normalize_input(text: str) -> str:
    """Canonicalize text so look-alike characters and control codes cannot hide content."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        char for char in normalized if char in "\n\t" or unicodedata.category(char)[0] != "C"
    ).strip()


def detect_prompt_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def other_domain_named(text: str, domain: str) -> str | None:
    """The other domain's data this question asks for. Results would come from the session's own
    domain, so answering would present them as the other domain's figures."""
    return next((m.group(1).lower() for m in _DOMAIN_SOURCE.finditer(text) if m.group(1).lower() != domain), None)
