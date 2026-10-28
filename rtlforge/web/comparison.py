from __future__ import annotations


def compare(a: dict, b: dict) -> dict:
    rows_a = {str(row["problem_id"]): row for row in a["results"] if row.get("problem_id")}
    rows_b = {str(row["problem_id"]): row for row in b["results"] if row.get("problem_id")}
    common = sorted(set(rows_a) & set(rows_b))
    evaluable = [key for key in common if rows_a[key].get("status") not in
                 {"ERROR", "SKIPPED_REFERENCE"} and rows_b[key].get("status") not in
                 {"ERROR", "SKIPPED_REFERENCE"}]
    changes = []
    counts = {"pass_to_fail": 0, "fail_to_pass": 0, "unchanged": 0}
    for key in evaluable:
        old, new = rows_a[key], rows_b[key]
        old_pass, new_pass = old.get("status") == "PASS", new.get("status") == "PASS"
        change = ("unchanged" if old_pass == new_pass else
                  "fail_to_pass" if new_pass else "pass_to_fail")
        counts[change] += 1
        changes.append({"problem_id": key, "change": change,
                        "a": {k: old.get(k) for k in ("status", "rescue", "task_id",
                             "candidate_count", "repair_attempts", "token_usage", "duration_ms")},
                        "b": {k: new.get(k) for k in ("status", "rescue", "task_id",
                             "candidate_count", "repair_attempts", "token_usage", "duration_ms")}})
    config_a, config_b = a.get("configuration") or {}, b.get("configuration") or {}
    fields = ("model", "prompt_version", "sampling", "candidate_count", "candidate_policy",
              "max_repairs", "memory_mode", "dataset_root", "docker_image")
    differences = [{"field": key, "a": config_a.get(key), "b": config_b.get(key)}
                   for key in fields if config_a.get(key) != config_b.get(key)]
    if a.get("protocol") != b.get("protocol"):
        differences.append({"field": "protocol", "a": a.get("protocol"),
                            "b": b.get("protocol")})
    return {"a_id": a["id"], "b_id": b["id"],
            "scope": {"a_completed": len(rows_a), "b_completed": len(rows_b),
                      "common": len(common), "common_evaluable": len(evaluable),
                      "only_a": sorted(set(rows_a) - set(rows_b)),
                      "only_b": sorted(set(rows_b) - set(rows_a)),
                      "excluded_common": sorted(set(common) - set(evaluable))},
            "changes": changes, "counts": counts,
            "configuration_differences": differences,
            "provisional": (len(rows_a) < (config_a.get("requested_case_count") or len(rows_a))
                            or len(rows_b) < (config_b.get("requested_case_count") or len(rows_b))
                            or any(row.get("error_kind") == "QUOTA_EXHAUSTED"
                                   for row in list(rows_a.values()) + list(rows_b.values())))}
