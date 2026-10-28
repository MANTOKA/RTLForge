from rtlforge.benchmarks.single_case_demo import _prompt_for_clarification


def test_number_selects_explicit_planner_answer(monkeypatch):
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert _prompt_for_clarification({
        "pause_reason": "contract_clarification",
        "clarification_question": "复位类型？",
        "clarification_options": ["同步复位", "异步复位"],
    }) == "异步复位"


def test_free_text_and_pause(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "同步高有效复位")
    assert _prompt_for_clarification({}) == "同步高有效复位"
    monkeypatch.setattr("builtins.input", lambda _: "/pause")
    assert _prompt_for_clarification({}) is None
