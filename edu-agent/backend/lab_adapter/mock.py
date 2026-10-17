"""离线实验室（SPEC §10.5）。

- 所有输出 `is_demo: true`
- **同时支持路径 A（有资料）与路径 B（无资料/自评）**
- 结构、字段与真实 adapter 完全一致，前端可以照此开发
- 真实调用失败**不会**切到这里（见 `__init__.get_adapter`）
"""
from __future__ import annotations

from ..core import subjects as subject_table
from .base import LabAdapter, empty_lab_state, normalize_result

SELF_ASSESS_PREFIX = "self_"
#: 错误归类（N18）
ERROR_CATEGORIES = ("概念混淆", "计算失误", "审题偏差")

#: 通用基础小测题库（无需资料，SPEC §10.4 路径 B）
#:
#: **每组题目必须与 topic 真实相关**：早期版本在专业课题库缺失时借用数学题，
#: 结果"模电诊断问了一道极限题"——这种串科目在演示现场是致命的，所以
#: `_quiz()` 现在只在**同类目**内回退，回退不到就诚实收尾。
_BANK: dict[str, list[dict]] = {
    "MATH_FOUNDATION": [
        {
            "question_id": "mf_frac_1",
            "stem": "计算：1/2 + 1/3 = ?",
            "options": [{"key": "A", "text": "2/5"}, {"key": "B", "text": "5/6"},
                        {"key": "C", "text": "1/6"}, {"key": "D", "text": "2/3"}],
            "answer": "B",
            "explanation": "先通分：1/2 = 3/6，1/3 = 2/6，相加得 5/6。",
            "feedback_wrong": "分数相加不能分子分母直接相加，必须先通分。",
            "category": "计算失误",
        },
        {
            "question_id": "mf_log_1",
            "stem": "若 2^x = 8，则 x = ?",
            "options": [{"key": "A", "text": "2"}, {"key": "B", "text": "3"},
                        {"key": "C", "text": "4"}, {"key": "D", "text": "1/3"}],
            "answer": "B",
            "explanation": "8 = 2^3，故 x = 3。",
            "feedback_wrong": "指数式与对数式互化：2^x = 8 ⇔ x = log₂8 = 3。",
            "category": "概念混淆",
        },
    ],
    "MATH_CALC": [
        {
            "question_id": "mc_lim_1",
            "stem": "当 x→0 时，lim (sin 3x)/x = ?",
            "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "3"},
                        {"key": "C", "text": "0"}, {"key": "D", "text": "不存在"}],
            "answer": "B",
            "explanation": "等价无穷小：sin u ~ u（u→0），故 (sin 3x)/x → 3x/x = 3。",
            "feedback_wrong": "把 sin 3x 直接当 3x 需要先确认 3x→0，这里漏掉了『等价无穷小替换的条件』。",
            "category": "概念混淆",
        },
        {
            "question_id": "mc_series_2",
            "stem": "级数 Σ(1/n^p)（n 从 1 到 ∞）收敛的条件是？",
            "options": [{"key": "A", "text": "p > 0"}, {"key": "B", "text": "p ≥ 0"},
                        {"key": "C", "text": "p > 1"}, {"key": "D", "text": "对任意 p 都收敛"}],
            "answer": "C",
            "explanation": "p 级数：p > 1 收敛，p ≤ 1 发散。",
            "feedback_wrong": "p 级数的判别阈值是 1，不是 0；这是级数收敛性里最容易记错的一条。",
            "category": "概念混淆",
        },
    ],
    "MATH_LINALG": [
        {
            "question_id": "ml_rank_1",
            "stem": "若 3 阶方阵 A 的行列式为 0，则 A 的秩最多是？",
            "options": [{"key": "A", "text": "3"}, {"key": "B", "text": "2"},
                        {"key": "C", "text": "1"}, {"key": "D", "text": "0"}],
            "answer": "B",
            "explanation": "|A| = 0 ⇒ A 不可逆 ⇒ r(A) < 3，即最多 2。",
            "feedback_wrong": "『不可逆』只说明秩小于阶数，并不代表秩一定是 1 或 0，这里把必要条件和更弱结论混在一起了。",
            "category": "概念混淆",
        },
        {
            "question_id": "ml_eigen_2",
            "stem": "n 阶矩阵 A 可对角化的充分条件是？",
            "options": [{"key": "A", "text": "A 有 n 个线性无关的特征向量"},
                        {"key": "B", "text": "A 的秩为 n"},
                        {"key": "C", "text": "A 所有特征值都相等"},
                        {"key": "D", "text": "A 是实对称矩阵的必要条件"}],
            "answer": "A",
            "explanation": "A 可对角化 ⇔ 有 n 个线性无关的特征向量；实对称矩阵是常见的充分情形之一。",
            "feedback_wrong": "可对角化的判据是特征向量的『个数与线性无关性』，与秩为 n（可逆）不是一回事。",
            "category": "概念混淆",
        },
    ],
    "ANALOG_CIRCUIT": [
        {
            "question_id": "ac_qpoint_1",
            "stem": "共射放大电路出现『饱和失真』，通常说明静态工作点 Q 的位置？",
            "options": [{"key": "A", "text": "Q 点偏低，靠近截止区"},
                        {"key": "B", "text": "Q 点偏高，靠近饱和区"},
                        {"key": "C", "text": "Q 点在负载线中点"},
                        {"key": "D", "text": "与 Q 点无关"}],
            "answer": "B",
            "explanation": "Q 点偏高时输入信号正半周使管子先进入饱和，输出出现底部削波，称为饱和失真。",
            "feedback_wrong": "饱和失真对应 Q 点偏高、截止失真对应 Q 点偏低；把两者的波形方向记反是这个知识点最常见的错误。",
            "category": "概念混淆",
        },
        {
            "question_id": "ac_gain_2",
            "stem": "共射放大电路接上旁路电容 C_E 后，交流电压增益会？",
            "options": [{"key": "A", "text": "变小"}, {"key": "B", "text": "变大"},
                        {"key": "C", "text": "不变"}, {"key": "D", "text": "变为 0"}],
            "answer": "B",
            "explanation": "C_E 把 R_E 交流短路，使 A_v ≈ -βR_L'/r_be，负反馈被旁路，故交流增益变大。",
            "feedback_wrong": "旁路电容的作用是交流旁路 R_E，直流工作点不变但交流增益提高。",
            "category": "概念混淆",
        },
    ],
    "DIGITAL_CIRCUIT": [
        {
            "question_id": "dc_kmap_1",
            "stem": "逻辑函数 F = A·B + A·B' 化简后等于？",
            "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"},
                        {"key": "C", "text": "A + B"}, {"key": "D", "text": "A·B"}],
            "answer": "A",
            "explanation": "A·B + A·B' = A·(B + B') = A·1 = A（吸收律/邻项合并）。",
            "feedback_wrong": "B + B' = 1 是化简的关键一步，这里没有用上互补律。",
            "category": "概念混淆",
        },
        {
            "question_id": "dc_ff_2",
            "stem": "JK 触发器在 J = K = 1 时的输出行为是？",
            "options": [{"key": "A", "text": "保持"}, {"key": "B", "text": "置 1"},
                        {"key": "C", "text": "翻转"}, {"key": "D", "text": "置 0"}],
            "answer": "C",
            "explanation": "J = K = 1 时 JK 触发器处于翻转（toggle）状态，每个时钟沿输出取反。",
            "feedback_wrong": "J = K = 1 是翻转而不是保持；保持对应 J = K = 0。",
            "category": "概念混淆",
        },
    ],
    "CIRCUIT_ANALYSIS": [
        {
            "question_id": "ca_kvl_1",
            "stem": "对理想电压源与电阻串联的单回路，回路电流由什么决定？",
            "options": [{"key": "A", "text": "电压源电压除以回路总电阻"},
                        {"key": "B", "text": "电压源的功率"},
                        {"key": "C", "text": "电阻的额定功率"},
                        {"key": "D", "text": "与电阻无关"}],
            "answer": "A",
            "explanation": "单回路中由 KVL 得 I = U/R_total。",
            "feedback_wrong": "KVL 建立的是电压关系，求电流必须除以回路总电阻。",
            "category": "概念混淆",
        },
    ],
    "COMM_THEORY": [
        {
            "question_id": "ct_mod_1",
            "stem": "2ASK 与 2PSK 相比，抗噪声性能更好的是？",
            "options": [{"key": "A", "text": "2ASK"}, {"key": "B", "text": "2PSK"},
                        {"key": "C", "text": "两者完全相同"}, {"key": "D", "text": "无法比较"}],
            "answer": "B",
            "explanation": "在相同误码率下 2PSK 所需信噪比比 2ASK 低约 3 dB，抗噪声性能更好。",
            "feedback_wrong": "相位调制把信息放在相位上，判决门限对幅度噪声不敏感，所以优于幅度调制。",
            "category": "概念混淆",
        },
    ],
    "SIG_SYS": [
        {
            "question_id": "ss_lti_1",
            "stem": "连续时间 LTI 系统稳定的充分必要条件是？",
            "options": [{"key": "A", "text": "系统函数的所有极点位于左半平面"},
                        {"key": "B", "text": "系统函数的所有零点位于左半平面"},
                        {"key": "C", "text": "冲激响应绝对可积即可，与极点无关"},
                        {"key": "D", "text": "系统阶数大于 2"}],
            "answer": "A",
            "explanation": "连续 LTI 系统稳定 ⇔ 全部极点位于 s 左半平面（因果系统）；一般情形还可用绝对可积判据。",
            "feedback_wrong": "稳定看极点不看零点，极点必须落在左半开平面。",
            "category": "概念混淆",
        },
    ],
    "ENG1": [
        {
            "question_id": "en_clause_1",
            "stem": "选出句子 \"The book, which was published in 2020, has been widely cited.\" 中 "
                    "which 引导的从句类型。",
            "options": [{"key": "A", "text": "限制性定语从句"}, {"key": "B", "text": "非限制性定语从句"},
                        {"key": "C", "text": "同位语从句"}, {"key": "D", "text": "状语从句"}],
            "answer": "B",
            "explanation": "逗号分隔 + which 指代先行词 the book，属非限制性定语从句。",
            "feedback_wrong": "有逗号分隔就不可能是限制性定语从句，这里被『修饰 book』的表象带偏了。",
            "category": "审题偏差",
        },
        {
            "question_id": "en_subj_2",
            "stem": "选出正确的虚拟语气形式：\"It is necessary that he ___ on time.\"",
            "options": [{"key": "A", "text": "is"}, {"key": "B", "text": "be"},
                        {"key": "C", "text": "was"}, {"key": "D", "text": "will be"}],
            "answer": "B",
            "explanation": "It is necessary that + 主语 + (should) do 型虚拟语气，动词用原形 be。",
            "feedback_wrong": "表『建议/要求/必要』的主语从句用 (should) + 动词原形。",
            "category": "概念混淆",
        },
    ],
    "POLITICS": [
        {
            "question_id": "po_practice_1",
            "stem": "『实践是检验真理的唯一标准』这一论断的哲学依据是？",
            "options": [{"key": "A", "text": "真理具有客观性"},
                        {"key": "B", "text": "实践具有直接现实性"},
                        {"key": "C", "text": "认识具有能动性"},
                        {"key": "D", "text": "真理具有相对性"}],
            "answer": "B",
            "explanation": "实践的直接现实性使其能把主观认识与客观实际相对照，因而成为检验真理的唯一标准。",
            "feedback_wrong": "关键在于实践的『直接现实性』这一特有品格，而不是真理的客观性。",
            "category": "概念混淆",
        },
    ],
    "PROG_BASIC": [
        {
            "question_id": "pb_loop_1",
            "stem": "下面循环执行后 i 的值是？ i = 0; while i < 5: i += 2",
            "options": [{"key": "A", "text": "4"}, {"key": "B", "text": "5"},
                        {"key": "C", "text": "6"}, {"key": "D", "text": "死循环"}],
            "answer": "C",
            "explanation": "i 依次为 0→2→4→6，6 不满足 i<5 时退出。",
            "feedback_wrong": "循环退出条件是在判断处生效的，i 会先自增到 6 才退出。",
            "category": "计算失误",
        },
    ],
}


