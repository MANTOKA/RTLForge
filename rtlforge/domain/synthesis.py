"""Implementation requirements are separate from the functional SpecIR contract."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class SynthesisConstraints:
    part: str | None = None
    top: str | None = None
    clock_port: str | None = None
    clock_period_ns: float | None = None
    xdc_path: str | None = None
    resource_limits: dict[str, int] = field(default_factory=dict)
    mode: str = "out_of_context"
    require_timing: bool = True

    def __post_init__(self):
        for name in ("top", "clock_port"):
            value = getattr(self, name)
            if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ValueError(f"invalid synthesis {name}")
        if self.part is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", self.part):
            raise ValueError("invalid FPGA part")
        period = self.clock_period_ns
        if period is not None and (
            isinstance(period, bool) or not isinstance(period, (int, float))
            or not math.isfinite(period) or period <= 0
        ):
            raise ValueError("clock_period_ns must be a positive finite number")
        if self.mode != "out_of_context":
            raise ValueError("only out_of_context synthesis is supported")
        if type(self.require_timing) is not bool:
            raise ValueError("require_timing must be boolean")
        for key, value in self.resource_limits.items():
            if key not in {"lut", "ff", "bram", "dsp"} or type(value) is not int or value < 0:
                raise ValueError("resource_limits requires nonnegative LUT/FF/BRAM/DSP counts")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def missing(self):
        questions = []
        if not self.part:
            questions.append("请指定综合目标 FPGA 器件型号（part）。")
        if not self.top:
            questions.append("缺少综合顶层模块（top）。")
        if self.require_timing and not self.xdc_path and (
            not self.clock_port or self.clock_period_ns is None
        ):
            questions.append("请提供时钟端口和周期（ns），或提供包含时钟定义的 XDC 路径。")
        return questions


@dataclass(slots=True)
class SynthesisResult:
    candidate_id: str
    rtl_sha256: str
    constraints: dict
    status: str = "ERROR"
    category: str = "SYNTHESIS_TOOL_ERROR"
    synthesis_status: str = "NOT_RUN"
    timing_status: str = "NOT_EVALUATED"
    resource_status: str = "NOT_EVALUATED"
    constraints_status: str = "UNKNOWN"
    constraint_coverage: dict = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    wns_ns: float | None = None
    vivado_version: str | None = None
    mcp_server_sha256: str | None = None
    mcp_server_info: dict = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    critical_path_summary: str = ""
    evidence: dict = field(default_factory=dict)
    scope: str = "post_synthesis_ooc; not post_route or full IO timing signoff"

    def to_dict(self):
        return asdict(self)

    def feedback(self):
        """Small, source-bound evidence for Agent prompts; no arbitrary report reads."""
        return {key: value for key, value in self.to_dict().items() if key not in {
            "evidence", "mcp_server_info", "mcp_server_sha256",
        }}
