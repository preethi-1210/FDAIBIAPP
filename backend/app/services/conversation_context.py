from typing import Any


def format_conversation_context(messages: list[dict[str, str]] | None) -> str:
    if not messages:
        return ""
    return "\n".join(
        f"{message['role'].capitalize()}: {message['content'][:2000]}"
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
