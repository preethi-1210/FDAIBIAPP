import networkx as nx
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from ..warehouse import warehouse_schema_columns, warehouse_status
from .schema_rag import SchemaRagService

_APP_TABLES = {
    "app_users",
    "conversation_messages",
    "conversations",
    "hitl_reviews",
    "query_history",
}


class ContextGraphService:
    def __init__(self) -> None:
        self.graph = nx.Graph()
        self.tables: dict[str, list[dict[str, str]]] = {}
        self.domain_tables: dict[str, dict[str, list[dict[str, str]]]] = {}
        self.glossary: list[dict[str, str]] = []
        self.rag = SchemaRagService()

    def rebuild(self, db: Session) -> None:
        self.graph.clear()
        self.tables = {}
        self.domain_tables = {}
        bind = db.get_bind()
        inspector = inspect(bind)
        for table in inspector.get_table_names():
            if table in _APP_TABLES:
                continue
            cols = inspector.get_columns(table)
            self.tables[table] = [{"name": c["name"], "type": str(c["type"])} for c in cols]
            self.graph.add_node(table)
            for fk in inspector.get_foreign_keys(table):
                referred = fk.get("referred_table")
                if referred:
                    self.graph.add_edge(table, referred, columns=fk.get("constrained_columns"))

        rows = db.execute(
            text("SELECT domain, business_term, table_name, column_name, description FROM glossary")
        ).mappings()
        self.glossary = [dict(r) for r in rows]
        if settings.warehouse in {"databricks", "snowflake"} and warehouse_status()["ready"]:
            if settings.warehouse == "databricks":
                for domain_entry in settings.databricks_domain_catalogs.split(","):
                    domain = domain_entry.strip().partition(":")[0]
                    domain_schema = warehouse_schema_columns(domain)
                    self.domain_tables[domain] = domain_schema
                    self.tables.update(domain_schema)
                self.rag.sync(self.tables, self.glossary, self.domain_tables)
                return
            self.refresh_warehouse_schema()
        self.rag.sync(self.tables, self.glossary)

    def resolve(
        self,
        question: str,
        domain: str | None = None,
        include_all_tables: bool = False,
        retrieval_text: str | None = None,
    ) -> dict:
        q = question.lower()
        glossary = [g for g in self.glossary if not domain or g.get("domain") == domain]
        tables = self.domain_tables.get(domain or "", self.tables)
        matched_tables: set[str] = set()
        retrieved = self.rag.retrieve(retrieval_text or question, domain) if domain else []
        for document in retrieved:
            table = document.metadata.get("table")
            if table in tables:
                matched_tables.add(table)
        for row in glossary:
            if row["business_term"].lower() in q:
                matched_tables.add(row["table_name"])
        for table in tables:
            if table.rstrip("s") in q or table in q:
                matched_tables.add(table)
        if not matched_tables:
            matched_tables = set(self.tables.keys())

        expanded = set(matched_tables)
        for table in list(matched_tables):
            expanded.update(self.graph.neighbors(table))

        join_paths = []
        nodes = list(expanded)
        for i, a in enumerate(nodes):
            for b in nodes[i + 1 :]:
                if nx.has_path(self.graph, a, b):
                    join_paths.append(nx.shortest_path(self.graph, a, b))

        schema_subset = tables if include_all_tables else {
            table: tables[table] for table in expanded if table in tables
        }
        return {
            "tables": schema_subset,
            "join_paths": join_paths,
            "glossary": [g for g in glossary if g["table_name"] in expanded],
            "rag_context": [document.page_content for document in retrieved],
        }

    def refresh_warehouse_schema(self) -> None:
        if settings.warehouse not in {"databricks", "snowflake"}:
            return
        if settings.warehouse == "databricks":
            self.domain_tables = {}
            for domain_entry in settings.databricks_domain_catalogs.split(","):
                domain = domain_entry.strip().partition(":")[0]
                self.domain_tables[domain] = warehouse_schema_columns(domain)
            self.tables = {
                table: columns
                for domain_schema in self.domain_tables.values()
                for table, columns in domain_schema.items()
            }
            self.graph.clear()
            for table in self.tables:
                self.graph.add_node(table)
            self.rag.sync(self.tables, self.glossary, self.domain_tables)
            return
        tables = warehouse_schema_columns()
        self.tables = tables
        self.graph.clear()
        for table in tables:
            self.graph.add_node(table)
        self.rag.sync(self.tables, self.glossary)
