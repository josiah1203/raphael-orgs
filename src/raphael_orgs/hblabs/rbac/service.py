"""Organization RBAC with PostgreSQL RLS design notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from raphael_orgs.hblabs.db import PlatformStore


class OrgRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"
    GUEST = "guest"


class Permission(str, Enum):
    REPO_READ = "repo:read"
    REPO_WRITE = "repo:write"
    REPO_ADMIN = "repo:admin"
    BRANCH_PUSH = "branch:push"
    BRANCH_MERGE = "branch:merge"
    ADAPTER_CONNECT = "adapter:connect"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[OrgRole, set[Permission]] = {
    OrgRole.OWNER: set(Permission),
    OrgRole.ADMIN: {
        Permission.REPO_READ,
        Permission.REPO_WRITE,
        Permission.REPO_ADMIN,
        Permission.BRANCH_PUSH,
        Permission.BRANCH_MERGE,
        Permission.ADAPTER_CONNECT,
        Permission.AUDIT_READ,
    },
    OrgRole.MEMBER: {
        Permission.REPO_READ,
        Permission.REPO_WRITE,
        Permission.BRANCH_PUSH,
        Permission.ADAPTER_CONNECT,
    },
    OrgRole.VIEWER: {Permission.REPO_READ, Permission.AUDIT_READ},
    OrgRole.GUEST: {Permission.REPO_READ},
}


@dataclass
class Org:
    id: str
    name: str
    plan: str = "free"
    region: str = "us-east-1"


@dataclass
class Team:
    id: str
    org_id: str
    name: str


@dataclass
class Membership:
    id: str
    org_id: str
    user_id: str
    role: OrgRole
    team_id: str | None = None


@dataclass
class RBACContext:
    org_id: str
    user_id: str
    role: OrgRole
    permissions: set[Permission] = field(default_factory=set)


class OrgRBACService:
    """Org → Teams → Members with org_id scoping.

    Production PostgreSQL uses RLS policies:
      CREATE POLICY org_isolation ON memberships
        USING (org_id = current_setting('app.org_id')::text);
    """

    def __init__(self, store: PlatformStore) -> None:
        self.store = store

    def create_org(self, org_id: str, name: str, *, plan: str = "free", region: str = "us-east-1") -> Org:
        import time

        self.store.execute(
            "INSERT INTO orgs (id, name, plan, region, created_at) VALUES (?, ?, ?, ?, ?)",
            (org_id, name, plan, region, time.time()),
        )
        return Org(id=org_id, name=name, plan=plan, region=region)

    def create_team(self, team_id: str, org_id: str, name: str) -> Team:
        self.store.execute(
            "INSERT INTO teams (id, org_id, name) VALUES (?, ?, ?)",
            (team_id, org_id, name),
        )
        return Team(id=team_id, org_id=org_id, name=name)

    def add_member(
        self, membership_id: str, org_id: str, user_id: str, role: OrgRole, *, team_id: str | None = None
    ) -> Membership:
        self.store.execute(
            """INSERT INTO memberships (id, org_id, team_id, user_id, role)
               VALUES (?, ?, ?, ?, ?)""",
            (membership_id, org_id, team_id, user_id, role.value),
        )
        return Membership(id=membership_id, org_id=org_id, user_id=user_id, role=role, team_id=team_id)

    def resolve(self, org_id: str, user_id: str) -> RBACContext | None:
        row = self.store.fetchone(
            "SELECT * FROM memberships WHERE org_id = ? AND user_id = ?",
            (org_id, user_id),
        )
        if not row:
            return None
        role = OrgRole(row["role"])
        return RBACContext(org_id=org_id, user_id=user_id, role=role, permissions=ROLE_PERMISSIONS[role])

    def has_permission(self, ctx: RBACContext | None, perm: Permission) -> bool:
        if ctx is None:
            return False
        return perm in ctx.permissions

    def list_members(self, org_id: str) -> list[dict[str, Any]]:
        rows = self.store.fetchall(
            "SELECT * FROM memberships WHERE org_id = ? ORDER BY role",
            (org_id,),
        )
        return [dict(r) for r in rows]
