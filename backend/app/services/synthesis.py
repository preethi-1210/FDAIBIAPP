from typing import Any

from ..llm import complete_text
from ..schemas import PredictionPayload, UncertaintyPayload
from .conversation_context import format_conversation_context


def synthesize(
    question: str,
    table: list[dict[str, Any]],
    sql: str | None,
    prediction: PredictionPayload | None,
    uncertainty: UncertaintyPayload | None,
    domain: str = "retail",
    llm_override: str | None = None,
    conversation_context: list[dict[str, str]] | None = None,
) -> str:
    preview = table[:12]
    pred_txt = ""
    if prediction and uncertainty:
        pred_txt = (
            f"Prediction type={prediction.type}. "
            f"Uncertainty/confidence range={uncertainty.confidence_range}. "
            f"Labels sample={prediction.labels[:8]}. "
            f"Probabilities sample={prediction.probabilities[:8]}. "
            f"Values sample={prediction.values[:8]}."
        )

    context = format_conversation_context(conversation_context)
    llm = complete_text(
        system=(
            "You are the Insight/Synthesis agent. Answer the user's BI question using the SQL result. "
            "If a prediction is present, always mention the confidence/uncertainty range. "
            "Then self-check: if the draft does not answer the original question, rewrite it. "
            "Return only the final answer."
        ),
        user=(
            f"Recent conversation for interpreting follow-up references:\n{context}\n"
            f"Domain: {domain}\nQuestion: {question}\nSQL: {sql}\nRows: {preview}\n{pred_txt}"
            if context
            else f"Domain: {domain}\nQuestion: {question}\nSQL: {sql}\nRows: {preview}\n{pred_txt}"
        ),
        override=llm_override,
    )
    if llm:
        return llm.strip()

    if not table:
        return "I queried the warehouse but found no matching rows for that question."

    if prediction and prediction.type == "classification" and prediction.probabilities:
        likely = sum(1 for p in prediction.probabilities if p >= 0.5)
        share = likely / len(prediction.probabilities)
        lo, hi = uncertainty.confidence_range if uncertainty else [0.6, 0.8]
        return (
            f"Based on customer order history, about {share:.0%} of the scored customers "
            f"are predicted as likely to churn. Typical model confidence sits between "
            f"{lo:.0%} and {hi:.0%}. Predictions run with self-hosted TabPFN "
            f"(no Genie or hosted ML API)."
        )

    if prediction and prediction.type == "regression" and prediction.values:
        lo, hi = uncertainty.confidence_range if uncertainty else [0, 0]
        avg = sum(prediction.values) / len(prediction.values)
        return (
            f"Projected value averages {avg:,.2f}, with an uncertainty band of "
            f"{lo:,.2f} to {hi:,.2f} from the self-hosted TabPFN model."
        )

    keys = list(table[0].keys())
    if "revenue" in keys and len(table) == 1:
        row = table[0]
        extra = f" across {row.get('order_count', 'n/a')} orders" if "order_count" in row else ""
        return f"Revenue is {float(row['revenue']):,.2f}{extra}."
    if "revenue" in keys:
        top = max(table, key=lambda r: float(r.get("revenue") or 0))
        label = top.get("region") or top.get("segment") or top.get("product") or top.get("month") or "the top group"
        return f"Highest revenue is {float(top['revenue']):,.2f} for {label}."
    return f"Returned {len(table)} rows from the warehouse for: {question}"
