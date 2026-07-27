from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class LocalHashEmbeddingProvider:
    """Deterministic multilingual-safe fallback for offline tests and bootstrapping."""

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dimension), dtype="float32")
        for row, text in enumerate(texts):
            for token in text.lower().split():
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "little") % self.dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vectors[row, idx] += sign
            norm = np.linalg.norm(vectors[row])
            if norm > 0:
                vectors[row] /= norm
        return vectors


@dataclass
class MemoryItem:
    id: str
    text: str
    source: str
    created_at: str
    importance: float
    metadata: dict


class FaissMemoryStore:
    def __init__(self, faiss_path: Path, metadata_path: Path, embeddings: EmbeddingProvider, max_items: int = 5000) -> None:
        self.faiss_path = faiss_path
        self.metadata_path = metadata_path
        self.embeddings = embeddings
        self.max_items = max_items
        self.faiss_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[MemoryItem] = []
        self._vectors = np.empty((0, embeddings.dimension), dtype="float32")
        self._faiss = self._load_faiss()
        self._index = None
        self._load()

    def _load_faiss(self):
        try:
            import faiss  # type: ignore

            return faiss
        except Exception:
            return None

    def _load(self) -> None:
        if self.metadata_path.exists():
            with self.metadata_path.open("r", encoding="utf-8") as handle:
                self.items = [MemoryItem(**json.loads(line)) for line in handle if line.strip()]
        if self._faiss and self.faiss_path.exists():
            self._index = self._faiss.read_index(str(self.faiss_path))
        elif self.items:
            self._vectors = self.embeddings.embed([item.text for item in self.items])
        elif self._faiss:
            self._index = self._faiss.IndexFlatIP(self.embeddings.dimension)

    def _persist(self) -> None:
        with self.metadata_path.open("w", encoding="utf-8") as handle:
            for item in self.items[-self.max_items :]:
                handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        if self._faiss and self._index is not None:
            self._faiss.write_index(self._index, str(self.faiss_path))

    def add(self, text: str, source: str, importance: float = 0.5, metadata: dict | None = None) -> str:
        item_id = f"mem_{hashlib.blake2b((text + source).encode('utf-8'), digest_size=10).hexdigest()}"
        item = MemoryItem(item_id, text, source, datetime.now(UTC).isoformat(), float(importance), metadata or {})
        vector = self.embeddings.embed([text]).astype("float32")
        self.items.append(item)
        self.items = self.items[-self.max_items :]
        if self._faiss:
            if self._index is None:
                self._index = self._faiss.IndexFlatIP(self.embeddings.dimension)
            self._index.add(vector)
        else:
            self._vectors = np.vstack([self._vectors, vector])[-self.max_items :]
        self._persist()
        return item_id

    def search(self, query: str, top_k: int = 5, safe_sources: set[str] | None = None) -> list[MemoryItem]:
        if not self.items:
            return []
        query_vector = self.embeddings.embed([query]).astype("float32")
        if self._faiss and self._index is not None and self._index.ntotal:
            scores, indexes = self._index.search(query_vector, min(top_k * 3, self._index.ntotal))
            candidates = [(int(i), float(s)) for i, s in zip(indexes[0], scores[0]) if i >= 0]
        else:
            scores = (self._vectors @ query_vector[0]).tolist()
            candidates = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)[: top_k * 3]
        results: list[MemoryItem] = []
        for idx, _score in candidates:
            if idx >= len(self.items):
                continue
            item = self.items[idx]
            if safe_sources and item.source not in safe_sources:
                continue
            results.append(item)
            if len(results) >= top_k:
                break
        return results

    def forget(self, memory_id: str) -> bool:
        before = len(self.items)
        self.items = [item for item in self.items if item.id != memory_id]
        changed = len(self.items) != before
        if changed:
            self._vectors = self.embeddings.embed([item.text for item in self.items]) if self.items else np.empty((0, self.embeddings.dimension), dtype="float32")
            if self._faiss:
                self._index = self._faiss.IndexFlatIP(self.embeddings.dimension)
                if self.items:
                    self._index.add(self._vectors.astype("float32"))
            self._persist()
        return changed

    def erase_all(self) -> None:
        self.items = []
        self._vectors = np.empty((0, self.embeddings.dimension), dtype="float32")
        if self._faiss:
            self._index = self._faiss.IndexFlatIP(self.embeddings.dimension)
        self._persist()
