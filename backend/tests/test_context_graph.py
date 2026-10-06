import unittest

from app.services.context_graph import ContextGraphService


class ContextGraphTests(unittest.TestCase):
    def test_sensitive_lookup_can_inspect_tables_outside_question_context(self) -> None:
        graph = ContextGraphService()
        graph.tables = {
            "products": [{"name": "name", "type": "VARCHAR"}],
            "claims": [{"name": "policy_number", "type": "VARCHAR"}],
        }
        graph.graph.add_nodes_from(graph.tables)
        graph.glossary = [
            {
                "domain": "insurance",
                "business_term": "policy",
                "table_name": "products",
                "column_name": "name",
                "description": "Insurance product",
            }
        ]

        contextual_schema = graph.resolve("Show policy numbers", "insurance")
        complete_schema = graph.resolve(
            "Show policy numbers",
            "insurance",
            include_all_tables=True,
        )

        self.assertNotIn("claims", contextual_schema["tables"])
        self.assertIn("claims", complete_schema["tables"])


if __name__ == "__main__":
    unittest.main()
