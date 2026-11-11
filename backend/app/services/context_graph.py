import logging
import re
from concurrent.futures import ThreadPoolExecutor

import networkx as nx
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from ..domains import finance_gold_glossary
from ..warehouse import warehouse_schema_columns, warehouse_status
from .schema_rag import SchemaRagError, SchemaRagService

logger = logging.getLogger(__name__)
_TABLE_AFFIXES = re.compile(r"^(?:gold|dim|fact|agg)_|_(?:daily|monthly)$")

_APP_TABLES = {
    "app_users",
    "conversation_messages",
    "conversations",
    "hitl_reviews",
    "oauth_identities",
    "query_history",
    "user_domain_tenants",
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
        if settings.warehouse == "databricks":
            self.glossary = [g for g in self.glossary if g.get("domain") != "finance"] + finance_gold_glossary()
        if settings.warehouse in {"databricks", "snowflake"} and warehouse_status()["ready"]:
            if settings.warehouse == "databricks":
                self.graph.clear()
                for domain, domain_schema in _load_domain_schemas().items():
                    self.domain_tables[domain] = domain_schema
                    self.tables.update(domain_schema)
                    self.graph.add_nodes_from(domain_schema)
                self._sync_rag(self.domain_tables)
                return
            self.refresh_warehouse_schema()
        self._sync_rag()

    def _sync_rag(self, domain_tables: dict[str, dict[str, list[dict[str, str]]]] | None = None) -> None:
        # Schema retrieval is an optional ranking aid for Ask AI; an unavailable embedding
        # service must not block startup or leave the schema graph unbuilt.
        try:
            self.rag.sync(self.tables, self.glossary, domain_tables)
        except SchemaRagError:
            logger.warning("Schema RAG index unavailable; continuing with keyword schema matching", exc_info=True)

    def resolve(
        self,
        question: str,
        domain: str | None = None,
        include_all_tables: bool = False,
        retrieval_text: str | None = None,
    ) -> dict:
        q = question.lower()
        glossary = [g for g in self.glossary if not domain or g.get("domain") == domain]
        if domain == "healthcare":
            tables = self.domain_tables.get(
                domain,
                {name: columns for name, columns in self.tables.items() if name.startswith("healthcare_")},
            )
        else:
            tables = self.domain_tables.get(domain or "", self.tables)
        matched_tables: set[str] = set()
        try:
            retrieved = self.rag.retrieve(retrieval_text or question, domain) if domain else []
        except SchemaRagError:
            # Embeddings are optional here; name and glossary matching still selects tables.
            logger.warning("Schema retrieval unavailable; using keyword matching", exc_info=True)
            retrieved = []
        for document in retrieved:
            table = document.metadata.get("table")
            if table in tables:
                matched_tables.add(table)
        for row in glossary:
            if row["business_term"].lower() in q:
                matched_tables.add(row["table_name"])
        words = {word.rstrip("s") for word in re.findall(r"[a-z]+", q)}
        for table in tables:
            if table.rstrip("s") in q or table in q:
                matched_tables.add(table)
                continue
            # Warehouse tables carry medallion prefixes/suffixes (gold_stores, agg_claims_daily);
            # match the entity the question names. Point-in-time copies are not offered.
            if table.lower().startswith(("snapshot_", "summary_")):
                continue
            entity = _TABLE_AFFIXES.sub("", table.lower())
            if entity.rstrip("s") in words:
                matched_tables.add(table)
        if not matched_tables:
            matched_tables = set(tables.keys())

        expanded = set(matched_tables)
        for table in list(matched_tables):
            if self.graph.has_node(table):
                expanded.update(self.graph.neighbors(table))

        join_paths = []
        nodes = list(expanded)
        for i, a in enumerate(nodes):
            for b in nodes[i + 1 :]:
                if (
                    self.graph.has_node(a)
                    and self.graph.has_node(b)
                    and nx.has_path(self.graph, a, b)
                ):
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
            self.domain_tables = _load_domain_schemas()
            self.tables = {
                table: columns
                for domain_schema in self.domain_tables.values()
                for table, columns in domain_schema.items()
            }
            self.graph.clear()
            for table in self.tables:
                self.graph.add_node(table)
            self._sync_rag(self.domain_tables)
            return
        tables = warehouse_schema_columns()
        self.tables = tables
        self.graph.clear()
        for table in tables:
            self.graph.add_node(table)
        self._sync_rag()


def _load_domain_schemas() -> dict[str, dict[str, list[dict[str, str]]]]:
    # Each domain's metadata comes from its own catalog, so the lookups run concurrently.
    domains = [
        entry.strip().partition(":")[0]
        for entry in settings.databricks_domain_catalogs.split(",")
        if entry.strip()
    ]
    with ThreadPoolExecutor(max_workers=max(1, len(domains)), thread_name_prefix="schema-load") as pool:
        schemas = list(pool.map(warehouse_schema_columns, domains))
    return dict(zip(domains, schemas))
