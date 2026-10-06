import hashlib
import unittest
from unittest.mock import patch

from app.security import hash_password, verify_password


class PasswordSecurityTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self) -> None:
        first = hash_password("correct horse battery staple")
        second = hash_password("correct horse battery staple")

        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("correct horse battery staple", first))
        self.assertFalse(verify_password("wrong password", first))

    def test_legacy_demo_hash_verifies_for_login_migration(self) -> None:
        with patch("app.config.settings.jwt_secret", "test-jwt-secret"):
            legacy = hashlib.sha256(b"test-jwt-secret:demo-password").hexdigest()
            self.assertTrue(verify_password("demo-password", legacy))
            self.assertFalse(verify_password("wrong-password", legacy))


if __name__ == "__main__":
    unittest.main()
