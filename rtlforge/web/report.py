from __future__ import annotations

from rtlforge.web.views import MISSING


def markdown(report: dict) -> str:
    config, metrics = report.get("configuration") or {}, report.get("metrics") or {}
    results = report.get("results") or []
    completed = len(results)
    expected = config.get("requested_case_count")
    status_counts = {key: sum(row.get("status") == key for row in results)
                     for key in ("PASS", "FAIL", "ERROR", "SKIPPED_REFERENCE")}
    usage = metrics.get("token_usage") or {}
    rescues = {kind: sum(row.get("rescue") == kind for row in results) for kind in (
        "RANK1_PASS", "FALLBACK_RESCUE", "REPAIR_RESCUE", "UNRESOLVED",
        "INFRASTRUCTURE_ERROR", "UNKNOWN_LEGACY",
    )}
    def display(value):
        return "NOT_REPORTED" if value is None else value
    lines = [f"# {report.get('dataset') or 'Benchmark'} 测试报告", "",
             f"生成时间：{report.get('updated_at') or report.get('created_at') or MISSING}",
             f"模型：{config.get('model') or MISSING}",
             "协议：oracle-assisted closed-loop；golden 仅由 Verifier 使用",
             f"Prompt 版本：{config.get('prompt_version') or MISSING}",
             f"候选策略：{config.get('candidate_policy') or MISSING}；"
             f"上限 {config.get('candidate_count', MISSING)}；"
             f"修复预算 {config.get('max_repairs', MISSING)}",
             f"Memory：{config.get('memory_mode') or MISSING}；"
             f"Docker：{config.get('docker_image') or MISSING}",
             f"采样：{config.get('sampling') or MISSING}",
             "",
             "## 范围与结果", "",
             f"已记录结果 {completed} / {expected if expected is not None else MISSING}；"
             f"未开始 {max(0, expected - completed) if isinstance(expected, int) else MISSING}",
             "；".join(f"{key} {value}" for key, value in status_counts.items()),
             f"可评测通过率：{metrics.get('agent_pass_rate_evaluable', MISSING)}；"
             f"可评测题数：{metrics.get('evaluable', MISSING)}",
             f"ERROR：{metrics.get('status_counts', {}).get('ERROR', MISSING)}；"
             f"其中额度错误：{metrics.get('quota_errors', MISSING)}",
             "原报告历史统计保留在 JSON 的 stored_metrics；网页功能口径仅计 PASS/FAIL。",
             f"修复后通过：{metrics.get('passed_after_repair', MISSING)}；"
             f"失败类别：{metrics.get('failure_categories') or MISSING}",
             f"路径类型：{rescues}",
             f"已知 Token：输入 {display(usage.get('input_tokens'))}；"
             f"输出 {display(usage.get('output_tokens'))}；"
             f"报告调用 {usage.get('reported_calls', MISSING)} / "
             f"{usage.get('call_count', MISSING)}；分角色 {usage.get('by_role') or MISSING}",
             f"停止原因：{report.get('last_error') or MISSING}", "",
             "## 逐题结果", "",
             "| Problem | Status | Rescue | Candidate | Repairs | Failure | Task |",
             "|---|---|---|---|---:|---|---|",]
    for row in results:
        lines.append("| " + " | ".join(str(row.get(key) if row.get(key) is not None else MISSING)
                                        .replace("|", "\\|").replace("\n", " ")
                                        for key in ("problem_id", "status", "rescue",
                                                    "selected_candidate_id",
                                                    "repair_attempts", "failure_category",
                                                    "task_id"))
                     + " |")
    lines.extend(["", "## 限制", "",
                  "未启动题目未计入失败；额度、超时及工具异常与功能失败分开。",
                  "静态质量分不代表功能正确；旧运行缺失字段保留为当时未记录。",
                  "如配置、题目范围或运行时间不同，不据此作因果结论。", ""])
    return "\n".join(lines)
