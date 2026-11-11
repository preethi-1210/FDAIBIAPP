import re
from typing import Any

from fastapi import HTTPException
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

_CVV_REQUEST = re.compile(
    r"\b(?:cvv|cvc|cvn|card\s+verification(?:\s+(?:value|code))?|security\s+code)\b",
    re.IGNORECASE,
)
_RESTRICTED_REQUEST = re.compile(
    r"\b(?:account\s*(?:numbers?|no\.?|ids?|#|details?)|bank\s+accounts?|card\s*(?:numbers?|no\.?|details)|payment\s+cards?|"
    r"pan\s*(?:numbers?)?|aadhaar|aadhar|ifsc|kyc|loan\s+(?:records?|details?|balances?)|transaction\s+history|"
    r"fraud\s+(?:case|investigation|notes?)|"
    r"policy\s*(?:ids?|numbers?|no\.?|identifiers?)|"
    r"policyholders?|members?\s*(?:ids?|numbers?)|customers?\s*(?:ids?|numbers?|profiles?|records?|information)|"
    r"social\s+security\s+numbers?|ssn|tax\s*(?:ids?|numbers?|returns?|information|details?)|passport\s*(?:ids?|numbers?|scans?)|"
    r"medical\s+records?|diagnos(?:is|es)|health\s+(?:reports?|records?|data)|lab\s+results?|nominees?|beneficiaries|"
    r"identity\s+documents?|payroll|salar(?:y(?!\s+accounts?\b)|ies)|compensation|bonuses?|financial\s+statements?|"
    r"executive\s+(?:pay|compensation)|employee\s+bank(?:ing)?\s+details?|bank\s+details?|budget\s+(?:reports?|details?)|"
    r"purchase\s+history|order\s+history|loyalty\s+(?:members?|program|points?|history)|"
    r"payment\s+information|home\s+addresses?|mailing\s+addresses?|phone\s+numbers?|email\s+addresses?|"
    r"personal\s+information|sensitive\s+(?:information|data|fields?)|pii|spi|raw\s+(?:database\s+)?records?|"
    r"hidden\s+columns?|unmask|unredact|export\s+all\s+(?:customer|employee|user)?\s*records?|"
    r"bypass\s+(?:rbac|access|security|masking))\b",
    re.IGNORECASE,
)
_ACCOUNT_REQUEST = re.compile(r"\b(?:account|acct|bank\s+account)\b", re.IGNORECASE)
_POLICY_REQUEST = re.compile(r"\b(?:policy|policyholder|member)\b", re.IGNORECASE)
_CARD_REQUEST = re.compile(r"\b(?:card\s*(?:number|no\.?|details)|payment\s+cards?)\b", re.IGNORECASE)
_PERSON_IDENTIFIER_REQUEST = re.compile(
    r"\b(?:customer\s*(?:ids?|numbers?)|social\s+security|ssn|tax\s*(?:ids?|numbers?)|passport)\b",
    re.IGNORECASE,
)
_CVV_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:cvv|cvc|cvn)(?:_?(?:2|code|value|number))?(?![a-z0-9])|"
    r"(?<![a-z0-9])(?:card_)?(?:security|verification)_code(?![a-z0-9])",
    re.IGNORECASE,
)
_RESTRICTED_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:account_number|account_no|account_id|acct_number|acct_no|bank_account_number|card_number|card_no|card_pan|pan|pan_number|primary_account_number|routing_number|iban|ifsc|"
    r"policy_id|policy_no|policy_number|policyholder_id|policyholder_number|member_id|member_number|customer_id_number|"
    r"ssn|social_security_number|tax_id|pan_number|aadhaar|aadhaar_number|aadhar|aadhar_number|passport_number|"
    r"email|email_address|phone|phone_number|mobile_number|address|street_address|date_of_birth|dob|"
    r"salary|employee_salary|payroll|bonus|compensation|tax_return|medical_record|diagnosis|health_report|"
    r"claimant_name|nominee_name|beneficiary_name|fraud_case|investigation_notes|transaction_history|"
    r"purchase_history|loyalty_id|"
    # Identifiers found in the governed gold schemas (Unity Catalog metadata).
    r"national_id|insurance_id|license_no|first_name|last_name|full_name|pincode|postal_code|"
    r"counterparty_account|expiry_date|card_expiry|exp_date|blood_group|icd10_code)(?![a-z0-9])",
    re.IGNORECASE,
)
# Never returned in full to anyone, including administrators.
_ALWAYS_REDACTED_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:aadhaar|aadhar|aadhaar_number|aadhar_number|national_id|expiry_date|card_expiry|exp_date)(?![a-z0-9])",
    re.IGNORECASE,
)
# Record-level linking keys and policy values: usable to count, total or join, but business
# users may not return them or filter on them to look up an individual record.
_LINKING_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:txn_id|card_id|patient_id|holder_id|encounter_id|claim_id|sum_insured)(?![a-z0-9])",
    re.IGNORECASE,
)
# The first alternative consumes UUIDs whole, so their digit runs are not read as identifiers. A
# "." only rules out a match as a decimal point; a number can still end a sentence.
_UUID_VALUE = r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
_CARD_CANDIDATE = re.compile(rf"{_UUID_VALUE}|(?<![\d.])\d(?:[ -]?\d){{12,18}}(?!\d|\.\d|[ -]\d)")
_AADHAAR_CANDIDATE = re.compile(rf"{_UUID_VALUE}|(?<![\d.])[2-9]\d{{3}}[ -]?\d{{4}}[ -]?\d{{4}}(?!\d|\.\d)")
_INDIAN_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")
_LABELLED_CVV = re.compile(r"\b(?:cvv2?|cvc2?|cvn|security\s+code)\b\s*[:=#-]?\s*\d{3,4}\b", re.IGNORECASE)
_EMAIL_VALUE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_VALUE = re.compile(r"(?:\+91[ -]?[6-9]\d{9}\b)|\b(?:phone|mobile)\b\s*[:=#-]?\s*\+?\d[\d -]{8,}\d", re.IGNORECASE)
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_SELECT_STAR = re.compile(r"\bselect\s+(?:distinct\s+)?(?:[\w\"`]+\.)?\*\s*(?:,|\bfrom\b)", re.IGNORECASE)
_CARD_NUMBER_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:card_number|card_no|card_pan|pan|primary_account_number)(?![a-z0-9])",
    re.IGNORECASE,
)
_CREDENTIAL_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:password|passwd|pwd|secret|access_token|api_key|credential|token_hash)(?![a-z0-9])",
    re.IGNORECASE,
)


