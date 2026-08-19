from __future__ import annotations

import json
from pathlib import Path

ZIRCON_DIR = ".zircon-code"

SUBDIRS = (
    "agents",
    "commands",
    "context_dumps", # saved LLM context snapshots (--dump-context)
    "embeddings",
    "logs",
    "sessions",
    "swarm",         # swarm session and artifact tracking
    "tasks",         # background task status tracking
    "vectors",       # reserved for future vector databases / FAISS / Chroma / etc.
    "analytics",     # reserved for code analysis data, dependency graphs, call graphs
    "cache",         # reserved for misc caches (AST caches, file hashes, etc.)
    "profiles",      # temporary profiler output (cProfile, .cpuprofile, pprof)
)

IDENTITY_FILENAME = "identity.json"
_IDENTITY_TEMPLATE = {
    "framework": "zircon",
    "version": "1.0.0",
    "description": "Autonomous coding agent framework",
}


def ensure_zircon_dir(repo_path: str | Path) -> Path:
    root = Path(repo_path).resolve()
    zircon_dir = root / ZIRCON_DIR
    zircon_dir.mkdir(parents=True, exist_ok=True)

    for name in SUBDIRS:
        (zircon_dir / name).mkdir(parents=True, exist_ok=True)

    identity_path = zircon_dir / IDENTITY_FILENAME
    if not identity_path.exists():
        identity_path.write_text(
            json.dumps(_IDENTITY_TEMPLATE, indent=2) + "\n",
            encoding="utf-8",
        )

    return zircon_dir


def zircon_path(repo_path: str | Path, *subpath: str) -> Path:
    return Path(repo_path).resolve() / ZIRCON_DIR / "/".join(subpath)