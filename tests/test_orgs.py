"""Organizations domain tests."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from raphael_orgs.app import app
from raphael_orgs.hblabs.db import PlatformStore
from raphael_orgs.hblabs.rbac.service import OrgRBACService, OrgRole, Permission
from raphael_orgs.connection_keys import ConnectionKeyService


def _fresh_client(tmp: str) -> tuple[TestClient, PlatformStore, OrgRBACService, ConnectionKeyService]:
    import raphael_orgs.routes as routes

    db = Path(tmp) / "orgs-test.db"
    routes._store = PlatformStore(db)
    routes._rbac = OrgRBACService(routes._store)
    routes._keys = ConnectionKeyService(routes._store)
    return TestClient(app), routes._store, routes._rbac, routes._keys


def test_health() -> None:
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["service"] == "raphael-orgs"


def test_list_and_create_org() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        res = client.get("/v1/orgs")
        assert res.status_code == 200
        assert "orgs" in res.json()

        res = client.post("/v1/orgs", json={"name": "Acme Corp", "plan": "team"})
        assert res.status_code == 200
        created = res.json()
        assert created["name"] == "Acme Corp"
        assert created["plan"] == "team"

        res = client.post(
            f"/v1/orgs/{created['id']}/invites",
            json={"email": "new@acme.io"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "invite_sent"


def test_create_invite_top_level() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        org = client.post("/v1/orgs", json={"name": "Top"}, headers={"X-Raphael-User-Id": "u1"}).json()
        res = client.post(
            "/v1/orgs/invites",
            json={"org_id": org["id"], "email": "top@example.com"},
            headers={"X-Raphael-User-Id": "u1"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "invite_sent"


def test_join_and_connection_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        org = client.post("/v1/orgs", json={"name": "Join Co"}).json()
        key_res = client.post(
            f"/v1/orgs/{org['id']}/connection-keys",
            json={"type": "join"},
            headers={"X-Raphael-Org-Id": org["id"]},
        )
        assert key_res.status_code == 200
        raw_key = key_res.json()["key"]

        join = client.post(
            "/v1/orgs/join",
            json={"key": raw_key},
            headers={"X-Raphael-User-Id": "usr_joiner"},
        )
        assert join.status_code == 200
        assert join.json()["status"] == "joined"

        listed = client.get(f"/v1/orgs/{org['id']}/connection-keys")
        assert listed.status_code == 200
        assert len(listed.json()["keys"]) >= 1


def test_invite_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        org = client.post("/v1/orgs", json={"name": "Invite Co"}).json()
        created = client.post(
            f"/v1/orgs/{org['id']}/invites",
            json={"email": "admin@invite.co", "role": "admin"},
        )
        assert created.status_code == 200
        invite_id = created.json()["invite_id"]

        listed = client.get(f"/v1/orgs/{org['id']}/invites")
        assert listed.status_code == 200
        invites = listed.json()["invites"]
        assert any(i["id"] == invite_id and i["role"] == "admin" for i in invites)

        revoked = client.delete(f"/v1/orgs/{org['id']}/invites/{invite_id}")
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

        after = client.get(f"/v1/orgs/{org['id']}/invites")
        assert not any(i["id"] == invite_id for i in after.json()["invites"])


def test_connection_key_rotate_and_revoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        org = client.post("/v1/orgs", json={"name": "Key Co"}).json()
        created = client.post(
            f"/v1/orgs/{org['id']}/connection-keys",
            json={"type": "ingest", "label": "warehouse"},
        ).json()
        key_id = created["id"]
        original_key = created["key"]

        rotated = client.post(f"/v1/orgs/{org['id']}/connection-keys/{key_id}/rotate")
        assert rotated.status_code == 200
        assert rotated.json()["key"] != original_key
        assert rotated.json()["rotated_from"] == key_id

        listed = client.get(f"/v1/orgs/{org['id']}/connection-keys")
        active_ids = {k["id"] for k in listed.json()["keys"]}
        assert rotated.json()["id"] in active_ids
        assert key_id not in active_ids

        revoke = client.delete(f"/v1/orgs/{org['id']}/connection-keys/{rotated.json()['id']}")
        assert revoke.status_code == 200
        assert revoke.json()["status"] == "revoked"


def test_join_rejects_invalid_and_ingest_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        assert client.post("/v1/orgs/join", json={"key": ""}).status_code == 400
        assert client.post("/v1/orgs/join", json={"key": "hbl_join_notreal"}).status_code == 404

        org = client.post("/v1/orgs", json={"name": "Ingest Only"}).json()
        ingest = client.post(
            f"/v1/orgs/{org['id']}/connection-keys",
            json={"type": "ingest"},
        ).json()
        res = client.post(
            "/v1/orgs/join",
            json={"key": ingest["key"]},
            headers={"X-Raphael-User-Id": "usr_ingest"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "key_not_join_type"


def test_membership_role_edges() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, rbac, _ = _fresh_client(tmp)

        org = client.post(
            "/v1/orgs",
            json={"name": "RBAC Co"},
            headers={"X-Raphael-User-Id": "owner_user"},
        ).json()
        org_id = org["id"]

        add = client.post(
            f"/v1/orgs/{org_id}/members",
            json={"id": "mbr_viewer", "user_id": "viewer_user", "role": "viewer"},
        )
        assert add.status_code == 200
        assert add.json()["role"] == "viewer"

        owner_ctx = rbac.resolve(org_id, "owner_user")
        viewer_ctx = rbac.resolve(org_id, "viewer_user")
        assert owner_ctx and rbac.has_permission(owner_ctx, Permission.REPO_ADMIN)
        assert viewer_ctx and not rbac.has_permission(viewer_ctx, Permission.REPO_WRITE)
        assert rbac.has_permission(viewer_ctx, Permission.REPO_READ)

        updated = client.patch(
            f"/v1/orgs/{org_id}/members/viewer_user",
            json={"role": "admin"},
        )
        assert updated.status_code == 200
        assert updated.json()["role"] == "admin"

        check = client.get(f"/v1/orgs/{org_id}/membership/viewer_user")
        assert check.status_code == 200
        assert check.json()["role"] == "admin"

        missing = client.get(f"/v1/orgs/{org_id}/membership/nobody")
        assert missing.status_code == 404

        members = client.get(f"/v1/orgs/{org_id}/members")
        assert members.status_code == 200
        roles = {m["user_id"]: m["role"] for m in members.json()["members"]}
        assert roles["owner_user"] == OrgRole.OWNER.value
        assert roles["viewer_user"] == OrgRole.ADMIN.value


def test_join_already_member() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _, _ = _fresh_client(tmp)

        org = client.post("/v1/orgs", json={"name": "Repeat Join"}).json()
        key = client.post(
            f"/v1/orgs/{org['id']}/connection-keys",
            json={"type": "join"},
        ).json()["key"]

        first = client.post(
            "/v1/orgs/join",
            json={"key": key},
            headers={"X-Raphael-User-Id": "repeat_user"},
        )
        assert first.json()["status"] == "joined"

        second = client.post(
            "/v1/orgs/join",
            json={"key": key},
            headers={"X-Raphael-User-Id": "repeat_user"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "already_member"
        assert second.json()["role"] == "member"
