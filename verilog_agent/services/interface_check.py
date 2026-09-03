from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from verilog_agent.domain import SpecIR

_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_DECLARATION_RE = re.compile(r"\b(input|output|inout)\b(?P<body>[^;]*);", re.DOTALL)


def check_rtl_interface(path: Path, spec: SpecIR) -> dict[str, Any]:
    """Compare one top-module declaration with the normalized SpecIR interface."""
    source = _COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
    module = _find_module(source, spec.module_name)
    if module is None:
        return _report(False, [f"expected module '{spec.module_name}' was not found"], {}, spec)

    header, body = module
    actual = _parse_ansi_ports(header)
    if not actual:
        actual = _parse_non_ansi_ports(header, body)

    expected = {
        port.name: {"direction": port.direction.value, "width": port.width}
        for port in spec.ports
    }
    errors: list[str] = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"missing ports: {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected ports: {', '.join(extra)}")
    for name in sorted(set(expected) & set(actual)):
        if actual[name]["direction"] != expected[name]["direction"]:
            errors.append(
                f"port {name} direction is {actual[name]['direction']}, "
                f"expected {expected[name]['direction']}"
            )
        actual_width = actual[name]["width"]
        if actual_width is not None and actual_width != expected[name]["width"]:
            errors.append(
                f"port {name} width is {actual_width}, expected {expected[name]['width']}"
            )
        if actual_width is None:
            errors.append(f"port {name} width could not be resolved deterministically")
    return _report(not errors, errors, actual, spec)


def _report(
    passed: bool, errors: list[str], actual: dict[str, dict[str, Any]], spec: SpecIR
) -> dict[str, Any]:
    return {
        "status": "PASS" if passed else "FAIL",
        "category": "OK" if passed else "INTERFACE_MISMATCH",
        "module_name": spec.module_name,
        "expected_ports": {
            port.name: {"direction": port.direction.value, "width": port.width}
            for port in spec.ports
        },
        "actual_ports": actual,
        "errors": errors,
    }


def _find_module(source: str, module_name: str) -> tuple[str, str] | None:
    match = re.search(rf"\bmodule\s+{re.escape(module_name)}\b", source)
    if match is None:
        return None
    cursor = match.end()
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    if cursor < len(source) and source[cursor] == "#":
        cursor += 1
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor >= len(source) or source[cursor] != "(":
            return None
        cursor = _matching_parenthesis(source, cursor) + 1
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    if cursor >= len(source) or source[cursor] != "(":
        return None
    header_end = _matching_parenthesis(source, cursor)
    semicolon = source.find(";", header_end)
    endmodule = source.find("endmodule", semicolon)
    if semicolon < 0 or endmodule < 0:
        return None
    return source[cursor + 1 : header_end], source[semicolon + 1 : endmodule]


def _matching_parenthesis(source: str, start: int) -> int:
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unterminated module port list")


def _split_commas(text: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(text):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "," and depth == 0:
            items.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return items


def _parse_ansi_ports(header: str) -> dict[str, dict[str, Any]]:
    ports: dict[str, dict[str, Any]] = {}
    direction: str | None = None
    width: int | None = 1
    saw_direction = False
    for item in _split_commas(header):
        direction_match = re.match(r"\s*(input|output|inout)\b", item)
        if direction_match:
            saw_direction = True
            direction = direction_match.group(1)
            width = _parse_width(item)
        if direction is None:
            continue
        identifiers = _IDENTIFIER_RE.findall(re.sub(r"\[[^]]+\]", " ", item))
        ignored = {"input", "output", "inout", "wire", "reg", "logic", "signed", "unsigned"}
        names = [name for name in identifiers if name not in ignored]
        if names:
            ports[names[-1]] = {"direction": direction, "width": width}
    return ports if saw_direction else {}


def _parse_non_ansi_ports(header: str, body: str) -> dict[str, dict[str, Any]]:
    header_names = set(_IDENTIFIER_RE.findall(header))
    ports: dict[str, dict[str, Any]] = {}
    for declaration in _DECLARATION_RE.finditer(body):
        direction = declaration.group(1)
        declaration_text = declaration.group(0)
        width = _parse_width(declaration_text)
        names = _IDENTIFIER_RE.findall(re.sub(r"\[[^]]+\]", " ", declaration.group("body")))
        ignored = {"wire", "reg", "logic", "signed", "unsigned"}
        for name in names:
            if name in header_names and name not in ignored:
                ports[name] = {"direction": direction, "width": width}
    return ports


def _parse_width(text: str) -> int | None:
    match = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", text)
    if match is None:
        return None if "[" in text else 1
    return abs(int(match.group(1)) - int(match.group(2))) + 1
