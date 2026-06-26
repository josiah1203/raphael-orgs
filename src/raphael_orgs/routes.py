"""Organizations API with memberships."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from raphael_orgs.hblabs.db import PlatformStore
from raphael_orgs.hblabs.rbac.service import OrgRBACService, OrgRole

router = APIRouter(tags=["orgs"])

_db = Path(os.environ.get("RAPHAEL_ORGS_DB", "/tmp/raphael-orgs.db"))
_store = PlatformStore(_db)
_rbac = OrgRBACService(_store)
_store.execute(
    """
    CREATE TABLE IF NOT EXISTS invites (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL,
        email TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """
)
_store.execute(
    "INSERT OR IGNORE INTO orgs (id, name, plan, region, created_at) VALUES (?, ?, 'free', 'us-east-1', ?)",
    ("org_default", "Default Organization", datetime.now(timezone.utc).timestamp()),
)


@router.get("")
def list_orgs() -> dict[str, list]:
    rows = _store.fetchall("SELECT id, name, plan, region FROM orgs ORDER BY created_at DESC")
    return {"orgs": [{"id": r["id"], "name": r["name"], "plan": r["plan"], "region": r["region"]} for r in rows]}


@router.post("")
def create_org(body: dict[str, Any]) -> dict[str, Any]:
    oid = body.get("id", f"org_{int(datetime.now(timezone.utc).timestamp())}")
    org = _rbac.create_org(oid, body["name"], plan=body.get("plan", "free"), region=body.get("region", "us-east-1"))
    return {"id": org.id, "name": org.name, "plan": org.plan, "region": org.region}


@router.post("/{org_id}/invites")
def create_invite(org_id: str, body: dict[str, Any]) -> dict[str, str]:
    iid = f"inv_{int(datetime.now(timezone.utc).timestamp())}"
    email = body["email"]
    _store.execute(
        "INSERT INTO invites (id, org_id, email, created_at) VALUES (?, ?, ?, ?)",
        (iid, org_id, email, datetime.now(timezone.utc).isoformat()),
    )

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
    rows = _rbac.list_members(org_id)
    return {"members": [{"user_id": r["user_id"], "role": r["role"]} for r in rows]}


@router.post("/{org_id}/members")
def add_member(org_id: str, body: dict[str, Any]) -> dict[str, str]:
    membership_id = body.get("id", f"mbr_{int(datetime.now(timezone.utc).timestamp())}")
    user_id = body["user_id"]
    role_value = body.get("role", "member")
    try:
        role = OrgRole(role_value)
    except ValueError as exc:
        raise HTTPException(400, detail="invalid_role") from exc
    member = _rbac.add_member(membership_id, org_id, user_id, role)
    return {"id": member.id, "org_id": member.org_id, "user_id": member.user_id, "role": member.role.value}
