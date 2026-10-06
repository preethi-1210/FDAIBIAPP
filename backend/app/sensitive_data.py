import re
from typing import Any

from fastapi import HTTPException

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
    r"identity\s+documents?|payroll|salar(?:y|ies)|compensation|bonuses?|financial\s+statements?|"
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
    r"purchase_history|loyalty_id)(?![a-z0-9])",
    re.IGNORECASE,
)
_SELECT_STAR = re.compile(r"\bselect\s+(?:distinct\s+)?(?:[\w\"`]+\.)?\*\s*(?:,|\bfrom\b)", re.IGNORECASE)
_CARD_NUMBER_COLUMN = re.compile(
    r"(?<![a-z0-9])(?:card_number|card_no|card_pan|pan|primary_account_number)(?![a-z0-9])",
    re.IGNORECASE,
)


def _mask_card_number(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value))
    return f"**** **** **** {digits[-4:]}" if 12 <= len(digits) <= 19 else "****"


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


def sanitize_sensitive_rows(rows: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        safe_row: dict[str, Any] = {}
        for key, value in row.items():
            normalized_key = _normalize_key(key)
            if _CVV_COLUMN.search(normalized_key):
                continue
            if role != "admin" and _RESTRICTED_COLUMN.search(normalized_key):
                safe_row[key] = "[REDACTED]"
            elif _CARD_NUMBER_COLUMN.search(normalized_key):
                safe_row[key] = _mask_card_number(value)
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


def _normalize_key(key: str) -> str:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-z0-9]+", "_", snake_case.lower()).strip("_")
