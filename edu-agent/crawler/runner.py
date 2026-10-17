"""抓取入口（构建期运行，产出落库）。

用法：
    python -m crawler.runner --school jlu
    python -m crawler.runner --school jlu --section score_line --limit 2
    python -m crawler.runner --list

产出：
    data/raw/<学校id>/<hash>.html     原始页面存档（可复查、可现场展示）
    data/schools/<学校id>.json        结构化数据（Agent 运行期只读这个）
"""
import argparse
import json
from pathlib import Path

from .sources import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "schools"


def save(records, school_id, verbose=True):
    RAW_DIR.joinpath(school_id).mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for rec in records:
        if rec.raw_html:
            (RAW_DIR / school_id / f"{rec.key()}.html").write_text(
                rec.raw_html, encoding="utf-8", errors="ignore")

    out_path = OUT_DIR / f"{school_id}.json"
    payload = {
        "school_id": school_id,
        "school": records[0].school if records else "",
        "count": len(records),
        "generated_at": records[0].fetched_at if records else "",
        "records": [r.to_dict() for r in records],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"\n  已写出 {out_path}")
        print(f"  原始存档 {RAW_DIR / school_id}/")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="考研数据抓取器")
    ap.add_argument("--school", help="学校 id，如 jlu；多个用逗号分隔")
    ap.add_argument("--section", help="只抓某栏目，如 score_line；多个用逗号分隔")
    ap.add_argument("--limit", type=int, default=5, help="每栏目最多抓几条详情")
    ap.add_argument("--delay", type=float, default=1.2, help="同域名请求间隔秒数")
    ap.add_argument("--list", action="store_true", help="列出已支持的学校")
    args = ap.parse_args()

    if args.list or not args.school:
        print("已支持的学校：")
        for sid, cls in REGISTRY.items():
            print(f"  {sid:<8} {cls.school_name}   {cls.base_url}")
        return

    cats = [c.strip() for c in args.section.split(",")] if args.section else None
    total = 0
    for sid in [s.strip() for s in args.school.split(",")]:
        cls = REGISTRY.get(sid)
        if not cls:
            print(f"未知学校 id：{sid}（用 --list 查看）")
            continue
        print(f"\n=== {cls.school_name} ({sid}) ===")
        src = cls(delay=args.delay)
        records = src.collect(categories=cats, limit=args.limit)
        if records:
            save(records, sid)
            total += len(records)
        else:
            print("  没有抓到任何记录")

    print(f"\n完成，共 {total} 条记录。")


if __name__ == "__main__":
    main()
