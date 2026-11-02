import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, inspect

from app import db
from app.models import Base


class DatabaseSessionTests(unittest.TestCase):
    def test_database_dependency_closes_session_after_success(self) -> None:
        session = Mock()
        with patch("app.db.SessionLocal", return_value=session):
            dependency = db.get_db()
            self.assertIs(next(dependency), session)
            with self.assertRaises(StopIteration):
                next(dependency)
        session.close.assert_called_once_with()
        session.rollback.assert_not_called()

    def test_database_dependency_rolls_back_and_closes_after_failure(self) -> None:
        session = Mock()
        with patch("app.db.SessionLocal", return_value=session):
            dependency = db.get_db()
            next(dependency)
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                dependency.throw(RuntimeError("write failed"))
        session.rollback.assert_called_once_with()
        session.close.assert_called_once_with()

    def test_oauth_identity_table_is_created_without_rebuilding_existing_schema(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            self.assertIn("oauth_identities", inspect(engine).get_table_names())
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
