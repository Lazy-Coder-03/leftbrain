import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# Compute isolation puts each call in a worker process (#28 §1 step 3). The suite drives the
# wrappers thousands of times, so it runs in-process by default; `tests/test_runner.py`
# turns it on and exercises the real pool, which is where the guarantee is tested.
os.environ.setdefault("LEFTBRAIN_COMPUTE_ISOLATION", "0")
