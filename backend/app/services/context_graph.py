import networkx as nx
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from ..warehouse import warehouse_schema_columns


class ContextGraphService:
    def __init__(self) -> None:
        self.graph = nx.Graph()
        self.tables: dict[str, list[dict[str, str]]] = {}
        self.glossary: list[dict[str, str]] = []

    def rebuild(self, db: Session) -> None:
        self.graph.clear()
        self.tables = {}
        bind = db.get_bind()
        inspector = inspect(bind)
        for table in inspector.get_table_names():
            if table in {"query_history", "app_users"}:
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

    def resolve(
        self,
        question: str,
        domain: str | None = None,
        include_all_tables: bool = False,
    ) -> dict:
        q = question.lower()
        glossary = [g for g in self.glossary if not domain or g.get("domain") == domain]
        matched_tables: set[str] = set()
        for row in glossary:
            if row["business_term"].lower() in q:
                matched_tables.add(row["table_name"])
        for table in self.tables:
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

        schema_subset = self.tables if include_all_tables else {t: self.tables[t] for t in expanded if t in self.tables}
        return {
            "tables": schema_subset,
            "join_paths": join_paths,
            "glossary": [g for g in glossary if g["table_name"] in expanded],
        }

    def refresh_warehouse_schema(self) -> None:
        if settings.warehouse != "databricks":
            return
        tables = warehouse_schema_columns()
        self.tables = tables
        for table in tables:
            self.graph.add_node(table)
