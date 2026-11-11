from typing import Any

from ..sensitive_data import redact_text


# Earlier answers help resolve references; they are not evidence for the current answer.
_SPEAKERS = {"user": "User", "assistant": "Assistant (earlier answer, not verified data)"}


def format_conversation_context(messages: list[dict[str, str]] | None) -> str:
    if not messages:
        return ""
    return "\n".join(
        f"{_SPEAKERS[message['role']]}: {redact_text(message['content'][:2000])}"
        for message in messages[-12:]
        if message.get("role") in {"user", "assistant"} and message.get("content")
    )


def retrieval_question(question: str, messages: list[dict[str, str]] | None) -> str:
    previous_user_questions = [
        message["content"][:500]
        for message in (messages or [])[-12:]
        if message.get("role") == "user" and message.get("content")
    ]
    if not previous_user_questions:
        return question
    return f"{' '.join(previous_user_questions)} {question}"
