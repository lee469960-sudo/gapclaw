"""Reversible schema migration for secure CodeAgent source metadata."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from app.models import (
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


def downgrade_secure_workspace_schema(engine: Engine) -> None:
    """Drop only tables owned by this migration, in dependency order."""
    for table in reversed(_SECURE_WORKSPACE_TABLES):
        table.drop(bind=engine, checkfirst=True)
