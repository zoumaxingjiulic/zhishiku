"""Shared application primitives for the modular API."""

from .database import UnitOfWork, connect

__all__ = ["UnitOfWork", "connect"]

