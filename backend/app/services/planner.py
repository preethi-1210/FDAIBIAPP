from typing import Any

from ..llm import complete_json


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


def plan_query(question: str, llm_override: str | None = None) -> dict[str, Any]:
    llm_plan = complete_json(
        system=(
            "You are the Planner agent for a BI platform. "
            "Return JSON with keys: subtasks (array of retrieval|relationship|prediction), "
            "is_prediction (boolean), intent (short string)."
        ),
        user=question,
        override=llm_override,
    )
    if llm_plan:
        llm_plan.setdefault("subtasks", ["retrieval"])
        llm_plan.setdefault("is_prediction", "prediction" in llm_plan.get("subtasks", []))
        return llm_plan

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
