"""Freeze verified module candidates for top-only generation and multi-file EDA."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from rtlforge.domain import SpecIR


def load_module(context_path: Path, candidate_id: str | None = None) -> dict:
    from rtlforge.continuation import read_source

    source, context = read_source(context_path)
    candidate = context.candidate(candidate_id or context.selected_candidate_id)
    rtl = Path(candidate.artifact_path)
    if sha256(rtl.read_bytes()).hexdigest() != candidate.code_hash:
        raise ValueError("模块 RTL 与历史 hash 不符")
    if not context.spec_ir or not context.task_tree_root or context.spec_ir.missing_semantics():
        raise ValueError("复用模块必须具有已澄清的 SpecIR 和 TaskTree")
    static = candidate.metadata.get("static_evaluation", {})
    if not static.get("eligible_for_verification") or static.get("quality_score", 0) < 75:
        raise ValueError("复用模块必须通过静态检查")
    evidence = source.parent / "verification.json"
    verification = json.loads(evidence.read_text(encoding="utf-8"))
    if (not verification.get("passed")
            or verification.get("selected_candidate_id") != candidate.candidate_id
            or verification.get("testbench", {}).get("source") not in {
                "user_provided", "benchmark_golden",
            }):
        raise ValueError("复用候选必须具有对应的独立 TB 通过证据")
    result = {
        "module_name": context.spec_ir.module_name, "candidate_id": candidate.candidate_id,
        "rtl_sha256": candidate.code_hash, "source_context": str(source.resolve()),
        "source_rtl": str(rtl.resolve()), "spec_ir": context.spec_ir.to_dict(),
        "task_tree": context.task_tree_root.to_dict(),
        "verification_sha256": sha256(evidence.read_bytes()).hexdigest(),
    }
    result["dependencies"] = [
        {**deepcopy(module), "source_rtl": str(path)} for module, path in zip(
            context.metadata.get("integration_modules", []), dependency_files(context), strict=True,
        )
    ]
    return result


def load_module_list(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))["modules"]
    if not rows:
        raise ValueError("模块列表不能为空")
    return [load_module((path.parent / row["source_context"]).resolve(), row.get("candidate_id"))
            for row in rows]


def attach_modules(context, task_root: Path, modules: list[dict]) -> None:
    frozen, names = [], {}
    pending = [(module, module.get("required_instance", True)) for module in modules]
    while pending:
        module, direct = pending.pop(0)
        name = module["module_name"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
            raise ValueError("模块名称必须合法")
        if name in names:
            if names[name]["rtl_sha256"] != module["rtl_sha256"]:
                raise ValueError(f"依赖中有不同实现的同名模块：{name}")
            names[name]["required_instance"] |= direct
            continue
        pending.extend((child, False) for child in module.get("dependencies", []))
        spec = SpecIR.from_dict(module["spec_ir"])
        if spec.module_name != name or spec.missing_semantics():
            raise ValueError("模块合同不完整或名称不符")
        content = Path(module["source_rtl"]).read_bytes()
        if sha256(content).hexdigest() != module["rtl_sha256"]:
            raise ValueError("模块源码已改变")
        snapshot = task_root / "dependencies" / name / f"{name}.sv"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with snapshot.open("xb") as stream:
            stream.write(content)
        for filename, key in (("spec_ir.json", "spec_ir"), ("task_tree.json", "task_tree")):
            (snapshot.parent / filename).write_text(
                json.dumps(module[key], ensure_ascii=False, indent=2), encoding="utf-8",
            )
        descriptor = {key: deepcopy(value) for key, value in module.items()
                      if key != "dependencies"}
        descriptor.update(snapshot=str(snapshot.relative_to(task_root)), required_instance=direct)
        frozen.append(descriptor)
        names[name] = descriptor
    context.metadata["integration_modules"] = frozen
    context.metadata["integration_task_root"] = str(task_root.resolve())
    (task_root / "integration_manifest.json").write_text(
        json.dumps({"modules": frozen}, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def dependency_files(context) -> list[Path]:
    modules = context.metadata.get("integration_modules", [])
    if not modules:
        return []
    root = Path(context.metadata["integration_task_root"]).resolve()
    paths = []
    for module in modules:
        path = (root / module["snapshot"]).resolve(strict=True)
        if (not path.is_relative_to(root / "dependencies")
                or sha256(path.read_bytes()).hexdigest() != module["rtl_sha256"]):
            raise PermissionError("冻结模块已改变，集成证据无效")
        paths.append(path)
    return paths


def module_contracts(context) -> list[dict]:
    return [{key: deepcopy(module[key]) for key in (
        "module_name", "candidate_id", "rtl_sha256", "spec_ir", "task_tree",
    )} | {"required_instance": module.get("required_instance", True)}
            for module in context.metadata.get("integration_modules", [])]


def check_top_only(path: Path, top: str, contracts: list[dict]) -> None:
    if not contracts:
        return
    source = re.sub(r"//[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    if re.findall(r"\bmodule\s+(\w+)", source) != [top]:
        raise ValueError("集成候选只能定义顶层，不能重定义冻结模块")
    for module in contracts:
        if not module.get("required_instance", True):
            continue
        name = re.escape(module["module_name"])
        if not re.search(r"\b" + name + r"\s+(?:#\s*\([^;]+?\)\s*)?\w+\s*\(", source):
            raise ValueError(f"顶层未实例化冻结模块 {module['module_name']}")


def copy_modules(source, target, task_root: Path) -> None:
    paths = dependency_files(source)
    if paths:
        attach_modules(target, task_root, [
            {**module, "source_rtl": str(path)} for module, path in zip(
                source.metadata["integration_modules"], paths, strict=True,
            )
        ])
