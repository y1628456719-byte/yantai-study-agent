"""Explicit paid/network check; requires --live and the user's local credential."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

from .. import Lab, LabError
from ..lab.aiping import AipingClient, AUTHORIZED_MODELS
from ..lab.content import demo_context, digest, runtime_fingerprint
from ..lab.models import GenerateJSONAdapter
from .demo import ROUTES
from .host_storage import MemoryHost


def check(model, scenario, output_dir):
    client = AipingClient(model=model)
    candidates = []

    def capture(messages, schema, timeout_seconds):
        text = client.generate_json(messages, schema, timeout_seconds)
        # Explicit demo-only verification records final output, never model reasoning.
        candidates.append(text)
        return text

    lab = Lab(model=GenerateJSONAdapter(capture), timeout_seconds=120)
    context = demo_context("live_" + model + "_" + scenario, runtime_mode="live")
    host = MemoryHost(context)
    results = []
    record = {"model": model, "scenario": scenario, "runtime_mode": "live", "is_demo": True,
              "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "whole_site_verified": False, "status": "pending", "responses": results}
    record["runtime_source_hashes"] = runtime_fingerprint()
    try:
        results.append(host.execute(lab, "start", "start", 0))
        for i, answer in enumerate(ROUTES[scenario]):
            before = host.read()
            results.append(host.execute(lab, "submit", f"answer_{i + 1}", before["revision"],
                                        results[-1]["lab_state"]["question"]["question_id"], answer))
        record["status"] = "completed"
    except LabError as exc:
        record["status"] = "failed"
        record["error"] = exc.to_dict()["error"]
        record["state_after_failure"] = host.read()["learning_state"]
    record["calls"] = client.calls
    record["candidate_outputs"] = candidates
    record["final_revision"] = host.read()["revision"]
    record["final_state_sha256"] = digest(host.read()["learning_state"])
    path = output_dir / (model + "_" + scenario + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"model": model, "scenario": scenario, "status": record["status"],
            "calls": len(client.calls), "final_revision": record["final_revision"],
            "output": str(path)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="明确确认本命令执行远端付费调用")
    p.add_argument("--models", nargs="+", choices=AUTHORIZED_MODELS, default=["DeepSeek-V4.1-Flash"])
    p.add_argument("--scenario", choices=ROUTES, default="revised")
    args = p.parse_args()
    if not args.live:
        p.error("远端检查必须明确提供--live；普通测试不会读取密钥或调用网络。")
    output = Path(__file__).resolve().parents[1] / "verification" / "live"
    output.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda m: check(m, args.scenario, output), args.models))
    print(json.dumps(results, ensure_ascii=False))
    return 0 if all(r["status"] == "completed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
