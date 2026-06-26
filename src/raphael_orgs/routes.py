"""Organizations API — /v1/orgs/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["orgs"])

_ORGS: list[dict[str, Any]] = [{"id": "org_default", "name": "Default Organization", "verified": False}]


@router.get("")
def list_orgs() -> dict[str, list]:
    return {"orgs": _ORGS}


@router.post("")
def create_org(body: dict[str, Any]) -> dict[str, Any]:
    org = {"id": body.get("id", f"org_{len(_ORGS)}"), "name": body["name"], "verified": False}
    _ORGS.append(org)
    return org


@router.post("/{org_id}/invites")
def create_invite(org_id: str, body: dict[str, Any]) -> dict[str, str]:
    return {"status": "invite_sent", "org_id": org_id, "email": body["email"]}
