import copy
import hashlib
import json
from pathlib import Path

from .errors import LabError


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_bank():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "logic_demo.json"
    return json.loads(path.read_text(encoding="utf-8"))


def runtime_fingerprint(package_root=None):
    root = Path(package_root) if package_root else Path(__file__).resolve().parents[1]
    paths = sorted((root / "lab").glob("*.py")) + sorted((root / "fixtures").glob("*.json"))
    return {str(p.relative_to(root)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def demo_context(session_id="s_demo", runtime_mode="mock"):
    bank = load_bank()
    return {"schema_version": "lab.v1", "session_id": session_id,
            "request_id": "start_01", "revision": 0,
            "profile": {"daily_minutes": 60},
            "target": {"school_id": "demo_school", "subject": "逻辑"},
            "material": copy.deepcopy(bank["material"]), "topic": bank["topic"],
            "learning_state": None, "runtime_mode": runtime_mode}


def public_question(question):
    keys = ["question_id", "stem", "options"]
    # Additive public labels for generated follow-ups; legacy questions keep
    # their exact shape.
    # Provenance remains available in server-side evidence records; public
    # question text carries only its learning-topic label.
    keys.extend(k for k in ("topic_label",) if k in question)
    return copy.deepcopy({k: question[k] for k in keys})


def material_evidence(material, quotes):
    records = []
    for i, quote in enumerate(quotes):
        start = material["text"].find(quote)
        if start < 0:
            raise LabError("EVIDENCE_INVALID", "样本引文不在当前材料中。")
        records.append({"id": f"m:{material['material_id']}:{i + 1}",
                        "type": "material", "material_id": material["material_id"],
                        "quote": quote, "locator": {"start": start, "end": start + len(quote),
                        "line": material["text"][:start].count("\n") + 1}})
    return records


def validate_evidence(records, material, answers):
    ids = set()
    answer_map = {a["answer_id"]: a for a in answers}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise LabError("EVIDENCE_INVALID", "证据格式无效。")
        if record["id"] in ids:
            raise LabError("EVIDENCE_INVALID", "证据ID重复。")
        ids.add(record["id"])
        if record.get("type") == "material":
            loc = record.get("locator", {})
            start, end = loc.get("start"), loc.get("end")
            text = material["text"]
            if (record.get("material_id") != material["material_id"]
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text)
                    or text[start:end] != record.get("quote")
                    or loc.get("line") != text[:start].count("\n") + 1):
                raise LabError("EVIDENCE_INVALID", "引文或定位与原文不一致。")
        elif record.get("type") == "answer":
            answer = answer_map.get(record.get("answer_id"))
            if not answer or any(record.get(k) != answer.get(k) for k in
                                 ("question_id", "answer", "is_correct")):
                raise LabError("EVIDENCE_INVALID", "作答证据找不到对应记录。")
        else:
            raise LabError("EVIDENCE_INVALID", "未知证据类型。")
    return ids