def _mask_card_number(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value))
    return f"**** **** **** {digits[-4:]}" if 12 <= len(digits) <= 19 else "****"


def _passes_luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number = number * 2 - 9 if number > 4 else number * 2
        total += number
    return total % 10 == 0


def _passes_verhoeff(digits: str) -> bool:
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[index % 8][int(char)]]
    return check == 0


def redact_text(text: str, role: str = "analyst") -> str:
    """Detect sensitive values in free text, whatever column or sentence they appear in.

    Enforced in code so it holds for any model output. Checksums keep amounts and other long
    numbers from being mistaken for card or Aadhaar numbers.
    """
    if not text:
        return text

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        if re.fullmatch(_UUID_VALUE, match.group()):
            return match.group()
        return _mask_card_number(digits) if 13 <= len(digits) <= 19 and _passes_luhn(digits) else match.group()

    def aadhaar(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        if re.fullmatch(_UUID_VALUE, match.group()):
            return match.group()
        return "[REDACTED-AADHAAR]" if _passes_verhoeff(digits) else match.group()

    redacted = _LABELLED_CVV.sub("[REDACTED-CVV]", text)
    redacted = _CARD_CANDIDATE.sub(card, redacted)
    redacted = _AADHAAR_CANDIDATE.sub(aadhaar, redacted)
    if role != "admin":
        redacted = _INDIAN_PAN.sub("[REDACTED-PAN]", redacted)
        redacted = _EMAIL_VALUE.sub("[REDACTED-EMAIL]", redacted)
        redacted = _PHONE_VALUE.sub("[REDACTED-PHONE]", redacted)
    return redacted


def llm_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The only form of query results an LLM may receive, regardless of the viewer's role."""
    safe: list[dict[str, Any]] = []
    for row in rows:
        safe_row: dict[str, Any] = {}
        for key, value in row.items():
            normalized = _normalize_key(key)
            if _CVV_COLUMN.search(normalized):
                continue
            if (
                _RESTRICTED_COLUMN.search(normalized)
                or _CREDENTIAL_COLUMN.search(normalized)
                or (_LINKING_COLUMN.search(normalized) and isinstance(value, str))
            ):
                safe_row[key] = "[REDACTED]"
            else:
                safe_row[key] = redact_text(value) if isinstance(value, str) else value
        safe.append(safe_row)
    return safe


def _linking_column_violations(sql: str) -> set[str]:
    try:
        tree = parse_one(sql, read="databricks")
    except (ParseError, ValueError):
        # Cannot tell how the column is used, so fail closed.
        return {match.group().lower() for match in _LINKING_COLUMN.finditer(sql)}
    violations = set()
    for column in tree.find_all(exp.Column):
        if _LINKING_COLUMN.fullmatch(column.name) and not (
            column.find_ancestor(exp.Count, exp.Sum, exp.Avg) or column.find_ancestor(exp.Join)
        ):
            violations.add(column.name.lower())
    return violations


def requested_sensitive_type(text: str) -> str | None:
    if _CVV_REQUEST.search(text):
        return "never_disclose"
    if _RESTRICTED_REQUEST.search(text):
        return "admin_only"
    return None


def sensitive_field_candidates(text: str) -> tuple[str, ...]:
    if _POLICY_REQUEST.search(text):
        return ("policy_id", "policy_number", "policy_no", "policyholder_id", "policyholder_number", "member_id", "member_number")
    if _CARD_REQUEST.search(text):
        return ("card_number", "card_no", "card_pan", "pan", "primary_account_number")
    if _PERSON_IDENTIFIER_REQUEST.search(text):
        return ("customer_id_number", "ssn", "social_security_number", "tax_id", "passport_number")
    if _ACCOUNT_REQUEST.search(text):
        return ("account_number", "account_no", "account_id", "acct_number", "acct_no", "bank_account_number", "iban")
    return ()


def require_sensitive_request_access(text: str, role: str) -> None:
    kind = requested_sensitive_type(text)
    if kind == "never_disclose":
        raise HTTPException(
            status_code=403,
            detail="CVV/CVC and card security codes are never retrievable, including by administrators.",
        )
    if kind == "admin_only" and role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Account numbers and policy/member identifiers are restricted to authorized administrators.",
        )


