"""哈尔滨工程大学（yzb.hrbeu.edu.cn）信息与通信工程方向专业目录解析。

输入（全部为已抓取的本地原始件，见 data/_probe/）：
    2027 硕士招生专业目录 PDF（32 页）
        页面 https://yzb.hrbeu.edu.cn/2026/0929/c3279a352656/page.htm
        附件 https://yzb.hrbeu.edu.cn/_upload/article/files/55/1a/06c933ef4ac9b8aa1372c53501a5/a26798bf-5848-4aae-a3e4-8804c8a8b2b1.pdf
    2026 硕士招生专业目录 PDF（27 页）
        页面 https://yzb.hrbeu.edu.cn/2025/0930/c3279a341475/page.htm
        附件 https://yzb.hrbeu.edu.cn/_upload/article/files/10/bd/21df1e9146c4ac4bb3c628fef3f5/0bb6ceec-9265-4271-ace8-aa952af9e392.pdf
    2027 招生简章 HTML（学制 §八 / 学费 §九）
        https://yzb.hrbeu.edu.cn/2026/0929/c3279a352656/page.htm

产出：data/schools/heu_catalog.json
    与 data/schools/jlu_catalog_502.json 同构，并额外给出
    other_colleges（同一专业代码由第二个学院招生）、history（历年）、
    metric_years（每个数值的取值年份）、missing_fields / unknowns。

## 为什么不用 page.extract_text() 的平铺文本
哈工程专业目录是 6 列合并单元格表格：
    [学院代码/专业代码+研究方向] [预计招生人数] [初试科目] [复试笔试科目] [同等学力加试科目] [备注]
pypdf 的平铺文本会把同一条 y 行上的多列碎片按内容流顺序吐出，导致
  * 「复试笔试科目」与「同等学力加试科目」两列的换行碎片互相插入
    （实测得到 "数字电子技术与通信原理与接口技术处理理技术" 这类垃圾）；
  * 「(1)101思想政治理论」被粘在人数/方向行尾，按行首正则匹配会丢掉科目①。
因此本脚本用 `extract_text(visitor_text=...)` 取每个文本碎片的 (x, y)：
按 y 聚成表格行、按 x 聚成 6 个表格列，再按列取值、按列内碎片顺序拼接。

## 关键站点事实（实测）
  * 081000 信息与通信工程在哈工程由 **两个学院分别招生**：
      005 水声工程学院（④810 信号与系统）
      008 信息与通信工程学院/集成电路学院（④815 电路、信号与系统）
    只按专业代码取值会把两个学院的人数混成一个数，必须按学院切分。
  * 学院标题行出现在该学院内容的**页末**，所以「某学院标题之前的全部内容」
    才是该学院的表格内容。
  * 085402 通信工程在哈工程专业目录中不存在（该校按 085400 电子信息招生）。

用法：
    python scripts/prepare_heu_catalog.py            # 解析已存档 PDF/HTML
    python scripts/prepare_heu_catalog.py --download # 先重新下载原始件
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEARCH_DIRS = [
    os.path.join(ROOT, "data", "_probe"),
    os.path.join(ROOT, "data", "raw", "_probe"),
]
OUT = os.path.join(ROOT, "data", "schools", "heu_catalog.json")

TARGET_CODES = ("081000", "085402", "085400")
MAIN_COLLEGE = "008"
# 真正开设信息与通信工程方向的两个学院；其余学院的 085400 电子信息与信通方向无关
KEEP_COLLEGES = ("008", "005")

# 信通学院 2026 年第一志愿复试名单 PDF（学院二级站附件）——本站唯一能取到分数的地方
RETEST_PDF_NAME = "heu_icec_fs2026.pdf"
RETEST_PAGE = "https://icec.hrbeu.edu.cn/2026/0323/c4658a346971/page.htm"
RETEST_PDF = ("https://icec.hrbeu.edu.cn/_upload/article/files/9d/72/3195bd884d0ebcfe6966d8d5237b/"
              "27dc757d-441b-43fd-a13b-49f28beec97c.pdf")

PDFS = [
    (2027, "heu_zsml2027.pdf",
     "https://yzb.hrbeu.edu.cn/2026/0929/c3279a352656/page.htm",
     "https://yzb.hrbeu.edu.cn/_upload/article/files/55/1a/06c933ef4ac9b8aa1372c53501a5/a26798bf-5848-4aae-a3e4-8804c8a8b2b1.pdf",
     "哈尔滨工程大学2027年硕士招生专业目录"),
    (2026, "heu_zsml2026.pdf",
     "https://yzb.hrbeu.edu.cn/2025/0930/c3279a341475/page.htm",
     "https://yzb.hrbeu.edu.cn/_upload/article/files/10/bd/21df1e9146c4ac4bb3c628fef3f5/0bb6ceec-9265-4271-ace8-aa952af9e392.pdf",
     "哈尔滨工程大学2026年硕士招生专业目录"),
]

GUIDE_HTML = ("heu_jz2027.html",
              "https://yzb.hrbeu.edu.cn/2026/0929/c3279a352656/page.htm")
SYLLABUS_PAGE = "https://icec.hrbeu.edu.cn/2026/1008/c4658a352763/page.htm"
SYLLABUS_PDF = "https://icec.hrbeu.edu.cn/_upload/article/files/d4/5f/8e7c8a9545ec90c0aef1a53de2cf/70a6dd55-4dbb-4562-b3a1-196bf8b51b08.pdf"


def find(name):
    for d in SEARCH_DIRS:
        p = os.path.join(d, os.path.basename(name))
        if os.path.exists(p):
            return p
    p = os.path.join(ROOT, name)
    return p if os.path.exists(p) else None


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# 1) 坐标网格重建
# --------------------------------------------------------------------------- #
HDR_MARK = ("哈尔滨工程大学", "学院代码、名称", "名称及研究方向", "预计招生人数",
            "推免+统考", "/非全日制)", "人数")
ROW_TOL = 3.0        # y 方向聚合为同一表格行的容差（pt）
COL_TOL = 10.0       # x 方向聚合为同一表格列的容差（pt）


def page_rows(page):
    """返回该页的表格行：[[(x, text), ...], ...]，已按 y 降序 / x 升序排序。"""
    frags = []

    def cb(text, cm, tm, font_dict, font_size):
        t = text.replace("\u3000", " ").strip()
        if t:
            frags.append((round(tm[5], 1), round(tm[4], 1), t))

    page.extract_text(visitor_text=cb)
    frags.sort(key=lambda f: (-f[0], f[1]))
    rows = []
    for y, x, t in frags:
        if rows and abs(rows[-1][0] - y) <= ROW_TOL:
            rows[-1][1].append((x, t))
        else:
            rows.append([y, [(x, t)]])
    for r in rows:
        r[1].sort()
    return [r[1] for r in rows]


def is_header_row(cells):
    s = "".join(t for _x, t in cells)
    return any(m in s for m in HDR_MARK)


def col_centers(data_rows):
    """从数据行里聚类出 6 个表格列的 x 中心。"""
    xs = sorted({x for r in data_rows for x, _t in r})
    if not xs:
        return []
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= COL_TOL:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    # 列数可能因某列在本页无内容而少于 6，取碎片数最多的 6 个即可
    if len(clusters) > 6:
        cnt = {i: 0 for i in range(len(clusters))}
        for r in data_rows:
            for x, _t in r:
                for i, c in enumerate(clusters):
                    if x >= c[0] - COL_TOL:
                        cnt[i] += 1
        keep = sorted(sorted(cnt, key=lambda i: -cnt[i])[:6])
        clusters = [clusters[i] for i in keep]
    centers = [c[0] for c in clusters]
    return centers


def bucket_row(cells, centers):
    """把一行的碎片按 x 落到最近的列，返回 {col_index: text}。"""
    out = {}
    for x, t in cells:
        if not centers:
            out.setdefault(0, "")
            out[0] += t
            continue
        idx = min(range(len(centers)), key=lambda i: abs(centers[i] - x))
        out[idx] = out.get(idx, "") + t
    return out


# --------------------------------------------------------------------------- #
# 2) 行级语义
# --------------------------------------------------------------------------- #
COLLEGE_HDR = re.compile(r"^(\d{3})\s*([\u4e00-\u9fa5/（）()]{3,30})$")
PROG_HDR = re.compile(r"^([0-9]{4}[0-9A-Z]{2})\s*([\u4e00-\u9fa5A-Za-z（）()、/·\-\s]{2,40})$")
DIR_HDR = re.compile(r"^([0-9A-Z]{2})\s*\((全日制|非全日制)\)\s*(.*)$")
COUNTS = re.compile(r"(\d{1,3})\s*\+\s*(\d{1,3})\s*/\s*(\d{1,3})")
SUBJ_SPLIT = re.compile(r"(?=\(\d\))")
ADDI_SPLIT = re.compile(r"(?=\d\s*[.、])")
# 08 门类下的专业学位代码前四位（其余 08xxxx 视为学术学位）。
# 判断依据：081000/080900/0804J7 是学术学位，085400/085500 是专业学位，
# 不能用「前缀 085」一刀切（085 属于「085xxx 专业学位」但 080 也以 08 开头）。
PRO_DEGREE_08 = {"51", "53", "54", "55", "56", "57", "58", "59", "60", "61"}


def split_subjects(text):
    """把 '...(1)101... ...(2)201...' 切成 4 条科目。"""
    out = []
    for piece in SUBJ_SPLIT.split(text or ""):
        m = re.match(r"^\((\d)\)\s*(.+)$", piece.strip())
        if not m:
            continue
        no = int(m.group(1))
        body = re.sub(r"\s+", "", m.group(2))
        if not body:
            continue
        mc = re.match(r"^(\d{3})\s*(.*)$", body)
        if mc:
            out.append({"no": no, "code": mc.group(1), "text": body})
        else:
            out.append({"no": no, "code": None, "text": body})
    seen, uniq = set(), []
    for s in sorted(out, key=lambda s: s["no"]):
        if s["no"] in seen:
            continue
        seen.add(s["no"])
        uniq.append(s)
    return uniq


def parse_college_rows(rows, centers=None):
    """一个学院段的行（列分桶后的 {col: text} dict）→ (专业列表, 学院标题或 None)。"""
    programs, cur_prog, cur_dir = [], None, None

    def close_dir():
        nonlocal cur_dir
        if cur_dir is None:
            return
        label_c = cur_dir.pop("_c0")
        cur_dir["label"] = (cur_dir.get("_label0", "") + "".join(label_c)).strip()
        cur_dir["re_exam_subjects"] = re.sub(
            r"\s+", "", "".join(cur_dir.pop("_c3"))) or None
        addi = re.sub(r"\s+", "", "".join(cur_dir.pop("_c4")))
        cur_dir["additional_exam_subjects"] = (
            [x for x in ADDI_SPLIT.split(addi) if x] or None)
        note = re.sub(r"\s+", "", "".join(cur_dir.pop("_c5")))
        cur_dir["directions_note"] = note or None
        raw = re.sub(r"\s+", "", "".join(cur_dir.pop("_c2")))
        cur_dir["exam_subjects"] = split_subjects(raw)
        cur_dir.pop("_label0", None)
        cur_dir = None

    for row in rows:
        c0 = row.get(0, "")
        skip_c0 = False
        if not c0 and not any(row.values()):
            continue
        m = COLLEGE_HDR.match(re.sub(r"\s+", " ", c0).strip())
        if m and "学院" in m.group(2):
            close_dir()
            return programs, (m.group(1), f"{m.group(1)} {m.group(2)}")
        mp = PROG_HDR.match(re.sub(r"\s+", " ", c0).strip())
        if mp:
            close_dir()
            cur_prog = {"program_code": mp.group(1),
                        "program_name": mp.group(2).strip(),
                        "directions": []}
            programs.append(cur_prog)
            continue
        md = DIR_HDR.match(re.sub(r"\s+", " ", c0).strip())
        if md and cur_prog is not None:
            close_dir()
            rest = md.group(3)
            mc = COUNTS.search(rest)
            label0, num, glued_subj = rest, None, ""
            if mc:
                label0 = rest[:mc.start()]
                num = {"recommend": int(mc.group(1)), "exam": int(mc.group(2)),
                       "part_time": int(mc.group(3)), "raw": mc.group(0)}
                # 2027 版式把「(1)101思想政治理论」粘在人数后面
                glued_subj = rest[mc.end():]
            cur_dir = {"direction_code": md.group(1), "study_mode": md.group(2),
                       "_label0": label0.strip(), "_c0": [],
                       "_c2": [glued_subj] if glued_subj.strip() else [],
                       "_c3": [], "_c4": [], "_c5": [],
                       "_num": num}
            cur_prog["directions"].append(cur_dir)
            # 关键：**不要 continue**。方向的起始行上同时铺着「人数 / 复试笔试科目 /
            # 同等学力加试科目 / 备注」各列的第一个换行碎片，跳过后会丢掉每个单元格的
            # 开头几个字（实测会得到「信原理」而不是「数字电子技术与通信原理」、
            # 「字电子技术，数字信号处理。选一…」而不是「模拟电子技术，…」）。
            skip_c0 = True
        elif cur_dir is None:
            continue
        else:
            skip_c0 = False
        for ci in range(6):
            if ci == 0:
                if not skip_c0:
                    cur_dir["_c0"].append(row.get(0, ""))
            elif ci == 1:
                cell = row.get(1, "")
                if cur_dir["_num"] is None:
                    mc = COUNTS.search(cell)
                    if mc:
                        cur_dir["_num"] = {
                            "recommend": int(mc.group(1)),
                            "exam": int(mc.group(2)),
                            "part_time": int(mc.group(3)),
                            "raw": mc.group(0)}
                        tail = cell[mc.end():]
                        if tail.strip():
                            cur_dir["_c2"].append(tail)
            elif ci == 2:
                cur_dir["_c2"].append(row.get(2, ""))
            elif ci == 3:
                cur_dir["_c3"].append(row.get(3, ""))
            elif ci == 4:
                cur_dir["_c4"].append(row.get(4, ""))
            else:
                cur_dir["_c5"].append(row.get(5, ""))
    close_dir()
    return programs, None


# --------------------------------------------------------------------------- #
# 3) PDF → 学院段
# --------------------------------------------------------------------------- #
def pdf_colleges(path, verbose=False):
    """返回 [{college_code, college_name, pages, college_total_note_raw, programs}]"""
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages_rows = [page_rows(p) for p in reader.pages]

    # 页级列中心：数据行（排除标题/表头行）聚类
    page_centers = []
    for rows in pages_rows:
        data = [r for r in rows if not is_header_row(r)]
        page_centers.append(col_centers(data))

    colleges = []
    active = None
    buf = []

    def flush():
        nonlocal active, buf
        if active is None:
            buf = []
            return
        progs, _ = parse_college_rows(buf)
        active["programs"] = progs
        colleges.append(active)
        active, buf = None, []

    for pi, rows in enumerate(pages_rows):
        centers = page_centers[pi]
        for cells in rows:
            if is_header_row(cells):
                continue
            row = bucket_row(cells, centers)
            c0 = re.sub(r"\s+", " ", row.get(0, "")).strip()
            if not c0 and not any(row.values()):
                continue
            mcol = COLLEGE_HDR.match(c0)
            if mcol and "学院" in mcol.group(2):
                # 学院标题行在**视觉顺序上位于该学院区块的开头**（页眉下方），
                # 因此它开启一个新学院，之前缓冲的行属于上一个学院。
                # 注意：pypdf 的平铺文本会把它吐在该页内容之后，靠坐标行序才不会错位。
                flush()
                active = {"college_code": mcol.group(1),
                          "college_name": f"{mcol.group(1)} {mcol.group(2)}",
                          "pages": [pi + 1], "note_raw": None}
                continue
            if active is None:
                continue
            if pi + 1 not in active["pages"]:
                active["pages"].append(pi + 1)
            mnote = re.search(r"预计接收推免生(\d+)人[；;]?\s*预计招收全日制统考(\d+)人[^。\n]*?非全日制(\d+)人", c0)
            if mnote:
                active["note_raw"] = mnote.group(0)
                continue
            buf.append(row)
    flush()
    out = []
    for c in colleges:
        out.append({"college_code": c["college_code"],
                    "college_name": c["college_name"],
                    "pages": c["pages"],
                    "college_total_note_raw": c["note_raw"],
                    "college_total_note": (
                        {"recommend": int(re.search(r"推免生(\d+)", c["note_raw"]).group(1)),
                         "exam": int(re.search(r"统考(\d+)", c["note_raw"]).group(1)),
                         "part_time": int(re.search(r"非全日制(\d+)", c["note_raw"]).group(1)),
                         "raw": c["note_raw"]}
                        if c["note_raw"] else None),
                    "programs": c["programs"]})
    if verbose:
        for c in out:
            print(f"    {c['college_name']} pages={c['pages']} "
                  f"progs={[(p['program_code'], len(p['directions'])) for p in c['programs']]}")
    return out


# --------------------------------------------------------------------------- #
# 4) 简章：学制 / 学费
# --------------------------------------------------------------------------- #
def parse_duration(html):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"学制一般为\s*(\d+(?:\.\d+)?)\s*年", t)
    return ({"duration_years": float(m.group(1)), "raw": m.group(0)} if m else None)


def parse_tuition(html):
    from crawler import extract
    rows_out = {}
    for table in extract.extract_tables(html):
        for row in table:
            joined = " ".join(row)
            if "万元" not in joined:
                continue
            mv = re.search(r"(\d+(?:\.\d+)?)\s*万元\s*/\s*生\s*·?\s*年", joined)
            if not mv:
                continue
            key = next((c for c in row if c and "硕士" in c or (c and "学位" in c)), "")
            if not key:
                key = row[0]
            rows_out[key.strip()] = {"value": f"{mv.group(1)}万元/生·年",
                                     "raw_row": row}
    return rows_out


# --------------------------------------------------------------------------- #
def flatten(college, tuition_map, duration):
    out = []
    for p in college["programs"]:
        dirs = p["directions"]
        if not dirs:
            continue
        base = next((d for d in dirs if d["study_mode"] == "全日制"), dirs[0])
        plan = base.get("_num")
        is_academic = not (p["program_code"][:2] == "08"
                           and p["program_code"][2:4] in PRO_DEGREE_08)
        if is_academic:
            tui_key = next((k for k in tuition_map if "学术学位硕士" in k), None)
        else:
            tui_key = next((k for k in tuition_map if "工程类专业学位" in k
                            and "电子信息" in k), None) \
                or next((k for k in tuition_map if "专业学位硕士" in k), None)
        scale = None
        if plan:
            scale = {"recommend": plan["recommend"], "exam": plan["exam"],
                     "part_time": plan["part_time"],
                     "admission_scale": plan["recommend"] + plan["exam"],
                     "sum_formula": "推免+统考", "raw": plan["raw"]}
        out.append({
            "program_code": p["program_code"],
            "program_name": p["program_name"],
            "degree_type": "全日制学术学位" if is_academic else "全日制专业学位",
            "admission_scale": scale,
            "base_plan_label": base["label"],
            "study_mode": base["study_mode"],
            "duration_years": duration["duration_years"] if duration else None,
            "tuition": tuition_map[tui_key]["value"] if tui_key else None,
            "exam_subjects": [s["text"] for s in base["exam_subjects"]],
            "exam_subject_codes": {str(s["no"]): s["code"] for s in base["exam_subjects"]},
            "re_exam_subjects": base["re_exam_subjects"],
            "additional_exam_subjects": base["additional_exam_subjects"],
            "directions_note": base["directions_note"],
            "other_plans": [
                {"label": d["label"], "study_mode": d["study_mode"],
                 "plan": ({"recommend": d["_num"]["recommend"],
                           "exam": d["_num"]["exam"],
                           "part_time": d["_num"]["part_time"],
                           "admission_scale": d["_num"]["recommend"] + d["_num"]["exam"],
                           "raw": d["_num"]["raw"]} if d.get("_num") else None),
                 "exam_subjects": [s["text"] for s in d["exam_subjects"]]}
                for d in dirs if d is not base],
        })
    return out


def retest_stats(path):
    """解析信通学院 2026 年第一志愿复试名单 PDF，按专业统计进入复试者的初试总分。

    **这是「进入复试名单者的最低初试总分」，不是学校公布的复试分数线**
    （哈工程研招网不公布普通计划专业复试线），因此结果只放进
    extracted.retest_list_2026 供追溯，绝不写进 score_line_* 字段。
    """
    from pypdf import PdfReader
    row_re = re.compile(
        r"^(\d{15})\s+(\S+)\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+"
        r"(\S+?)\s+(\S.*)$")
    by_major = {}
    reader = PdfReader(str(path))
    for page in reader.pages:
        text = (page.extract_text() or "").replace("\u3000", " ")
        for line in text.splitlines():
            m = row_re.match(line.strip())
            if not m:
                continue
            major = m.group(8)
            by_major.setdefault(major, []).append(
                {"id": m.group(1), "zz": int(m.group(3)), "wy": int(m.group(4)),
                 "math": int(m.group(5)), "major_course": int(m.group(6)),
                 "total": int(m.group(7))})
    out = {}
    for major, rows in sorted(by_major.items()):
        totals = sorted(r["total"] for r in rows)
        out[major] = {
            "count": len(rows),
            "min_total": totals[0], "max_total": totals[-1],
            "min_5_totals": totals[:5],
            "min_single": {k: min(r[k] for r in rows)
                           for k in ("zz", "wy", "math", "major_course")},
            "note": ("进入复试名单者的初试总分统计（第一志愿），"
                     "不是学校公布的复试分数线；也不能当作录取最低分。"),
        }
    return {"applicable_year": 2026, "source": {"title":
            "附件4 信息与通信工程学院2026年硕士研究生复试名单（第一志愿报考我院）",
            "page_url": RETEST_PAGE, "attachment_url": RETEST_PDF,
            "pdf_pages": len(reader.pages), "source_level": "official"},
            "by_major": out}


def retest_line_from_records(year=None):
    """从已抓取的 data/schools/heu.json 里取学院「复试工作办法」页的分专业复试要求。

    哈工程研招网不公布普通计划专业复试线，但**信通学院自己的复试工作办法 HTML
    里有一张分专业招生计划 + 复试分数要求 + 差额比例表**（2025 年版实测有），
    这是本站唯一可文本抽取、可追溯到 URL 的复试分数要求来源。

    返回 {applicable_year, source{...}, by_major{code:{...}}, re_exam_subjects,
          additional_exam_subjects, score_weight, score_formula, raw_table}
    """
    path = os.path.join(ROOT, "data", "schools", "heu.json")
    if not os.path.exists(path):
        return None
    doc = json.loads(read(path))
    for rec in doc.get("records", []):
        if "复试工作办法" not in (rec.get("title") or ""):
            continue
        table = None
        for t in rec.get("tables") or []:
            flat = " ".join(" ".join(str(c) for c in row) for row in t)
            if "专业代码" in flat and "差额" in flat:
                table = t
                break
        if not table:
            continue
        by_major = {}
        for row in table[1:]:
            cells = [str(c).strip() for c in row] + [""] * 7
            code = cells[0].strip()
            if not re.match(r"^[01]\d{5}$", code):
                continue
            req = cells[3]
            m = re.search(r"总分不低于\s*(\d+)\s*分", req)
            ratio = re.search(r"(\d{2,3})\s*%", cells[6])
            by_major.setdefault(code, []).append({
                "program_name": cells[1], "study_mode": cells[2],
                "score_requirement": req,
                "score_line_total": int(m.group(1)) if m else None,
                "plan": int(cells[4]) if cells[4].isdigit() else None,
                "retest_count": int(cells[5]) if cells[5].isdigit() else None,
                "retest_ratio": (ratio.group(1) + "%") if ratio else None,
                "note": cells[6] if not ratio else "",
            })
        content = rec.get("content") or ""
        subj = re.search(r"笔试\s*[：:]\s*考试科目[为]?\s*([^\n，。]+)", content)
        # 加试科目优先用「按照先 X，后 Y 的顺序」这一句（最完整、不受 OCR 断句影响），
        # 退化为「需加试…」后按顿号/逗号切分。
        addi_m = re.search(r"按照先\s*([^，,]+?)\s*[，,]\s*后\s*([^的]+?)\s*的顺序", content)
        if addi_m:
            addi_list = [addi_m.group(1).strip(), addi_m.group(2).strip()]
        else:
            addi_m = re.search(r"需加试\s*([^\n。]+?)\s*两门", content)
            addi_list = ([s.strip() for s in re.split(r"[、，,]", addi_m.group(1)) if s.strip()]
                         if addi_m else None)
        formula = re.search(r"总成绩\s*=\s*\(?\s*初试总成绩[^\n]+", content)
        ym = re.search(r"(20\d{2})\s*年", rec.get("title") or "")
        return {
            "applicable_year": int(ym.group(1)) if ym else None,
            "source": {"title": rec.get("title"), "page_url": rec.get("url"),
                       "published": rec.get("published"),
                       "fetched_at": rec.get("fetched_at"), "source_level": "official"},
            "by_major": {k: v for k, v in by_major.items()},
            "re_exam_subjects": (re.sub(r"^[为：:]\s*", "", subj.group(1).strip())
                                 if subj else None),
            "additional_exam_subjects": addi_list,
            "score_formula": re.sub(r"\s+", "", formula.group(0)) if formula else None,
            "raw_table": table,
        }
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.download:
        from crawler import http
        os.makedirs(SEARCH_DIRS[0], exist_ok=True)
        for _y, name, _page, att, _title in PDFS:
            dest = os.path.join(SEARCH_DIRS[0], name)
            raw = http.fetch_binary(att, delay=1.2)
            with open(dest, "wb") as f:
                f.write(raw)
            print("saved", dest, len(raw))

    history = []
    for year, name, page_url, att_url, title in PDFS:
        path = find(name)
        if not path:
            print(f"  ! 缺 {name}，跳过 {year}")
            continue
        print(f"  解析 {year} {name}")
        colls = pdf_colleges(path, verbose=not args.quiet)
        # 只保留真正开设信息与通信工程方向的两个学院，避免把同校其他学院
        # （004 智能科学与工程学院 / 051 烟台研究院 等）的 085400 电子信息
        # 也拉进来 —— 那些学院的初试科目与信通方向无关。
        rec = {"applicable_year": year,
               "source": {"title": title, "page_url": page_url,
                          "attachment_url": att_url,
                          "pdf_pages": len(__import__("pypdf").PdfReader(path).pages),
                          "source_level": "official"},
               "colleges": [c for c in colls
                            if c["college_code"] in KEEP_COLLEGES
                            and any(p["program_code"] in TARGET_CODES
                                    for p in c["programs"])]}
        print(f"    -> {year} 命中 {len(rec['colleges'])} 个学院")
        if rec["colleges"]:
            history.append(rec)

    if not history:
        print("没有解析到任何年份")
        return 1
    history.sort(key=lambda h: h["applicable_year"], reverse=True)

    gp = find(GUIDE_HTML[0])
    duration = tuition_map = None
    if gp:
        gh = read(gp)
        duration = parse_duration(gh)
        tuition_map = parse_tuition(gh)
        print("  学制:", json.dumps(duration, ensure_ascii=False))
        print("  学费表:", json.dumps({k: v["value"] for k, v in tuition_map.items()},
                                       ensure_ascii=False))

    cur = history[0]
    main_coll = next((c for c in cur["colleges"] if c["college_code"] == MAIN_COLLEGE),
                     cur["colleges"][0])
    others = [c for c in cur["colleges"] if c is not main_coll]

    rp = find(RETEST_PDF_NAME)
    retest = retest_stats(rp) if rp else None
    if retest:
        print("  复试名单 2026:", json.dumps(
            {k: (v["count"], v["min_total"], v["max_total"])
             for k, v in retest["by_major"].items()}, ensure_ascii=False))

    # 学院「复试工作办法」里的分专业复试要求（分数要求 / 差额比例 / 复试科目 / 加试 /
    # 总成绩折算）。这是本校唯一可文本抽取的复试分数要求来源。
    retest_line = retest_line_from_records()
    metric_years = {"exam_subjects": cur["applicable_year"],
                    "admission_scale": cur["applicable_year"],
                    "duration_years": cur["applicable_year"],
                    "tuition": cur["applicable_year"]}
    programs = flatten(main_coll, tuition_map or {}, duration)
    if retest_line:
        print("  复试要求(%s):" % retest_line["applicable_year"], json.dumps(
            {k: [(v[0]["score_requirement"], v[0]["plan"], v[0]["retest_count"],
                  v[0]["retest_ratio"]) for v in [vv]]
             for k, vv in retest_line["by_major"].items()}, ensure_ascii=False))
        for prog in programs:
            rows = retest_line["by_major"].get(prog["program_code"])
            if not rows:
                continue
            full_time = [r for r in rows if r["study_mode"] == "全日制"] or rows
            r0 = full_time[0]
            prog["score_line_total"] = r0["score_line_total"]
            prog["score_line_total_source"] = {
                "applicable_year": retest_line["applicable_year"],
                "score_requirement": r0["score_requirement"],
                "page_url": retest_line["source"]["page_url"],
                "published": retest_line["source"]["published"],
                "fetched_at": retest_line["source"]["fetched_at"],
                "note": "学院复试工作办法原文的复试分数要求；不是全校统一分数线。",
            }
            if r0["score_line_total"] is None:
                prog["score_line_total_note"] = (
                    "原文只写「%s」，未给出具体总分要求，因此 score_line_total 保持 null，"
                    "不把国家线数值填进来。" % r0["score_requirement"])
            prog["re_exam_ratio"] = r0["retest_ratio"]
            prog["retest_plan"] = {"plan": r0["plan"], "retest_count": r0["retest_count"],
                                   "study_mode": r0["study_mode"],
                                   "applicable_year": retest_line["applicable_year"]}
            prog["re_exam_subjects"] = retest_line["re_exam_subjects"]
            prog["additional_exam_subjects"] = retest_line["additional_exam_subjects"]
            prog["score_weight"] = retest_line["score_formula"]
            metric_years.update({"score_line_total": retest_line["applicable_year"],
                                 "re_exam_ratio": retest_line["applicable_year"],
                                 "re_exam_subjects": retest_line["applicable_year"],
                                 "score_weight": retest_line["applicable_year"]})

    missing = [
        "score_line_single", "line_vs_national",
        "admission_ratio", "admitted_min_score",
        "recommend_ratio", "reference_books_count", "discipline_rating", "has_phd",
        "accepts_cross_major", "policy_change_flag",
    ]
    if not retest_line:
        missing = ["score_line_total", "re_exam_ratio", "score_weight"] + missing
    unknowns = [
        "复试分数线 score_line_total/score_line_single：哈工程研招网不公布普通计划的"
        "专业复试线，全站栏目扫描只找到「退役大学生士兵计划」专项分数线。"
        "本文件的 score_line_total 取自学院（008 信通学院）自己发布的《复试工作办法》"
        "正文表格里的「分数要求」列（081000：满足国家线且总分不低于 280 分），"
        "source 已逐条给出 page_url / published / fetched_at；"
        "单科线（score_line_single）与「与国家线之差」原文未给，保持 null。"
        "2026 年普通计划复试名单 PDF（见 data/schools/heu.json 的 attachments）"
        "可反推进入复试者的最低初试总分，但那是「进入复试者最低分」而不是官方分数线，"
        "且不等同于录取最低分，故不写进 score_line_*，只放在 retest_list_2026。",
        "2025 及更早年份的专业目录页面已被统一身份认证（CAS）拦截"
        "（跳转 https://cas.hrbeu.edu.cn/cas/login?...），无法抓取，"
        "所以 history 只覆盖 2026 / 2027 两个年度，不构成「近三年」。",
        "参考书目 reference_books_count：哈工程公布的是「初试科目考试大纲」"
        "（考试内容范围说明，见 %s 的 PDF %s），只写考试范围不列参考书，"
        "故 reference_books 为空。" % (SYLLABUS_PAGE, SYLLABUS_PDF),
        "085402 通信工程：哈工程按 085400 电子信息大类招生，专业目录中不存在 "
        "085402 这个专业代码，本文件以 085400 电子信息作为最接近方向，"
        "program_code 保留 085400 原值未改动。",
        "学费表（简章 §九）在 HTML 里是合并单元格，extract_tables 拍平后"
        "「学习形式」列会错位；本文件按「行内出现电子信息等工程类专业学位 = 1.6 万元/生·年」"
        "这一更具体的规则取值，并把原始行随 raw_row 一起保留以便复核。",
        "学院级总数与各专业人数不自洽：008 学院 2027 学院级注为「推免 153 人 / 统考 178 人」，"
        "而该学院各专业（081000 59+31、085400 基础 40+97 加各专项、0804J7 14+5、080900 7+5）"
        "逐项求和与学院级数字不相等（学院级含其他专业），不能互相校验，"
        "本文件两者分别保留、不做换算。",
    ]

    doc = {
        "school_id": "heu",
        "school_name": "哈尔滨工程大学",
        "applicable_year": cur["applicable_year"],
        "college_code": main_coll["college_code"],
        "college_name": main_coll["college_name"],
        "metric_years": metric_years,
        "source": dict(cur["source"],
                       fetched_at=datetime.now(CST).isoformat(timespec="seconds"),
                       site_render="服务端渲染 HTML + 博达 CMS 附件 PDF（无需 JS）"),
        "extracted": {
            "college_pages": main_coll["pages"],
            "college_note": (
                "081000 信息与通信工程在哈工程由两个学院分别招生："
                "008 信息与通信工程学院/集成电路学院（初试④815 电路、信号与系统）与 "
                "005 水声工程学院（初试④810 信号与系统），两处招生人数必须分开看。"
                "本文件 college_code/college_name 指向 008，005 的专业全部放在 other_colleges。"
                "专业目录 PDF 为 6 列合并单元格表格，本文件由文本碎片的 (x, y) 坐标"
                "重建列后取值，不依赖平铺文本的行序。"
            ),
            "college_total_note": main_coll["college_total_note"],
            "college_total_note_raw": main_coll["college_total_note_raw"],
            "duration_note": duration,
            "tuition_table": {k: v["value"] for k, v in (tuition_map or {}).items()},
            "tuition_table_raw_rows": {k: v["raw_row"] for k, v in (tuition_map or {}).items()},
            "programs": programs,
            "other_colleges": [
                {"college_code": c["college_code"], "college_name": c["college_name"],
                 "college_pages": c["pages"],
                 "college_total_note": c["college_total_note"],
                 "college_total_note_raw": c["college_total_note_raw"],
                 "programs": flatten(c, tuition_map or {}, duration)}
                for c in others
            ],
            "reference_books": {"by_exam_code": {}, "titles": [], "count": 0,
                                "note": "哈工程只公布初试科目考试大纲（考试内容范围说明），不列参考书"},
            "exam_syllabus": {"page_url": SYLLABUS_PAGE, "pdf_url": SYLLABUS_PDF,
                              "subject_name": "电路、信号与系统（815）"},
            "retest_list_2026": retest,
            "retest_line": retest_line,
            "history": history,
        },
        "missing_fields": missing,
        "unknowns": unknowns,
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
