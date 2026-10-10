from typing import Any

PREDICTION_HINTS = (
    "predict",
    "prediction",
    "likely",
    "forecast",
    "project",
    "projection",
    "will",
    "churn",
    "next month",
    "next quarter",
)


def plan_query(
    question: str,
    llm_override: str | None = None,
    conversation_context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    q = question.lower()
    is_prediction = any(h in q for h in PREDICTION_HINTS)
    is_relationship = any(w in q for w in ("related", "relationship", "join", "connected"))
    subtasks = ["retrieval"]
    if is_relationship:
        subtasks.append("relationship")
    if is_prediction:
        subtasks.append("prediction")
    return {
        "subtasks": subtasks,
        "is_prediction": is_prediction,
        "intent": "prediction" if is_prediction else "retrieval",
    }
