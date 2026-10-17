import copy
from types import SimpleNamespace

import pytest

from vendor.blindspot_lab.examples.adaptive_host import AdaptiveHost
from vendor.blindspot_lab.lab.adaptive import AdaptiveLab, adaptive_context
from vendor.blindspot_lab.lab.errors import LabError as PackageLabError
from vendor.blindspot_lab.lab.content import digest, public_question
from vendor.blindspot_lab.lab.dynamic_questions import (
    SOURCES, TOPICS, generate_question, generate_questions, unique_solution, validate_bank,
)
from vendor.blindspot_lab.lab.errors import LabError


def valid_bundle():
    labels = {
        "direction": ("培训完成", "收到通知", "账户激活"),
        "necessary": ("持有凭证", "进入展厅", "账户激活"),
        "contrapositive": ("提交申请", "审核通过", "收到通知"),
        "chain": ("提交申请", "审核通过", "取得资格"),
        "and_or": ("完成注册", "通过审核", "取得资格"),
    }
    formulas = {
        # These deliberately differ from the former fixed question templates.
        "direction": ("entails", ["P->Q", "Q->R"], None,
                      ["P->R", "R->P", "Q->P", "R->Q"]),
        "necessary": ("entails", ["Q->P", "R->Q"], None,
                      ["R->P", "P->Q", "Q->R", "not R->not P"]),
        "contrapositive": ("entails", ["P->Q"], None,
                           ["not Q->not P", "Q->P", "not P->not Q", "R->P"]),
        "chain": ("entails", ["Q->P", "P->R"], None,
                  ["Q->R", "R->Q", "P->Q", "R->P"]),
        "and_or": ("equivalent", [], "not (not P or not Q)",
                   ["P or Q", "P and Q", "not P and not Q", "P and not Q"]),
    }
    candidates = []
    for topic_id, spec in TOPICS.items():
        mode, premises, target, option_formulas = formulas[topic_id]
        candidates.append({
            "topic_id": topic_id,
            "labels": dict(zip(("P", "Q", "R"), labels[topic_id])),
            "mode": mode,
            "premises": copy.deepcopy(premises),
            "target_formula": target,
            "options": [{"id": chr(65 + i), "formula": formula}
                        for i, formula in enumerate(option_formulas)],
            "citation": {"source_id": spec["source_id"],
                         "quote": SOURCES[spec["source_id"]]["quote"]},
        })
    return {"candidates": candidates}


def fake_model(bundle=None):
    def call(messages, schema, timeout_seconds):
        assert timeout_seconds > 0
        assert "学习者信息" in str(messages)
        assert "session-id-private" not in str(messages)
        return copy.deepcopy(bundle if bundle is not None else valid_bundle())
    return call


def test_model_candidate_is_rendered_and_answer_comes_from_truth_table():
    question = generate_question(fake_model(), session_id="session-id-private",
                                 request_id="req-1", preferred_track="transfer",
                                 existing_questions=[])
    assert question["topic_id"] == "chain"
    assert question["track"] == "transfer"
    assert question["correct_option_id"] == "A"
    assert "提交申请" in question["stem"]
    assert "correct_option_id" not in public_question(question)
    assert "source_label" not in public_question(question)
    assert "自编符号练习" not in question["stem"]
    assert question["citation"]["quote"] == SOURCES["logic_extension_chain"]["quote"]
    assert question["citation"]["start"] < question["citation"]["end"]
    assert question["logical_contract"]["premises"] == ["Q->P", "P->R"]
    assert "给定规则：Q→P；P→R" in question["stem"]


def test_model_composes_non_template_premises_and_options():
    question = generate_question(fake_model(), session_id="sid", request_id="req",
                                 preferred_track="direction", existing_questions=[])
    contract = question["logical_contract"]
    assert contract["premises"] == ["P->Q", "Q->R"]
    assert [item["formula"] for item in contract["options"]] == [
        "P->R", "R->P", "Q->P", "R->Q"]
    assert question["correct_option_id"] == "A"


def test_candidate_cannot_add_answer_key_or_change_formula_contract():
    bundle = valid_bundle()
    bundle["candidates"][0]["correct_option_id"] = "D"
    with pytest.raises(LabError):
        generate_question(fake_model(bundle), session_id="sid", request_id="req",
                          preferred_track="direction", existing_questions=[])


