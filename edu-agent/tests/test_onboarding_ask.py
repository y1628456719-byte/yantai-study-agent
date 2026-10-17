"""P1-4 回归：阶段追问「问什么由规则定、怎么问由模型说」。

三条断言分别守住：
1. 模型可用时，追问用模型的说法，但**规则原话必须仍留在 `rule_next_question`**（可审计）；
2. 模型挂了（LLMError）时，追问退回规则原话、`next_question_source=rules(degraded)`，
   **不能因此让建档/改档案失败**——追问文案的失败不该打断主流程；
3. 缺什么就问什么：模型换了说法也不能改问别的（`next_question_key` 仍是规则选定的那一项）。
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import llm
from backend.main import app  # noqa: E402  （conftest 已设好 MOCK_LLM / EDU_AGENT_DB）


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def envelope(resp, expect=200):
    assert resp.status_code == expect, resp.text
    body = resp.json()
    assert body["ok"] is True and body["error"] is None
    return body["data"]


# ------------------------------------------------------------------ 离线：规则原话


def test_offline_session_creation_uses_rule_question(client):
    data = envelope(client.post("/api/v1/sessions", json={"profile": {}}))
    onboarding = data["onboarding"]
    assert onboarding["next_question_key"] == "exam_year_known"
    assert onboarding["next_question_source"] == "rules(offline)"
    # 离线时模型说法 = 规则说法，两者必须一致
    assert onboarding["next_question"] == onboarding["rule_next_question"]


# ------------------------------------------------- 模型可用：换说法但不换问题


def test_model_question_is_used_and_rule_text_kept(client, monkeypatch):
    seen = {}

    async def fake_generate_json(messages, schema, **kwargs):
        seen["system"] = messages[0]["content"]
        seen["payload"] = json.loads(messages[1]["content"])
        return {"question": "同学，你准备考哪一年的研究生呀？"}

    monkeypatch.setattr(llm, "generate_json", fake_generate_json)
    monkeypatch.setattr(llm.config, "MOCK_LLM", False)
    monkeypatch.setattr(llm.config, "LLM_API_KEY", "sk-test-key")
    try:
        data = envelope(client.post("/api/v1/sessions", json={"profile": {}}))
    finally:
        pass
    onboarding = data["onboarding"]
    assert onboarding["next_question"] == "同学，你准备考哪一年的研究生呀？"
    assert onboarding["next_question_source"] == "model"
    # 规则原话仍在（审计与对齐 SPEC §5.4 用）
    assert onboarding["rule_next_question"] == "你打算参加哪一年的考研？"
    assert onboarding["next_question_key"] == "exam_year_known"
    # 交给模型的是"已经定好的问题"，不是让它自由发挥
    assert seen["payload"]["must_ask_about"] == "exam_year_known"
    assert "只问这一项" in seen["system"]


def test_patch_profile_asks_the_next_missing_item(client, monkeypatch):
    created = envelope(client.post("/api/v1/sessions", json={"profile": {"exam_year": 2027}}))
    sid = created["session_id"]

    async def fake_generate_json(messages, schema, **kwargs):
        return {"question": "有想考的专业方向吗？没有的话我按通信大类给你找。"}

    monkeypatch.setattr(llm, "generate_json", fake_generate_json)
    monkeypatch.setattr(llm.config, "MOCK_LLM", False)
    monkeypatch.setattr(llm.config, "LLM_API_KEY", "sk-test-key")
    data = envelope(client.patch(f"/api/v1/sessions/{sid}/profile", json={"major": None}))
    onboarding = data["onboarding"]
    assert onboarding["next_question_key"] == "has_direction"
    assert onboarding["next_question_source"] == "model"
    assert onboarding["next_question"] == "有想考的专业方向吗？没有的话我按通信大类给你找。"


# --------------------------------------------------- 模型挂了：退回规则且不报错


def test_model_failure_falls_back_to_rule_question(client, monkeypatch):
    async def boom(messages, schema, **kwargs):
        raise llm.LLMError("模型服务调用失败（测试注入）")

    monkeypatch.setattr(llm, "generate_json", boom)
    monkeypatch.setattr(llm.config, "MOCK_LLM", False)
    monkeypatch.setattr(llm.config, "LLM_API_KEY", "sk-test-key")
    data = envelope(client.post("/api/v1/sessions", json={"profile": {}}))
    onboarding = data["onboarding"]
    assert onboarding["next_question"] == onboarding["rule_next_question"]
    assert onboarding["next_question_source"] == "rules(degraded)"
    assert onboarding["next_question_error"]


def test_empty_model_question_falls_back(client, monkeypatch):
    async def blank(messages, schema, **kwargs):
        return {"question": "   "}

    monkeypatch.setattr(llm, "generate_json", blank)
    monkeypatch.setattr(llm.config, "MOCK_LLM", False)
    monkeypatch.setattr(llm.config, "LLM_API_KEY", "sk-test-key")
    data = envelope(client.post("/api/v1/sessions", json={"profile": {}}))
    onboarding = data["onboarding"]
    assert onboarding["next_question"] == onboarding["rule_next_question"]
    assert onboarding["next_question_source"] == "rules(degraded)"
