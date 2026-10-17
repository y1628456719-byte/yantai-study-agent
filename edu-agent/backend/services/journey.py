"""准备旅程的持久摘要、演示历史与基于证据的下一步安排。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from decimal import Decimal, InvalidOperation

from ..core.errors import conflict, invalid_argument, session_not_found
from . import session_store

TRACKS = ("direction", "reading", "transfer")
LABELS = {
    "direction": "条件方向判断",
    "reading": "必要条件表述",
    "transfer": "条件链迁移",
}
FEEDBACK_LABELS = {
    "unclear": "我还没理解",
    "too_easy": "这组题太简单",
    "guessed": "我是猜的",
    "new_example": "我想换个例子",
}
_CN_DIGITS = "零〇一二两三四五六七八九"
_CN_NUMERAL_CHARS = _CN_DIGITS + "十百千万"
_NUMBER_TOKEN = rf"(?:\d+(?:\.\d+)?|[{_CN_NUMERAL_CHARS}]+|半)"
_WHOLE_HOURS_TOKEN = rf"(?:\d+|[{_CN_NUMERAL_CHARS}]+)"
_TIME_TOKEN_CHARS = rf"0-9A-Za-z_+.\-−－负負{_CN_NUMERAL_CHARS}半个点~～"
_TIME_BOUNDARY = _TIME_TOKEN_CHARS
_TIME_SUFFIX = rf"0-9A-Za-z_{_CN_NUMERAL_CHARS}半几多左右上下以上以下以内不到余来到至后"
_TIME_UNIT = r"(?:个小时|小时|分钟|分)"
_TIME_EXPRESSION_RE = re.compile(
    rf"(?<![{_TIME_BOUNDARY}])"
    rf"(?:(?P<mixed>{_WHOLE_HOURS_TOKEN}(?:个)?半小时)"
    rf"|(?P<hour_and_half>{_WHOLE_HOURS_TOKEN}(?:个)?小时半)"
    rf"|(?P<number>{_NUMBER_TOKEN})\s*(?P<unit>{_TIME_UNIT}))"
    rf"(?![{_TIME_SUFFIX}])")
_TIME_CANDIDATE_RE = re.compile(
    rf"(?<![{_TIME_TOKEN_CHARS}])"
    rf"[{_TIME_TOKEN_CHARS}]+(?:\s+[{_TIME_TOKEN_CHARS}]+)*\s*{_TIME_UNIT}(?:半)?")
_TIME_MODIFIER = r"(?:半|几|多|左右|上下|以上|以下|以内|不到|余|来|到|至|后)"
_UNSUPPORTED_TIME_RE = re.compile(
    rf"(?<![{_TIME_BOUNDARY}])(?:"
    rf"{_WHOLE_HOURS_TOKEN}(?:个)?(?:半小时|小时半){_TIME_MODIFIER}"
    rf"|{_NUMBER_TOKEN}(?:个)?(?:几|多){_TIME_UNIT}"
    rf"|{_NUMBER_TOKEN}\s*{_TIME_UNIT}{_TIME_MODIFIER}"
    rf"|{_NUMBER_TOKEN}\s*(?:到|至|~|～|-)\s*{_NUMBER_TOKEN}\s*{_TIME_UNIT})")
_CN_DIGIT_VALUES = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _parse_chinese_integer(raw: str) -> int:
    """Parse a complete common Chinese integer (including 十/百/千/万)."""
    if raw and all(char in _CN_DIGIT_VALUES for char in raw):
        return int("".join(str(_CN_DIGIT_VALUES[char]) for char in raw))
    total = section = 0
    number = None
    last_small_unit = 10_000
    saw_wan = False
    previous_digit = None
    for char in raw:
        if char in _CN_DIGIT_VALUES:
            if previous_digit is not None and previous_digit not in "零〇":
                raise ValueError("中文数词数字位之间缺少位值单位")
            number = _CN_DIGIT_VALUES[char]
            previous_digit = char
        elif char in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            if unit >= last_small_unit:
                raise ValueError("中文数词位值顺序无效")
            if number == 0:
                raise ValueError("中文数词位值前不能是零")
            section += (number if number is not None else 1) * unit
            number = None
            last_small_unit = unit
            previous_digit = None
        elif char == "万":
            if saw_wan:
                raise ValueError("中文数词不能重复使用万位")
            prefix = section + (number or 0)
            total += (prefix or 1) * 10_000
            section = 0
            number = None
            last_small_unit = 10_000
            saw_wan = True
            previous_digit = None
        else:
            raise ValueError("中文数词包含不支持字符")
    return total + section + (number or 0)


def _number_value(raw: str) -> Decimal:
    if raw == "半":
        return Decimal("0.5")
    if any(char in _CN_NUMERAL_CHARS for char in raw):
        return Decimal(_parse_chinese_integer(raw))
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError("预算数字格式无效") from None


@lru_cache(maxsize=1)
def _question_bank() -> dict:
    root = Path(__file__).resolve().parents[2]
    questions = {}
    for relative in (
        "vendor/blindspot_lab/fixtures/logic_demo.json",
        "vendor/blindspot_lab/fixtures/logic_adaptive.json",
    ):
        data = json.loads((root / relative).read_text(encoding="utf-8"))
        for question in data.get("questions", []):
            item = copy.deepcopy(question)
            if "track" not in item:
                qid = item.get("question_id", "")
                item["track"] = "reading" if qid == "q_verify_reading_01" else "direction"
            questions[item["question_id"]] = item
    return questions


def parse_daily_minutes(goal: str) -> int | None:
    """Parse exactly one complete Arabic/Chinese daily-time expression.

    Examples: ``二十分钟`` -> 20, ``一百二十分钟`` -> 120,
    ``半小时`` -> 30, ``一个半小时``/``一小时半`` -> 90.
    Ambiguous, qualified, ranged, malformed, or truncated expressions fail
    closed instead of taking a numeric suffix.
    """
    text = goal or ""
    matches = list(_TIME_EXPRESSION_RE.finditer(text))
    valid_spans = {match.span() for match in matches}
    if any(match.span() not in valid_spans for match in _TIME_CANDIDATE_RE.finditer(text)):
        raise invalid_argument("目标中的每日时间数字不完整或不受支持，请写一个完整的正数时长。")
    unsupported = list(_UNSUPPORTED_TIME_RE.finditer(text))
    if any(match.span() not in valid_spans for match in unsupported):
        raise invalid_argument("目标中的每日时间表达不完整或不受支持，请写完整的分钟/小时数。")
    if len(matches) > 1:
        raise invalid_argument("目标中包含多个每日时间，请只写一个明确的分钟/小时数。")
    if not matches:
        return None
    match = matches[0]
    try:
        if match.group("mixed") is not None:
            hours = _number_value(match.group("mixed")[:-3].removesuffix("个"))
            minutes = hours * 60 + 30
        elif match.group("hour_and_half") is not None:
            raw = match.group("hour_and_half")[:-3].removesuffix("个")
            hours = _number_value(raw)
            minutes = hours * 60 + 30
        else:
            value = _number_value(match.group("number"))
            minutes = value * (60 if "小时" in match.group("unit") else 1)
    except ValueError as exc:
        raise invalid_argument(f"目标中的每日时间数词无效：{exc}。") from None
    if minutes < 1 or minutes > 1440 or minutes != minutes.to_integral_value():
        raise invalid_argument("目标中的每日时间须为 1 至 1440 分钟的整数。")
    return int(minutes)


def explicit_minutes(goal: str) -> int | None:
    """Backward-compatible internal name for the public parser helper."""
    return parse_daily_minutes(goal)


def _new_journey(goal: str) -> dict:
    return {"version": 1, "goal": goal, "records": [], "feedback_records": []}


def _append_answer(journey: dict, *, answer_id, question_id, choice, correct, origin,
                   track: str | None = None) -> None:
    if (not isinstance(answer_id, str) or not answer_id or not isinstance(question_id, str)
            or not isinstance(choice, str) or type(correct) is not bool or origin not in {"preset", "actual"}):
        return
    if any(item.get("answer_id") == answer_id for item in journey.get("records", [])):
        return
    question = _question_bank().get(question_id)
    known_track = question.get("track") if question else None
    if known_track and track and known_track != track:
        return
    if known_track is None and isinstance(question_id, str) and question_id.startswith("q_dyn_"):
        known_track = track
    if known_track not in TRACKS:
        return
    journey.setdefault("records", []).append({
        "answer_id": answer_id,
        "question_id": question_id,
        "track": known_track,
        "choice": choice.strip().upper(),
        "correct": correct,
        "origin": origin,
    })


def _seed_records() -> list[dict]:
    """生成清楚标注为预置的历史；这些记录不会进入盲点引擎快照。"""
    bank = _question_bank()
    right_counts = {"direction": 6, "reading": 5, "transfer": 3}
    records = []
    for track in TRACKS:
        candidates = sorted([q for q in bank.values() if q.get("track") == track],
                            key=lambda item: item["question_id"])
        for index in range(8):
            question = candidates[index % len(candidates)]
            correct = index < right_counts[track]
            choice = question["correct_option_id"]
            if not correct:
                choice = next(option["id"] for option in question["options"]
                              if option["id"] != question["correct_option_id"])
            records.append({
                "answer_id": f"preset-{track}-{index + 1:02d}",
                "question_id": question["question_id"],
                "track": track,
                "choice": choice,
                "correct": correct,
                "origin": "preset",
            })
    return records


def _import_existing_snapshot(journey: dict, session: dict) -> None:
    """Carry prior answered evidence into a newly created journey after host validation."""
    raw = ((session.get("learning_state") or {}).get("blindspot"))
    if not isinstance(raw, str) or not raw.strip():
        return
    try:
        from vendor.blindspot_lab.examples.adaptive_host import AdaptiveHost
        host = AdaptiveHost.restore_json(raw)
        dynamic_tracks = {item.get("question_id"): item.get("track")
                          for item in host.context.get("dynamic_bank", [])
                          if isinstance(item, dict)}
        state = (host.context.get("learning_state") or {})
        if state.get("schema_version") == "lab.v2":
            answers = state.get("core", {}).get("answers", []) + state.get("followups", [])
        else:
            answers = state.get("answers", [])
        for answer in answers:
            _append_answer(journey, answer_id=answer.get("answer_id"),
                           question_id=answer.get("question_id"), choice=answer.get("answer"),
                           correct=answer.get("is_correct"), origin="actual",
                           track=dynamic_tracks.get(answer.get("question_id")))
        feedbacks = state.get("feedback_records", []) if state.get("schema_version") == "lab.v2" else []
        known = {item.get("id") for item in journey.get("feedback_records", [])}
        track_by_id = {item["answer_id"]: item["track"] for item in journey.get("records", [])}
        for feedback in feedbacks:
            fid = feedback.get("id")
            kind = feedback.get("kind")
            if not fid or fid in known or kind not in FEEDBACK_LABELS:
                continue
            journey.setdefault("feedback_records", []).append({
                "id": fid, "kind": kind,
                "target_answer_id": feedback.get("target_answer_id"),
                "track": track_by_id.get(feedback.get("target_answer_id")),
            })
            known.add(fid)
    except Exception:
        # public_state validates the host snapshot before prepare; a missing old
        # snapshot is simply an empty history, while existing records stay intact.
        return


def record_public_answers(journey: dict, public_lab: dict | None) -> dict:
    """Append graded answers from the real public lab evidence, deduplicated by answer ID."""
    if not isinstance(journey, dict) or not isinstance(public_lab, dict):
        return journey
    for item in public_lab.get("evidence") or []:
        if not isinstance(item, dict) or item.get("type") != "answer":
            continue
        _append_answer(journey, answer_id=item.get("answer_id"),
                       question_id=item.get("question_id"), choice=item.get("answer"),
                       correct=item.get("is_correct"), origin="actual",
                       track=item.get("track"))
    return journey


def record_feedback(journey: dict, feedback: str, request_id: str) -> dict:
    if feedback not in FEEDBACK_LABELS:
        return journey
    records = journey.setdefault("records", [])
    latest = next((record for record in reversed(records) if record.get("origin") == "actual"), None)
    items = journey.setdefault("feedback_records", [])
    if any(item.get("id") == request_id for item in items):
        return journey
    items.append({"id": request_id, "kind": feedback,
                  "target_answer_id": latest.get("answer_id") if latest else None,
                  "track": latest.get("track") if latest else None})
    return journey


def _feedback_counts(journey: dict) -> dict[str, int]:
    counts = {track: 0 for track in TRACKS}
    for item in journey.get("feedback_records") or []:
        if item.get("track") in TRACKS and item.get("kind") in {"guessed", "unclear"}:
            counts[item["track"]] += 1
    return counts


def _goal_intent(goal: str) -> tuple[str | None, str | None]:
    """Map explicit wording to a requested practice track, never to a skill grade."""
    text = (goal or "").strip().lower()
    # A broad topic such as "充分与必要条件" mentions 必要, but does not
    # explicitly request necessary-condition wording practice.
    for phrase in ("充分条件与必要条件", "充分条件和必要条件", "充分与必要条件",
                   "充分和必要条件", "充分必要条件"):
        text = text.replace(phrase, " ")
    groups = (
        ("transfer", ("条件链", "多步", "传递"), "目标提到条件链、多步或迁移练习"),
        ("reading", ("只有", "必要", "必须", "除非"), "目标提到必要条件表述"),
        ("direction", ("方向", "箭头", "逆否"), "目标提到条件方向判断"),
    )
    for track, terms, reason in groups:
        if any(term in text for term in terms):
            return track, reason
    return None, None


def _summary(session: dict, journey: dict) -> dict:
    records = [item for item in journey.get("records", [])
               if item.get("track") in TRACKS and item.get("origin") in {"preset", "actual"}
               and type(item.get("correct")) is bool and isinstance(item.get("choice"), str)]
    profile = session.get("profile") or {}
    minutes = profile.get("daily_minutes")
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        minutes = 60
    stats = {track: {"total": 0, "correct": 0, "uncertain": 0} for track in TRACKS}
    for record in records:
        item = stats[record["track"]]
        item["total"] += 1
        item["correct"] += int(record["correct"])
    for track, count in _feedback_counts(journey).items():
        stats[track]["uncertain"] = count
    feedback_records = journey.get("feedback_records") or []
    last_feedback = feedback_records[-1] if feedback_records else None
    goal_track, goal_reason = _goal_intent(journey.get("goal", ""))
    feedback_focus = None
    if last_feedback:
        kind = last_feedback.get("kind")
        target_track = last_feedback.get("track")
        if kind == "too_easy":
            feedback_focus = "transfer"
        elif kind == "new_example":
            feedback_focus = target_track if target_track in TRACKS else "reading"
        elif kind == "unclear":
            feedback_focus = target_track if target_track in TRACKS else "reading"
        elif kind == "guessed":
            feedback_focus = target_track if target_track in TRACKS else "direction"

    def status_of(track):
        item = stats[track]
        if item["uncertain"]:
            return "needs_check"
        if not item["total"]:
            return "pending"
        if item["total"] >= 5 and item["correct"] / item["total"] >= 0.8:
            return "showing_strength"
        if item["correct"] < item["total"]:
            return "needs_practice"
        return "provisional"

    skills = [{"id": track, "label": LABELS[track], "total": stats[track]["total"],
               "correct": stats[track]["correct"], "status": status_of(track)}
              for track in TRACKS]

    sampled = [track for track in TRACKS if stats[track]["total"]]
    if sampled:
        # Error and uncertainty rates determine priority. Uncertain feedback can
        # override a correct guess but never changes that answer's grade.
        def focus_score(track):
            item = stats[track]
            if not item["total"]:
                return -1.0
            return ((item["total"] - item["correct"]) + 1.5 * item["uncertain"] + 0.25) / (item["total"] + 0.5)
        ranked = sorted(TRACKS, key=lambda track: (-focus_score(track), TRACKS.index(track)))
        if goal_track in TRACKS:
            ranked.remove(goal_track)
            ranked.insert(0, goal_track)
        if feedback_focus in TRACKS:
            ranked.remove(feedback_focus)
            ranked.insert(0, feedback_focus)
        focus_track = (feedback_focus if feedback_focus in TRACKS else
                       goal_track if goal_track in TRACKS else ranked[0])
        focus_item = stats[focus_track]
        if last_feedback and last_feedback.get("kind") == "too_easy":
            reason = "你反馈当前练习太简单，下一步提高到条件链迁移；这不等于长期掌握。"
        elif last_feedback and last_feedback.get("kind") == "new_example":
            reason = f"你请求换个例子，下一步用新情境核对{LABELS[focus_track]}。"
        elif last_feedback and last_feedback.get("kind") in {"unclear", "guessed"}:
            label = "未理解" if last_feedback["kind"] == "unclear" else "猜测"
            reason = f"你反馈这次作答属于{label}，下一步核对{LABELS[focus_track]}；反馈不会改判分。"
        elif goal_track in TRACKS:
            reason = f"{goal_reason}，先练习{LABELS[focus_track]}；这是自报意图，不是掌握证据。"
        elif focus_item["uncertain"]:
            reason = (f"{LABELS[focus_track]}有 {focus_item['uncertain']} 条猜测或未理解的自报，"
                      "即使作答正确也需要再核对。")
        elif focus_item["total"] - focus_item["correct"]:
            reason = (f"{LABELS[focus_track]}记录中答错 {focus_item['total'] - focus_item['correct']} / "
                      f"{focus_item['total']} 条，下一步优先练习；这只是当前样本。")
        else:
            reason = f"目前的记录中，{LABELS[focus_track]}最需要新题确认；记录有限，不据此判断长期掌握。"
    else:
        ranked = list(TRACKS)
        if goal_track in TRACKS:
            ranked.remove(goal_track)
            ranked.insert(0, goal_track)
        if feedback_focus in TRACKS:
            ranked.remove(feedback_focus)
            ranked.insert(0, feedback_focus)
        focus_track = (feedback_focus if feedback_focus in TRACKS else
                       goal_track if goal_track in TRACKS else "direction")
        reason = ("你反馈当前练习太简单，下一步提高到条件链迁移；这不等于长期掌握。"
                  if last_feedback and last_feedback.get("kind") == "too_easy" else
                  f"你请求换个例子，下一步先用新情境检查{LABELS[focus_track]}。"
                  if last_feedback and last_feedback.get("kind") == "new_example" else
                  f"你反馈这次作答属于{'未理解' if last_feedback.get('kind') == 'unclear' else '猜测'}，下一步核对{LABELS[focus_track]}；反馈不会改判分。"
                  if last_feedback and last_feedback.get("kind") in {"unclear", "guessed"} else
                  f"{goal_reason}，先练习{LABELS[focus_track]}；这是自报意图，不是掌握证据。"
                  if goal_track in TRACKS else
                  "还没有作答记录，先从基础方向题收集当前基线。")

    counts = {origin: sum(1 for item in records if item["origin"] == origin)
              for origin in ("preset", "actual")}
    if counts["preset"] and counts["actual"]:
        source_label = "预置演示档案与本次真实作答"
    elif counts["preset"]:
        source_label = f"预置演示档案（{counts['preset']} 条）"
    elif counts["actual"]:
        source_label = "本次真实作答记录"
    else:
        source_label = "尚无作答记录"

    selected = ranked[:min(len(TRACKS), minutes)]
    weights = {track: max(1, len(TRACKS) + 2 - index) for index, track in enumerate(selected)}
    weight_total = sum(weights.values())
    allocations = {track: minutes * weights[track] // weight_total for track in selected}
    remainder = minutes - sum(allocations.values())
    for track in sorted(selected, key=lambda item: (-(minutes * weights[item] % weight_total), selected.index(item)))[:remainder]:
        allocations[track] += 1
    plan = []
    for track in selected:
        allocated = allocations[track]
        if allocated <= 0:
            continue
        item = stats[track]
        if last_feedback and last_feedback.get("kind") == "too_easy" and track == "transfer":
            detail = "你反馈当前题目太简单，下一步提高到条件链迁移，不把答对等同于掌握。"
        elif last_feedback and last_feedback.get("kind") == "new_example" and track == focus_track:
            detail = "你请求换个例子，下一轮换新情境再核对。"
        elif last_feedback and last_feedback.get("kind") in {"unclear", "guessed"} and track == focus_track:
            detail = "你反馈这次作答属于未理解或猜测，保留原判分并安排新题核对。"
        elif item["uncertain"]:
            detail = f"有 {item['uncertain']} 条猜测或未理解反馈，正确答案也要再次核对。"
        elif item["total"]:
            detail = f"当前记录 {item['total']} 条、答对 {item['correct']} 条；用新题继续检查。"
        else:
            detail = "当前还没有该方向作答，先保留少量时间收集证据。"
        plan.append({"track": track, "task": f"练习：{LABELS[track]}", "minutes": allocated,
                     "reason": f"{detail} 本计划按每日 {minutes} 分钟预算安排。"})

    feedback_text = FEEDBACK_LABELS.get(last_feedback.get("kind")) if last_feedback else None
    if last_feedback and last_feedback.get("kind") in {"guessed", "unclear"}:
        next_step = "对这次作答再做一道未见题核对；猜测或未理解的自报会保留，不会把答对等同于掌握。"
    elif last_feedback and last_feedback.get("kind") == "too_easy":
        next_step = "按反馈提高难度，加入条件链迁移题；即使答对也只说明当前样本表现。"
    elif last_feedback and last_feedback.get("kind") == "new_example":
        next_step = "换一个新情境再核对当前方向，保留已有判分，不据单次答对判断长期掌握。"
    elif records:
        latest_actual = next((item for item in reversed(records) if item["origin"] == "actual"), None)
        if latest_actual and not latest_actual["correct"]:
            next_step = f"先按 {LABELS[latest_actual['track']]} 的薄弱点复习，再用新题核对。"
        else:
            next_step = "继续完成盲点实验室的三题闭环，再用新情境检查本次表现；不据少量题目判断长期掌握。"
    else:
        next_step = "进入盲点实验室完成三题诊断，再用新情境核对本次表现。"

    public_records = [{"track": item["track"], "choice": item["choice"],
                       "grade": "correct" if item["correct"] else "incorrect",
                       "origin": item["origin"]} for item in records]
    return {
        "goal": journey.get("goal", ""),
        "daily_minutes": minutes,
        "can_return": bool(journey.get("return_session_id")),
        "seed_count": counts["preset"],
        "actual_count": counts["actual"],
        "source_label": source_label,
        "skills": skills,
        "focus": {"track": focus_track, "label": LABELS[focus_track], "reason": reason},
        "records": public_records,
        "plan": plan,
        "feedback_text": feedback_text,
        "next_step": next_step,
    }


def public_summary(session: dict) -> dict | None:
    learning = session.get("learning_state") or {}
    journey = learning.get("journey") if isinstance(learning, dict) else None
    if not isinstance(journey, dict):
        return None
    return _summary(session, journey)


def preferred_track(learning_state: dict | None) -> str | None:
    journey = (learning_state or {}).get("journey")
    if not isinstance(journey, dict):
        return None
    summary = _summary({"profile": {}}, journey)
    track = (summary.get("focus") or {}).get("track")
    return track if track in TRACKS else None


def _refresh_journey(journey: dict, profile: dict) -> dict:
    summary = _summary({"profile": profile}, journey)
    out = copy.deepcopy(journey)
    out["summary"] = summary
    return out


def _prepare_fingerprint(goal: str, use_demo: bool, expected_revision: int) -> str:
    return hashlib.sha256(json.dumps(
        ["journey.prepare", goal, use_demo, expected_revision],
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def cached_prepare(*, session_id: str, expected_revision: int, request_id: str,
                   goal: str, use_demo: bool) -> dict | None:
    """Read the exact cached prepare result, including after the cookie switched to a demo SID."""
    return session_store.cached_lab_request(
        session_id, request_id, _prepare_fingerprint(goal, use_demo, expected_revision))


def _return_fingerprint(expected_revision: int) -> str:
    return hashlib.sha256(json.dumps(
        ["journey.return", expected_revision], ensure_ascii=False,
        sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def cached_return(*, session_id: str, expected_revision: int, request_id: str) -> dict | None:
    """Read an idempotent return result from the current isolated demo."""
    return session_store.cached_lab_request(
        session_id, request_id, _return_fingerprint(expected_revision))


async def prepare(*, session_id: str, expected_revision: int, request_id: str,
                  goal: str, use_demo: bool, base_state: dict) -> dict:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise invalid_argument("session_id 须为 1 至 128 个字符。")
    if type(expected_revision) is not int or expected_revision < 0:
        raise invalid_argument("expected_revision 须为非负整数。")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise invalid_argument("request_id 须为 1 至 128 个字符。")
    if not isinstance(goal, str) or len(goal) > 2000:
        raise invalid_argument("goal 须为不超过 2000 个字符的文本。")
    if type(use_demo) is not bool:
        raise invalid_argument("use_demo 须为布尔值。")
    if not session_store.exists(session_id):
        raise session_not_found(session_id)
    try:
        fingerprint = _prepare_fingerprint(goal, use_demo, expected_revision)
    except (TypeError, ValueError):
        raise invalid_argument("请求参数必须是有效 JSON 值。") from None
    cached = session_store.cached_lab_request(session_id, request_id, fingerprint)
    if cached is not None:
        return cached

    session = session_store.snapshot(session_id)
    base = session["revision"]
    if expected_revision != base:
        raise conflict("页面状态已更新，请读取最新状态后重试；本次准备未保存。")
    profile = copy.deepcopy(session.get("profile") or {})
    minutes = explicit_minutes(goal)
    existing_journey = ((session.get("learning_state") or {}).get("journey") or {})
    isolated_demo = bool(session.get("is_demo") and isinstance(existing_journey, dict)
                         and existing_journey.get("return_session_id"))
    if use_demo and not isolated_demo:
        if minutes is not None:
            profile["daily_minutes"] = minutes
        demo_session_id = session_store.new_id("s_")
        demo_journey = _new_journey(goal)
        demo_journey["return_session_id"] = session_id
        demo_journey["records"].extend(_seed_records())
        demo_journey = _refresh_journey(demo_journey, profile)
        demo_learning = {"journey": demo_journey}
        demo_candidate = {"session_id": demo_session_id, "revision": 1,
                          "profile": profile, "learning_state": demo_learning,
                          "is_demo": True}
        demo_summary = _summary(demo_candidate, demo_journey)
        state = copy.deepcopy(base_state)
        state.update(session_id=demo_session_id, revision=1,
                     session=session_store.public_session(demo_candidate),
                     journey=demo_summary, lab_state=None, plan=None)
        return session_store.commit_journey_demo(
            session_id, demo_session_id, request_id, fingerprint, base,
            profile=profile, learning_state=demo_learning, result={"state": state})

    fields = {"learning_state": copy.deepcopy(session.get("learning_state") or {})}
    if minutes is not None:
        profile["daily_minutes"] = minutes
        fields["profile"] = profile

    learning = fields["learning_state"]
    journey = learning.get("journey")
    if not isinstance(journey, dict):
        journey = _new_journey(goal)
        _import_existing_snapshot(journey, session)
        learning["journey"] = journey
    else:
        journey = copy.deepcopy(journey)
        journey["goal"] = goal
        learning["journey"] = journey
    if use_demo and not any(item.get("origin") == "preset" for item in journey.get("records", [])):
        journey.setdefault("records", []).extend(_seed_records())
    journey = _refresh_journey(journey, profile)
    learning["journey"] = journey
    fields["learning_state"] = learning

    # Preview the exact same deterministic time synchronization that the
    # atomic commit will apply, so a changed budget is reflected in this
    # request's cached public lab response as well as the persisted snapshot.
    old_blindspot = (session.get("learning_state") or {}).get("blindspot")
    if "profile" in fields:
        fields = session_store._sync_minutes(session_id, fields)
    learning = fields["learning_state"]
    profile = fields.get("profile", profile)

    candidate = {**session, "profile": profile, "learning_state": learning,
                 "revision": base + 1}
    summary = _summary(candidate, journey)
    state = copy.deepcopy(base_state)
    state.update(revision=base + 1, session_id=session_id,
                 session=session_store.public_session(candidate), journey=summary)
    if old_blindspot != learning.get("blindspot") and learning.get("blindspot"):
        from ..lab_adapter import LabError, build_context, get_adapter
        try:
            refreshed = await get_adapter().public_read(build_context(
                candidate, material=None, diagnosis_mode="self_assessment"))
        except LabError as exc:
            from .lab_service import raise_lab
            raise_lab(exc)
        state.update(lab_state=refreshed["lab_state"], plan=refreshed["plan"],
                     runtime_mode=refreshed["runtime_mode"])
    return session_store.commit_lab_request(session_id, request_id, fingerprint, base, fields,
                                            {"state": state})


async def return_to_origin(*, session_id: str, expected_revision: int,
                           request_id: str) -> dict:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise invalid_argument("session_id 须为 1 至 128 个字符。")
    if type(expected_revision) is not int or expected_revision < 0:
        raise invalid_argument("expected_revision 须为非负整数。")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise invalid_argument("request_id 须为 1 至 128 个字符。")
    fingerprint = _return_fingerprint(expected_revision)
    cached = session_store.cached_lab_request(session_id, request_id, fingerprint)
    if cached is not None:
        return cached

    demo = session_store.snapshot(session_id)
    if demo["revision"] != expected_revision:
        raise conflict("演示档案状态已更新，请读取最新状态后重试。")
    learning = demo.get("learning_state") or {}
    journey = learning.get("journey") if isinstance(learning, dict) else None
    origin_session_id = journey.get("return_session_id") if isinstance(journey, dict) else None
    if not demo.get("is_demo") or not isinstance(origin_session_id, str) or not origin_session_id:
        raise conflict("当前档案没有可返回的来源。")

    from . import lab_service
    for _attempt in range(3):
        state = await lab_service.public_state(origin_session_id)
        result = session_store.commit_journey_return(
            session_id, request_id, fingerprint, expected_revision,
            origin_session_id, state["revision"], {"state": state})
        if result is not None:
            return result
    raise conflict("来源档案持续更新，请读取最新状态后重试。")
