"""决策编排层：Agent 自主决定"下一步做什么"。

三层分工（docs/AGENT_ALIGNMENT.md §2）：
* **决策编排层（本文件）**：调哪个工具、要不要追问、要不要先做小测再重新推荐。
* **计算判定层（services/）**：冲/稳/保分档、排序、维度取舍 —— 硬规则，可复现可审计。
* **表述层（services/modelsvc.py）**：理由文本 —— 只把已定的事实说清楚，禁止新增数字。

两种决策来源：
* 真实模型（`MOCK_LLM=0`）：`llm.generate_json` + `DECISION_SCHEMA`，每轮一个决策。
* 离线（`MOCK_LLM=1`）：`_offline_decide` 确定性策略，产出与模型同形状的 trace，
  响应里显式标 `is_demo=true / generated_by="rules(offline)"`（N23 离线演示模式，**不是**静默降级）。
"""
from __future__ import annotations

import re
import time

from . import config, llm, prompts, tools
from .core.errors import AppError
from .services import session_store

# ------------------------------------------------------------------ 决策 schema

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": ["tool", "ask", "final"]},
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "reply": {"type": "string"},
    },
    "required": ["thought", "action", "tool", "args", "reply"],
}

MAX_REPLY_CHARS = 1200

# ------------------------------------------------------------------ 提示词里的城市/科目词典

_REGION_WORDS = (
    "长春", "吉林", "哈尔滨", "黑龙江", "沈阳", "辽宁", "大连", "北京", "上海", "天津",
    "南京", "杭州", "广州", "深圳", "武汉", "西安", "成都", "重庆", "合肥", "济南", "青岛",
)
_MAJOR_WORDS = (
    "信息与通信工程", "通信工程", "电子科学与技术", "电子信息", "集成电路",
    "控制科学与工程", "控制工程", "计算机科学与技术", "计算机技术", "软件工程",
    "人工智能", "网络空间安全", "仪器科学与技术", "电气工程", "数学", "物理学",
)
_WEAK_WORDS = (
    ("数学", "MATH_CALC"), ("高数", "MATH_CALC"), ("微积分", "MATH_CALC"),
    ("线代", "MATH_LINALG"), ("线性代数", "MATH_LINALG"), ("概率", "MATH_PROB"),
    ("英语", "ENG1"), ("政治", "POLITICS"),
    ("模电", "ANALOG_CIRCUIT"), ("数电", "DIGITAL_CIRCUIT"), ("电路", "CIRCUIT_ANALYSIS"),
    ("信号与系统", "SIG_SYS"), ("通信原理", "COMM_THEORY"),
)
_SCHOOL_WORDS = (
    ("吉林大学", "jlu"), ("吉大", "jlu"),
    ("哈尔滨工程大学", "heu"), ("哈工程", "heu"), ("哈船院", "heu"),
    ("东北大学", "neu"), ("东大", "neu"),
)
_ANSWER_RE = re.compile(r"^\s*[（(]?([ABCDabcd])[)）.。、]?\s*$")


# ------------------------------------------------------------------ 提示词/状态块


def _system_prompt() -> str:
    return (prompts.AGENT_SYSTEM
            .replace("__CATALOG__", tools.render_catalog())
            .replace("__MAX_STEPS__", str(config.MAX_AGENT_STEPS)))


def _state_block(*, session, message, history, observations) -> str:
    lines = []
    if history:
        lines.append("【最近几轮对话】")
        for item in history[-6:]:
            role = "同学" if item.get("role") == "user" else "你"
            content = str(item.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"- {role}：{content[:300]}")
        lines.append("")
    lines.append(f"【同学最新一句话】{message.strip()}")
    lines.append("")
    lines.append("【会话状态】")
    if not session:
        lines.append("还没有会话（session_id 为空）。")
    else:
        profile = session.get("profile") or {}
        onboarding = session.get("onboarding") or {}
        materials = session.get("materials") or []
        learning = session.get("learning_state") or {}
        ability = learning.get("ability_profile") or []
        lab = learning.get("lab") or {}
        recos = session.get("recommendations") or {}
        items = recos.get("recommendations") or []
        lines.append(f"- session_id={session.get('session_id')} revision={session.get('revision')}")
        lines.append(f"- 档案：{profile}")
        lines.append(f"- 阶段：{onboarding.get('phase')}（{onboarding.get('phase_name')}）"
                     f" confidence={onboarding.get('phase_confidence')}"
                     f" 缺关键信息={onboarding.get('missing_critical')}")
        lines.append(f"- 已选院校：{session.get('selected_school_id') or '未选'}"
                     f"；候选清单：{session.get('shortlist') or '未设'}")
        lines.append(f"- 资料：{len(materials)} 份")
        lines.append(f"- 能力画像：{ability or '暂无实测证据'}")
        if lab:
            lines.append(f"- 实验室：stage={lab.get('stage')} 待作答={lab.get('current_question_id')}"
                         f" next_action={lab.get('next_action')}")
        lines.append(f"- 已有推荐：{len(items)} 所"
                     + (f"（ability_flags={len(recos.get('ability_flags') or [])} 条）" if items else ""))
    if observations:
        lines.append("")
        lines.append("【你本轮已经做过的动作与观察结果】")
        for obs in observations:
            lines.append(f"- {obs}")
    lines.append("")
    lines.append("现在输出你的下一个决策（只输出 JSON）。")
    return "\n".join(lines)


