"""从吉林大学研招网『招生专业目录』PDF 附件中抽取指定学院的专业/人数/考试科目。

背景（见 REVIEW.md §3.2）：
  院校的初试考试科目、招生人数、推免人数**不在 HTML 表格里**，而在 PDF 附件里；
  附件链接是西安博达 CMS 形式（不以 .pdf 结尾）：
    /system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=2109433285&wbfileid=18205630

用法：
    python prepare_jlu_catalog.py                      # 默认抽取 502 通信工程学院
    python prepare_jlu_catalog.py --college 502 --out data/jlu_catalog_502.json
    python prepare_jlu_catalog.py --pdf path/to/附件1.pdf

依赖：requests, pypdf
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE = "https://yzb.jlu.edu.cn"
# 2027 年全日制硕士研究生招生专业目录（附件1）；owner 来自页面 HTML 的 href
ATTACHMENTS = {
    "full_time": {
        "wbfileid": "18205630",
        "owner": "2109433285",
        "title": "吉林大学2027年全日制硕士研究生招生专业目录",
        "source_url": "https://yzb.jlu.edu.cn/info/1047/3678.htm",
        "published": "2026-09",
    },
    "part_time": {"wbfileid": "18205631", "owner": "2109433285", "title": "吉林大学2027年非全日制硕士研究生招生专业目录"},
    "veteran": {"wbfileid": "18205632", "owner": "2109433285", "title": "吉林大学2027年退役大学生士兵专项计划招生专业目录"},
}

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Referer": "https://yzb.jlu.edu.cn/info/1047/3678.htm",
}

CN_NUM = "①②③④⑤⑥"
# 6 位专业代码（学科门类 01–14 → 首位为 0/1；两侧不得紧邻数字或连字符，否则会误吃电话号 0431-85095775）
PROG_RE = re.compile(r"(?<![\d\-])([01]\d{5})(?!\d)\s*")
# 复试科目取值时的终止标记（下一个专业块 / 学位块 / 参考书目）
SECTION_STOP_RE = re.compile(r"(?:(?<![\d\-])[01]\d{5}(?!\d)|(?:全日制|非全日制)(?:学术|专业)学位|参考书目)")
DEGREE_RE = re.compile(r"(全日制|非全日制)(学术|专业)学位\s*[（(]\s*学制\s*(\d+)\s*年\s*[)）]\s*(\d+)")
COLLEGE_TITLE_RE = re.compile(r"^\s*(\d{3})\s+([\u4e00-\u9fa5]{2,12}(?:学院|研究院|中心|学部|所))\s*$")


def download(att: dict, dest_dir: str) -> str:
    import requests

    url = (
        f"{BASE}/system/_content/download.jsp?urltype=news.DownloadAttachUrl"
        f"&owner={att['owner']}&wbfileid={att['wbfileid']}"
    )
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"jlu_{att['wbfileid']}.pdf")
    if not os.path.exists(dest):
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        if r.content[:5] != b"%PDF-":
            raise RuntimeError(f"附件不是 PDF（wbfileid={att['wbfileid']}），可能是登录墙或被限流")
        with open(dest, "wb") as fh:
            fh.write(r.content)
    return dest


def page_texts(pdf_path: str) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    out = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.replace("\u3000", " ")
        out.append(text)
    return out


def split_colleges(pages: list[str]) -> dict[str, list[tuple[int, str]]]:
    """把每页文本按学院切分；返回 {学院标题: [(页码, 文本)]}。"""
    sections: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for idx, text in enumerate(pages, start=1):
        if idx == 1:
            continue  # 目录页
        # 页眉形如：@吉林大学 通信工程学院(2027 总数)
        header = re.search(r"@?\s*吉林大学\s*([\u4e00-\u9fa5]{2,12}学院)", text)
        title = None
        for line in text.splitlines():
            m = COLLEGE_TITLE_RE.match(line)
            if m:
                title = f"{m.group(1)} {m.group(2)}"
                break
        if header and (title is None or header.group(1) in title):
            title = title or header.group(1)
        if title:
            current = title
        if current is None:
            continue
        sections.setdefault(current, []).append((idx, text))
    return sections


def parse_college(chunks: list[tuple[int, str]]) -> dict:
    pages = [p for p, _ in chunks]
    flat = "\n".join(t for _, t in chunks)

    total = None
    m = re.search(r"(\d+)\s*招收跨学科考生", flat)
    if m:
        total = int(m.group(1))
    else:
        m = re.search(r"\(20\d{2}\s*总数\)\s*\n?\s*(\d+)", flat)
        if m:
            total = int(m.group(1))

    contact = {}
    m = re.search(r"联系人[：:]\s*([\u4e00-\u9fa5、\s]{2,30})", flat)
    if m:
        contact["person"] = m.group(1).strip()
    m = re.search(r"联系电话[：:]\s*([\d\-—]{7,20})", flat)
    if m:
        contact["phone"] = m.group(1)

    policy_notes = [ln.strip() for ln in flat.splitlines() if "招收跨学科考生" in ln or "同等学力" in ln]

    # 按“学位类型块”切分
    blocks = []
    for mm in DEGREE_RE.finditer(flat):
        blocks.append(
            {
                "degree_type": f"{mm.group(1)}{mm.group(2)}学位",
                "duration_years": int(mm.group(3)),
                "declared_scale": int(mm.group(4)),
                "start": mm.end(),
            }
        )
    for i, b in enumerate(blocks):
        b["end"] = blocks[i + 1]["start"] if i + 1 < len(blocks) else len(flat)

    programs = []
    book_section = flat.split("参考书目", 1)[1] if "参考书目" in flat else ""
    body = flat.split("参考书目", 1)[0]

    marks = list(PROG_RE.finditer(body))
    for i, mk in enumerate(marks):
        code = mk.group(1)
        seg_end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        seg = body[mk.end() : seg_end]
        # 名称：到第一个独立数字（人数）之前
        num_m = re.search(r"(\d{1,4})\s*(?=[①-⑩]|复试科目|$)", seg, re.S)
        if num_m:
            name = seg[: num_m.start()]
            scale = int(num_m.group(1))
        else:
            name = seg
            scale = None
        name = re.sub(r"\s+", "", name)
        subjects = []
        for sm in re.finditer(r"([①-⑩])\s*([^①-⑩]+)", seg):
            item = re.sub(r"\s+", " ", sm.group(2)).strip()
            item = item.split("复试科目")[0].strip()
            if item:
                subjects.append(item)
        re_exam = None
        rm = re.search(r"复试科目[：:]\s*", seg)
        if rm:
            rest = seg[rm.end() :]
            stop = SECTION_STOP_RE.search(rest)
            raw = rest[: stop.start()] if stop else rest
            raw = raw.split("备注")[0]
            re_exam = re.sub(r"\s+", "", raw) or None
        notes = None
        nm = re.search(r"备注[：:]\s*(.+)", seg, re.S)
        if nm:
            notes = re.sub(r"\s+", "", nm.group(1))[:120]

        degree_type = None
        duration = None
        for b in blocks:
            if b["start"] <= mk.start() < b["end"]:
                degree_type = b["degree_type"]
                duration = b["duration_years"]
                break
        programs.append(
            {
                "program_code": code,
                "program_name": name,
                "admission_scale": scale,
                "degree_type": degree_type,
                "duration_years": duration,
                "exam_subjects": subjects,
                "re_exam_subjects": re_exam,
                "notes": notes,
            }
        )

    books = {}
    if book_section:
        for bm in re.finditer(r"(\d{3})\s*([\u4e00-\u9fa5（）()、]{2,20})", book_section):
            books.setdefault(bm.group(1), []).append(re.sub(r"\s+", "", bm.group(2)))
        for bm in re.finditer(r"《([^》]+)》", book_section):
            books.setdefault("_titles", []).append(bm.group(1))

    return {
        "college_pages": pages,
        "college_total_2027": total,
        "contact": contact,
        "policy_notes": policy_notes,
        "degree_blocks": [{k: v for k, v in b.items() if k not in ("start", "end")} for b in blocks],
        "programs": programs,
        "reference_books": {
            "by_exam_code": {k: v for k, v in books.items() if k != "_titles"},
            "titles": books.get("_titles", []),
            "count": len(books.get("_titles", [])),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--college", default="502", help="学院代码，如 502")
    ap.add_argument("--pdf", default=None, help="本地 PDF 路径；缺省则自动下载附件1")
    ap.add_argument("--download-dir", default=os.path.join(os.path.dirname(__file__), "..", ".probe"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "jlu_catalog_502.json"))
    args = ap.parse_args()

    if args.pdf:
        pdf_path = args.pdf
    else:
        pdf_path = download(ATTACHMENTS["full_time"], args.download_dir)
    pages = page_texts(pdf_path)
    sections = split_colleges(pages)

    target_key = next((k for k in sections if k.startswith(args.college)), None)
    if not target_key:
        print("未找到学院:", args.college, "可用:", list(sections)[:20], file=sys.stderr)
        return 2

    parsed = parse_college(sections[target_key])
    result = {
        "school_id": "jlu",
        "school_name": "吉林大学",
        "applicable_year": 2027,
        "college_code": args.college,
        "college_name": target_key,
        "source": {
            "title": ATTACHMENTS["full_time"]["title"],
            "page_url": ATTACHMENTS["full_time"]["source_url"],
            "attachment_wbfileid": ATTACHMENTS["full_time"]["wbfileid"],
            "attachment_download": f"{BASE}/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner={ATTACHMENTS['full_time']['owner']}&wbfileid={ATTACHMENTS['full_time']['wbfileid']}",
            "pdf_pages": len(pages),
            "source_level": "official",
            "fetched_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "extracted": parsed,
    }
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"[ok] {target_key} 页={parsed['college_pages']} 总数={parsed['college_total_2027']} "
          f"专业数={len(parsed['programs'])} 参考书={parsed['reference_books']['count']}")
    for p in parsed["programs"]:
        print(f"   {p['program_code']} {p['program_name']} | {p['degree_type']} {p['duration_years']}年 | "
              f"人数={p['admission_scale']} | 初试={p['exam_subjects']} | 复试={p['re_exam_subjects']}")
    print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
