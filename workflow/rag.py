"""
RAG over the CIS Kubernetes Benchmark PDF.

Chunks by recommendation section (N.N.N pattern).
Embeds with Ollama (nomic-embed-text).
Retrieves with cosine similarity.
Index is cached to disk keyed by PDF mtime.
"""

import os
import re
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf
import numpy as np
import ollama

from config import MIN_CONFIDENCE_RAG_QUERIES, OLLAMA_HOST, EMBED_MODEL
from logger import log
CACHE_DIR = Path(__file__).parent.parent / ".rag_cache"

_SECTION_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+(.+)")
_REF_RE = re.compile(r"https?://\S+")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RAGChunk:
    section: str        # e.g. "5.2.6"
    title: str          # e.g. "Ensure that root containers are not admitted"
    text: str           # full recommendation text
    page: int           # 1-based PDF page number
    references: list[str] = field(default_factory=list)


@dataclass
class RAGResult:
    chunk: RAGChunk
    score: float


@dataclass
class RAGIndex:
    chunks: list[RAGChunk]
    embeddings: np.ndarray   # shape (N, D)


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

def _extract_chunks(pdf_path: str) -> list[RAGChunk]:
    doc = fitz.open(pdf_path)
    chunks: list[RAGChunk] = []
    current: dict | None = None

    for page_num, page in enumerate(doc):
        text = page.get_text()
        lines = [l.strip() for l in text.splitlines()]

        for line in lines:
            if not line:
                continue
            m = _SECTION_RE.match(line)
            if m:
                if current:
                    chunks.append(_finalise(current))
                current = {
                    "section": m.group(1),
                    "title": m.group(2).rstrip("()").strip(),
                    "text": line,
                    "page": page_num + 1,
                    "references": [],
                }
            elif current:
                current["text"] += "\n" + line
                refs = _REF_RE.findall(line)
                current["references"].extend(refs)

    if current:
        chunks.append(_finalise(current))

    doc.close()
    return chunks


def _finalise(raw: dict) -> RAGChunk:
    return RAGChunk(
        section=raw["section"],
        title=raw["title"],
        text=raw["text"][:3000],   # cap to avoid huge embeddings
        page=raw["page"],
        references=list(set(raw["references"])),
    )


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _embed_batch(texts: list[str]) -> np.ndarray:
    cl = ollama.Client(host=OLLAMA_HOST)
    resp = cl.embed(model=EMBED_MODEL, input=texts)
    return np.array(resp.embeddings, dtype=np.float32)


def _embed_one(text: str) -> np.ndarray:
    return _embed_batch([text])[0]


def _cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query / (np.linalg.norm(query) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    normed = matrix / norms
    return normed @ q


# ---------------------------------------------------------------------------
# Index build / cache
# ---------------------------------------------------------------------------

def _cache_path(pdf_path: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    mtime = int(os.path.getmtime(pdf_path))
    stem = Path(pdf_path).stem
    return CACHE_DIR / f"{stem}_{mtime}.pkl"


def build_index(pdf_path: str) -> RAGIndex:
    cache = _cache_path(pdf_path)
    if cache.exists():
        log("RAG", f"loading cached index from {cache.name}")
        with open(cache, "rb") as f:
            return pickle.load(f)

    log("RAG", "building index from PDF (this runs once)…")
    chunks = _extract_chunks(pdf_path)
    log("RAG", f"extracted {len(chunks)} recommendation chunks")

    texts = [f"{c.section} {c.title}\n{c.text}" for c in chunks]
    all_embs: list[np.ndarray] = []
    n_batches = (len(texts) - 1) // 32 + 1
    for i in range(0, len(texts), 32):
        batch = texts[i : i + 32]
        log("RAG", f"embedding batch {i // 32 + 1}/{n_batches}")
        all_embs.append(_embed_batch(batch))

    embeddings = np.vstack(all_embs)
    index = RAGIndex(chunks=chunks, embeddings=embeddings)

    with open(cache, "wb") as f:
        pickle.dump(index, f)
    log("RAG", f"index cached to {cache.name}")
    return index


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query(index: RAGIndex, query_text: str, k: int = 3) -> list[RAGResult]:
    q_emb = _embed_one(query_text)
    scores = _cosine_similarity(q_emb, index.embeddings)
    top_indices = np.argsort(scores)[::-1][:k]
    return [
        RAGResult(chunk=index.chunks[i], score=float(scores[i]))
        for i in top_indices if float(scores[i]) >= MIN_CONFIDENCE_RAG_QUERIES
    ]