def _default_topic(context: dict) -> str:
    """没有指定 topic 时，从**目标院校的真实考试科目**里挑一个可诊断的科目。

    早期版本读的是 `school["metrics"]`——但 `school_data.get()` 返回的院校对象里
    并没有 `metrics`（指标是按专业现算的），导致这里永远回退到 MATH_CALC。
    现在优先用会话里已缓存的推荐结果（含 exam_subject_codes），其次现场计算。
    """
    session_id = context.get("session_id")
    codes: list[str] = []
    if session_id:
        from ..services import session_store
        try:
            cached = (session_store.snapshot(session_id).get("recommendations") or {})
        except Exception:  # 会话不存在时不应影响诊断
            cached = {}
        selected = context.get("selected_school_id")
        for item in cached.get("recommendations") or []:
            if selected and item.get("school_id") != selected:
                continue
            codes.extend((item.get("metrics") or {}).get("exam_subject_codes") or [])
    if not codes:
        codes = _codes_from_school(
            context.get("selected_school_id") or
            (context.get("selected_school") or {}).get("school_id"),
            (context.get("profile") or {}).get("major"),
        )
    return _pick_diagnosable(codes) or "MATH_CALC"


#: 诊断优先序：**优先专业课**（诊断价值高、贴合目标院校），其次数学/英语，最后政治。
#: 早期版本直接取 exam_subject_codes 里第一个能出题的科目，结果一上来诊断政治——
#: 演示效果和产品说服力都很差。
_CATEGORY_PRIORITY = ("major_base", "math", "general", "public", "re_exam")


