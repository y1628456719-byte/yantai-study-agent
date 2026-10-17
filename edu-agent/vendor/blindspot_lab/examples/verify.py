"""Local module evidence only; never labels stub calls as a real model run."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

from .. import Lab, LabError
from ..lab.content import demo_context, runtime_fingerprint
from .demo import ROUTES, run


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    package = Path(__file__).resolve().parents[1]
    workspace = package.parent
    output = package / "verification"
    output.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "blindspot_lab/tests", "-v"]
    result = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8")
    (output / "tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    examples = package / "examples" / "outputs"
    examples.mkdir(exist_ok=True)
    for route in ROUTES:
        (examples / f"{route}.json").write_text(
            json.dumps(run(route), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    errors = []
    for mode in ("live", "replay"):
        try:
            Lab().start_lab(demo_context(runtime_mode=mode))
        except LabError as exc:
            errors.append({"operation": "start_lab", "requested_mode": mode, **exc.to_dict()})
    bad = demo_context()
    bad["material"]["text"] = "不在当前审核范围的资料"
    try:
        Lab().start_lab(bad)
    except LabError as exc:
        errors.append({"operation": "start_lab", "requested_mode": "mock", **exc.to_dict()})
    (examples / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sources = sorted(p for p in package.rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and "verification" not in p.parts
                     and "outputs" not in p.parts)
    manifest = {str(p.relative_to(package)).replace("\\", "/"): sha256(p) for p in sources}
    source_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (output / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runtime_hashes = runtime_fingerprint(package)
    live_checks = []
    for saved in sorted((output / "live").glob("*.json")):
        record = json.loads(saved.read_text(encoding="utf-8"))
        live_checks.append({"file": str(saved.relative_to(output)).replace("\\", "/"),
                            "model": record["model"], "scenario": record["scenario"],
                            "status": record["status"],
                            "runtime_sources_match": record.get("runtime_source_hashes") == runtime_hashes})
    completed = [r for r in live_checks if r["status"] == "completed" and r["runtime_sources_match"]]
    git_probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace,
                               capture_output=True, text=True)
    receipt = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "source_manifest_sha256": source_hash,
               "test_command": "python -m unittest discover -s blindspot_lab/tests -v",
               "test_exit_code": result.returncode, "test_log": "tests.log",
               "sample_routes": list(ROUTES), "verification_command_uses_network": False,
               "test_execution_modes": ["mock", "replay", "injected_transport_stub"],
               "simulated_transport_adapters_tested": True,
               "real_model": {"verified": bool(completed), "completed_routes": completed,
                              "all_recorded_routes": live_checks,
                              "boundary": "真实API使用自编材料和脚本答案；不等于学生效果或整站联调。"},
               "whole_site": {"verified": False, "reason": "未提供主后端、网页和真实持久层。"},
               "git": {"repository": git_probe.returncode == 0, "commit": None, "pushed": False},
               "independent_review": "see review result and reviewed source hashes separately"}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"test_exit_code": result.returncode, "source_manifest_sha256": source_hash,
                      "saved_examples": len(ROUTES), "real_model_routes_verified": len(completed),
                      "whole_site_verified": False}, ensure_ascii=False))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
