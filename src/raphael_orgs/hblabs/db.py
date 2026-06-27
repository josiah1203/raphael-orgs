"""SQLite store for local/dev; PostgreSQL when RAPHAEL_DATABASE_URL is set."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class PlatformStore:
    """Persistent store for hosted platform entities."""

    def __init__(self, db_path: Path | str) -> None:
        from raphael_contracts import db as rdb

        self._postgres = rdb.is_postgres()
        if self._postgres:
            rdb.ensure_migrations()
            self.db_path = Path("postgres")
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._postgres:
            raise RuntimeError("conn property is SQLite-only")
        return self._conn

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                invite_token TEXT,
                invite_accepted INTEGER DEFAULT 0,
                mfa_secret TEXT,
                webauthn_credential TEXT,
                recovery_codes TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                revoked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS magic_links (
                token_hash TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS orgs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                plan TEXT DEFAULT 'free',
                region TEXT DEFAULT 'us-east-1',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memberships (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                team_id TEXT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                name TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                revoked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS webhooks (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                url TEXT NOT NULL,
                secret_hash TEXT NOT NULL,
                events TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                redirect_uri TEXT,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invites (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS connection_keys (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                key_type TEXT NOT NULL DEFAULT 'join',
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                rotated_from TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_connection_keys_hash ON connection_keys (key_hash);
            """
        )
        self._conn.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        if self._postgres:
            from raphael_contracts.db import pg_execute

            return pg_execute(sql, params)
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Any | None:
        if self._postgres:
            from raphael_contracts.db import pg_fetchone

            return pg_fetchone(sql, params)
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if self._postgres:
            from raphael_contracts.db import pg_fetchall

            return pg_fetchall(sql, params)
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        if not self._postgres:
            self._conn.close()
