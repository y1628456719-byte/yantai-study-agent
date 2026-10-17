"""把当前后端的 OpenAPI 契约导出到 `docs/openapi.json`（前端/Postman 可直接导入）。

改了路由或请求模型之后重新跑一次，保证 `docs/openapi.json` 与代码一致：
    .venvs\\edu-agent\\Scripts\\python.exe scripts/dump_openapi.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.main import app  # noqa: E402

OUT = ROOT / "docs" / "openapi.json"


def main() -> int:
    spec = app.openapi()
    OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = sorted(spec.get("paths") or {})
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"paths={len(paths)}")
    for p in paths:
        methods = ",".join(sorted(m.upper() for m in spec["paths"][p]))
        print(f"  {methods:12s} {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
