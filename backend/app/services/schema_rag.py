import logging
from threading import RLock
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

from ..config import settings
from ..domains import DOMAINS

logger = logging.getLogger(__name__)


class SchemaRagError(RuntimeError):
    pass


class SchemaRagService:
    """Retrieve only schema and glossary text; business rows are never indexed."""

    def __init__(self) -> None:
        base_url = settings.ollama_base_url.removesuffix("/v1").rstrip("/")
        self._embeddings = OllamaEmbeddings(
            model=settings.rag_embedding_model,
            base_url=base_url,
        )
        self._lock = RLock()
        self._vector_store = self._new_vector_store()
        self._documents: list[Document] = []
        self._ids: list[str] = []
        self._indexed = False

    def _new_vector_store(self) -> Chroma:
        return Chroma(
            collection_name="business_schema",
            embedding_function=self._embeddings,
            persist_directory=settings.chroma_db_path,
        )

    def sync(
        self,
        tables: dict[str, list[dict[str, str]]],
        glossary: list[dict[str, str]],
        domain_tables: dict[str, dict[str, list[dict[str, str]]]] | None = None,
    ) -> None:
        documents: list[Document] = []
        ids: list[str] = []
        for domain in DOMAINS:
            if domain_tables is not None and domain not in domain_tables:
                continue
            domain_schema = (domain_tables or {}).get(domain, tables)
            for table, columns in domain_schema.items():
                fields = ", ".join(f"{column['name']} ({column['type']})" for column in columns)
                doc_id = f"schema:{domain}:{table}"
                ids.append(doc_id)
                documents.append(
                    Document(
                        id=doc_id,
                        page_content=f"Business domain: {domain}. Table: {table}. Fields: {fields}",
                        metadata={"domain": domain, "kind": "schema", "table": table},
                    )
                )
            for item in glossary:
                if item.get("domain") != domain:
                    continue
                doc_id = (
                    f"glossary:{domain}:{item['business_term']}:"
                    f"{item['table_name']}:{item['column_name']}"
                )
                ids.append(doc_id)
                documents.append(
                    Document(
                        id=doc_id,
                        page_content=(
                            f"Business domain: {domain}. Business term: {item['business_term']}. "
                            f"Table: {item['table_name']}. Field: {item['column_name']}. "
                            f"Definition: {item['description']}"
                        ),
                        metadata={
                            "domain": domain,
                            "kind": "glossary",
                            "table": str(item["table_name"]),
                        },
                    )
                )

        with self._lock:
            try:
                self._vector_store.reset_collection()
                # self._vector_store = self._new_vector_store()
                if documents:
                    self._vector_store.add_documents(documents, ids=ids)
            except Exception as exc:
                self._indexed = False
                raise SchemaRagError(
                    f"Could not index schema/glossary with Ollama embedding model "
                    f"{settings.rag_embedding_model}: {exc}"
                ) from exc
            self._documents = documents
            self._ids = ids
            self._indexed = True

    def retrieve(self, question: str, domain: str, limit: int = 8) -> list[Document]:
        if not self._indexed:
            return []
        with self._lock:
            try:
                return self._vector_store.similarity_search(
                    question,
                    k=limit,
                    filter={"domain": domain},
                )
            except Exception as exc:
                error_text = str(exc).lower()
                stale_collection = (
                    "error finding id" in error_text
                    or "error getting collection" in error_text
                    or "collection" in error_text and "does not exist" in error_text
                )

                if not stale_collection:
                    raise SchemaRagError(
                        f"Schema retrieval failed for the {domain} domain: {exc}"
                    ) from exc

                logger.warning(
                    "Recreating stale Chroma schema collection and retrying retrieval",
                    extra={"domain": domain},
                )

                try:
                    # Recreate the wrapper to obtain a fresh collection reference.
                    self._vector_store = self._new_vector_store()

                    if self._documents:
                        self._vector_store.add_documents(
                            self._documents,
                            ids=self._ids,
                        )

                    return self._vector_store.similarity_search(
                        question,
                        k=limit,
                        filter={"domain": domain},
                    )
                except Exception as recovery_exc:
                    self._indexed = False
                    raise SchemaRagError(
                        f"Schema retrieval failed for the {domain} domain; "
                        f"rebuilding the stale schema index also failed: {recovery_exc}"
                    ) from recovery_exc

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "indexed": self._indexed,
            "store": "ChromaDB",
            "embedding_model": settings.rag_embedding_model,
            "content": "database schemas and business glossary only",
        }
