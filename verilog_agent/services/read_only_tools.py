"""Explicitly allowlisted, bounded reference reads for model-requested tools."""

from __future__ import annotations

from pathlib import Path


class ReadOnlyReferenceTools:
    def __init__(self, files: list[str]) -> None:
        self.files = {Path(name).resolve(strict=True) for name in files}
        if not self.files or any(not path.is_file() for path in self.files):
            raise ValueError("reference_files must name existing files")

    def call(self, name: str, path: str) -> str:
        target = Path(path).resolve(strict=True)
        if name == "read_file" and target in self.files:
            return target.read_text(encoding="utf-8")[:8192]
        if name == "list_dir" and target in {file.parent for file in self.files}:
            return "\n".join(sorted(file.name for file in self.files if file.parent == target))
        raise PermissionError("read-only tool target is not allowlisted")
