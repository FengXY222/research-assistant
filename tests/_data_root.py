"""Set a process-local data root before persistence modules are imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_ROOT = tempfile.TemporaryDirectory(prefix="research-assistant-v11-tests-")
DATA_ROOT = Path(TEST_ROOT.name) / "data"
os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(DATA_ROOT)
