import os
import sys
from pathlib import Path

# Add src directory to path so tests can import the module
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

# Keep the shared module-level _ui_state in-memory for tests (Phase 14): server.py defaults the
# transcript to a durable on-disk store, but the tests mutate the module singleton and would
# otherwise write - and reload stale data across runs - into ./data/transcript.json. Set here (at
# conftest import, before any test module imports server) so the singleton is built with no path.
# Persistence itself is covered directly against TranscriptionState(store_path=...) in
# test_ui_state.py, which is the honest place to test it.
os.environ["TAU_TRANSCRIPT_STORE_PATH"] = ""
