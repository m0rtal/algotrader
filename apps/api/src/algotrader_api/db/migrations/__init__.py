"""Migrations package."""
from pathlib import Path

MIGRATIONS_DIR = str(Path(__file__).parent)

__all__ = ["MIGRATIONS_DIR"]
