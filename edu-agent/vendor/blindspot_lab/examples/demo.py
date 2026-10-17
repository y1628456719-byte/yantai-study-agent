"""Run from D:\\莱森: python -m blindspot_lab.examples.demo --scenario revised"""
import argparse
import json
from pathlib import Path

from .. import Lab, LabError, build_learning_plan
from ..lab.content import demo_context
from .host_storage import MemoryHost


ROUTES = {"supported": ["A", "B", "B"], "revised": ["A", "A", "B"],
          "inconclusive": ["A", "D", "A"], "correct": ["B", "A", "B"]}


def run(scenario="revised"):
    lab = Lab()
    host = MemoryHost(demo_context("s_" + scenario))
    responses = [host.execute(lab, "start", "start_01", 0)]
    for i, answer in enumerate(ROUTES[scenario]):
        context = host.read()
        qid = context["learning_state"]["lab_state"]["question"]["question_id"]
        responses.append(host.execute(lab, "submit", f"answer_{i + 1}", context["revision"], qid, answer))
    # JSON serialization and a new store object simulate process-boundary recovery.
    host = MemoryHost.restore_json(host.export_json())
    state = host.read()["learning_state"]
    plan60 = build_learning_plan(state, 60)
    before = host.read()
    host.update_daily_minutes(30, before["revision"])
    resumed = host.execute(lab, "start", "refresh_after_time_change", host.read()["revision"])
    return {"scenario": scenario, "is_demo": True, "runtime_mode": "mock",
            "verification_level": "module_and_reference_memory_store_only",
            "initial_context": demo_context("s_" + scenario), "responses": responses,
            "restored_lab_state": resumed["lab_state"],
            "plans": {"60": plan60, "30": build_learning_plan(state, 30)},
            "real_model_verified": False, "whole_site_verified": False}


def main():
    parser = argparse.ArgumentParser(description="盲点实验室：明确标记的三题规则模拟")
    parser.add_argument("--scenario", choices=ROUTES, default="revised")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    result = run(args.scenario)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.full:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for r in result["responses"]:
            view = r["lab_state"]
            print(json.dumps({"stage": view["stage"], "substage": view["substage"],
                              "question_id": (view["question"] or {}).get("question_id"),
                              "hypotheses": [(h["id"], h["status"]) for h in view["hypotheses"]],
                              "summary": view["summary"], "mode": view["runtime_mode"]}, ensure_ascii=False))
        print("模拟闭环完成；真实模型与整站联调尚未验证。")


if __name__ == "__main__":
    try:
        main()
    except LabError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False))
        raise SystemExit(1)
