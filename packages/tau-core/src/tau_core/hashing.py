import hashlib
import json
from typing import Any


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Deterministic hash of tool-call arguments, used to bind an approval to the exact call it was granted for."""
    canonical = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
