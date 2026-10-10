from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .context_graph import ContextGraphService
from .conversation_context import retrieval_question
from .planner import plan_query


class AssistantState(TypedDict, total=False):
    question: str
    domain: str
    llm_override: str | None
    include_all_tables: bool
    conversation_context: list[dict[str, str]]
    plan: dict[str, Any]
    schema_context: dict[str, Any]


class BusinessAssistantGraph:
    """Plan and retrieve domain-scoped context before guarded SQL execution."""

    def __init__(self, context: ContextGraphService) -> None:
        self.context = context
        workflow = StateGraph(AssistantState)
        workflow.add_node("plan", self._plan)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", "retrieve")
        workflow.add_edge("retrieve", END)
        self._workflow = workflow.compile()

    def invoke(
        self,
        question: str,
        domain: str,
        llm_override: str | None = None,
        include_all_tables: bool = False,
        conversation_context: list[dict[str, str]] | None = None,
    ) -> AssistantState:
        return self._workflow.invoke(
            {
                "question": question,
                "domain": domain,
                "llm_override": llm_override,
                "include_all_tables": include_all_tables,
                "conversation_context": conversation_context or [],
            }
        )

    @staticmethod
    def _plan(state: AssistantState) -> AssistantState:
        return {
            "plan": plan_query(
                state["question"],
                state.get("llm_override"),
                state.get("conversation_context"),
            )
        }

    def _retrieve(self, state: AssistantState) -> AssistantState:
        return {
            "schema_context": self.context.resolve(
                state["question"],
                state["domain"],
                include_all_tables=state.get("include_all_tables", False),
                retrieval_text=retrieval_question(
                    state["question"],
                    state.get("conversation_context"),
                ),
            )
        }