# ------------------------------------------------------------------ 离线确定性策略


def _hints(message: str) -> dict:
    text = message or ""
    hints: dict = {}

    year = re.search(r"(20\d{2})\s*年?", text)
    if year:
        hints["exam_year"] = int(year.group(1))

    regions = [w for w in _REGION_WORDS if w in text]
    if regions:
        hints["preferred_regions"] = regions

    majors = [w for w in _MAJOR_WORDS if w in text]
    if majors:
        hints["major"] = max(majors, key=len)

    minutes = re.search(r"每天[^\d]{0,6}(\d+(?:\.\d+)?)\s*(分钟|个小时|小时|h)", text)
    if minutes:
        value = float(minutes.group(1))
        hints["daily_minutes"] = int(value * 60) if minutes.group(2) != "分钟" else int(value)

    score = re.search(r"(?:目标|考|至少)\s*(\d{3})\s*分", text)
    if score:
        hints["target_score"] = int(score.group(1))

    if "在职" in text or "边工作" in text or "工作" in text:
        hints["study_status"] = "边工作边考"
    elif "同等学力" in text:
        hints["study_status"] = "同等学力"
    elif "已毕业" in text or "往届" in text:
        hints["study_status"] = "已毕业"
    elif any(w in text for w in ("在校", "大三", "大四", "应届", "本科在读", "大二")):
        hints["study_status"] = "在校"

    if "跨考" in text or "跨专业" in text:
        hints["is_cross_major"] = True

    english = None
    for level in ("六级", "四级", "雅思", "托福", "专八", "专四"):
        if level in text:
            english = level
            break
    if english:
        hints["english_level"] = english

    return hints


def _weak_topics(message: str) -> list[str]:
    text = message or ""
    topics: list[str] = []
    for word, code in _WEAK_WORDS:
        if word in text and code not in topics:
            topics.append(code)
    return topics


def _mentions_weak(message: str) -> bool:
    text = message or ""
    return any(w in text for w in ("弱", "不好", "一般", "没底", "心里没数", "基础差",
                                   "不太行", "跟不上", "吃力", "拉分", "没学"))


def _school_in(message: str) -> str | None:
    text = message or ""
    for word, sid in _SCHOOL_WORDS:
        if word in text:
            return sid
    return None


