"""Explicitly allowlisted, bounded reference reads for model-requested tools."""

from __future__ import annotations

from pathlib import Path


class ReadOnlyReferenceTools:
    def __init__(self, files: list[str]) -> None:
        self.files = {Path(name).resolve(strict=True) for name in files}
        if not self.files or any(not path.is_file() for path in self.files):
            raise ValueError("reference_files must name existing files")

    def call(
        self, name: str, path: str, *, start_line: int = 1, max_lines: int = 80,
        max_characters: int = 8192,
    ) -> str:
        if type(start_line) is not int or not 1 <= start_line <= 100_000:
            raise ValueError("start_line must be between 1 and 100000")
        if type(max_lines) is not int or not 1 <= max_lines <= 200:
            raise ValueError("max_lines must be between 1 and 200")
        if type(max_characters) is not int or not 1 <= max_characters <= 8192:
            raise ValueError("max_characters must be between 1 and 8192")
        target = Path(path).resolve(strict=True)
        if name == "read_file" and target in self.files:
            lines: list[str] = []
            characters = 0
            with target.open(encoding="utf-8") as stream:
                for number in range(1, start_line + max_lines):
                    line = stream.readline(8193)
                    if len(line) > 8192:
                        raise ValueError("reference physical line exceeds 8192 characters")
                    if not line:
                        break
                    if number >= start_line:
                        lines.append(line[:max_characters - characters])
                        characters += len(lines[-1])
                        if characters >= max_characters:
                            break
            return "".join(lines)
        if name == "list_dir" and target in {file.parent for file in self.files}:
            return "\n".join(sorted(
                file.name for file in self.files if file.parent == target
            ))[:max_characters]
        raise PermissionError("read-only tool target is not allowlisted")
