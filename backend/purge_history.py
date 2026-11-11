"""Apply the retention policy to Ask AI history. Dry run unless --apply is given.

Conversations and query history older than HISTORY_RETENTION_DAYS are deleted. Access-audit events
and human-review decisions are kept. Schedule it, for example daily.
"""

import argparse
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db import SessionLocal
from app.models import Conversation, ConversationMessage, ConversationQueryState, QueryHistory


def purge(db, days: int, apply: bool) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    expired = [row.id for row in db.query(Conversation.id).filter(Conversation.updated_at < cutoff)]
    counts = {
        "conversations": len(expired),
        "messages": db.query(ConversationMessage).filter(ConversationMessage.conversation_id.in_(expired)).count(),
        "query_history": db.query(QueryHistory).filter(QueryHistory.created_at < cutoff).count(),
    }
    if apply:
        db.query(ConversationMessage).filter(ConversationMessage.conversation_id.in_(expired)).delete(synchronize_session=False)
        db.query(ConversationQueryState).filter(ConversationQueryState.conversation_id.in_(expired)).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.id.in_(expired)).delete(synchronize_session=False)
        db.query(QueryHistory).filter(QueryHistory.created_at < cutoff).delete(synchronize_session=False)
        db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=settings.history_retention_days)
    parser.add_argument("--apply", action="store_true", help="Delete the rows; without it only counts are shown.")
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("Set HISTORY_RETENTION_DAYS or pass --days with a positive number.")
    with SessionLocal() as db:
        print(("deleted" if args.apply else "would delete"), purge(db, args.days, args.apply))


if __name__ == "__main__":
    main()