def _offline_decide(*, session, message, observations, used) -> dict:
    """离线（MOCK_LLM=1）时的确定性决策：规则明确、可预期，用于断网演示与回归测试。

    两条纪律（都是被真实跑测逼出来的）：
    * **同一个 (工具, 参数) 一轮里只调一次** —— 否则条件仍成立时会反复重调
      （曾出现一次回复里连调 8 次 recommend、8 次 select_school）。
    * **不替用户答题** —— 刚从实验室拿到题就把题抛给用户（ask），不接着自动作答。
    """
    hints = _hints(message)
    weak = _weak_topics(message)
    profile = dict((session or {}).get("profile") or {})
    learning = (session or {}).get("learning_state") or {}
    lab = learning.get("lab") or {}
    ability = learning.get("ability_profile") or []
    recos = (session or {}).get("recommendations") or {}
    sid = (session or {}).get("session_id")

    done = {(step.get("tool") or "", _args_key(step.get("args") or {}))
            for step in (used or [])}
    last_tool = (used or [{}])[-1].get("tool") if used else None

    def tried(tool: str, args: dict | None = None) -> bool:
        return (tool, _args_key(args or {})) in done

    def did(tool: str) -> bool:
        return any(name == tool for name, _ in done)

    # 0) 刚从实验室拿到题 → 把题抛给用户，不接着自动作答
    if last_tool in ("lab_start", "lab_answer"):
        pending = lab.get("current_question_id") or ((lab.get("asked") or [None])[-1])
        return {"thought": "题目已经拿到，交给同学作答——我不替他答题。",
                "action": "ask", "tool": "", "args": {},
                "reply": (f"这道题请你来答（question_id={pending}）：选一个选项回我"
                          "（比如直接回 A/B/C/D），我按你的作答更新能力画像。")}

    # 0.5) 刚答完"数据哪来的" → 给数据面的实话，顺带提醒还有题没答
    if last_tool == "list_sources":
        pending = lab.get("current_question_id")
        tail = f"（另外上一道题还没作答，question_id={pending}）" if pending else ""
        return {"thought": "数据来源已经查到，用工具返回的数字回答，不自己编。",
                "action": "final", "tool": "", "args": {},
                "reply": ("数据来自各校研招网的官方页面，详情页和 PDF 附件都留了原始链接；"
                          "凡是官方只给了图片的地方（比如东北大学近三年复试线）我都标注成人工转录，"
                          "没有推算过国家线，也没有拿非官方数字凑数。" + tail)}

    # 1) 用户明确表态：选校 / 改时间 / 问数据来源
    school = _school_in(message)
    if sid and school and any(w in message for w in ("就考", "确定", "选", "目标")):
        if session.get("selected_school_id") == school or tried("select_school"):
            return {"thought": f"目标院校已经是 {school}，不重复写决策时间线。",
                    "action": "final", "tool": "", "args": {},
                    "reply": f"目标院校已确认为 {school}，决策时间线里记下了。"
                             "要我按你的每日时间排一份计划吗？"}
        return {"thought": f"同学明确提到了目标院校，写入决策时间线（{school}）。",
                "action": "tool", "tool": "select_school",
                "args": {"school_id": school, "user_note": message.strip()[:80]}, "reply": ""}

    if sid and "daily_minutes" in hints and hints["daily_minutes"] != profile.get("daily_minutes"):
        if not session.get("selected_school_id"):
            return {"thought": "改计划要先有目标院校（后端会直接拒），先请同学定一所。",
                    "action": "ask", "tool": "", "args": {},
                    "reply": "改计划要先确认一所目标院校。你想定哪一所？（吉林大学 jlu / "
                             "哈尔滨工程大学 heu / 东北大学 neu，也可以说“就考 XX 大学”）"}
        if not tried("revise_plan", {"daily_minutes": hints["daily_minutes"]}):
            return {"thought": f"同学给了新的每日时间（{hints['daily_minutes']} 分钟），重排计划。",
                    "action": "tool", "tool": "revise_plan",
                    "args": {"daily_minutes": hints["daily_minutes"]}, "reply": ""}

    if any(w in message for w in ("数据哪来", "哪来的数据", "来源", "抓了多少", "官方")) \
            and not did("list_sources"):
        return {"thought": "同学在问数据来源，拉一次数据覆盖率给他看。",
                "action": "tool", "tool": "list_sources", "args": {}, "reply": ""}

    # 2) 还没有会话 → 建会话
    if not sid:
        return {"thought": "还没有会话，先按同学这句话里能确定的字段建档。",
                "action": "tool", "tool": "create_session",
                "args": {"profile": hints}, "reply": ""}

    # 3) 有新的档案信息 → 写进去
    fresh = {k: v for k, v in hints.items() if profile.get(k) != v}
    if fresh and not tried("update_profile", fresh):
        return {"thought": f"这句话里有新信息（{'、'.join(fresh)}），先写进档案再往下走。",
                "action": "tool", "tool": "update_profile", "args": fresh, "reply": ""}

    # 4) 有待作答的题：像答案就提交**一次**，否则把题再问一次
    pending = lab.get("current_question_id")
    if pending:
        answer = _ANSWER_RE.match(message)
        if answer and not did("lab_answer"):
            return {"thought": f"同学给出了选项 {answer.group(1).upper()}，提交给实验室批改。",
                    "action": "tool", "tool": "lab_answer",
                    "args": {"question_id": pending, "answer": answer.group(1).upper()}, "reply": ""}
        return {"thought": "还有一道题没作答，先把题再抛一次，不做别的动作。",
                "action": "ask", "tool": "", "args": {},
                "reply": f"上一道题还没作答（question_id={pending}）。选一个选项回我，我据此更新你的能力画像。"}

    # 5) 有新证据或档案关键项变了 → 重新推荐（硬规则重算，能力画像反哺择校）
    echo = recos.get("profile_echo") or {}
    relevant = ("exam_year", "major", "preferred_regions", "target_score")
    profile_changed = bool(recos) and any(
        not _same_value(profile.get(key), echo.get(key)) for key in relevant
    )
    new_evidence = len(ability) > len(recos.get("ability_profile_echo") or [])
    if (new_evidence or profile_changed) and not did("recommend"):
        why = "档案里的关键项变了" if profile_changed else "拿到了新的实测证据"
        return {"thought": f"{why}，重新推荐一次——风险档要跟着变。",
                "action": "tool", "tool": "recommend", "args": {}, "reply": ""}

    # 6) 信息实在不够 → 只问一个问题（不报错）
    if not profile.get("major") and not profile.get("preferred_regions"):
        return {"thought": "方向和地区都不知道，推荐会没有依据，先问最关键的一个。",
                "action": "ask", "tool": "", "args": {},
                "reply": "先问一个最关键的：你想考哪个**专业方向**？（比如信息与通信工程、控制科学与工程；"
                         "顺带说一句想去的城市也行）"}

    # 7) 还没推荐过 → 让硬规则出分档
    if not (recos.get("recommendations") or []) and not did("recommend"):
        return {"thought": "档案够用了，让硬规则给出冲/稳/保分档与淘汰名单。",
                "action": "tool", "tool": "recommend", "args": {}, "reply": ""}

    # 8) 提到某科弱 → 主动建议先做一轮小测（没有资料也能测）
    if not lab.get("stage") and weak and _mentions_weak(message) and not did("lab_start"):
        topic = weak[0]
        return {"thought": f"同学提到 {topic} 心里没底。先别急着下结论，做一轮小测拿实测证据。",
                "action": "tool", "tool": "lab_start",
                "args": {"material_id": None, "topic": topic, "mode": "self_assessment"},
                "reply": ""}

    # 9) 收尾
    items = recos.get("recommendations") or []
    tiers = "；".join(
        f"{it.get('school_name')}={(it.get('metrics') or {}).get('tier') or '未分档'}" for it in items
    )
    if not _enough_profile(profile):
        return {"thought": "该做的动作做完了，但档案还缺关键信息，先补一句再接下一步。",
                "action": "ask", "tool": "", "args": {},
                "reply": "还想再准一点：你是**哪一年考**、想考**什么方向**？（说一个也行）"}
    return {"thought": "这一轮该做的动作都做完了，给同学一个基于工具结果的收尾。",
            "action": "final", "tool": "", "args": {},
            "reply": (f"目前按你的档案筛出 {len(items)} 所候选（{tiers}），"
                      f"另有 {len(recos.get('excluded') or [])} 所进了淘汰名单（淘汰理由都写在里面）。"
                      "接下来你可以：① 告诉我每天能学多久，我按时间排计划；"
                      "② 说一门心里没底的科目，我做一轮小测拿实测证据再重新推荐。")}


