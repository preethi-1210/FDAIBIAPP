import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import AuthUser
from app.db import Base
from app.main import _get_conversation_context, query as query_endpoint
from app.models import Conversation, ConversationMessage
from app.schemas import QueryRequest, QueryResponse


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = AuthUser(username="analyst", domain="retail", role="analyst")

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_context_is_limited_to_latest_twelve_messages_in_order(self) -> None:
        conversation_id = uuid4()
        self.db.add(
            Conversation(
                id=str(conversation_id),
                username=self.user.username,
                domain=self.user.domain,
                title="Sales",
            )
        )
        self.db.add_all(
            [
                ConversationMessage(
                    conversation_id=str(conversation_id),
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"turn-{index}",
                )
                for index in range(13)
            ]
        )
        self.db.commit()

        _conversation, context = _get_conversation_context(
            self.db,
            self.user,
            conversation_id,
            "ignored title",
        )

        self.assertEqual(len(context), 12)
        self.assertEqual(context[0]["content"], "turn-1")
        self.assertEqual(context[-1]["content"], "turn-12")

    def test_context_cannot_be_read_across_users_or_domains(self) -> None:
        conversation_id = uuid4()
        self.db.add(
            Conversation(
                id=str(conversation_id),
                username="another-user",
                domain="retail",
                title="Private",
            )
        )
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            _get_conversation_context(self.db, self.user, conversation_id, "ignored")

        self.assertEqual(raised.exception.status_code, 404)

    def test_new_conversation_is_scoped_to_the_authenticated_domain(self) -> None:
        conversation_id = uuid4()

        conversation, context = _get_conversation_context(
            self.db,
            self.user,
            conversation_id,
            "Revenue by region",
        )

        self.assertEqual(conversation.username, "analyst")
        self.assertEqual(conversation.domain, "retail")
        self.assertEqual(context, [])
        self.assertEqual(conversation.title, "Revenue by region")

    def test_query_endpoint_persists_and_reuses_follow_up_context(self) -> None:
        calls: list[list[dict[str, str]]] = []

        def run(_db, question, _user, _provider, *, conversation_context, conversation_id):
            calls.append(conversation_context)
            return QueryResponse(answer=f"Answer: {question}", conversation_id=conversation_id)

        with patch("app.main.orchestrator.run", side_effect=run):
            first = query_endpoint(QueryRequest(question="Revenue by region"), self.db, self.user)
            second = query_endpoint(
                QueryRequest(
                    question="And the prior month?",
                    conversation_id=first.conversation_id,
                ),
                self.db,
                self.user,
            )

        self.assertEqual(calls[0], [])
        self.assertEqual(calls[1], [
            {"role": "user", "content": "Revenue by region"},
            {"role": "assistant", "content": "Answer: Revenue by region"},
        ])
        self.assertEqual(second.conversation_id, first.conversation_id)
        self.assertEqual(self.db.query(ConversationMessage).count(), 4)


if __name__ == "__main__":
    unittest.main()
