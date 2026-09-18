"""Compatibility exports for modules not yet migrated to ``app.core``."""

from .core.database import UnitOfWork, connect

__all__ = ["UnitOfWork", "connect"]

