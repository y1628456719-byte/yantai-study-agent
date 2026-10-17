"""Single local entry point for the integrated workbench; credentials stay in memory."""
import argparse
import os
from pathlib import Path
import sys


def main():
    p = argparse.ArgumentParser(description="砚台考研工作台")
    p.add_argument("--mode", choices=("mock", "live"), default="mock")
    p.add_argument("--port", type=int, default=8790)
    a = p.parse_args()
    if not 1 <= a.port <= 65535:
        p.error("port must be between 1 and 65535")
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    os.chdir(root)
    # A separate database per execution mode avoids restoring a mock run as live.
    os.environ.setdefault("EDU_AGENT_DB", str(root / "data" / ("workbench-" + a.mode + ".sqlite3")))
    os.environ["MOCK_LLM"] = "1" if a.mode == "mock" else "0"
    os.environ["LAB_ADAPTER"] = "blindspot"
    os.environ["LAB_BLINDSPOT_MODE"] = a.mode
    os.environ["LLM_BASE_URL"] = "https://aiping.cn/api/v1"
    os.environ["LLM_MODEL"] = "DeepSeek-V4.1-Flash"
    os.environ.setdefault("LLM_TIMEOUT", "150")
    os.environ.setdefault("LAB_TIMEOUT", "180")
    os.environ.setdefault("LLM_MAX_RETRIES", "1")
    if a.mode == "live":
        from vendor.blindspot_lab.lab.aiping import load_api_key
        try:
            key = os.environ.get("LLM_API_KEY") or load_api_key()
        except Exception:
            raise SystemExit("真实模式需要本机已有加密凭据或 AIPING_API_KEY；未切换为模拟模式。") from None
        os.environ["LLM_API_KEY"] = key
        os.environ["AIPING_API_KEY"] = key
    import uvicorn
    print(f"http://127.0.0.1:{a.port}/  mode={a.mode}; SQLite local session storage", flush=True)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=a.port, log_level="info")


if __name__ == "__main__":
    main()
