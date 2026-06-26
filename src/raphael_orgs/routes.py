"""Organizations API with memberships."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["orgs"])

_db = Path(os.environ.get("RAPHAEL_ORGS_DB", "/tmp/raphael-orgs.db"))
_conn = sqlite3.connect(_db, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS orgs (id TEXT PRIMARY KEY, name TEXT NOT NULL, verified INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS memberships (org_id TEXT, user_id TEXT, role TEXT, PRIMARY KEY (org_id, user_id));
    CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, org_id TEXT, email TEXT, created_at TEXT);
    """
)
_conn.execute("INSERT OR IGNORE INTO orgs (id, name) VALUES ('org_default', 'Default Organization')")
_conn.commit()


@router.get("")
def list_orgs() -> dict[str, list]:
    rows = _conn.execute("SELECT id, name, verified FROM orgs").fetchall()
    return {"orgs": [{"id": r["id"], "name": r["name"], "verified": bool(r["verified"])} for r in rows]}


@router.post("")
def create_org(body: dict[str, Any]) -> dict[str, Any]:
    oid = body.get("id", f"org_{int(datetime.now(timezone.utc).timestamp())}")
    _conn.execute("INSERT INTO orgs (id, name) VALUES (?, ?)", (oid, body["name"]))
    _conn.commit()
    return {"id": oid, "name": body["name"], "verified": False}


@router.post("/{org_id}/invites")
def create_invite(org_id: str, body: dict[str, Any]) -> dict[str, str]:
    iid = f"inv_{int(datetime.now(timezone.utc).timestamp())}"
    email = body["email"]
    _conn.execute(
        "INSERT INTO invites (id, org_id, email, created_at) VALUES (?, ?, ?, ?)",
        (iid, org_id, email, datetime.now(timezone.utc).isoformat()),
    )
    _conn.commit()
    import httpx

    notif = os.environ.get("RAPHAEL_NOTIFICATIONS_URL", "http://127.0.0.1:8090")
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(
                f"{notif}/v1/notifications/events",
                json={"type": "raphael.orgs.invite.created", "data": {"org_id": org_id, "email": email, "user_id": email}},
            )
    except httpx.RequestError:
        pass
    return {"status": "invite_sent", "org_id": org_id, "email": email, "invite_id": iid}


@router.get("/{org_id}/members")
def list_members(org_id: str) -> dict[str, list]:
    rows = _conn.execute("SELECT user_id, role FROM memberships WHERE org_id = ?", (org_id,)).fetchall()
    return {"members": [{"user_id": r["user_id"], "role": r["role"]} for r in rows]}