def _pick_diagnosable(codes: list[str]) -> str | None:
    """从考试科目里挑一个**有题可出**的科目码，按类目优先。"""
    ordered: list[tuple[int, str]] = []
    for code in codes:
        priority = _CATEGORY_PRIORITY.index(_category_of(code)) \
            if _category_of(code) in _CATEGORY_PRIORITY else len(_CATEGORY_PRIORITY)
        if code in _BANK:
            ordered.append((priority, code))
        for sub in subject_table.composed_of(code):  # 组合科目（如 806）拆到细分科目
            if sub in _BANK:
                ordered.append((priority, sub))
    if not ordered:
        return None
    ordered.sort(key=lambda item: item[0])
    return ordered[0][1]


def _codes_from_school(school_id: str | None, major_hint: str | None) -> list[str]:
    """现场计算目标专业的考试科目码（会话里还没有推荐结果时使用）。"""
    if not school_id:
        return []
    from ..services import school_data
    built = school_data.build_metrics(school_id, None, major_hint)
    return list((built.get("metrics") or {}).get("exam_subject_codes") or [])


def _category_of(code: str) -> str:
    subject = subject_table.by_code().get(code) or {}
    return subject.get("category", "")


def _quiz(topic: str, index: int) -> dict | None:
    """按 topic 取题；题库不足时**只在本类目内**回退。"""
    items = _BANK.get(topic) or []
    if index < len(items):
        return dict(items[index])

    category = _category_of(topic)
    for other, other_items in _BANK.items():
        if other == topic or _category_of(other) != category:
            continue
        if index < len(other_items):
            return dict(other_items[index])
    return None


