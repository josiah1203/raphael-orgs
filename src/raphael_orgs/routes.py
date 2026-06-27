"""Organizations API with memberships."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException

from raphael_orgs.connection_keys import ConnectionKeyService
from raphael_orgs.hblabs.db import PlatformStore
from raphael_orgs.hblabs.rbac.service import OrgRBACService, OrgRole

router = APIRouter(tags=["orgs"])

_db = Path(os.environ.get("RAPHAEL_ORGS_DB", "/tmp/raphael-orgs.db"))
_store = PlatformStore(_db)
_rbac = OrgRBACService(_store)
_keys = ConnectionKeyService(_store)

if not os.environ.get("RAPHAEL_DATABASE_URL"):
    _store.execute(
        """
        CREATE TABLE IF NOT EXISTS invites (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL
        )
        """
    )
    try:
        _store.execute("ALTER TABLE invites ADD COLUMN role TEXT DEFAULT 'member'")
    except Exception:
        pass

_store.execute(
    "INSERT OR IGNORE INTO orgs (id, name, plan, region, created_at) VALUES (?, ?, 'free', 'us-east-1', ?)",
    ("org_default", "Default Organization", datetime.now(timezone.utc).timestamp()),
)


@router.get("")
def list_orgs() -> dict[str, list]:
    rows = _store.fetchall("SELECT id, name, plan, region FROM orgs ORDER BY created_at DESC")
    return {"orgs": [{"id": r["id"], "name": r["name"], "plan": r["plan"], "region": r["region"]} for r in rows]}


@router.post("")
def create_org(
    body: dict[str, Any],
    x_raphael_user_id: str | None = Header(default=None, alias="X-Raphael-User-Id"),
) -> dict[str, Any]:
    oid = body.get("id", f"org_{int(datetime.now(timezone.utc).timestamp())}")
    org = _rbac.create_org(oid, body["name"], plan=body.get("plan", "free"), region=body.get("region", "us-east-1"))
    if x_raphael_user_id:
        membership_id = f"mbr_{int(datetime.now(timezone.utc).timestamp())}"
        _rbac.add_member(membership_id, oid, x_raphael_user_id, OrgRole.OWNER)
    return {"id": org.id, "name": org.name, "plan": org.plan, "region": org.region}


@router.post("/invites")
def create_invite_top_level(body: dict[str, Any]) -> dict[str, str]:
    org_id = body.get("org_id")
    if not org_id:
        raise HTTPException(400, detail="org_id_required")
    return create_invite(org_id, body)


@router.post("/join")
def join_org(
    body: dict[str, Any],
    x_raphael_user_id: str | None = Header(default=None, alias="X-Raphael-User-Id"),
) -> dict[str, Any]:
    raw_key = (body.get("key") or "").strip()
    if not raw_key:
        raise HTTPException(400, detail="key_required")
    resolved = _keys.resolve(raw_key)
    if not resolved:
        raise HTTPException(404, detail="invalid_key")
    if resolved["key_type"] != "join":
        raise HTTPException(400, detail="key_not_join_type")
    org_id = resolved["org_id"]
    user_id = x_raphael_user_id or body.get("user_id", "usr_anonymous")
    existing = _rbac.resolve(org_id, user_id)
    org_row = _store.fetchone("SELECT id, name, plan, region FROM orgs WHERE id = ?", (org_id,))
    if existing:
        return {
            "status": "already_member",
            "org_id": org_id,
            "org_name": org_row["name"] if org_row else org_id,
            "role": existing.role.value,
        }
    membership_id = f"mbr_{int(datetime.now(timezone.utc).timestamp())}"
    member = _rbac.add_member(membership_id, org_id, user_id, OrgRole.MEMBER)
    return {
        "status": "joined",
        "org_id": org_id,
        "org_name": org_row["name"] if org_row else org_id,
        "membership_id": member.id,
        "role": member.role.value,
    }


@router.get("/{org_id}/invites")
def list_invites(org_id: str) -> dict[str, list]:
    rows = _store.fetchall(
        "SELECT id, email, role, created_at FROM invites WHERE org_id = ? ORDER BY created_at DESC",
        (org_id,),
    )
    return {
        "invites": [
            {
                "id": r["id"],
                "email": r["email"],
                "role": r["role"] if "role" in r.keys() else "member",
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@router.delete("/{org_id}/invites/{invite_id}")
def revoke_invite(org_id: str, invite_id: str) -> dict[str, str]:
    _store.execute("DELETE FROM invites WHERE id = ? AND org_id = ?", (invite_id, org_id))
    return {"status": "revoked", "invite_id": invite_id}


@router.post("/{org_id}/invites")
def create_invite(org_id: str, body: dict[str, Any]) -> dict[str, str]:
    iid = f"inv_{int(datetime.now(timezone.utc).timestamp())}"
    email = body["email"]
    role = body.get("role", "member")
    _store.execute(
        "INSERT INTO invites (id, org_id, email, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (iid, org_id, email, role, datetime.now(timezone.utc).isoformat()),
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


@router.patch("/{org_id}/members/{user_id}")
def update_member_role(org_id: str, user_id: str, body: dict[str, Any]) -> dict[str, str]:
    role_value = body.get("role", "member")
    try:
        role = OrgRole(role_value)
    except ValueError as exc:
        raise HTTPException(400, detail="invalid_role") from exc
    ctx = _rbac.resolve(org_id, user_id)
    if not ctx:
        raise HTTPException(404, detail="not_a_member")
    _store.execute(
        "UPDATE memberships SET role = ? WHERE org_id = ? AND user_id = ?",
        (role.value, org_id, user_id),
    )
    return {"status": "updated", "user_id": user_id, "role": role.value}


@router.get("/{org_id}/membership/{user_id}")
def check_membership(org_id: str, user_id: str) -> dict[str, str]:
    ctx = _rbac.resolve(org_id, user_id)
    if not ctx:
        raise HTTPException(404, detail="not_a_member")
    return {"org_id": org_id, "user_id": user_id, "role": ctx.role.value}


@router.patch("/{org_id}/settings")
def patch_org_settings(org_id: str, body: dict[str, Any]) -> dict[str, str]:
    return {"status": "saved", "org_id": org_id}


@router.get("/{org_id}/connection-keys")
def list_org_connection_keys(org_id: str) -> dict[str, list]:
    return {"keys": _keys.list_keys(org_id)}


@router.post("/{org_id}/connection-keys")
def create_org_connection_key(org_id: str, body: dict[str, Any]) -> dict[str, Any]:
    key_type = body.get("type") or body.get("key_type", "join")
    if key_type not in ("join", "ingest"):
        raise HTTPException(400, detail="invalid_key_type")
    return _keys.create(org_id, key_type=key_type, label=body.get("label"))


@router.post("/{org_id}/connection-keys/{key_id}/rotate")
def rotate_org_connection_key(org_id: str, key_id: str) -> dict[str, Any]:
    rotated = _keys.rotate(org_id, key_id)
    if not rotated:
        raise HTTPException(404, detail="not_found")
    return rotated


@router.delete("/{org_id}/connection-keys/{key_id}")
def revoke_org_connection_key(org_id: str, key_id: str) -> dict[str, str]:
    if not _keys.revoke(org_id, key_id):
        raise HTTPException(404, detail="not_found")
    return {"status": "revoked", "id": key_id}
