"""Default persistent Memory location for user-facing runs."""

from pathlib import Path

DEFAULT_MEMORY_DATABASE = (
    Path(__file__).resolve().parents[2] / "memory_data"
)