def require_sql_sensitive_access(sql: str, role: str) -> None:
    if _CVV_COLUMN.search(sql):
        raise HTTPException(
            status_code=403,
            detail="CVV/CVC and card security codes are never retrievable, including by administrators.",
        )
    if _SELECT_STAR.search(sql):
        raise HTTPException(
            status_code=403,
            detail="Wildcard column selection is disabled to prevent accidental exposure of sensitive fields. Select only required columns.",
        )
    if role != "admin" and _RESTRICTED_COLUMN.search(sql):
        raise HTTPException(
            status_code=403,
            detail="This query targets restricted account or policy identifiers. Only authorized administrators may access those fields.",
        )
    if role != "admin":
        linking = _linking_column_violations(sql)
        if linking:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Record-level identifiers ({', '.join(sorted(linking))}) can only be counted, totalled or "
                    "joined on. Ask for an aggregate instead."
                ),
            )


def require_compiled_sql_safe(sql: str) -> None:
    """For SQL compiled from the metric registry, for every role: identifiers may only link tables.

    Stricter than require_sql_sensitive_access for what a query returns or filters on, and allows
    the one thing an approved aggregate needs, joining on an account or customer key.
    """
    if _CVV_COLUMN.search(sql):
        raise HTTPException(status_code=403, detail="CVV/CVC and card security codes are never retrievable, including by administrators.")
    tree = parse_one(sql, read="databricks")
    for column in tree.find_all(exp.Column):
        identifier = _RESTRICTED_COLUMN.fullmatch(column.name) or _LINKING_COLUMN.fullmatch(column.name)
        if identifier and not column.find_ancestor(exp.Join):
            raise HTTPException(status_code=403, detail=f"Approved queries may not read {column.name}.")


def sanitize_sensitive_rows(rows: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        safe_row: dict[str, Any] = {}
        for key, value in row.items():
            normalized_key = _normalize_key(key)
            if _CVV_COLUMN.search(normalized_key):
                continue
            if _ALWAYS_REDACTED_COLUMN.search(normalized_key):
                safe_row[key] = "[REDACTED]"
            elif role != "admin" and (
                _RESTRICTED_COLUMN.search(normalized_key)
                or (_LINKING_COLUMN.search(normalized_key) and isinstance(value, str))
            ):
                safe_row[key] = "[REDACTED]"
            elif _CARD_NUMBER_COLUMN.search(normalized_key):
                safe_row[key] = _mask_card_number(value)
            elif isinstance(value, str):
                # Sensitive values can sit in any column (free text, notes, unlisted fields).
                safe_row[key] = redact_text(value, role)
            else:
                safe_row[key] = value
        sanitized.append(safe_row)
    return sanitized


def has_cvv_columns(rows: list[dict[str, Any]]) -> bool:
    return any(
        _CVV_COLUMN.search(_normalize_key(key))
        for row in rows
        for key in row
    )


def has_restricted_columns(rows: list[dict[str, Any]]) -> bool:
    return any(
        _RESTRICTED_COLUMN.search(_normalize_key(key))
        for row in rows
        for key in row
    )


def is_sensitive_column(column_name: str) -> bool:
    normalized = _normalize_key(column_name)
    return bool(
        _CVV_COLUMN.search(normalized)
        or _RESTRICTED_COLUMN.search(normalized)
        or _CREDENTIAL_COLUMN.search(normalized)
    )


def _normalize_key(key: str) -> str:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-z0-9]+", "_", snake_case.lower()).strip("_")