def test_wrong_citation_is_rejected_against_local_exact_source():
    bundle = valid_bundle()
    bundle["candidates"][3]["citation"]["quote"] = "虚构的条件链来源。"
    with pytest.raises(LabError) as exc:
        generate_question(fake_model(bundle), session_id="sid", request_id="req",
                          preferred_track="transfer", existing_questions=[])
    assert exc.value.code == "EVIDENCE_INVALID"


def test_formula_dsl_rejects_unknown_tokens_and_excessive_depth():
    bundle = valid_bundle()
    bundle["candidates"][0]["premises"] = ["P->S"]
    with pytest.raises(LabError):
        generate_question(fake_model(bundle), session_id="sid", request_id="req",
                          preferred_track="direction", existing_questions=[])
    bundle = valid_bundle()
    bundle["candidates"][0]["premises"] = ["not (not (not (not (not (not P)))))"]
    with pytest.raises(LabError):
        generate_question(fake_model(bundle), session_id="sid", request_id="req",
                          preferred_track="direction", existing_questions=[])


def test_truth_table_rejects_multiple_entailed_options():
    with pytest.raises(LabError, match="唯一正确选项"):
        unique_solution("entails", ["P->Q"], [
            {"id": "A", "formula": "P->Q"},
            {"id": "B", "formula": "not Q->not P"},
            {"id": "C", "formula": "Q->P"},
            {"id": "D", "formula": "R->P"},
        ])


def test_contrapositive_policy_accepts_composed_antecedent():
    from vendor.blindspot_lab.lab.dynamic_questions import _FormulaParser, _validate_topic_policy
    premises = [_FormulaParser("(P and Q)->R").parse()]
    options = [(key, _FormulaParser(formula).parse()) for key, formula in zip(
        ("A", "B", "C", "D"),
        ("not R->not (P and Q)", "P->R", "Q->R", "R->P"))]
    _validate_topic_policy("contrapositive", "entails", premises, None, options)


def test_dynamic_bank_digest_detects_tampering_and_survives_host_restart():
    q = generate_question(fake_model(), session_id="sid", request_id="req",
                          preferred_track="transfer", existing_questions=[])
    bank = [q]
    assert validate_bank(bank, digest(bank))
    changed = copy.deepcopy(bank)
    changed[0]["options"][0]["text"] = "篡改的选项"
    assert not validate_bank(changed, digest(bank))

    context = adaptive_context("sid", "mock")
    host = AdaptiveHost(context)
    host.context.update(dynamic_bank=bank, dynamic_bank_digest=digest(bank))
    restored = AdaptiveHost.restore_json(host.export_json())
    lab = AdaptiveLab()
    lab.set_dynamic_questions(restored.context["dynamic_bank"])
    assert q["question_id"] in lab.questions
    assert q["question_id"] not in AdaptiveLab().questions


def test_session_question_banks_do_not_cross_contaminate():
    q1 = generate_question(fake_model(), session_id="sid-a", request_id="req-a",
                           preferred_track="reading", existing_questions=[])
    q2 = generate_question(fake_model(), session_id="sid-b", request_id="req-b",
                           preferred_track="transfer", existing_questions=[])
    host_a = AdaptiveHost(adaptive_context("sid-a", "mock"))
    host_b = AdaptiveHost(adaptive_context("sid-b", "mock"))
    host_a.context.update(dynamic_bank=[q1], dynamic_bank_digest=digest([q1]))
    host_b.context.update(dynamic_bank=[q2], dynamic_bank_digest=digest([q2]))
    lab_a = AdaptiveLab()
    lab_b = AdaptiveLab()
    lab_a.set_dynamic_questions(host_a.context["dynamic_bank"])
    lab_b.set_dynamic_questions(host_b.context["dynamic_bank"])
    assert q1["question_id"] in lab_a.questions and q2["question_id"] not in lab_a.questions
    assert q2["question_id"] in lab_b.questions and q1["question_id"] not in lab_b.questions


