#!/usr/bin/env python3
"""Download a pre-quantized ONNX cross-encoder reranker.

Pins a specific Hugging Face repo (default: Xenova/bge-reranker-base) and
attempts to fetch the smallest supported ONNX variant plus tokenizer.json.
Verifies the total artifact size against the configured cap. If no available
variant fits under the cap, the script exits non-zero with a clear message so
the build fails closed.
"""

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Variants ordered by ascending observed size for Xenova/bge-reranker-base.
ONNX_VARIANTS = [
    "onnx/model_uint8.onnx",
    "onnx/model_int8.onnx",
    "onnx/model_quantized.onnx",
    "onnx/model_q4f16.onnx",
    "onnx/model_fp16.onnx",
    "onnx/model_q4.onnx",
    "onnx/model_bnb4.onnx",
    "onnx/model.onnx",
]


def _dir_size_mb(path: Path) -> float:
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / (1024 * 1024)


def download(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    output = Path(args.output)
    if output.exists():
        logger.info("Clearing existing output dir: %s", output)
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download, list_repo_tree
    except ImportError as exc:
        logger.error("huggingface_hub is not installed: %s", exc)
        return 1

    # Discover which variants actually exist in the repo and their sizes.
    available = {}
    for entry in list_repo_tree(args.model, recursive=True):
        if hasattr(entry, "path") and entry.path in ONNX_VARIANTS:
            available[entry.path] = entry.size / (1024 * 1024)

    if not available:
        logger.error("No supported ONNX variants found in %s", args.model)
        return 1

    smallest = min(available.items(), key=lambda kv: kv[1])
    logger.info(
        "Smallest available ONNX variant is %s (%.1f MB)",
        smallest[0],
        smallest[1],
    )

    # tokenizer.json is mandatory.
    tokenizer_src = "tokenizer.json"
    try:
        tokenizer_file = hf_hub_download(
            repo_id=args.model,
            filename=tokenizer_src,
            local_dir=str(output),
            local_dir_use_symlinks=False,
        )
    except Exception as exc:
        logger.error("Failed to download tokenizer.json: %s", exc)
        return 1

    try:
        model_file = hf_hub_download(
            repo_id=args.model,
            filename=smallest[0],
            local_dir=str(output),
            local_dir_use_symlinks=False,
        )
    except Exception as exc:
        logger.error("Failed to download %s: %s", smallest[0], exc)
        return 1

    # Normalize layout: both files must live directly under output.
    model_path = output / "model.onnx"
    tokenizer_path = output / "tokenizer.json"
    downloaded_model = Path(model_file)
    if downloaded_model.name != "model.onnx":
        shutil.move(str(downloaded_model), str(model_path))
    if Path(tokenizer_file) != tokenizer_path:
        shutil.move(str(tokenizer_file), str(tokenizer_path))

    # Remove any other downloaded artifacts so the size check is precise.
    for child in output.iterdir():
        if child.is_dir() and child.name != output.name:
            shutil.rmtree(child)
        elif child.is_file() and child.name not in {
            "model.onnx",
            "tokenizer.json",
        }:
            child.unlink()

    size_mb = _dir_size_mb(output)
    manifest = {
        "model": args.model,
        "artifact_mb": round(size_mb, 4),
        "max_mb": args.max_mb,
        "model_onnx": "model.onnx",
        "tokenizer_json": "tokenizer.json",
        "downloaded_variant": smallest[0],
    }
    with open(output / "size.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if size_mb > args.max_mb:
        logger.error(
            "Artifact size %.1f MB exceeds %d MB cap. Build rejected; "
            "runtime will fall back to hybrid fusion without reranking.",
            size_mb,
            args.max_mb,
        )
        return 1

    logger.info(
        "Reranker ready at %s (%.1f MB). Manifest written to size.json.",
        output,
        size_mb,
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="Xenova/bge-reranker-base",
        help="Hugging Face repo id for the ONNX reranker",
    )
    parser.add_argument(
        "--output",
        default="crawl/models/reranker",
        help="Output directory for the model artifacts",
    )
    parser.add_argument(
        "--max-mb",
        type=int,
        default=150,
        help="Maximum total artifact size in MB",
    )
    args = parser.parse_args()
    sys.exit(download(args))


if __name__ == "__main__":
    main()