def _enough_profile(profile: dict) -> bool:
    """档案是否已经够"讲得出道理"（方向/地区/年份至少有一个明确的）。"""
    return bool(profile.get("major") or profile.get("preferred_regions") or profile.get("exam_year"))


def _same_value(left, right) -> bool:
    """把 None / "" / [] / {} 视为同一种"没填"，避免空值形态差异导致无限重推荐。"""
    empties = (None, "", [], {})
    if left in empties and right in empties:
        return True
    return left == right


def _args_key(args: dict) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(args)


# ------------------------------------------------------------------ 主循环


async def run_agent(app, *, message: str, session_id: str | None = None,
                    history: list[dict] | None = None) -> dict:
    """跑一轮 Agent（可自主调用多个工具），返回 reply + trace。"""
    message = (message or "").strip()
    if not message:
        raise AppError("INVALID_ARGUMENT", "message 不能为空。")

    offline = bool(config.MOCK_LLM)
    trace: list[dict] = []
    observations: list[str] = []
    steps: list[dict] = []
    artifacts: dict = {}
    reply = ""
    terminated = "reply"

    for index in range(1, config.MAX_AGENT_STEPS + 1):
        session = _load_session(session_id)
        if offline:
            decision = _offline_decide(session=session, message=message,
                                       observations=observations, used=steps)
        else:
            decision = await _model_decide(session=session, message=message,
                                           history=history or [], observations=observations)

        action = (decision.get("action") or "").strip()
        thought = (decision.get("thought") or "").strip()
        tool_name = (decision.get("tool") or "").strip()
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}

        if action in ("ask", "final"):
            reply = (decision.get("reply") or "").strip()[:MAX_REPLY_CHARS]
            trace.append({"step": index, "thought": thought, "action": action,
                          "tool": None, "args": {}, "result_summary": None,
                          "ok": True, "error": None})
            break

        if action != "tool":
            reply = reply or "（决策层没有给出有效动作，本轮先停在这里。）"
            trace.append({"step": index, "thought": thought, "action": action or "unknown",
                          "tool": None, "args": {}, "result_summary": None,
                          "ok": False, "error": f"未知 action：{action}"})
            terminated = "invalid_decision"
            break

        started = time.perf_counter()
        try:
            data, summary = await tools.execute(tool_name, args, app=app, session_id=session_id)
        except tools.ToolError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            trace.append({"step": index, "thought": thought, "action": "tool", "tool": tool_name,
                          "args": args, "result_summary": None, "ok": False,
                          "error": str(exc), "code": exc.code, "latency_ms": elapsed})
            observations.append(f"{index}. 调用 {tool_name} 失败：{exc}（请改正参数或换一条路）")
            continue

        elapsed = int((time.perf_counter() - started) * 1000)
        new_sid = data.get("session_id") if isinstance(data, dict) else None
        if new_sid and (tool_name == "create_session" or not session_id):
            # 建档必须采用新 id；调用方给了不存在的 id 时也以新建的为准
            session_id = new_sid
        if tool_name in ("lab_start", "lab_answer", "lab_action") and isinstance(data, dict):
            artifacts["lab_state"] = data
        if tool_name == "recommend" and isinstance(data, dict):
            artifacts["recommendations_summary"] = {
                "count": len(data.get("recommendations") or []),
                "excluded": len(data.get("excluded") or []),
                "ability_flags": data.get("ability_flags") or [],
            }

        trace.append({"step": index, "thought": thought, "action": "tool", "tool": tool_name,
                      "args": args, "result_summary": summary, "ok": True, "error": None,
                      "latency_ms": elapsed})
        observations.append(f"{index}. {tool_name}({_short_args(args)}) → {summary}")
        steps.append({"step": index, "tool": tool_name, "args": args})
    else:
        terminated = "max_steps"
        reply = (f"我已经连续调用了 {config.MAX_AGENT_STEPS} 个工具，先停在这里。"
                 "你可以看看上面的决策轨迹，或者直接告诉我下一步想做什么。")

    if not reply:
        reply = "本轮没有产生结论，请再给我一句更具体的话。"

    session = _load_session(session_id)
    state_patch = {
        "revision": (session or {}).get("revision"),
        "phase": ((session or {}).get("onboarding") or {}).get("phase"),
        "selected_school_id": (session or {}).get("selected_school_id"),
    }

    return {
        "session_id": session_id,
        "reply": reply,
        "trace": trace,
        "steps": len(steps),
        "tools_used": [s["tool"] for s in steps],
        "state_patch": state_patch,
        "artifacts": artifacts,
        "terminated": terminated,
        "model_used": not offline,
        "generated_by": "rules(offline)" if offline else "model",
        "is_demo": offline,
    }


def _short_args(args: dict) -> str:
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        text = str(value)
        parts.append(f"{key}={text[:40]}" + ("…" if len(text) > 40 else ""))
    return "，".join(parts)


def _load_session(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    try:
        return session_store.snapshot(session_id)
    except AppError:
        return None


async def _model_decide(*, session, message: str, history: list[dict],
                        observations: list[str]) -> dict:
    """真实模型决策（失败会抛 LLMError，由路由转 503 MODEL_UNAVAILABLE，不静默降级）。"""
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _state_block(session=session, message=message,
                                                 history=history, observations=observations)},
    ]
    decision = await llm.generate_json(messages, DECISION_SCHEMA, temperature=0.2)
    if not isinstance(decision, dict):
        raise llm.LLMError("决策层没有返回 JSON 对象。")
    action = decision.get("action")
    if action not in ("tool", "ask", "final"):
        raise llm.LLMError(f"决策层的 action 非法：{action!r}")
    if action == "tool" and decision.get("tool") not in tools.TOOLS:
        raise llm.LLMError(f"决策层调用了不存在的工具：{decision.get('tool')!r}")
    return decision
