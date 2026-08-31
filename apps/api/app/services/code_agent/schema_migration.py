"""Reversible schema migration for secure CodeAgent source metadata."""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy import inspect, text

from app.models import (
    CodeAgentRun,
    CodeDeployCredential,
    CodeRepositorySource,
    CodeScanReport,
    CodeSourceSnapshot,
)


_SECURE_WORKSPACE_TABLES = (
    CodeScanReport.__table__,
    CodeDeployCredential.__table__,
    CodeRepositorySource.__table__,
    CodeSourceSnapshot.__table__,
)


def upgrade_secure_workspace_schema(engine: Engine) -> None:
    """Create the secure source tables without modifying existing data."""
    for table in _SECURE_WORKSPACE_TABLES:
        table.create(bind=engine, checkfirst=True)

    additions = {
        "publish_state": "VARCHAR(32) DEFAULT 'not_requested'",
        "publish_preflight": "TEXT DEFAULT '{}'",
        "publish_confirmation": "VARCHAR(64) DEFAULT ''",
        "publish_result": "TEXT DEFAULT '{}'",
    }
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for table in (CodeScanReport.__table__.name, CodeAgentRun.__table__.name):
            if table not in existing_tables:
                continue
            columns = {column["name"] for column in inspect(engine).get_columns(table)}
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    ))


def downgrade_secure_workspace_schema(engine: Engine) -> None:
    """Drop only tables owned by this migration, in dependency order."""
    for table in reversed(_SECURE_WORKSPACE_TABLES):
        table.drop(bind=engine, checkfirst=True)
