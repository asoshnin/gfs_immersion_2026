"""Small CPU cross-encoder reranker (Sprint 18).

Uses ONNX Runtime directly and the lightweight ``tokenizers`` library.
The model is loaded lazily and fails closed: if the artifact is missing,
oversized, or corrupted the class reports ``available == False`` and the
retrieval path continues without reranking.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

try:
    import onnxruntime as ort
except Exception as _ort_exc:  # pragma: no cover - runtime dep guard
    ort = None  # type: ignore

try:
    from tokenizers import Tokenizer
except Exception as _tok_exc:  # pragma: no cover - runtime dep guard
    Tokenizer = None  # type: ignore

logger = logging.getLogger(__name__)


class CPUReranker:
    """ONNX cross-encoder reranker with a strict size cap and lazy load."""

    MODEL_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"  # pre-quantized int8 ONNX cross-encoder (22.6 MB)
    RERANK_MAX_K = 20
    PASSAGE_TRUNC_TOKENS = 256
    MAX_LENGTH = 512
    SIZE_CAP_MB = 150

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = (
            model_dir or Path(__file__).parent / "models" / "reranker"
        )
        self._session: Optional["ort.InferenceSession"] = None  # type: ignore
        self._tokenizer: Optional[Tokenizer] = None  # type: ignore
        self._error: Optional[Exception] = None

    @property
    def available(self) -> bool:
        return self._load() is not None

    @property
    def unavailable(self) -> bool:
        return not self.available

    def _dir_size_mb(self) -> float:
        total = 0
        if self.model_dir.exists():
            for path in self.model_dir.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size
        return total / (1024 * 1024)

    def _load(self):
        if self._session is not None:
            return self._session
        if self._error is not None:
            return None
        if ort is None or Tokenizer is None:
            self._error = RuntimeError("reranker dependencies missing")
            return None

        try:
            size_mb = self._dir_size_mb()
            if size_mb > self.SIZE_CAP_MB:
                raise RuntimeError(
                    f"reranker model {size_mb:.1f} MB exceeds "
                    f"{self.SIZE_CAP_MB} MB cap"
                )

            tokenizer_path = self.model_dir / "tokenizer.json"
            model_path = self.model_dir / "model.onnx"
            if not tokenizer_path.exists() or not model_path.exists():
                raise FileNotFoundError(
                    f"reranker artifact missing at {self.model_dir}"
                )

            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = tokenizer
            self._session = session
            logger.info(
                "Loaded CPU reranker from %s (%.1f MB)",
                self.model_dir,
                size_mb,
            )
            return session
        except Exception as exc:
            self._error = exc
            logger.warning(
                "CPU reranker unavailable; serving without reranking: %s",
                exc,
            )
            return None

    def _truncate_passage(self, text: str) -> str:
        tokenizer = self._tokenizer
        if tokenizer is None:
            return text[: self.PASSAGE_TRUNC_TOKENS * 4]
        # Truncate the passage to ~PASSAGE_TRUNC_TOKENS before pairing it with
        # the query. Decode back to a string so the pair tokenizer sees the
        # same token budget we intend.
        tokenizer.enable_truncation(self.PASSAGE_TRUNC_TOKENS)
        try:
            encoding = tokenizer.encode(text)
            return tokenizer.decode(encoding.ids, skip_special_tokens=True)
        finally:
            tokenizer.no_truncation()

    def score(self, query: str, passages: List[str]) -> List[float]:
        """Return a relevance score for each passage against the query."""
        session = self._load()
        if session is None or not passages:
            return [0.0] * len(passages)

        tokenizer = self._tokenizer
        assert tokenizer is not None
        tokenizer.enable_truncation(self.MAX_LENGTH)
        try:
            scores: List[float] = []
            for passage in passages:
                truncated = self._truncate_passage(passage)
                encoding = tokenizer.encode(query, truncated)
                input_ids = np.array([encoding.ids], dtype=np.int64)
                attention_mask = np.array(
                    [encoding.attention_mask], dtype=np.int64
                )
                type_ids = encoding.type_ids
                inputs = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                }
                if type_ids is not None:
                    inputs["token_type_ids"] = np.array(
                        [type_ids], dtype=np.int64
                    )

                # Feed only the inputs the model actually declares; ignore
                # optional extras so different ONNX layouts do not break.
                feed = {}
                for meta in session.get_inputs():
                    if meta.name in inputs:
                        feed[meta.name] = inputs[meta.name]
                    else:
                        raise RuntimeError(
                            f"reranker expects input '{meta.name}' "
                            f"not supported by tokenizer"
                        )

                outputs = session.run(None, feed)
                logit = float(np.asarray(outputs[0]).reshape(-1)[0])
                # Cross-encoder relevance score: sigmoid over the logit.
                scores.append(float(1.0 / (1.0 + np.exp(-logit))))
            return scores
        finally:
            tokenizer.no_truncation()
