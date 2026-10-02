import hashlib
import json
import sqlite3
from pathlib import Path


class ConflictError(Exception):
    pass


class ActivationStore:
    """Durable idempotency ledger; uncertain outcomes are never repeated automatically."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS activations "
            "(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT, status INTEGER)"
        )

    def claim(self, key: str, payload: dict) -> tuple[int, dict] | None:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        try:
            self.db.execute(
                "INSERT INTO activations(key, fingerprint) VALUES (?, ?)", (key, fingerprint)
            )
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT fingerprint, result, status FROM activations WHERE key = ?", (key,)
            ).fetchone()
            if row[0] != fingerprint:
                raise ConflictError("Idempotency key reused with a different request") from None
            if row[1] is None:
                raise ConflictError(
                    "Activation pending reconciliation; automatic replay blocked"
                ) from None
            return row[2], json.loads(row[1])
        return None

    def complete(self, key: str, status: int, result: dict):
        self.db.execute(
            "UPDATE activations SET result = ?, status = ? WHERE key = ?",
            (json.dumps(result), status, key),
        )

    def close(self):
        self.db.close()
