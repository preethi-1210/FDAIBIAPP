"""Governed knowledge base for definition and documentation questions.

Sources are approved documents stored in PostgreSQL, the metric registry and the glossary.
Retrieval is lexical with a confidence threshold: the corpus is small, it adds no infrastructure,
and pgvector is not installed on the current server. It never answers questions about live data.

    python -m app.services.knowledge ingest --source-key KEY --title TITLE --domain all --file doc.md --approved-by NAME
    python -m app.services.knowledge delete --source-key KEY
"""

import argparse
import json
import logging
import math
import hashlib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..domains import finance_gold_glossary
from ..models import Glossary, KnowledgeChunk, KnowledgeDocument
from .banking_registry import METRICS as BANKING_METRICS, REGISTRY_VERSION
from .metrics import LOSS_RATIO_LIMITATION, METRICS
from .policy import detect_prompt_injection, normalize_input

_MAX_CHUNK_CHARS = 800
logger = logging.getLogger(__name__)
_MAX_CONTEXT_CHARS = 4000
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "calculated", "computed", "define", "defined", "definition",
    "do", "does", "explain", "for", "how", "in", "is", "it", "mean", "meaning", "means", "of", "on", "or", "our",
    "please", "the", "this", "to", "we", "what", "whats", "when", "where", "which", "who", "why", "with", "you",
}


def _terms(text: str) -> list[str]:
    return [
        word[:-1] if word.endswith("s") and len(word) > 3 else word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in _STOPWORDS
    ]


def chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > _MAX_CHUNK_CHARS:
            cut = paragraph.rfind(" ", 0, _MAX_CHUNK_CHARS)
            cut = cut if cut > 0 else _MAX_CHUNK_CHARS
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > _MAX_CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks


def ingest_document(
    db: Session,
    source_key: str,
    title: str,
    text: str,
    domain: str,
    approved_by: str,
) -> dict[str, Any]:
    """Add or update an approved document. Identical content is a no-op; malicious content is refused."""
    text = normalize_input(text)
    if not text:
        raise ValueError("The document is empty.")
    if detect_prompt_injection(text):
        raise ValueError("The document contains instruction-like text aimed at the assistant and was not ingested.")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.source_key == source_key).one_or_none()
    if document is not None and document.status == "active" and document.content_hash == content_hash:
        return {"source_key": source_key, "version": document.version, "changed": False}
    if document is None:
        document = KnowledgeDocument(
            source_key=source_key, title=title, domain=domain, version=1, content_hash=content_hash, approved_by=approved_by
        )
        db.add(document)
        db.flush()
    else:
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).delete(synchronize_session=False)
        document.version += 1
        document.title = title
        document.domain = domain
        document.content_hash = content_hash
        document.status = "active"
        document.approved_by = approved_by
        document.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    chunks = chunk_text(text)
    vectors = embed_texts(chunks) or [None] * len(chunks)
    db.add_all(
        KnowledgeChunk(
            document_id=document.id,
            chunk_index=index,
            text=chunk,
            embedding=json.dumps(vector) if vector else None,
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
    )
    db.commit()
    return {"source_key": source_key, "version": document.version, "changed": True}


def delete_document(db: Session, source_key: str) -> bool:
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.source_key == source_key).one_or_none()
    if document is None:
        return False
    db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).delete(synchronize_session=False)
    document.status = "deleted"
    db.commit()
    return True


def _domain_registries() -> dict:
    # Imported here: the registries read report and metric definitions that import this module's peers.
    from .domain_registries import REGISTRIES

    return REGISTRIES


def _built_in_entries(db: Session, domain: str) -> list[dict[str, Any]]:
    entries = [
        {
            "source": f"metric registry: {metric.key}",
            "title": metric.label,
            "version": None,
            "stale": False,
            "text": f"{metric.label}. {metric.formula} Filters: {metric.filters}. Source tables: {', '.join(metric.sources)}.",
        }
        for metric in METRICS
        if metric.domain == domain
    ]
    if domain == "banking":
        entries = [
            {
                "source": f"banking metric registry v{REGISTRY_VERSION}: {metric.id}",
                "title": metric.label,
                "version": None,
                "stale": False,
                "text": (
                    f"{metric.label}. {metric.definition} Calculated as {metric.aggregation} from "
                    f"{metric.source.from_clause.split()[0]}. {metric.time_semantics}"
                ),
            }
            for metric in BANKING_METRICS
        ]
    registry = _domain_registries().get(domain)
    if registry is not None:
        entries = [
            {
                "source": f"{domain} metric registry v{registry.version}: {metric.id}",
                "title": metric.label,
                "version": None,
                "stale": False,
                "text": (
                    f"{metric.label}. {metric.definition} Calculated as {metric.aggregation} from "
                    f"{metric.source.from_clause.split()[0]}. {metric.time_semantics}"
                ),
            }
            for metric in registry.metrics
        ]
    if domain == "insurance":
        entries.append({
            "source": "metric registry: insurance_loss_ratio",
            "title": "Insurance loss ratio",
            "version": None,
            "stale": False,
            "text": f"Insurance loss ratio. {LOSS_RATIO_LIMITATION}",
        })
    terms = (
        [Glossary(**row) for row in finance_gold_glossary()]
        if domain == "finance" and settings.warehouse == "databricks"
        else db.query(Glossary).filter(Glossary.domain == domain).limit(500)
    )
    for term in terms:
        entries.append({
            "source": f"glossary: {term.business_term}",
            "title": term.business_term,
            "version": None,
            "stale": False,
            "text": f"{term.business_term}. {term.description}",
        })
    return entries


