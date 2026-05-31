import os, sys
os.environ.setdefault("TIMING_DATA_ROOT", "warehouse/timing")

from pathlib import Path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
from bollydog.cli import main

if __name__ == "__main__":
    main()
