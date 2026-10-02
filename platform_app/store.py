import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4


class ConflictError(Exception):
    pass


class ActivationStore:
    """Durable idempotency ledger; uncertain outcomes are never repeated automatically."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS activations "
            "(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT, status INTEGER)"
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(activations)")}
        for name in ("activation_id", "payload", "state"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE activations ADD COLUMN {name} TEXT")
        # Historical rows have no saved request. Preserve them without inventing evidence.
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS effects "
            "(activation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS reconciliation_attempts "
            "(id INTEGER PRIMARY KEY, key TEXT NOT NULL, evidence TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
        )

    def claim(self, key: str, payload: dict) -> tuple[int, dict] | None:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        try:
            self.db.execute(
                "INSERT INTO activations(key, fingerprint, activation_id, payload, state) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (key, fingerprint, str(uuid4()), json.dumps(payload)),
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
            "UPDATE activations SET result = ?, status = ?, state = ? "
            "WHERE key = ? AND (state IS NULL OR state != 'active')",
            (json.dumps(result), status, result["status"], key),
        )

    def get(self, key: str) -> dict | None:
        row = self.db.execute(
            "SELECT activation_id, payload, state, result FROM activations WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row[3]) if row[3] else None
        latest = self.db.execute(
            "SELECT evidence FROM reconciliation_attempts WHERE key = ? ORDER BY id DESC LIMIT 1",
            (key,),
        ).fetchone()
        return {
            "activation_id": row[0] or (result or {}).get("activation_id"),
            "payload": json.loads(row[1]) if row[1] else None,
            "status": row[2] or (result or {}).get("status", "requires_reconciliation"),
            "result": result,
            "reconciliation": json.loads(latest[0]) if latest else None,
        }

    def effect(self, activation_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT result FROM effects WHERE activation_id = ?", (activation_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def apply_effect(self, activation_id: str, payload: dict, status: str) -> tuple[dict, bool]:
        # The synthetic business effect and its receipt are the same atomic durable row.
        business = {"plan": payload["plan"], "region": payload["region"]}
        fingerprint = hashlib.sha256(json.dumps(business, sort_keys=True).encode()).hexdigest()
        result = {"activation_id": activation_id, "status": status, **business, "effect_count": 1}
        with self.lock:
            try:
                self.db.execute(
                    "INSERT INTO effects VALUES (?, ?, ?)",
                    (activation_id, fingerprint, json.dumps(result)),
                )
            except sqlite3.IntegrityError:
                row = self.db.execute(
                    "SELECT fingerprint, result FROM effects WHERE activation_id = ?",
                    (activation_id,),
                ).fetchone()
                if row[0] != fingerprint:
                    raise ConflictError(
                        "Activation ID reused with a different business request"
                    ) from None
                return json.loads(row[1]), True
        return result, False

    def reconcile(self, key: str, result: dict | None, evidence: dict):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if result is not None:
                    self.complete(key, 201, result)
                self.db.execute(
                    "INSERT INTO reconciliation_attempts(key, evidence) VALUES (?, ?)",
                    (key, json.dumps(evidence)),
                )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        self.db.close()
