"""Console-only output-limit recovery; every network attempt remains separately metered."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from rtlforge.providers.base import OutputLimitError
from rtlforge.providers.usage import RecordingProvider


class InteractiveProvider(RecordingProvider):
    def __init__(self, provider, evidence_root: Path):
        super().__init__(provider, role="interactive")
        self.evidence_root = evidence_root
        self.retry_number = 0

    @property
    def settings(self):
        return getattr(self.provider, "settings", None)

    def generate_structured(self, **kwargs):
        schema_name = kwargs["schema_name"]
        self.role = (
            "debugger" if "repair" in schema_name or "debug" in schema_name else
            "generator" if "candidates" in schema_name else
            "testbench" if "testbench" in schema_name else "planner"
        )
        while True:
            try:
                return super().generate_structured(**kwargs)
            except OutputLimitError as error:
                self.retry_number += 1
                root = self.evidence_root / "output_limit" / f"attempt_{self.retry_number:03d}"
                root.mkdir(parents=True)
                (root / "partial_response.txt").write_text(error.partial_text, encoding="utf-8")
                record = {"schema_name": schema_name, "old_limit": error.limit,
                          "action": "stopped", "new_limit": None}
                print(f"\n{self.role} 输出达到 {error.limit} token，响应不完整。")
                print(f"截断证据：{root}；不会接受这份 RTL/TB。")
                print("可提高上限，重新请求当前步骤的完整 JSON；不是接着拼接残缺文本。")
                try:
                    while True:
                        raw = input(
                            f"输入新上限（Enter={error.limit * 2}；N 停止，重试消耗额度）："
                        ).strip()
                        if raw.lower() == "n":
                            raise error
                        if raw and (not raw.isdecimal() or int(raw) <= error.limit):
                            print("请输入大于当前上限的整数，或 N。")
                            continue
                        limit = int(raw) if raw else error.limit * 2
                        self.provider.settings = replace(
                            self.provider.settings, max_output_tokens=limit,
                        )
                        record.update(action="retry", new_limit=limit)
                        break
                finally:
                    (root / "decision.json").write_text(
                        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8",
                    )
