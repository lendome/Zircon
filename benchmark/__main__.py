import sys
from pathlib import Path

_agent_dir = str(Path(__file__).parent.parent)
_parent_dir = str(Path(_agent_dir).parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from zirconAgent.benchmark.run_benchmark import main

if __name__ == "__main__":
    main()
