import base64
import hashlib
import hmac
import secrets


_HASH_SCHEME = "pbkdf2_sha256"
_HASH_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _HASH_ITERATIONS)
    salt_text = base64.urlsafe_b64encode(salt).decode()
    derived_text = base64.urlsafe_b64encode(derived).decode()
    return f"{_HASH_SCHEME}${_HASH_ITERATIONS}${salt_text}${derived_text}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash.startswith(f"{_HASH_SCHEME}$"):
        from .config import settings

        legacy_hash = hashlib.sha256(f"{settings.jwt_secret}:{password}".encode()).hexdigest()
        return hmac.compare_digest(legacy_hash, stored_hash)

    try:
        scheme, iterations, salt_text, expected_text = stored_hash.split("$", maxsplit=3)
        if scheme != _HASH_SCHEME:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(expected_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
