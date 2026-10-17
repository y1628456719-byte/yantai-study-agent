"""Agent 的工具注册表 —— 每个工具就是现有 HTTP 契约里的一个端点。

设计约束（docs/AGENT_ALIGNMENT.md P0-1）：
* **不改任何现有接口**：Agent 只是这些接口的另一个调用方。
* 工具通过 **进程内 ASGI 调用**（httpx.ASGITransport）打到同一个 app，
  因此校验、错误码、持久化、`revision` 递增全部复用同一套逻辑，不存在"第二实现"。
* 每个工具只声明：路径模板、路径参数、请求体参数、参数 JSON Schema、结果摘要函数。

（旧版 tools.py 是「单科备考闭环」时期的本地工具集，随 DESIGN.md 一起作废；
 `_extract_json` 已由 `backend/llm.py` 承接。）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class ApiTool:
    name: str
    description: str
    method: str
    path: str                        # 可含 {session_id}
    path_args: tuple[str, ...] = ()   # 从 args 里取出填进路径的参数
    schema: dict = field(default_factory=dict)  # 给模型的参数 JSON Schema
    needs_session: bool = True

    def build_path(self, args: dict, session_id: str | None) -> str:
        values = {k: args[k] for k in self.path_args if k in args}
        if "{session_id}" in self.path:
            values["session_id"] = session_id or ""
        return self.path.format(**values)

    def build_body(self, args: dict) -> dict:
        return {k: v for k, v in args.items() if k not in self.path_args}


TOOLS: dict[str, ApiTool] = {}


def _reg(tool: ApiTool) -> ApiTool:
    TOOLS[tool.name] = tool
    return tool


_reg(ApiTool(
    name="create_session",
    description="给这位同学建立会话档案。第一次对话（还没有 session_id）时**必须**先用它，profile 里只放从用户话里能确定的字段。",
    method="POST",
    path="/api/v1/sessions",
    schema={
        "profile": {
            "type": "object",
            "description": "档案片段，可只给已知字段",
            "properties": {
                "exam_year": {"type": "integer", "description": "考研年份，如 2027"},
                "major": {"type": "string", "description": "报考专业方向，如 信息与通信工程"},
                "background": {"type": "string", "description": "本科背景"},
                "preferred_regions": {"type": "array", "items": {"type": "string"}, "description": "意向地区/城市"},
                "daily_minutes": {"type": "integer", "description": "每天可投入分钟数"},
                "target_score": {"type": "integer", "description": "目标总分，用户明确说了才填"},
                "study_status": {"type": "string", "description": "在校/已毕业/边工作边考/同等学力"},
                "english_level": {"type": "string"},
                "is_cross_major": {"type": "boolean"},
            },
        }
    },
    needs_session=False,
))

_reg(ApiTool(
    name="update_profile",
    description="补充或修正档案。拿到新信息（地区、专业、年份、每天时间、目标分、在校状态）就用它写进去。",
    method="PATCH",
    path="/api/v1/sessions/{session_id}/profile",
    schema={
        "exam_year": {"type": "integer"},
        "major": {"type": "string"},
        "background": {"type": "string"},
        "preferred_regions": {"type": "array", "items": {"type": "string"}},
        "daily_minutes": {"type": "integer"},
        "target_score": {"type": "integer"},
        "study_status": {"type": "string"},
        "english_level": {"type": "string"},
        "is_cross_major": {"type": "boolean"},
        "preferences": {"type": "string"},
    },
))

_reg(ApiTool(
    name="get_session",
    description="读当前档案与会话状态（阶段、缺什么字段、已选院校、资料数、能力画像）。不确定现状时先读它。",
    method="GET",
    path="/api/v1/sessions/{session_id}",
    schema={},
))

_reg(ApiTool(
    name="recommend",
    description="生成院校推荐（含冲/稳/保分档、风险、淘汰名单、维度命中）。档案够用之后再调；可重复调用。",
    method="POST",
    path="/api/v1/sessions/{session_id}/recommendations",
    schema={},
))

_reg(ApiTool(
    name="select_school",
    description="把某所院校确认为目标院校（会写入决策时间线）。用户明确表态后才调。",
    method="PUT",
    path="/api/v1/sessions/{session_id}/target",
    schema={
        "school_id": {"type": "string", "description": "院校 id，如 jlu / heu / neu"},
        "user_note": {"type": "string", "description": "用户原话或理由，可省略"},
    },
))

_reg(ApiTool(
    name="set_shortlist",
    description="把候选范围缩小到指定几所（用户说'就在这几所里挑'时用）。",
    method="PUT",
    path="/api/v1/sessions/{session_id}/shortlist",
    schema={"school_ids": {"type": "array", "items": {"type": "string"}}},
))

_reg(ApiTool(
    name="list_materials",
    description="列出已录入的学习资料（含标题、字数、科目）。",
    method="GET",
    path="/api/v1/sessions/{session_id}/materials",
    schema={},
))

_reg(ApiTool(
    name="add_material",
    description="录入一份学习资料（用户贴了笔记/提纲/真题文本时用）。",
    method="POST",
    path="/api/v1/sessions/{session_id}/materials",
    schema={
        "title": {"type": "string"},
        "text": {"type": "string", "description": "资料正文"},
        "subject": {"type": "string", "description": "标准科目代码，如 ANALOG_CIRCUIT"},
        "source_url": {"type": "string"},
    },
))

_reg(ApiTool(
    name="delete_material",
    description="删除一份资料（按 material_id）。已被诊断引用过的会软删除，证据仍可溯源。",
    method="DELETE",
    path="/api/v1/sessions/{session_id}/materials/{material_id}",
    path_args=("material_id",),
    schema={"material_id": {"type": "string"}},
))

_reg(ApiTool(
    name="lab_start",
    description="开始一轮盲点诊断。有资料时传 material_id；**没有资料也能测**：material_id=null + mode=self_assessment。",
    method="POST",
    path="/api/v1/sessions/{session_id}/lab/start",
    schema={
        "material_id": {"type": ["string", "null"], "description": "资料 id，无资料必须显式传 null"},
        "topic": {"type": "string", "description": "标准科目代码，如 MATH_CALC / ANALOG_CIRCUIT"},
        "mode": {"type": "string", "enum": ["material", "self_assessment"]},
    },
))

_reg(ApiTool(
    name="lab_answer",
    description="提交诊断题的答案。answer 传选项 key（A/B/C/D）。question_id 必须是上一步返回的那个。",
    method="POST",
    path="/api/v1/sessions/{session_id}/lab/answer",
    schema={
        "question_id": {"type": "string"},
        "answer": {"type": "string"},
    },
))

_reg(ApiTool(
    name="lab_action",
    description="实验室的其它动作：feedback（只看批改）/ continue（继续追问）/ time（加时间）/ stop（结束本轮）。",
    method="POST",
    path="/api/v1/sessions/{session_id}/lab/action",
    schema={
        "action": {"type": "string", "enum": ["feedback", "continue", "time", "stop"]},
        "question_id": {"type": "string", "description": "submit 之外的动作用不到，可省略"},
        "answer": {"type": "string", "description": "feedback 时可选：unclear|too_easy|guessed|new_example"},
        "feedback": {"type": "string", "enum": ["unclear", "too_easy", "guessed", "new_example"]},
        "minutes": {"type": "integer", "description": "action=time 时必填：新增分钟数"},
    },
))

_reg(ApiTool(
    name="revise_plan",
    description="按新的每日时间重排学习计划（不会清空院校/资料/作答记录）。",
    method="POST",
    path="/api/v1/sessions/{session_id}/plan/revise",
    schema={"daily_minutes": {"type": "integer", "description": "新的每日分钟数"}},
))

_reg(ApiTool(
    name="list_sources",
    description="列出院校数据的来源与覆盖情况（抓了多少条官方记录、哪些年份）。用户问'数据哪来的'时用。",
    method="GET",
    path="/api/v1/meta/sources",
    schema={},
    needs_session=False,
))

_reg(ApiTool(
    name="list_subjects",
    description="列出标准科目表（代码、名称、包含关系）。需要把用户说的科目名对到代码时用。",
    method="GET",
    path="/api/v1/meta/subjects",
    schema={},
    needs_session=False,
))


def tool_catalog() -> list[dict]:
    """给模型看的工具清单（名字 + 何时用 + 参数）。"""
    return [{"tool": t.name, "when": t.description, "args": t.schema} for t in TOOLS.values()]


def render_catalog() -> str:
    return json.dumps(tool_catalog(), ensure_ascii=False, indent=1)


class ToolError(RuntimeError):
    """工具执行失败（业务错误或参数错误）：作为观察结果交回模型，不炸掉整轮。"""

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


async def execute(name: str, args: dict | None, *, app, session_id: str | None) -> tuple[dict, str]:
    """进程内调用对应端点，返回 (data, 人读摘要)。"""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"没有叫 {name} 的工具。可用工具：{'、'.join(TOOLS)}")

    args = dict(args or {})
    if tool.needs_session and not session_id:
        raise ToolError("这一步还缺 session_id：请先调 create_session 建立档案。")

    path = tool.build_path(args, session_id)
    body = tool.build_body(args) if tool.method in ("POST", "PUT", "PATCH") else None

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1",
                                 timeout=180.0) as client:
        try:
            resp = await client.request(tool.method, path, json=body)
        except Exception as exc:  # 内部异常也当观察结果交回
            raise ToolError(f"调用 {tool.method} {path} 失败：{exc}") from exc

    try:
        payload = resp.json()
    except Exception as exc:
        raise ToolError(f"{tool.method} {path} 返回了非 JSON 内容（HTTP {resp.status_code}）") from exc

    if not isinstance(payload, dict) or "ok" not in payload:
        raise ToolError(f"{tool.method} {path} 返回结构异常（HTTP {resp.status_code}）")

    if payload.get("ok") is not True:
        error = payload.get("error") or {}
        message = error.get("message") or f"HTTP {resp.status_code}"
        raise ToolError(message, code=error.get("code"))

    data = payload.get("data")
    return data, summarize(name, data, args, session_id)


# ---------------------------------------------------------------- 结果摘要（给人看的）


def summarize(name: str, data, args: dict, session_id: str | None) -> str:
    if name == "create_session" and isinstance(data, dict):
        onboarding = data.get("onboarding") or {}
        tail = f"，缺：{'、'.join(onboarding.get('missing_critical') or [])}" \
            if onboarding.get("missing_critical") else ""
        return (f"已建会话 {data.get('session_id')}，阶段 {onboarding.get('phase')}"
                f"（{onboarding.get('phase_name')}），revision={data.get('revision')}{tail}")

    if name == "update_profile" and isinstance(data, dict):
        onboarding = data.get("onboarding") or {}
        tail = f"，仍缺：{'、'.join(onboarding.get('missing_critical') or [])}" \
            if onboarding.get("missing_critical") else ""
        return (f"档案已更新，阶段 {onboarding.get('phase')}（{onboarding.get('phase_name')}），"
                f"revision={data.get('revision')}{tail}")

    if name == "get_session" and isinstance(data, dict):
        onboarding = data.get("onboarding") or {}
        profile = data.get("profile") or {}
        return (f"阶段 {onboarding.get('phase')}（{onboarding.get('phase_name')}）；档案："
                f"{profile.get('exam_year') or '?'} 年 / {profile.get('major') or '方向未定'} / "
                f"地域 {'、'.join(profile.get('preferred_regions') or []) or '未填'}；"
                f"资料 {len(data.get('materials') or [])} 份；"
                f"已选院校 {data.get('selected_school_id') or '未选'}")

    if name == "recommend" and isinstance(data, dict):
        items = data.get("recommendations") or []
        excluded = data.get("excluded") or []
        tiers = [f"{it.get('school_name')}={(it.get('metrics') or {}).get('tier') or '未分档'}"
                 for it in items]
        flags = data.get("ability_flags") or []
        return (f"{len(items)} 所候选（{'；'.join(tiers)}），{len(excluded)} 所进淘汰名单；"
                f"维度命中 {len(data.get('dimension_hits') or [])}、缺失 "
                f"{len(data.get('dimension_missing') or [])}"
                + (f"；能力警示 {len(flags)} 条" if flags else ""))

    if name == "select_school" and isinstance(data, dict):
        return f"目标院校已确认为 {data.get('selected_school_id')}，revision={data.get('revision')}"

    if name == "set_shortlist" and isinstance(data, dict):
        return f"候选范围已收窄为 {'、'.join(data.get('shortlist') or [])}"

    if name == "list_materials":
        items = data.get("materials") if isinstance(data, dict) else data
        return f"当前有 {len(items or [])} 份资料"

    if name == "add_material" and isinstance(data, dict):
        return (f"资料已入库（{data.get('title')}，{data.get('character_count')} 字，"
                f"id={data.get('material_id')}）")

    if name == "delete_material" and isinstance(data, dict):
        return (f"资料 {data.get('material_id')} 已处理（"
                f"{'软删除，证据仍可溯源' if data.get('soft') else '直接删除'}）")

    if name == "lab_start" and isinstance(data, dict):
        question = data.get("question") or {}
        return (f"已开始诊断：stage={data.get('stage')}，题目 {question.get('question_id')}"
                f"（{len(question.get('options') or [])} 个选项），"
                + ("引用资料" if any(e.get("kind") == "material" for e in (data.get("evidence") or []))
                   else "无资料基线（自评/实测）"))

    if name == "lab_answer" and isinstance(data, dict):
        hyp = data.get("hypotheses") or []
        states = "、".join(f"{h.get('label')}={h.get('status')}" for h in hyp) or "无"
        ev = len(data.get("evidence") or [])
        return (f"已批改：stage={data.get('stage')}，next_action={data.get('next_action')}，"
                f"假设 {states}；证据 {ev} 条")

    if name == "lab_action" and isinstance(data, dict):
        return f"实验室动作已生效：stage={data.get('stage')}，next_action={data.get('next_action')}"

    if name == "revise_plan" and isinstance(data, dict):
        changes = data.get("changes") or []
        dropped = [c for c in changes if c.get("action") == "dropped"]
        return (f"计划已按 {data.get('daily_minutes')} 分钟重排：保留 {len(data.get('plan') or [])} 项，"
                f"暂缓 {len(dropped)} 项（院校/资料/作答记录未清空）")

    if name == "list_sources" and isinstance(data, dict):
        coverage = data.get("coverage") or []
        return (f"共 {data.get('total_records')} 条官方记录，覆盖 {len(coverage)} 所院校：" +
                "；".join(f"{c.get('school_id')}({c.get('record_count')} 条)" for c in coverage))

    if name == "list_subjects" and isinstance(data, dict):
        return f"标准科目表共 {len(data.get('subjects') or [])} 个科目（version={data.get('version')}）"

    return "已完成"
