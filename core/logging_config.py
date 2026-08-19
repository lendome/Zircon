from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .constants import ZIRCON_DIR

LOG_FORMAT = "%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    repo_path: str | Path,
    console: bool = False,
    level: str = "DEBUG",
) -> Path:
    log_dir = Path(repo_path) / ZIRCON_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"

    root = logging.getLogger("agent")
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    if not root.handlers:
        fh = RotatingFileHandler(
            str(log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(getattr(logging, level.upper(), logging.DEBUG))
        fh.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        root.addHandler(fh)

        if console:
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
            root.addHandler(ch)

    return log_file
