"""Build-time embedding computation with BAAI/bge-small-en-v1.5.

Conforms to Sprint 08 / ADR-12, FR-06, Task 3.2, Task 3.3:
- Pinned model: BAAI/bge-small-en-v1.5 (ONNX quantised, 384-dim).
- Batched inference for efficiency.
- Zero silent degradation / Fail-closed: Missing/unloadable model raises ModelLoadError
  and exits with code 78. Never substitutes a synthetic or heuristic vector.
- Single source of truth for EMBED_DIM = 384.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Iterable, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ADR-12: Single Source of Truth
MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
EMBED_DIM = 384
EX_CONFIG = 78  # Standard sysexits.h exit code for configuration/model failure

# Sprint 09 (Task 2.2): bge-small is an asymmetric retrieval model. Queries must
# be encoded with this instruction prefix; document chunks stay on the passage
# path. fastembed exposes this as ``TextEmbedding.query_embed``.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class ModelLoadError(RuntimeError):
    """Raised when the embedding model cannot be located, downloaded, or initialized."""
    pass


class EmbeddingEngine:
    """Manages dense vector generation using pinned ONNX BGE-small-en-v1.5."""

    def __init__(
        self,
        model_name: str = MODEL_ID,
        expected_dim: int = EMBED_DIM,
        threads: Optional[int] = None,
    ) -> None:
        self.model_name = model_name
        self.expected_dim = expected_dim
        self._model = None
        # Records which query-encoding path was used: "query_embed" or
        # "manual_prefix". Never "passage" — that would silently degrade recall.
        self.query_path: Optional[str] = None
        self._load_model(threads)

    def _load_model(self, threads: Optional[int] = None) -> None:
        """Attempt to load the embedding model; fail-closed on any error."""
        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self.model_name,
                threads=threads,
            )
        except Exception as e:
            logger.critical(
                "FAIL-CLOSED: Unable to initialize embedding model '%s': %s",
                self.model_name,
                e,
            )
            raise ModelLoadError(
                f"Embedding model '{self.model_name}' could not be initialized: {e}"
            ) from e

    def embed_texts(self, texts: Sequence[str], batch_size: int = 64) -> List[np.ndarray]:
        """Generate normalized 384-dim float32 embeddings for a sequence of texts."""
        if not texts:
            return []

        if self._model is None:
            raise ModelLoadError("Embedding model is not loaded.")

        try:
            # fastembed returns generator of np.ndarray
            embeddings_gen = self._model.embed(texts, batch_size=batch_size)
            results: List[np.ndarray] = []
            for vec in embeddings_gen:
                v = np.asarray(vec, dtype=np.float32)
                if v.shape[-1] != self.expected_dim:
                    raise ValueError(
                        f"Mismatched embedding dimension: expected {self.expected_dim}, got {v.shape[-1]}"
                    )
                results.append(v)
            return results
        except Exception as e:
            logger.critical("Embedding inference failure: %s", e)
            raise

    def embed_query(self, query: str) -> np.ndarray:
        """Encode a search query on the BGE *query* path (Sprint 09, Task 2.2).

        ``bge-small-en-v1.5`` is asymmetric: a query must carry the instruction
        prefix, while passages must not. This method therefore never falls back
        to the passage path.

        Two explicit paths are supported:

        1. ``query_embed`` — preferred; fastembed applies the model-specific
           query instruction itself.
        2. ``manual_prefix`` — only when the installed fastembed lacks
           ``query_embed``; the prefix is prepended before ``embed``.

        If neither path can be established, this raises ``ModelLoadError``
        (fail-closed) instead of silently encoding the query as a passage.
        """
        if not isinstance(query, str):
            raise TypeError("embed_query expects a string query.")
        if not query.strip():
            raise ValueError("embed_query requires a non-empty query.")
        if self._model is None:
            raise ModelLoadError("Embedding model is not loaded.")

        query_embed = getattr(self._model, "query_embed", None)
        use_query_embed = callable(query_embed) and self._query_embed_is_effective()
        if use_query_embed:
            try:
                vectors = list(query_embed(query))
            except Exception as e:
                logger.critical(
                    "FAIL-CLOSED: query_embed path unavailable for '%s': %s",
                    self.model_name,
                    e,
                )
                raise ModelLoadError(
                    f"Query-encoding path (query_embed) failed: {e}"
                ) from e
            path = "query_embed"
        else:
            # Either fastembed lacks `query_embed`, or it is a silent no-op for
            # this model. fastembed 0.9.1 + bge-small-en-v1.5 does NOT override
            # `query_embed`: it is a pass-through to embed() with NO instruction
            # prefix (round-2 finding DOM2-01). Apply the prefix manually.
            try:
                vectors = list(self._model.embed([QUERY_INSTRUCTION + query]))
            except Exception as e:
                logger.critical(
                    "FAIL-CLOSED: manual query-prefix path unusable for '%s': %s",
                    self.model_name,
                    e,
                )
                raise ModelLoadError(
                    f"Query-encoding path (manual prefix) failed: {e}"
                ) from e
            path = "manual_prefix"

        if not vectors:
            raise ModelLoadError("Query embedding produced no vector.")

        vector = np.asarray(vectors[0], dtype=np.float32)
        if vector.shape[-1] != self.expected_dim:
            raise ValueError(
                f"Mismatched query embedding dimension: expected {self.expected_dim}, "
                f"got {vector.shape[-1]}"
            )

        self.query_path = path
        logger.debug("Encoded query via %s path", path)
        return vector


    def _query_embed_is_effective(self) -> bool:
        """Empirically prove `query_embed` differs from the passage path.

        fastembed 0.9.1 does not override ``query_embed`` for
        ``bge-small-en-v1.5``; it is a silent pass-through to ``embed([...])``
        with no instruction prefix. Trusting its mere presence silently
        degraded recall (round-2 finding DOM2-01). The probe result is cached.
        """
        cached = getattr(self, "_query_embed_probe", None)
        if cached is not None and cached[0] is self._model:
            return cached[1]
        effective = False
        try:
            probe = "probe query"
            via_query = np.asarray(
                list(self._model.query_embed([probe]))[0], dtype=np.float32
            )
            via_passage = np.asarray(
                list(self._model.embed([probe]))[0], dtype=np.float32
            )
            # If the two are identical, query_embed applied no prefix at all.
            effective = not np.array_equal(via_query, via_passage)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("query_embed probe failed (%s); using manual prefix", e)
            effective = False
        if not effective:
            logger.warning(
                "query_embed is a no-op for '%s' (fastembed %s); "
                "using the manual instruction prefix instead.",
                self.model_name,
                getattr(__import__("fastembed"), "__version__", "unknown"),
            )
        self._query_embed_probe = (self._model, effective)
        return effective


def vector_to_blob(vector: np.ndarray) -> bytes:
    """Serialize float32 numpy vector into byte string for SQLite storage."""
    v = np.asarray(vector, dtype=np.float32)
    return v.tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    """Deserialize byte string from SQLite back into float32 numpy vector."""
    return np.frombuffer(blob, dtype=np.float32)


def get_embedding_engine_or_halt() -> EmbeddingEngine:
    """Get embedding engine, or exit with status code 78 if model fails (fail-closed)."""
    try:
        return EmbeddingEngine()
    except ModelLoadError as e:
        sys.stderr.write(f"\n[FATAL] Model Load Failure (exit {EX_CONFIG}): {e}\n")
        sys.exit(EX_CONFIG)

