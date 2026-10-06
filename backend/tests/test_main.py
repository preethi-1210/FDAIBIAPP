import unittest
from unittest.mock import patch

from app.main import list_domains


class DemoAccountVisibilityTests(unittest.TestCase):
    def test_demo_accounts_are_not_advertised_outside_demo_mode(self) -> None:
        with patch("app.main.settings.demo_mode", False):
            response = list_domains()
        self.assertEqual(response["users"], [])
        self.assertTrue(response["domains"])

    def test_demo_accounts_remain_available_in_demo_mode(self) -> None:
        with patch("app.main.settings.demo_mode", True):
            response = list_domains()
        self.assertTrue(any(user["username"] == "ins.user" for user in response["users"]))


if __name__ == "__main__":
    unittest.main()
