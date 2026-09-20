import logging
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from security_mcp_server import crypto_store

logger = logging.getLogger(__name__)

def _default_data() -> dict:
    # A function, not a module-level dict constant: `dict(_SHARED)` would only shallow-copy,
    # leaving every fresh store's "intrusion_log" pointing at the SAME list object as every other
    # store that ever hit this default - appends would leak across instances (and across tests).
    return {
        "lockdown_active": False,
        "lockdown_reason": None,
        "lockdown_entered_at": None,
        "intrusion_log": [],
    }


class SecurityStore:
    """Persistent security state store: lockdown status, intrusion log, approval tokens.

    Encrypted at rest (AES-256-GCM via crypto_store) when TAU_MASTER_KEY is set; plaintext,
    unchanged, if unset. `_load`/`_save` are lock-guarded and `_save` writes atomically
    (temp file + os.replace) - concurrent FastMCP tool calls (enter_lockdown/log_incident/
    exit_lockdown are registered sync, so they run on FastMCP's thread pool) would otherwise be
    able to race on load-mutate-save.
    """

    def __init__(self, db_path: str = "./data/security.json"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._key = crypto_store.resolve_key(self.db_path)
        self._data = self._load()

    def _load(self) -> dict:
        # Deliberately no except here, matching ProfileStore's pattern rather than
        # PendingActionQueue's fail-open one: silently degrading to default state on a decrypt
        # failure would mean a wrong/missing TAU_MASTER_KEY silently clears lockdown_active back
        # to False on restart - a dangerous failure mode specifically for a security store. A
        # bad key or corrupted file must be a loud startup failure here, not a quiet reset.
        if not self.db_path.exists():
            return _default_data()
        return crypto_store.read_json(self.db_path, self._key)

    def _save_locked(self) -> None:
        """Caller must hold self._lock."""
        try:
            payload = crypto_store.write_json_bytes(self._data, self._key)
            fd, tmp_name = tempfile.mkstemp(dir=str(self.db_path.parent), prefix=".security-")
            try:
                with open(fd, "wb") as f:
                    f.write(payload)
                Path(tmp_name).replace(self.db_path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError:
            logger.exception("failed to persist %s", self.db_path)

    def enter_lockdown(self, reason: str) -> dict:
        """Enter lockdown mode. Disables all destructive operations."""
        with self._lock:
            self._data["lockdown_active"] = True
            self._data["lockdown_reason"] = reason
            self._data["lockdown_entered_at"] = datetime.utcnow().isoformat()
            self._save_locked()
            return {
                "status": "lockdown_entered",
                "reason": reason,
                "entered_at": self._data["lockdown_entered_at"],
            }

    def exit_lockdown(self) -> dict:
        """Exit lockdown mode. Requires approval token (checked by caller)."""
        with self._lock:
            self._data["lockdown_active"] = False
            self._data["lockdown_reason"] = None
            self._data["lockdown_entered_at"] = None
            self._save_locked()
            return {"status": "lockdown_exited"}

    def get_intrusion_status(self) -> dict:
        """Get current security status."""
        with self._lock:
            return {
                "lockdown_active": self._data["lockdown_active"],
                "lockdown_reason": self._data["lockdown_reason"],
                "lockdown_entered_at": self._data["lockdown_entered_at"],
                "intrusion_count": len(self._data["intrusion_log"]),
            }

    def log_incident(self, incident_type: str, description: str) -> dict:
        """Log a security incident."""
        with self._lock:
            incident = {
                "type": incident_type,
                "description": description,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self._data["intrusion_log"].append(incident)
            self._save_locked()
            return {"logged": True, "incident": incident}

    def is_lockdown_active(self) -> bool:
        """Check if lockdown is currently active."""
        with self._lock:
            return self._data["lockdown_active"]