def _public_question(question: dict, topic: str) -> dict:
    return {
        "question_id": question["question_id"],
        "stem": question["stem"],
        "options": question["options"],
        "topic": topic,
        "subject_code": topic,
    }


def _hypothesis(topic: str, status: str, reason: str) -> dict:
    return {
        "label": f"{subject_table.display_name(topic)}掌握情况",
        "subject_code": topic,
        "status": status,
        "reason": reason,
    }


class MockLabAdapter(LabAdapter):
    name = "mock"

    # ---------------------------------------------------------------- start
    def start(self, context: dict) -> dict:
        mode = context.get("diagnosis_mode") or ("material" if context.get("material") else "self_assessment")
        topic = context.get("topic") or _default_topic(context)
        if topic not in subject_table.by_code():
            matched, _method = subject_table.match(str(topic))
            topic = matched or "MATH_CALC"

        learning_state = dict(context.get("learning_state") or {})
        learning_state["lab"] = {
            "diagnosis_mode": mode,
            "topic": topic,
            "round": 0,
            "asked": [],
            "answered": [],
            "started": True,
        }
        learning_state["is_demo"] = True

        material = context.get("material")
        if mode == "material" and material:
            result = self._start_material(context, topic, material)
        else:
            result = self._start_self_assessment(context, topic)
        result["learning_state"] = learning_state
        return normalize_result(result, is_demo=True)

    def _start_material(self, context: dict, topic: str, material: dict) -> dict:
        from ..services import session_store

        lab_state = empty_lab_state(is_demo=True)
        lab_state["stage"] = "diagnosis"
        name = subject_table.display_name(topic)
        evidence: list[dict] = []
        fallback_only = False
        session_id = context.get("session_id")
        if session_id:
            hits = session_store.search_material(
                session_id, name, material_id=material.get("material_id"), top_k=2
            )
            real = [h for h in hits if not h.get("fallback")]
            fallback_only = bool(hits) and not real
            # ⚠️ 只把**真正命中关键词**的段落当证据。检索器兜底（fallback）返回的段落
            # 与知识点无关，若把它当证据，等于用《信号与系统》的原文去证明"模电薄弱"，
            # 答辩时会被当场拆穿（N14 要求 quote+locator 能定位到**支持该假设**的原文）。
            for hit in (real or []):
                evidence.append({
                    "kind": "material",
                    "material_id": hit.get("material_id"),
                    "quote": (hit.get("quote") or "")[:120],
                    "locator": hit.get("locator"),
                })
        if evidence:
            reason = f"资料《{material.get('title')}》{evidence[0].get('locator')} 出现与" \
                     f"{name}相关的表述，但仅 1 条证据，先记为待验证"
            lab_state["hypotheses"] = [_hypothesis(topic, "tentative", reason)]
        elif fallback_only:
            lab_state["hypotheses"] = [_hypothesis(
                topic, "tentative",
                f"资料《{material.get('title')}》里没有与{name}直接相关的表述，"
                "为避免用无关段落充当证据，本条不生成资料证据，改用小题取实测证据"
            )]
            lab_state["feedback"] = (
                f"这份资料里没有检索到与「{name}」直接相关的表述，"
                "所以我不拿它当证据，直接出一道小题来取实测结果。"
            )
        else:
            lab_state["hypotheses"] = [_hypothesis(
                topic, "tentative", "资料中没有检索到该知识点的直接表述，改用小测获取实测证据"
            )]
        lab_state["evidence"] = evidence
        question = _quiz(topic, 0)
        if question:
            lab_state["question"] = _public_question(question, topic)
            lab_state["next_action"] = "answer"
        else:
            lab_state["stage"] = "summary"
            lab_state["next_action"] = "complete"
            lab_state["explanation"] = "该科目暂无内置题目，已根据资料给出待验证假设；请补充题库或改用自评路径。"
        return {"lab_state": lab_state, "learning_state": None}

    def _start_self_assessment(self, context: dict, topic: str) -> dict:
        lab_state = empty_lab_state(is_demo=True)
        lab_state["stage"] = "diagnosis"
        lab_state["hypotheses"] = [_hypothesis(
            topic, "tentative", "用户尚未提供资料，先通过自评量表建立先验，再用小测获取实测证据"
        )]
        lab_state["question"] = {
            "question_id": f"{SELF_ASSESS_PREFIX}{topic}",
            "stem": f"请自评你目前对「{subject_table.display_name(topic)}」的掌握程度（1=完全没开始，5=已能稳定做题）",
            "options": [{"key": str(i), "text": f"{i} 分"} for i in range(1, 6)],
            "kind": "self_assessment",
            "topic": topic,
            "subject_code": topic,
        }
        lab_state["next_action"] = "answer"
        return {"lab_state": lab_state, "learning_state": None}

    # --------------------------------------------------------------- answer
    def answer(self, context: dict, question_id: str, answer: str) -> dict:
        learning_state = dict(context.get("learning_state") or {})
        lab = dict(learning_state.get("lab") or {})
        topic = lab.get("topic") or _default_topic(context)
        answered = list(lab.get("answered") or [])
        asked = list(lab.get("asked") or [])
        if question_id not in asked:
            asked.append(question_id)

        lab_state = empty_lab_state(is_demo=True)

        # ---- 自评题：记为 self_reported，不进排除依据（SPEC §10.4 关键约束）
        if question_id.startswith(SELF_ASSESS_PREFIX):
            try:
                score = int(str(answer).strip())
            except ValueError:
                score = 3
            score = min(5, max(1, score))
            answered.append({"question_id": question_id, "answer": str(answer), "score": score,
                             "basis": "self_reported"})
            notes = list(learning_state.get("notes") or [])
            notes.append(
                f"自评：{subject_table.display_name(topic)} {score}/5（自评不可靠，仅作先验，不作为淘汰院校的依据）"
            )
            learning_state["notes"] = notes
            # 结构化留存自评，便于前端把「自评先验」与「实测证据」明显分开渲染（SPEC §10.4）
            self_assessment = list(learning_state.get("self_assessment") or [])
            self_assessment.append({
                "topic": topic,
                "topic_name": subject_table.display_name(topic),
                "score": score,
                "scale": 5,
                "basis": "self_reported",
                "note": "自评只作先验，不作为排除院校的依据（SPEC §10.4）",
            })
            learning_state["self_assessment"] = self_assessment
            lab_state["stage"] = "diagnosis"
            lab_state["feedback"] = f"已记录自评 {score}/5。自评只作为先验，接下来用一道小题取实测证据。"
            lab_state["hypotheses"] = [_hypothesis(
                topic, "tentative", f"用户自评 {score}/5（basis=self_reported），证据仅 1 条，需实测验证"
            )]
            lab_state["evidence"] = [{
                "kind": "answer", "question_id": question_id, "quote": f"自评 {score}/5",
                "basis": "self_reported",
            }]
            question = _quiz(topic, 0)
            if question:
                lab_state["question"] = _public_question(question, topic)
                lab_state["next_action"] = "answer"
            else:
                lab_state["stage"] = "summary"
                lab_state["next_action"] = "complete"
            lab["answered"] = answered
            lab["asked"] = asked
            lab["round"] = int(lab.get("round") or 0) + 1
            learning_state["lab"] = lab
            learning_state["is_demo"] = True
            return normalize_result({"lab_state": lab_state, "learning_state": learning_state}, is_demo=True)

        # ---- 小测题：实测证据
        question = _find_question(topic, question_id) or _find_question_anywhere(question_id)
        correct = bool(question and str(answer).strip().upper() == question["answer"])
        answered.append({
            "question_id": question_id,
            "answer": str(answer),
            "correct": correct,
            "basis": "measured",
            "category": None if correct else question.get("category") if question else "概念混淆",
        })
        lab["answered"] = answered
        lab["asked"] = asked
        lab["round"] = int(lab.get("round") or 0) + 1

        wrong_count = sum(1 for a in answered if a.get("basis") == "measured" and a.get("correct") is False)
        right_count = sum(1 for a in answered if a.get("basis") == "measured" and a.get("correct") is True)

        lab_state["evidence"] = [{
            "kind": "answer",
            "question_id": question_id,
            "quote": f"第 {lab['round']} 题作答：{answer}（{'正确' if correct else '错误'}）",
            "correct": correct,
        }]

        if correct:
            lab_state["stage"] = "summary"
            lab_state["next_action"] = "complete"
            # N20：答对 → supported
            lab_state["hypotheses"] = [_hypothesis(
                topic, "supported", f"{right_count} 条实测证据支持，{wrong_count} 条反向证据"
            )]
            lab_state["feedback"] = "本题作答正确。"
            lab_state["explanation"] = question["explanation"] if question else None
        else:
            # N20：答错 → revised；N17：feedback 与 explanation 分两层
            lab_state["stage"] = "verification"
            lab_state["next_action"] = "answer"
            lab_state["hypotheses"] = [_hypothesis(
                topic, "revised", f"出现 {wrong_count} 条反向证据，原假设被修正为『该知识点掌握不牢』"
            )]
            lab_state["feedback"] = "本题作答错误。" + (question.get("feedback_wrong") if question else "")
            lab_state["feedback"] += f"（错误归类：{question.get('category') if question else '概念混淆'}）"
            lab_state["explanation"] = question["explanation"] if question else None
            # N19：验证阶段的新题绑定同一个知识点
            follow_up = _quiz(topic, lab["round"])
            if follow_up:
                lab_state["question"] = _public_question(follow_up, topic)
            else:
                lab_state["stage"] = "summary"
                lab_state["next_action"] = "complete"
                lab_state["explanation"] = (lab_state["explanation"] or "") + " 该科目题目已用尽，先给出结论。"

        learning_state["lab"] = lab
        learning_state["ability_profile"] = _update_ability_profile(
            learning_state.get("ability_profile") or [], topic, right_count, wrong_count
        )
        learning_state["is_demo"] = True
        return normalize_result({"lab_state": lab_state, "learning_state": learning_state}, is_demo=True)


def _update_ability_profile(profile: list[dict], topic: str, right: int, wrong: int) -> list[dict]:
    """N27：`basis=measured` + 证据条数；自评不写进 ability_profile 的 weak 判断。"""
    others = [item for item in profile if item.get("topic") != topic]
    evidence_count = right + wrong
    if evidence_count == 0:
        return profile
    if wrong > right or (wrong >= 1 and right == 0):
        status = "weak"
    elif right >= 2 and wrong == 0:
        status = "solid"
    else:
        status = "tentative"
    others.append({
        "topic": topic,
        "status": status,
        "evidence_count": evidence_count,
        "basis": "measured",
    })
    return others


def _find_question(topic: str, question_id: str) -> dict | None:
    for item in _BANK.get(topic) or []:
        if item["question_id"] == question_id:
            return item
    return None


def _find_question_anywhere(question_id: str) -> dict | None:
    for items in _BANK.values():
        for item in items:
            if item["question_id"] == question_id:
                return item
    return None
