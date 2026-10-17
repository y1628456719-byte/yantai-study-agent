"""Bounded, fail-closed generation of follow-up logic questions.

The model composes formulas in a tiny P/Q/R DSL. This module parses and
renders that contract, verifies a unique answer by exhaustive truth table,
and binds the selected topic to an exact span in reviewed local demo material.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
from pathlib import Path

from .content import digest, load_bank
from .errors import LabError


MAX_DYNAMIC_QUESTIONS = 2
MAX_DYNAMIC_BYTES = 24_000
MAX_PREMISES = 3
MAX_OPTIONS = 4
MAX_FORMULA_LENGTH = 80
MAX_FORMULA_OPERATORS = 6
MAX_FORMULA_DEPTH = 5
_LABEL_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]{2,12}\Z")


# These cards constrain subject matter and evidence; the formulas themselves
# are supplied by the model and are not fixed templates.
TOPICS = {
    "direction": {"track": "direction", "label": "条件方向判断",
                  "source_id": "logic_demo_direction", "policy": "entails_implication"},
    "necessary": {"track": "reading", "label": "必要条件表述",
                  "source_id": "logic_demo_necessary", "policy": "entails_implication"},
    "contrapositive": {"track": "direction", "label": "逆否关系",
                        "source_id": "logic_extension_contrapositive", "policy": "contrapositive"},
    "chain": {"track": "transfer", "label": "条件链迁移",
              "source_id": "logic_extension_chain", "policy": "chain"},
    "and_or": {"track": "transfer", "label": "且与或",
               "source_id": "logic_extension_and_or", "policy": "and_or_equivalence"},
}

TRACK_TOPICS = {"direction": ("direction", "contrapositive"),
                "reading": ("necessary",), "transfer": ("chain", "and_or")}


def _source_catalog() -> dict:
    base = load_bank()
    extension_path = Path(__file__).resolve().parents[1] / "fixtures" / "logic_extensions.json"
    extension = json.loads(extension_path.read_text(encoding="utf-8"))
    quotes = {
        "logic_demo_direction": (base["material"], base["quotes"][0]),
        "logic_demo_necessary": (base["material"], "P是Q的充分条件，Q是P的必要条件。"),
    }
    for definition in extension["topics"].values():
        quotes[definition["source_id"]] = (extension["material"], definition["quote"])
    out = {}
    for source_id, (material, quote) in quotes.items():
        start = material["text"].find(quote)
        if start < 0 or material["text"].find(quote, start + 1) >= 0:
            raise LabError("EVIDENCE_INVALID", "审核规则卡引文不在材料中或定位不唯一。")
        out[source_id] = {"material_id": material["material_id"], "title": material["title"],
                          "quote": quote, "start": start, "end": start + len(quote),
                          "line": material["text"][:start].count("\n") + 1,
                          "source_digest": digest(material["text"])}
    return out


SOURCES = _source_catalog()


def _tokens(expression: str) -> list[str]:
    if not isinstance(expression, str) or not expression or len(expression) > MAX_FORMULA_LENGTH:
        raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式长度或类型无效。")
    tokens = re.findall(r"->|[A-Za-z]+|[()]", expression)
    if not tokens or "".join(tokens) != re.sub(r"\s+", "", expression):
        raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式包含不支持的字符。")
    if sum(token in {"not", "and", "or", "->"} for token in tokens) > MAX_FORMULA_OPERATORS:
        raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式运算层数超过上限。")
    return tokens


class _FormulaParser:
    def __init__(self, expression: str):
        self.tokens = _tokens(expression)
        self.index = 0
        self.paren_depth = 0

    def _take(self, token: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index] == token:
            self.index += 1
            return True
        return False

    def parse(self):
        result = self._implication()
        if self.index != len(self.tokens):
            raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式语法无效。")
        if _ast_depth(result) > MAX_FORMULA_DEPTH:
            raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式嵌套超过上限。")
        return result

    def _implication(self):
        left = self._or()
        if self._take("->"):
            return ("implies", left, self._implication())
        return left

    def _or(self):
        result = self._and()
        while self._take("or"):
            result = ("or", result, self._and())
        return result

    def _and(self):
        result = self._unary()
        while self._take("and"):
            result = ("and", result, self._unary())
        return result

    def _unary(self):
        if self._take("not"):
            return ("not", self._unary())
        if self._take("("):
            self.paren_depth += 1
            if self.paren_depth > MAX_FORMULA_DEPTH:
                raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式嵌套超过上限。")
            result = self._implication()
            if not self._take(")"):
                raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式缺少右括号。")
            self.paren_depth -= 1
            return result
        if self.index >= len(self.tokens) or self.tokens[self.index] not in {"P", "Q", "R"}:
            raise LabError("MODEL_OUTPUT_INVALID", "逻辑表达式含未知命题变量。")
        atom = self.tokens[self.index]
        self.index += 1
        return ("atom", atom)


def _ast_depth(ast) -> int:
    if ast[0] == "atom":
        return 1
    return 1 + max(_ast_depth(child) for child in ast[1:])


def _walk(ast):
    yield ast
    for child in ast[1:]:
        if isinstance(child, tuple):
            yield from _walk(child)


def _evaluate(ast, values: dict[str, bool]) -> bool:
    op = ast[0]
    if op == "atom":
        return values[ast[1]]
    if op == "not":
        return not _evaluate(ast[1], values)
    if op == "and":
        return _evaluate(ast[1], values) and _evaluate(ast[2], values)
    if op == "or":
        return _evaluate(ast[1], values) or _evaluate(ast[2], values)
    return not _evaluate(ast[1], values) or _evaluate(ast[2], values)


def _truth_vector(ast):
    rows = [dict(zip(("P", "Q", "R"), bits))
            for bits in itertools.product((False, True), repeat=3)]
    return tuple(_evaluate(ast, row) for row in rows)


def _simple_implication(ast):
    if (ast[0] == "implies" and ast[1][0] == "atom" and ast[2][0] == "atom"):
        return ast[1][1], ast[2][1]
    return None


def _contrapositive(ast):
    if ast[0] != "implies":
        return None
    # For A -> B, not B -> not A remains equivalent when A/B are bounded DSL expressions.
    return ("implies", ("not", ast[2]), ("not", ast[1]))


def _entailed(premise_asts, candidate_ast) -> bool:
    rows = [dict(zip(("P", "Q", "R"), bits))
            for bits in itertools.product((False, True), repeat=3)]
    valid = [row for row in rows if all(_evaluate(ast, row) for ast in premise_asts)]
    return bool(valid) and all(_evaluate(candidate_ast, row) for row in valid)


def _validate_topic_policy(topic_id, mode, premise_asts, target_ast, option_asts):
    policy = TOPICS[topic_id]["policy"]
    implication_nodes = [node for ast in premise_asts for node in _walk(ast)
                         if node[0] == "implies"]
    if policy == "entails_implication":
        if mode != "entails" or not premise_asts or not implication_nodes:
            raise LabError("MODEL_OUTPUT_INVALID", "该主题须提供含条件关系的可推导前提。")
    elif policy == "contrapositive":
        contrapositives = [_contrapositive(ast) for ast in premise_asts]
        contrapositives = [ast for ast in contrapositives if ast is not None]
        if mode != "entails" or not contrapositives or not any(
                _truth_vector(option) == _truth_vector(contrapositive)
                for _, option in option_asts for contrapositive in contrapositives):
            raise LabError("MODEL_OUTPUT_INVALID", "逆否题须包含条件前提及其逻辑等价逆否项。")
    elif policy == "chain":
        simple = {_simple_implication(ast) for ast in premise_asts}
        simple.discard(None)
        chains = [(left_a, right_b) for left_a, mid_a in simple for mid_b, right_b in simple
                  if mid_a == mid_b and left_a != right_b]
        if (mode != "entails" or len(premise_asts) < 2 or not chains
                or not any(_truth_vector(option) == _truth_vector(
                    _FormulaParser(f"{left}->{right}").parse())
                    for _, option in option_asts for left, right in chains)):
            raise LabError("MODEL_OUTPUT_INVALID", "条件链题须含两步相接的条件及其传递结论。")
    elif policy == "and_or_equivalence":
        if (mode != "equivalent" or premise_asts or target_ast is None
                or not any(node[0] in {"and", "or"} for node in _walk(target_ast))):
            raise LabError("MODEL_OUTPUT_INVALID", "且或题须比较含合取或析取的等价表达。")


def unique_solution(mode: str, premises: list[str], options: list[dict], target: str | None = None) -> str:
    """Return the sole entailed/equivalent option ID, or reject the candidate."""
    if (not isinstance(premises, list) or len(premises) > MAX_PREMISES
            or not isinstance(options, list) or len(options) != MAX_OPTIONS):
        raise LabError("MODEL_OUTPUT_INVALID", "前提或选项数量超过受限题型上限。")
    premise_asts = [_FormulaParser(value).parse() for value in premises]
    option_asts = [(item["id"], _FormulaParser(item["formula"]).parse()) for item in options]
    if [item[0] for item in option_asts] != ["A", "B", "C", "D"]:
        raise LabError("MODEL_OUTPUT_INVALID", "四个选项须按A至D排列。")
    target_ast = _FormulaParser(target).parse() if target else None
    if mode == "entails":
        if target_ast is not None:
            raise LabError("MODEL_OUTPUT_INVALID", "推导题不得附带等价比较目标。")
        matches = [option_id for option_id, ast in option_asts if _entailed(premise_asts, ast)]
    elif mode == "equivalent" and target_ast is not None and not premise_asts:
        expected = _truth_vector(target_ast)
        matches = [option_id for option_id, ast in option_asts if _truth_vector(ast) == expected]
    else:
        raise LabError("MODEL_OUTPUT_INVALID", "候选题型与逻辑规则不匹配。")
    if len(matches) != 1:
        raise LabError("MODEL_OUTPUT_INVALID", "程序未能验证出唯一正确选项。")
    return matches[0]


def _schema() -> dict:
    formula = {"type": "string", "minLength": 1, "maxLength": MAX_FORMULA_LENGTH}
    return {"type": "object", "additionalProperties": False, "required": ["candidates"],
            "properties": {"candidates": {"type": "array", "minItems": len(TOPICS),
                "maxItems": len(TOPICS), "items": {"type": "object", "additionalProperties": False,
                "required": ["topic_id", "labels", "mode", "premises", "target_formula", "options", "citation"],
                "properties": {
                    "topic_id": {"type": "string", "enum": list(TOPICS)},
                    "labels": {"type": "object", "additionalProperties": False,
                        "required": ["P", "Q", "R"], "properties": {
                            key: {"type": "string", "minLength": 2, "maxLength": 12}
                            for key in ("P", "Q", "R")}},
                    "mode": {"type": "string", "enum": ["entails", "equivalent"]},
                    "premises": {"type": "array", "items": formula, "maxItems": MAX_PREMISES},
                    "target_formula": {"type": ["string", "null"], "maxLength": MAX_FORMULA_LENGTH},
                    "options": {"type": "array", "minItems": MAX_OPTIONS, "maxItems": MAX_OPTIONS,
                        "items": {"type": "object", "additionalProperties": False,
                            "required": ["id", "formula"], "properties": {
                                "id": {"type": "string", "enum": ["A", "B", "C", "D"]},
                                "formula": formula}}},
                    "citation": {"type": "object", "additionalProperties": False,
                        "required": ["source_id", "quote"], "properties": {
                            "source_id": {"type": "string"},
                            "quote": {"type": "string", "minLength": 1, "maxLength": 300}}},
                }}}}}


def _prompt() -> list[dict]:
    catalog = []
    for topic_id, spec in TOPICS.items():
        source = SOURCES[spec["source_id"]]
        catalog.append({"topic_id": topic_id, "topic_label": spec["label"],
                        "policy": spec["policy"],
                        "citation": {"source_id": spec["source_id"], "quote": source["quote"]}})
    system = (
        "你只为程序验证生成受限逻辑题候选，输入是自编演示规则卡。只输出符合JSON Schema的JSON，"
        "必须为每个topic_id恰好返回一项。你需要实际组合题目，不要回声固定题型模板："
        "可自行选择或组合前提、目标表达式和四个选项中的P/Q/R命题公式。"
        "公式只允许变量P/Q/R、not、and、or、->和括号；每题最多3条前提、恰好A-D四项，"
        "每个公式不超过80字符、运算符最多6个、嵌套深度最多5。"
        "entails题按前提判断必然推出项；equivalent题须给target_formula且无前提。"
        "所有题必须恰有一个真值表正确选项。contrapositive题须有A->B前提及语义等价的not B->not A选项；"
        "chain题须有原子条件A->B、B->C和A->C选项；and_or题须用含and/or的等价目标。"
        "labels只用于命题变量的简短中文解释。citation.source_id与quote必须逐字保留对应规则卡，"
        "不得生成自由文本题干、答案、评分、解释或额外字段。不得加入用户学习者信息。")
    return [{"role": "system", "content": system},
            {"role": "user", "content": "受限主题与可引用原文：" + json.dumps(catalog, ensure_ascii=False)}]


def _render_formula(value: str) -> str:
    tokens = _tokens(value)
    symbols = {"not": "¬", "and": "∧", "or": "∨", "->": "→"}
    return "".join(symbols.get(token, token) for token in tokens)


def _atoms(formulas):
    result = set()
    for formula in formulas:
        result.update(token for token in _tokens(formula) if token in {"P", "Q", "R"})
    return result


def _render_question(topic_id: str, labels: dict, contract: dict) -> tuple[str, list[dict]]:
    formulas = list(contract["premises"]) + [item["formula"] for item in contract["options"]]
    if contract["target"]:
        formulas.append(contract["target"])
    atoms = "；".join(f'{atom}表示“{labels[atom]}”' for atom in ("P", "Q", "R") if atom in _atoms(formulas))
    pieces = [f"【{TOPICS[topic_id]['label']}】根据以下条件判断。", atoms]
    if contract["premises"]:
        pieces.append("给定规则：" + "；".join(_render_formula(value) for value in contract["premises"]) + "。")
    if contract["mode"] == "equivalent":
        pieces.append(f"目标表达：{_render_formula(contract['target'])}。哪一项与目标在所有情况下等价？")
    else:
        pieces.append("哪一项是给定规则必然推出的？")
    stem = "".join(piece for piece in pieces if piece)
    options = [{"id": item["id"], "text": _render_formula(item["formula"])}
               for item in contract["options"]]
    return stem, options


def _validate_candidate(candidate: dict, topic_id: str) -> dict:
    topic = TOPICS[topic_id]
    required = {"topic_id", "labels", "mode", "premises", "target_formula", "options", "citation"}
    if not isinstance(candidate, dict) or set(candidate) != required or candidate["topic_id"] != topic_id:
        raise LabError("MODEL_OUTPUT_INVALID", "模型候选字段或题目范围不符合约束。")
    labels = candidate["labels"]
    if (not isinstance(labels, dict) or set(labels) != {"P", "Q", "R"}
            or any(not isinstance(value, str) or not _LABEL_RE.fullmatch(value.strip())
                   for value in labels.values())
            or any(value != value.strip() for value in labels.values())
            or len({value.strip() for value in labels.values()}) != 3):
        raise LabError("MODEL_OUTPUT_INVALID", "命题标签须为三个不同的简短名词短语。")
    premises = candidate["premises"]
    options = candidate["options"]
    target = candidate["target_formula"]
    if (not isinstance(premises, list) or len(premises) > MAX_PREMISES
            or any(not isinstance(value, str) for value in premises)
            or not isinstance(options, list) or len(options) != MAX_OPTIONS
            or any(not isinstance(item, dict) or set(item) != {"id", "formula"}
                   or not isinstance(item["formula"], str) for item in options)
            or (target is not None and not isinstance(target, str))):
        raise LabError("MODEL_OUTPUT_INVALID", "模型修改了受限题目结构或数量。")
    if [item["id"] for item in options] != ["A", "B", "C", "D"]:
        raise LabError("MODEL_OUTPUT_INVALID", "四个选项须按A至D排列。")
    premise_asts = [_FormulaParser(value).parse() for value in premises]
    option_asts = [(item["id"], _FormulaParser(item["formula"]).parse()) for item in options]
    target_ast = _FormulaParser(target).parse() if target is not None else None
    _validate_topic_policy(topic_id, candidate["mode"], premise_asts, target_ast, option_asts)
    correct_id = unique_solution(candidate["mode"], premises, options, target)
    citation = candidate["citation"]
    source = SOURCES[topic["source_id"]]
    if (not isinstance(citation, dict) or set(citation) != {"source_id", "quote"}
            or citation["source_id"] != topic["source_id"] or citation["quote"] != source["quote"]):
        raise LabError("EVIDENCE_INVALID", "模型引用与审核材料原文不一致。")
    contract = {"mode": candidate["mode"], "premises": list(premises), "target": target,
                "options": copy.deepcopy(options)}
    stem, public_options = _render_question(topic_id, labels, contract)
    qid_hash = digest({"topic": topic_id, "labels": labels, "contract": contract,
                       "source": source["source_digest"]})[:16]
    source_ref = {**source, "source_id": topic["source_id"]}
    return {"question_id": f"q_dyn_{topic_id}_{qid_hash}", "role": "verification",
            "track": topic["track"], "topic_id": topic_id, "topic_label": topic["label"],
            "source_label": "AI生成·规则推理题",
            "labels": {key: value.strip() for key, value in labels.items()},
            "stem": stem, "options": public_options, "correct_option_id": correct_id,
            "rationale": f"程序按受限真值表核对：只有{correct_id}项符合题干规则；这只说明本题的逻辑结果。",
            "difficulty": 2 if topic_id in {"chain", "and_or", "contrapositive"} else 1,
            "citation": source_ref, "generated_by": "AI Ping DeepSeek-V4.1-Flash / max",
            "logical_contract": contract}


def generate_questions(model_call, *, session_id: str, request_id: str,
                       preferred_track: str | None, existing_questions: list[dict],
                       count: int = 1, timeout_seconds: int = 60) -> list[dict]:
    """Generate a topic-neutral bundle, then locally select questions by focus.

    The remote model receives only synthetic rule cards. Per-user focus/history
    stays local and is used only after the bounded candidate bundle returns.
    """
    if (not isinstance(session_id, str) or not isinstance(request_id, str)
            or type(count) is not int or not 1 <= count <= MAX_DYNAMIC_QUESTIONS):
        raise LabError("INVALID_INPUT", "动态题生成上下文无效。")
    raw = model_call(_prompt(), _schema(), timeout_seconds)
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_DYNAMIC_BYTES:
            raise LabError("MODEL_OUTPUT_INVALID", "动态题候选超过保存长度限制。")
        try:
            bundle = json.loads(raw)
        except (ValueError, RecursionError):
            raise LabError("MODEL_OUTPUT_INVALID", "动态题候选不是有效JSON。") from None
    else:
        bundle = copy.deepcopy(raw)
    if not isinstance(bundle, dict) or set(bundle) != {"candidates"}:
        raise LabError("MODEL_OUTPUT_INVALID", "模型必须返回受限候选集合。")
    candidates = bundle["candidates"]
    if not isinstance(candidates, list) or len(candidates) != len(TOPICS):
        raise LabError("MODEL_OUTPUT_INVALID", "候选集合数量不符合审核上限。")
    validated = {}
    for item in candidates:
        topic_id = item.get("topic_id") if isinstance(item, dict) else None
        if topic_id not in TOPICS or topic_id in validated:
            raise LabError("MODEL_OUTPUT_INVALID", "候选主题缺失或重复。")
        validated[topic_id] = _validate_candidate(item, topic_id)
    if set(validated) != set(TOPICS):
        raise LabError("MODEL_OUTPUT_INVALID", "候选集合未覆盖全部受限题型。")

    prior_topics = {item.get("topic_id") for item in existing_questions if isinstance(item, dict)}
    track = preferred_track if preferred_track in TRACK_TOPICS else "direction"
    ranked_topics = list(TRACK_TOPICS[track])
    ranked_topics.extend(topic for topic in TOPICS if topic not in ranked_topics)
    choices = [topic for topic in ranked_topics if topic not in prior_topics]
    if len(choices) < count:
        choices.extend(topic for topic in ranked_topics if topic not in choices)
    selected = []
    for index, topic_id in enumerate(choices[:count], 1):
        question = copy.deepcopy(validated[topic_id])
        question["question_id"] += "_" + digest([session_id, request_id, index])[:8]
        selected.append(question)
    return selected


def generate_question(model_call, **kwargs) -> dict:
    """Compatibility wrapper for callers needing one generated item."""
    return generate_questions(model_call, count=1, **kwargs)[0]


def validate_bank(questions: list[dict], bank_digest: str) -> bool:
    if not isinstance(questions, list) or len(questions) > MAX_DYNAMIC_QUESTIONS:
        return False
    if digest(questions) != bank_digest:
        return False
    try:
        seen = set()
        for question in questions:
            if (not isinstance(question, dict) or question.get("question_id") in seen
                    or question.get("topic_id") not in TOPICS):
                return False
            topic_id = question["topic_id"]
            topic = TOPICS[topic_id]
            source_expected = SOURCES[topic["source_id"]]
            source = question.get("citation")
            contract = question.get("logical_contract")
            labels = question.get("labels")
            if (not isinstance(labels, dict) or set(labels) != {"P", "Q", "R"}
                    or any(not isinstance(value, str) or not _LABEL_RE.fullmatch(value)
                           for value in labels.values())
                    or len(set(labels.values())) != 3
                    or not isinstance(contract, dict)
                    or set(contract) != {"mode", "premises", "target", "options"}):
                return False
            premises, options, target = contract["premises"], contract["options"], contract["target"]
            if not isinstance(premises, list) or any(not isinstance(x, str) for x in premises):
                return False
            if not isinstance(options, list) or any(not isinstance(x, dict) for x in options):
                return False
            premise_asts = [_FormulaParser(value).parse() for value in premises]
            option_asts = [(item["id"], _FormulaParser(item["formula"]).parse()) for item in options]
            target_ast = _FormulaParser(target).parse() if target is not None else None
            _validate_topic_policy(topic_id, contract["mode"], premise_asts, target_ast, option_asts)
            expected_answer = unique_solution(contract["mode"], premises, options, target)
            expected_stem, expected_options = _render_question(topic_id, labels, contract)
            if (question.get("track") != topic["track"]
                    or question.get("topic_label") != topic["label"]
                    or question.get("role") != "verification"
                    or question.get("options") != expected_options
                    or question.get("stem") != expected_stem
                    or question.get("correct_option_id") != expected_answer
                    or question.get("source_label") != "AI生成·规则推理题"
                    or question.get("generated_by") != "AI Ping DeepSeek-V4.1-Flash / max"
                    or not isinstance(source, dict)
                    or source.get("source_id") != topic["source_id"]
                    or source.get("quote") != source_expected["quote"]
                    or source.get("material_id") != source_expected["material_id"]
                    or source.get("title") != source_expected["title"]
                    or source.get("start") != source_expected["start"]
                    or source.get("end") != source_expected["end"]
                    or source.get("line") != source_expected["line"]
                    or source.get("source_digest") != source_expected["source_digest"]):
                return False
            seen.add(question["question_id"])
        return True
    except (LabError, KeyError, TypeError, ValueError, IndexError, RecursionError):
        return False


def evidence_for(question: dict) -> dict:
    source = question["citation"]
    return {"id": "dm:" + question["question_id"], "type": "rule_source",
            "question_id": question["question_id"], "material_id": source["material_id"],
            "material_title": source["title"], "source_id": source["source_id"],
            "quote": source["quote"],
            "locator": {"start": source["start"], "end": source["end"], "line": source["line"]}}
