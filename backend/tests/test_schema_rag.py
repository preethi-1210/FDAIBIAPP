import unittest
from unittest.mock import Mock, patch

from langchain_core.documents import Document

from app.services.assistant_graph import BusinessAssistantGraph
from app.services.context_graph import ContextGraphService
from app.services.schema_rag import SchemaRagService


class SchemaRagTests(unittest.TestCase):
    @patch("app.services.schema_rag.Chroma")
    @patch("app.services.schema_rag.OllamaEmbeddings")
    def test_index_contains_only_domain_scoped_schema_and_glossary(self, _embeddings, chroma) -> None:
        vector_store = chroma.return_value
        vector_store.get.return_value = {"ids": []}
        rag = SchemaRagService()

        rag.sync(
            {"orders": [{"name": "total_amount", "type": "DOUBLE"}]},
            [
                {
                    "domain": "retail",
                    "business_term": "revenue",
                    "table_name": "orders",
                    "column_name": "total_amount",
                    "description": "Sales value",
                }
            ],
        )

        documents = vector_store.add_documents.call_args.args[0]
        ids = vector_store.add_documents.call_args.kwargs["ids"]
        self.assertEqual(len(documents), 5)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all("total_amount" in doc.page_content for doc in documents))
        self.assertTrue(all("domain" in doc.metadata for doc in documents))
        self.assertTrue(any(doc.metadata["kind"] == "glossary" for doc in documents))
        self.assertTrue(rag.status()["indexed"])

    @patch("app.services.schema_rag.Chroma")
    @patch("app.services.schema_rag.OllamaEmbeddings")
    def test_retrieval_filters_to_the_signed_in_domain(self, _embeddings, chroma) -> None:
        vector_store = chroma.return_value
        vector_store.get.return_value = {"ids": []}
        expected = Document(
            page_content="Retail revenue is orders total amount.",
            metadata={"domain": "retail", "kind": "glossary", "table": "orders"},
        )
        vector_store.similarity_search.return_value = [expected]
        rag = SchemaRagService()
        rag.sync({"orders": [{"name": "total_amount", "type": "DOUBLE"}]}, [])

        results = rag.retrieve("sales revenue", "retail")

        self.assertEqual(results, [expected])
        vector_store.similarity_search.assert_called_once_with(
            "sales revenue",
            k=8,
            filter={"domain": "retail"},
        )

    @patch("app.services.assistant_graph.plan_query", return_value={"is_prediction": False})
    def test_langgraph_runs_planning_then_domain_context_retrieval(self, _plan) -> None:
        context = Mock()
        context.resolve.return_value = {"tables": {"orders": []}, "rag_context": []}
        graph = BusinessAssistantGraph(context)
        history = [{"role": "user", "content": "sales by region"}]

        state = graph.invoke(
            "and for last month?",
            "retail",
            conversation_context=history,
        )

        self.assertFalse(state["plan"]["is_prediction"])
        self.assertIn("orders", state["schema_context"]["tables"])
        _plan.assert_called_once_with("and for last month?", None, history)
        context.resolve.assert_called_once_with(
            "and for last month?",
            "retail",
            include_all_tables=False,
            retrieval_text="sales by region and for last month?",
        )


if __name__ == "__main__":
    unittest.main()
