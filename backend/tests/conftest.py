"""Keep tests independent of a developer's environment.

Settings read .env from the working directory at import. Running from an empty directory means the
suite behaves the same locally and in CI, and can never reach a real database or warehouse.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(tempfile.mkdtemp(prefix="ryze-tests-"))
# Keyword search unless a test supplies embeddings, so nothing calls a local model.
os.environ["KNOWLEDGE_MIN_SIMILARITY"] = "0"
