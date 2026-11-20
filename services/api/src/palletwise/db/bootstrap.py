"""Cluster bootstrap, run as a PostgreSQL superuser before migrations.

Creates the roles of canonical model 11.3 and simulator spec 2, the schemas
that roles own, and the extension used for deterministic IDs. Idempotent.
In deployed environments infrastructure performs this step instead.
"""

from collections.abc import Mapping

import psycopg
from psycopg import sql

# Login roles used by processes. pw_owner is a non-login group (CWM 11.4).
LOGIN_ROLES: tuple[str, ...] = (
    "pw_migrator",
    "pw_app",
    "pw_ingest",
    "pw_evaluator",
    "pw_proposal_writer",
    "pw_workflow",
    "pw_sim_admin",
    "pw_agent_read",
    "pw_memory",
    "pw_cache",
    "sim_owner",
    "sim_outbox_reader",
)


def bootstrap(conn: psycopg.Connection, database: str, passwords: Mapping[str, str]) -> None:
    """Create roles, schemas and extensions. `passwords` maps login role to password."""
    missing = [r for r in LOGIN_ROLES if r not in passwords]
    if missing:
        raise ValueError(f"missing passwords for roles: {', '.join(missing)}")

    with conn.cursor() as cur:
        # Owner group: owns schema objects, never logs in, never bypasses RLS.
        cur.execute(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pw_owner') THEN "
            "CREATE ROLE pw_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB; "
            "END IF; END $$;"
        )
        for role in LOGIN_ROLES:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            stmt = "ALTER ROLE {} " if cur.fetchone() else "CREATE ROLE {} "
            cur.execute(
                sql.SQL(stmt + "LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(passwords[role])
                )
            )

        # Migrations run as pw_migrator and switch to pw_owner with SET ROLE.
        cur.execute("GRANT pw_owner TO pw_migrator WITH INHERIT FALSE, SET TRUE")

        db = sql.Identifier(database)
        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                db, sql.SQL(", ").join(sql.Identifier(r) for r in LOGIN_ROLES)
            )
        )
        cur.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO pw_owner").format(db))
        cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

        # Deterministic UUIDv5 for master data (CWM 2).
        cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public')

        # Alembic bookkeeping lives outside the application schemas.
        cur.execute("CREATE SCHEMA IF NOT EXISTS pw_meta AUTHORIZATION pw_owner")

        # The simulator owns its schema; PalletWise roles get nothing in it.
        cur.execute("CREATE SCHEMA IF NOT EXISTS sim AUTHORIZATION sim_owner")
        cur.execute("REVOKE ALL ON SCHEMA sim FROM PUBLIC")
        cur.execute("GRANT USAGE ON SCHEMA sim TO sim_outbox_reader")
    conn.commit()