def _document_entries(db: Session, domain: str) -> list[dict[str, Any]]:
    stale_before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=settings.knowledge_stale_after_days)
    rows = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(KnowledgeDocument.status == "active", KnowledgeDocument.domain.in_(("all", domain)))
        .limit(2000)
    )
    return [
        {
            "source": document.source_key,
            "title": document.title,
            "version": document.version,
            "stale": document.updated_at < stale_before,
            "text": chunk.text,
            "embedding": chunk.embedding,
        }
        for chunk, document in rows
    ]


_embedding_cache: dict[str, list[float]] = {}


def embed_texts(texts: list[str], kind: str = "document") -> list[list[float]] | None:
    """Embeddings from the local model, or None when it is unavailable and keywords must do.

    Nomic models are trained with these task prefixes, and matching degrades without them.
    """
    if settings.knowledge_min_similarity <= 0 or not texts:
        return None
    try:
        from langchain_ollama import OllamaEmbeddings

        model = OllamaEmbeddings(
            model=settings.rag_embedding_model,
            base_url=settings.ollama_base_url.removesuffix("/v1"),
        )
        prefix = f"search_{kind}: " if "nomic" in settings.rag_embedding_model else ""
        return model.embed_documents([prefix + text for text in texts])
    except Exception as exc:
        logger.warning("Embedding model unavailable (%s); using keyword search", type(exc).__name__)
        return None


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    size = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / size if size else 0.0


def _entry_vector(entry: dict[str, Any]) -> list[float] | None:
    """A chunk's stored vector, or a cached one for built-in entries, which are not stored."""
    stored = entry.get("embedding")
    if stored:
        return json.loads(stored)
    return _embedding_cache.get(entry["text"])


def search_knowledge(db: Session, query: str, domain: str, limit: int = 3) -> list[dict[str, Any]]:
    """Evidence for a query, restricted to the domain before anything is scored.

    A keyword match or a semantic match above its threshold qualifies. Without the embedding
    model this is keyword search alone.
    """
    query_terms = set(_terms(query))
    if not query_terms:
        return []
    entries = [
        entry
        for entry in _built_in_entries(db, domain) + _document_entries(db, domain)
        # Content is re-checked at retrieval, so older or edited rows cannot inject instructions.
        if not detect_prompt_injection(entry["text"])
    ]
    missing = [entry["text"] for entry in entries if not entry.get("embedding") and entry["text"] not in _embedding_cache]
    query_vector = None
    embedded = embed_texts([query], "query")
    if embedded:
        query_vector = embedded[0]
        vectors = embed_texts(missing) if missing else []
        if vectors:
            if len(_embedding_cache) > 5000:
                _embedding_cache.clear()
            _embedding_cache.update(zip(missing, vectors))
    scored = []
    for entry in entries:
        counts = Counter(_terms(f"{entry['title']} {entry['text']}"))
        matched = query_terms & set(counts)
        confidence = len(matched) / len(query_terms)
        vector = _entry_vector(entry) if query_vector else None
        similarity = _cosine(query_vector, vector) if vector else 0.0
        if confidence >= settings.knowledge_min_confidence or (vector and similarity >= settings.knowledge_min_similarity):
            title_terms = set(_terms(entry["title"]))
            score = confidence + 0.5 * len(query_terms & title_terms) / len(query_terms) + 0.01 * sum(counts[t] for t in matched) + similarity
            scored.append({
                **{key: value for key, value in entry.items() if key != "embedding"},
                "confidence": round(max(confidence, similarity), 2),
                "score": round(score, 3),
            })
    scored.sort(key=lambda entry: (-entry["score"], entry["source"]))
    hits: list[dict[str, Any]] = []
    used = 0
    for entry in scored[:limit]:
        if used + len(entry["text"]) > _MAX_CONTEXT_CHARS:
            break
        used += len(entry["text"])
        hits.append(entry)
    return hits


def format_knowledge_answer(hits: list[dict[str, Any]]) -> str:
    """Quote approved evidence with its source; no LLM paraphrase means nothing can be invented."""
    lines = []
    for hit in hits:
        version = f" v{hit['version']}" if hit["version"] else ""
        stale = " (not reviewed recently)" if hit["stale"] else ""
        lines.append(f"{hit['text']} [Source: {hit['source']}{version}{stale}]")
    return "\n\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage approved knowledge documents.")
    parser.add_argument("action", choices=("ingest", "ingest-dir", "delete"))
    parser.add_argument("--source-key")
    parser.add_argument("--dir", help="For ingest-dir: every .md file, keyed by domain and file name")
    parser.add_argument("--title")
    parser.add_argument("--domain", default="all", choices=("all", "banking", "finance", "healthcare", "insurance", "retail"))
    parser.add_argument("--file")
    parser.add_argument("--approved-by")
    args = parser.parse_args()

    from ..db import SessionLocal
    from ..seed import ensure_schema

    ensure_schema()
    with SessionLocal() as db:
        if args.action == "ingest-dir":
            if not (args.dir and args.approved_by):
                parser.error("ingest-dir needs --dir and --approved-by")
            for path in sorted(Path(args.dir).glob("*.md")):
                content = path.read_text(encoding="utf-8")
                title = content.splitlines()[0].lstrip("# ").strip()
                print(ingest_document(db, f"{args.domain}/{path.stem}", title, content, args.domain, args.approved_by))
            return
        if not args.source_key:
            parser.error("--source-key is required")
        if args.action == "delete":
            print("deleted" if delete_document(db, args.source_key) else "not found")
            return
        if not (args.title and args.file and args.approved_by):
            parser.error("ingest requires --title, --file and --approved-by")
        print(ingest_document(
            db, args.source_key, args.title, Path(args.file).read_text(encoding="utf-8"), args.domain, args.approved_by
        ))


if __name__ == "__main__":
    main()
