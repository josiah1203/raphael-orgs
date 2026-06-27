"""Connection key generation and validation for workspace join/ingest."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from raphael_orgs.hblabs.db import PlatformStore

JOIN_PREFIX = "hbl_join_"
INGEST_PREFIX = "hbl_ingest_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def mask_key(prefix: str) -> str:
    return f"{prefix}…"


class ConnectionKeyService:
    def __init__(self, store: PlatformStore) -> None:
        self.store = store

    def create(
        self,
        org_id: str,
        *,
        key_type: str = "join",
        label: str | None = None,
    ) -> dict[str, Any]:
        prefix = JOIN_PREFIX if key_type == "join" else INGEST_PREFIX
        raw = f"{prefix}{secrets.token_urlsafe(24)}"
        key_id = f"ck_{secrets.token_hex(8)}"
        key_prefix = raw[: len(prefix) + 8]
        now = datetime.now(timezone.utc).isoformat()
        self.store.execute(
            """
            INSERT INTO connection_keys (id, org_id, key_type, key_prefix, key_hash, label, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key_id, org_id, key_type, key_prefix, hash_key(raw), label, now),
        )
        return {
            "id": key_id,
            "org_id": org_id,
            "key_type": key_type,
            "key_prefix": key_prefix,
            "key": raw,
            "label": label,
            "created_at": now,
        }

    def list_keys(self, org_id: str) -> list[dict[str, Any]]:
        rows = self.store.fetchall(
            """
            SELECT id, org_id, key_type, key_prefix, label, created_at, revoked_at, rotated_from
            FROM connection_keys
            WHERE org_id = ? AND revoked_at IS NULL
            ORDER BY created_at DESC
            """,
            (org_id,),
        )
        return [
            {
                "id": r["id"],
                "org_id": r["org_id"],
                "key_type": r["key_type"],
                "key_prefix": r["key_prefix"],
                "masked_key": mask_key(r["key_prefix"]),
                "label": r["label"] if r["label"] else None,
                "created_at": r["created_at"],
                "rotated_from": r["rotated_from"] if "rotated_from" in r.keys() else None,
            }
            for r in rows
        ]

    def resolve(self, raw_key: str) -> dict[str, Any] | None:
        row = self.store.fetchone(
            """
            SELECT id, org_id, key_type, key_prefix, label, created_at, revoked_at
            FROM connection_keys
            WHERE key_hash = ? AND revoked_at IS NULL
            """,
            (hash_key(raw_key.strip()),),
        )
        return dict(row) if row else None

    def revoke(self, org_id: str, key_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        row = self.store.fetchone(
            "SELECT id FROM connection_keys WHERE id = ? AND org_id = ? AND revoked_at IS NULL",
            (key_id, org_id),
        )
        if not row:
            return False
        self.store.execute(
            "UPDATE connection_keys SET revoked_at = ? WHERE id = ? AND org_id = ?",
            (now, key_id, org_id),
        )
        return True

    def rotate(self, org_id: str, key_id: str) -> dict[str, Any] | None:
        row = self.store.fetchone(
            """
            SELECT id, key_type, label FROM connection_keys
            WHERE id = ? AND org_id = ? AND revoked_at IS NULL
            """,
            (key_id, org_id),
        )
        if not row:
            return None
        self.revoke(org_id, key_id)
        label = row["label"] if "label" in row.keys() else None
        created = self.create(org_id, key_type=row["key_type"], label=label)
        self.store.execute(
            "UPDATE connection_keys SET rotated_from = ? WHERE id = ?",
            (key_id, created["id"]),
        )
        created["rotated_from"] = key_id
        return created
