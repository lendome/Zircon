from __future__ import annotations

import hashlib
import json
import numpy as np
from pathlib import Path
from typing import Union


class LocalEmbedder:
    _instance = None
    _model = None

    def __new__(cls, *a, **kw):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_dir: str | Path | None = None):
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5",
            trust_remote_code=True,
        )
        self._dim = 768
        if cache_dir:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._cache_dir = None
        self._mem_cache: dict[str, np.ndarray] = {}

    def _cache_key(self, text: str, prefix: str) -> str:
        return hashlib.sha256(f"{prefix}:{text}".encode()).hexdigest()[:16]

    def embed(self, texts: list[str], prefix: str = "search_document:") -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        results = []
        uncached = []
        uncached_idx = []
        for i, t in enumerate(texts):
            key = self._cache_key(t, prefix)
            if key in self._mem_cache:
                results.append((i, self._mem_cache[key]))
                continue
            if self._cache_dir:
                npz = self._cache_dir / f"{key}.npz"
                if npz.exists():
                    arr = np.load(npz)["emb"]
                    self._mem_cache[key] = arr
                    results.append((i, arr))
                    continue
            uncached.append(f"{prefix}{t}")
            uncached_idx.append(i)

        if uncached:
            embeddings = self._model.encode(uncached, normalize_embeddings=True, show_progress_bar=False)
            for j, idx in enumerate(uncached_idx):
                arr = np.array(embeddings[j], dtype=np.float32)
                key = self._cache_key(texts[idx], prefix)
                self._mem_cache[key] = arr
                if self._cache_dir:
                    np.savez_compressed(self._cache_dir / f"{key}.npz", emb=arr)
                results.append((idx, arr))

        results.sort(key=lambda x: x[0])
        return np.stack([r[1] for r in results])

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text], prefix="search_query:")[0]

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self.embed(texts, prefix="search_document:")

    @staticmethod
    def similarity(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    def top_k(self, query: str, candidates: dict[str, str], k: int = 10) -> list[tuple[str, float]]:
        q_emb = self.embed_query(query)
        keys = list(candidates.keys())
        texts = [candidates[k] for k in keys]
        if not texts:
            return []
        doc_embs = self.embed_documents(texts)
        scores = np.dot(doc_embs, q_emb)
        top_indices = np.argsort(scores)[::-1][:k]
        return [(keys[i], float(scores[i])) for i in top_indices]
