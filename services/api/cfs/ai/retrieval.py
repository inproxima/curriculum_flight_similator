"""Hybrid retrieval over the application's own corpus (same evidence for every provider).

- Scope filters are applied in SQL *before* anything can reach a model: organization, documents assigned to
  the curriculum version (curriculum_sources), and each document's provider policy.
- PostgreSQL full-text ranking + pgvector cosine similarity, fused with reciprocal-rank fusion.
- Only vectors from the configured embedding model/dimension are searched (no mixed spaces).
- Without embeddings the mode degrades to full-text only, and results say so.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.ai.routes import EMBEDDING_DIM, routes
from cfs.models import ChunkEmbedding, CurriculumSource, Document, DocumentChunk, DocumentVersion

RRF_K = 60


def embed_document(db: Session, org_id: uuid.UUID, dv_id: uuid.UUID, job_id: uuid.UUID | None = None) -> dict:
    """Embed every chunk of a document version that lacks an embedding for the current model. Idempotent."""
    from cfs.ai import gateway

    model = routes()["embed"].model
    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.document_version_id == dv_id).order_by(DocumentChunk.ordinal)
    ).all()
    have = set(
        db.scalars(
            select(ChunkEmbedding.chunk_id).where(
                ChunkEmbedding.chunk_id.in_([c.id for c in chunks]),
                ChunkEmbedding.embedding_model == model,
                ChunkEmbedding.dimension == EMBEDDING_DIM,
            )
        )
    )
    todo = [c for c in chunks if c.id not in have]
    for i in range(0, len(todo), 64):
        batch = todo[i : i + 64]
        texts = [f"{c.section_title or ''}\n{c.text}"[:8000] for c in batch]
        vecs, model = gateway.embed(db, org_id, texts, doc_version_ids=[dv_id], job_id=job_id)
        for c, t, v in zip(batch, texts, vecs, strict=True):
            db.add(
                ChunkEmbedding(
                    chunk_id=c.id,
                    embedding_model=model,
                    dimension=EMBEDDING_DIM,
                    input_hash=hashlib.sha256(t.encode()).hexdigest(),
                    embedding=v,
                )
            )
        db.commit()
    return {"embedded": len(todo), "already": len(have), "model": model}


def search(
    db: Session,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    query: str,
    *,
    limit: int = 8,
    provider: str | None = None,
    allow_vector: bool = True,
) -> dict[str, Any]:
    """Return ranked chunks from documents assigned to `version_id`, optionally restricted to documents whose
    AI policy permits `provider` (use this whenever results will be sent to that provider)."""
    base = (
        select(DocumentChunk.id)
        .join(CurriculumSource, CurriculumSource.document_version_id == DocumentChunk.document_version_id)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .where(CurriculumSource.curriculum_version_id == version_id, DocumentChunk.organization_id == org_id)
    )
    if provider:
        base = base.where((DocumentVersion.ai_providers.is_(None)) | (DocumentVersion.ai_providers.op("?")(provider)))
    tsq = func.websearch_to_tsquery("english", query)
    fts = db.execute(
        base.add_columns(func.ts_rank(DocumentChunk.tsv, tsq).label("r"))
        .where(DocumentChunk.tsv.op("@@")(tsq))
        .order_by(func.ts_rank(DocumentChunk.tsv, tsq).desc())
        .limit(limit * 3)
    ).all()
    ranks: dict[uuid.UUID, float] = {}
    for i, (cid, _r) in enumerate(fts):
        ranks[cid] = ranks.get(cid, 0) + 1 / (RRF_K + i + 1)
    mode = "full_text_only"
    if allow_vector:
        try:
            from cfs.ai import gateway

            vec, model = gateway.embed(db, org_id, [query])
            dist = ChunkEmbedding.embedding.cosine_distance(vec[0])
            vrows = db.execute(
                base.add_columns(dist.label("d"))
                .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
                .where(ChunkEmbedding.embedding_model == model, ChunkEmbedding.dimension == EMBEDDING_DIM)
                .order_by(dist)
                .limit(limit * 3)
            ).all()
            if vrows:
                mode = "hybrid"
            for i, (cid, _d) in enumerate(vrows):
                ranks[cid] = ranks.get(cid, 0) + 1 / (RRF_K + i + 1)
        except Exception:  # noqa: BLE001 — degrade to full-text; mode reports it
            mode = "full_text_only"
    top = sorted(ranks.items(), key=lambda kv: (-kv[1], str(kv[0])))[:limit]
    rows = {
        c.id: (c, dv, d)
        for c, dv, d in db.execute(
            select(DocumentChunk, DocumentVersion, Document)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentChunk.id.in_([t[0] for t in top]))
        )
    }
    results = []
    for cid, score in top:
        c, dv, d = rows[cid]
        results.append(
            {
                "chunk_id": str(c.id),
                "document_version_id": str(dv.id),
                "document_title": d.title,
                "document_type": d.document_type.value,
                "academic_year": dv.academic_year,
                "publication_date": str(dv.publication_date) if dv.publication_date else None,
                "pages": [c.page_start, c.page_end],
                "section": c.section_title,
                "text": c.text[:2500],
                "score": round(score, 5),
            }
        )
    return {"mode": mode, "results": results}