def test_invalid_bank_digest_is_rejected_before_read_or_action():
    q = generate_question(fake_model(), session_id="sid", request_id="req",
                          preferred_track="transfer", existing_questions=[])
    context = adaptive_context("sid", "mock")
    context.update(dynamic_bank=[], dynamic_bank_digest=digest([]))
    host = AdaptiveHost(context)
    host.execute(AdaptiveLab(), "start", "start-1", 0)
    host.context.update(dynamic_bank=[q], dynamic_bank_digest="bad")
    lab = AdaptiveLab()
    lab.set_dynamic_questions([q])
    probe = copy.deepcopy(host.context)
    probe["request_id"] = "read"
    with pytest.raises(LabError):
        lab._context(probe)
    assert host.context["learning_state"] is not None
    assert host.context["revision"] == 1


def test_two_composed_followups_survive_submit_feedback_continue_cycle():
    bank = generate_questions(
        fake_model(), session_id="cycle-sid", request_id="cycle-bank", preferred_track="transfer",
        existing_questions=[], count=2)
    context = adaptive_context("cycle-sid", "mock")
    context.update(dynamic_bank=bank, dynamic_bank_digest=digest(bank),
                   dynamic_followups_enabled=True, max_rounds=5, preferred_track="transfer")
    host = AdaptiveHost(context)
    lab = AdaptiveLab()
    lab.set_dynamic_questions(bank)

    def act(action, **params):
        return host.execute(lab, action, f"cycle-{host.context['revision'] + 1}",
                            host.context["revision"], **params)

    state = act("start")
    for _ in range(3):
        question_id = state["lab_state"]["question"]["question_id"]
        state = act("submit", question_id=question_id,
                    answer=(lab.questions | lab.core.questions)[question_id]["correct_option_id"])
        if len(state["learning_state"]["core"]["answers"]) == 2:
            state = act("feedback", feedback="guessed")
    state = act("continue")
    first_id = state["lab_state"]["question"]["question_id"]
    assert first_id.startswith("q_dyn_")
    state = act("submit", question_id=first_id,
                answer=(lab.questions | lab.core.questions)[first_id]["correct_option_id"])
    state = act("feedback", feedback="unclear")
    state = act("continue")
    second_id = state["lab_state"]["question"]["question_id"]
    assert second_id.startswith("q_dyn_") and second_id != first_id
    assert state["lab_state"]["cycle"]["round"] == 3
    assert state["lab_state"]["cycle"]["max_rounds"] == 5


def test_generation_failure_does_not_bind_or_persist_partial_bank():
    from backend.lab_adapter.blindspot import BlindspotLabAdapter

    context = adaptive_context("failure-sid", "live")
    context.update(dynamic_bank=[], dynamic_bank_digest=digest([]), preferred_track="transfer")
    host = AdaptiveHost(context)

    def fail(*args, **kwargs):
        raise PackageLabError("MODEL_UNAVAILABLE", "synthetic provider failure")

    adapter = object.__new__(BlindspotLabAdapter)
    adapter.runtime_mode = "mock"
    adapter._timeout_seconds = 30
    adapter._lab = SimpleNamespace(core=SimpleNamespace(runners={"live": SimpleNamespace(call=fail)}))
    before = copy.deepcopy(host.context)
    with pytest.raises(PackageLabError):
        adapter._generate_dynamic(host, None, "failure-request")
    assert host.context == before
    assert host.context["dynamic_bank"] == []


def test_dynamic_rule_source_survives_adapter_public_projection_with_line():
    from backend.lab_adapter.blindspot import BlindspotLabAdapter

    adapter = object.__new__(BlindspotLabAdapter)
    adapter.runtime_mode = "mock"
    state = {"stage": "verification", "question": None, "hypotheses": [],
             "evidence": [{"type": "rule_source", "material_id": "rules-1",
                           "material_title": "逻辑规则卡", "quote": "条件链规则",
                           "locator": {"start": 12, "end": 18, "line": 3}}]}
    projected = adapter._to_contract(state, plan={}, their_next_action="answer")
    assert projected["evidence"] == [{"kind": "material", "material_id": "rules-1",
        "material_title": "逻辑规则卡", "quote": "条件链规则", "locator": "第 3 行，随包材料第 12-18 字符"}]
