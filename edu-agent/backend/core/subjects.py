"""N31 标准科目表：加载、归一化、匹配（SPEC §9.3 / §7.13 / §10.3）。

这是全流程的**公共词典**，也是创新点 1 的中间件：
    院校 exam_subjects  ──┐
                          ├─→ subject_code ─→ ability_flags
    lab ability_profile ──┘

关键设计（v1.1 修正）：
- 院校自命题常常是"**2 门课组合**"（吉大 806 = 模电+数电；848 = 电路+模电），
  所以除了 SPEC 要求的 ``prerequisites[]``，本模块还消费 ``composed_of[]``：
  **诊断结果可以回填到细分 topic，院校侧仍按整门科目匹配**。
- 匹配失败**必须显式暴露**，不能静默失配（否则创新点 1 会无声地空掉）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SUBJECTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "subjects.json"

_EXAM_CODE_RE = re.compile(r"(?<!\d)(\d{3})(?!\d)")


@lru_cache(maxsize=1)
def _table() -> dict:
    with open(SUBJECTS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def subjects() -> tuple[dict, ...]:
    return tuple(_table()["subjects"])


@lru_cache(maxsize=1)
def by_code() -> dict:
    return {s["subject_code"]: s for s in subjects()}


@lru_cache(maxsize=1)
def _alias_index() -> dict:
    """别名/名称（归一化后）-> subject_code。先到先得，标准名称优先。"""
    index: dict[str, str] = {}
    for s in subjects():
        for key in [s["name"], *s.get("aliases", [])]:
            norm = _norm(key)
            if norm and norm not in index:
                index[norm] = s["subject_code"]
    return index


@lru_cache(maxsize=1)
def _exam_code_index() -> dict:
    index: dict[str, str] = {}
    for s in subjects():
        for code in s.get("exam_codes", []):
            index.setdefault(str(code), s["subject_code"])
    return index


def _norm(text: str) -> str:
    """去掉空白/全角括号/常见修饰，便于匹配。

    容错：调用方可能传进来非字符串（例如某个抓取脚本把考试科目写成了
    `{"code": "101", "name": "思想政治理论"}`）。这里统一转成文本，
    而不是让 `.strip()` 抛 AttributeError 把整个接口打成 500。
    """
    if not isinstance(text, str):
        if isinstance(text, dict):
            parts = [text.get("code"), text.get("name"), text.get("text")]
            text = " ".join(str(p) for p in parts if p)
        elif text is None:
            text = ""
        else:
            text = str(text)
    text = text.strip()
    text = text.replace("（", "(").replace("）", ")").replace("　", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]", "", text)
    text = re.sub(r"^\d{3}", "", text)  # 去掉科目代码前缀
    return text.strip("。.、,，:：")


def categories() -> dict:
    return _table().get("categories", {})


def version() -> str:
    return _table().get("version", "")


def match(raw: str) -> tuple[str | None, str]:
    """把一段考试科目文本归一到 ``subject_code``。

    返回 ``(subject_code|None, method)``，``method`` ∈
    ``exact`` / ``alias`` / ``exam_code`` / ``contains`` / ``unmatched``。

    **匹配不到时返回 None 而不是猜**——调用方必须把这一类显式暴露给前端。
    """
    if not raw:
        return None, "unmatched"

    index = _alias_index()
    norm = _norm(raw)
    if not norm:
        return None, "unmatched"

    # 1) 名称/别名精确
    if norm in index:
        return index[norm], "exact"

    # 2) 院校自命题代码（如 "806"）
    for code in _EXAM_CODE_RE.findall(raw):
        if code in _exam_code_index():
            return _exam_code_index()[code], "exam_code"

    # 3) 包含匹配：取最长命中，避免"数学（一）"被"数学"抢走
    best: tuple[int, str] | None = None
    for alias, code in index.items():
        if len(alias) >= 2 and alias in norm:
            if best is None or len(alias) > best[0]:
                best = (len(alias), code)
    if best:
        return best[1], "contains"

    return None, "unmatched"


def match_many(raws: list[str]) -> tuple[list[str], list[str]]:
    """批量归一化，返回 (命中的 subject_code 去重列表, 未匹配的原文列表)。"""
    codes: list[str] = []
    unmatched: list[str] = []
    for raw in raws or []:
        code, _method = match(raw)
        if code and code not in codes:
            codes.append(code)
        elif not code:
            unmatched.append(raw)
    return codes, unmatched


def prerequisite_closure(code: str, _seen: set | None = None) -> list[str]:
    """递归展开前置知识（跨考场景用于算前置知识缺口）。"""
    _seen = _seen or set()
    if code in _seen:
        return []
    _seen.add(code)
    out: list[str] = []
    for pre in by_code().get(code, {}).get("prerequisites", []):
        if pre not in _seen:
            out.append(pre)
            out.extend(prerequisite_closure(pre, _seen))
    return out


def composed_of(code: str) -> list[str]:
    """组合科目的细分课程（如 806 → 模电 + 数电）。"""
    return list(by_code().get(code, {}).get("composed_of", []))


def display_name(code: str) -> str:
    subject = by_code().get(code)
    return subject["name"] if subject else code


def exam_codes(code: str) -> list[str]:
    return list(by_code().get(code, {}).get("exam_codes", []))


def label_for(code: str) -> str:
    """给前端/理由文本用的展示名，带自命题代码时写成『806 模拟与数字电子技术』。"""
    subject = by_code().get(code)
    if not subject:
        return code
    codes = subject.get("exam_codes") or []
    self_codes = [c for c in codes if c not in ("101", "201", "202", "203", "204", "301", "302", "303", "408")]
    if self_codes:
        return f"{self_codes[0]} {subject['name']}"
    return subject["name"]


def as_meta() -> dict:
    """GET /api/v1/meta/subjects 的响应体。"""
    return {
        "version": version(),
        "categories": categories(),
        "subjects": [
            {
                "subject_code": s["subject_code"],
                "name": s["name"],
                "aliases": s.get("aliases", []),
                "prerequisites": s.get("prerequisites", []),
                "composed_of": s.get("composed_of", []),
                "category": s.get("category", ""),
                "exam_codes": s.get("exam_codes", []),
            }
            for s in subjects()
        ],
    }


def ability_flags(ability_profile: list[dict], exam_subject_codes: list[str]) -> list[dict]:
    """N27 的核心：把能力画像与院校考试科目对齐，生成 ability_flags。

    - 院校科目命中用户薄弱项 → ``direction="hit"``（风险上调）
    - 用户强项命中院校科目 → ``direction="solid"``（可作优势说明）
    - 组合科目（806）走 ``composed_of`` 双向展开，避免整门科目无法对齐
    """
    if not ability_profile or not exam_subject_codes:
        return []

    # 院校科目展开成"可对齐集合"（整门 + 细分）
    exam_set: dict[str, str] = {}
    for code in exam_subject_codes:
        exam_set[code] = code
        for sub in composed_of(code):
            exam_set.setdefault(sub, code)

    flags: list[dict] = []
    for item in ability_profile:
        topic = item.get("topic")
        if not topic:
            continue
        matched_as = None
        if topic in exam_set:
            matched_as = topic
        else:
            for sub in composed_of(topic):
                if sub in exam_set:
                    matched_as = sub
                    break
        if not matched_as:
            continue
        status = item.get("status", "tentative")
        if status == "weak":
            direction = "hit"
        elif status == "solid":
            direction = "solid"
        else:
            continue
        flags.append({
            "topic": topic,
            "topic_name": display_name(topic),
            "matched_exam_subject": exam_set[matched_as],
            "matched_exam_subject_name": display_name(exam_set[matched_as]),
            "status": status,
            "evidence_count": int(item.get("evidence_count", 0)),
            "basis": item.get("basis", "measured"),
            "direction": direction,
        })
    return flags
