"""KnowledgeGraphMemory must not leak file descriptors while indexing.

Regression test for errno 24 (Too many open files): sqlite3's context
manager commits but never closes, so a connection per write leaked fds
until repo indexing crashed the whole TUI.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.kg_memory import KnowledgeGraphMemory


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


@unittest.skipIf(sys.platform == "win32", "/dev/fd is Unix-only")
class TestKnowledgeGraphFdLeak(unittest.TestCase):
    def test_ingesting_many_files_does_not_leak_fds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kg = KnowledgeGraphMemory(tmp)
            before = _open_fd_count()

            for i in range(200):
                kg.ingest_file_structure(
                    f"src/module_{i}.py",
                    [
                        {"name": f"func_{j}", "kind": "function", "line": j}
                        for j in range(5)
                    ],
                )
                kg.add_node(f"concept:{i}", "concept", {"i": i})
                kg.add_edge(f"file:src/module_{i}.py", f"concept:{i}", "relates_to")

            after = _open_fd_count()

        # Allow a little slack (WAL sidecars etc.), but 200 files must not
        # hold hundreds of connections open.
        self.assertLessEqual(after - before, 5)


if __name__ == "__main__":
    unittest.main()
