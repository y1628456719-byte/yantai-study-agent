"""东北大学（yz.neu.edu.cn）信息与通信工程方向专业目录解析。

输入（全部为已抓取的本地原始页，见 data/raw/_probe/）：
    招生专业目录（HTML 内联表，含 ①-④ 初试科目与代码）
        https://yz.neu.edu.cn/2023/0919/c5933a235261/page.htm   2024
        https://yz.neu.edu.cn/2024/1001/c5933a269652/page.htm   2025
        https://yz.neu.edu.cn/2025/1009/c5933a293436/page.htm   2026
        https://yz.neu.edu.cn/2026/0930/c5933a480171/page.htm   2027
    招生章程附件 PDF（拟招生人数 / 学制 / 学费）
        https://yz.neu.edu.cn/2025/1009/c5933a293424/page.htm

产出：data/schools/neu_catalog.json
    与 data/schools/jlu_catalog_502.json 同构（school_id/school_name/applicable_year/
    college_code/college_name/source/extracted），额外补 history（历年科目）与
    missing_fields/unknowns —— 拿不到的维度一律 null，绝不推测。

用法：
    python scripts/prepare_neu_catalog.py            # 解析已存档 HTML/PDF，产出 JSON
    python scripts/prepare_neu_catalog.py --download # 先重新下载原始页再解析
"""
import argparse
import html as _html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 原始页/PDF 分散在两处存档目录，统一按文件名查找
SEARCH_DIRS = [
    os.path.join(ROOT, "data", "raw", "_probe"),
    os.path.join(ROOT, "data", "raw", "_probe", "pdf"),
    os.path.join(ROOT, "data", "_probe"),
]


def find(name):
    """按文件名在所有存档目录里查找，返回绝对路径或 None。"""
    for d in SEARCH_DIRS:
        p = os.path.join(d, os.path.basename(name))
        if os.path.exists(p):
            return p
    return None


OUT = os.path.join(ROOT, "data", "schools", "neu_catalog.json")

TARGET_COLLEGE = "017"
TARGET_CODES = ("081000", "085402")

CATALOG_PAGES = [
    # (applicable_year, page_url, local_html)
    (2027, "https://yz.neu.edu.cn/2026/0930/c5933a480171/page.htm", "neu_2027_catalog.html"),
    (2026, "https://yz.neu.edu.cn/2025/1009/c5933a293436/page.htm", "neu_2026_catalog.html"),
    (2025, "https://yz.neu.edu.cn/2024/1001/c5933a269652/page.htm", "neu_zsml2025.html"),
    (2024, "https://yz.neu.edu.cn/2023/0919/c5933a235261/page.htm", "neu_zsml2024.html"),
]

# 2026 招生章程页的附件（拟招生人数 / 学制 / 学费）
PLAN_PAGE = "https://yz.neu.edu.cn/2025/1009/c5933a293424/page.htm"
SCALE_PDF = "neu_zc2026_pdf.pdf"
XUEZHI_PDF = "neu_xuezhi2026.pdf"
XUEFEI_PDF = "neu_xuefei2026.pdf"

# 2027 招生章程页的附件（学制 / 学费；**2027 年拟招生人数表尚未发布**）
# 2027 的学制/学费明细表里确实逐行列了 017 学院 081000 / 085402（已人工抽查复核），
# 所以这两个维度取 2027 年度值，比 2026 更贴近当前招生年度。
GUIDE_2027_PAGE = "https://yz.neu.edu.cn/2026/0930/c5933a480167/page.htm"
XUEZHI_PDF_2027 = "neu_2027_xuezhi.pdf"
XUEFEI_PDF_2027 = "neu_2027_xuefei.pdf"
XUEZHI_ATT_2027 = "fbd72bb5-da62-41ab-935c-65bf68fb6a91.pdf"
XUEFEI_ATT_2027 = "ad11c8dc-9d3a-4bd9-a9ee-06834729942c.pdf"

# 2026 年复试工作安排页（含复试名单）——本校唯一能文本抽取到分数的页面
RETEST_PAGE = "https://yz.neu.edu.cn/2026/0314/c5932a451100/page.htm"
RETEST_PDF_DIR = os.path.join(ROOT, "data", "raw", "_probe", "pdf", "neu_fs2026")
RETEST_PDF_NAME = "c7773530-ec79-48ee-963f-babc0aae4392.pdf"
RETEST_PDF = ("https://yz.neu.edu.cn/_upload/article/files/41/6c/"
              "ef07f6dc4ec4b560a0b9269d1a51/c7773530-ec79-48ee-963f-babc0aae4392.pdf")

# 2026 年 017 学院复试参考信息（复试科目/比例/加试/总成绩折算的唯一官方件）
REF017_PAGE = "https://yz.neu.edu.cn/2025/1018/c5933a293857/page.htm"
REF017_PDF = ("https://yz.neu.edu.cn/_upload/article/files/1f/0d/"
              "b7d07d3c48029807e784d5331a44/ecdc4ae2-dd60-440d-b8ab-9c860f438488.pdf")
REF017_NAME = "neu_2026_ref_017.pdf"

TAG = re.compile(r"(?s)<[^>]+>")
WS = re.compile(r"[ \t\u3000]+")


# --------------------------------------------------------------------------- #
# rowspan/colspan 感知的表格解析：把合并单元格展开成规整矩阵
# --------------------------------------------------------------------------- #
class SpanTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []          # [[ [cell,...], ... ]]
        self._stack = []          # 当前表 -> 行列表
        self._cells = None
        self._buf = []
        self._in_cell = False
        self._span = (1, 1)
        self._pending = []        # 因 rowspan 悬空的单元格：{col, left, text}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append([])
            self._pending = []
        elif tag == "tr" and self._stack:
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._in_cell = True
            self._buf = []
            try:
                cs = int(a.get("colspan") or 1)
            except ValueError:
                cs = 1
            try:
                rs = int(a.get("rowspan") or 1)
            except ValueError:
                rs = 1
            self._span = (max(cs, 1), max(rs, 1))
        elif tag == "br" and self._in_cell:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            txt = WS.sub(" ", "".join(self._buf)).strip()
            cs, rs = self._span
            self._cells.append({"text": txt, "colspan": cs, "rowspan": rs})
            self._in_cell = False
        elif tag == "tr" and self._cells is not None and self._stack:
            self._stack[-1].append(self._cells)
            self._cells = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
            self._pending = []
            self._cells = None

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)


def html_tables(html):
    p = SpanTable()
    p.feed(html)
    return p.tables


def expand_table(rows):
    """把 rowspan/colspan 展开为规则矩阵；返回 list[list[str]]。"""
    pending = {}   # col -> [剩余行数, 文本]
    matrix = []
    for cells in rows:
        row = []
        col = 0
        it = list(cells)
        while it or col in pending:
            if col in pending:
                cnt, txt = pending[col]
                row.append(txt)
                if cnt - 1 <= 0:
                    del pending[col]
                else:
                    pending[col] = [cnt - 1, txt]
                col += 1
                continue
            cell = it.pop(0)
            for k in range(cell["colspan"]):
                row.append(cell["text"])
                if cell["rowspan"] > 1:
                    pending[col + k] = [cell["rowspan"] - 1, cell["text"]]
            col += cell["colspan"]
        matrix.append(row)
    return matrix


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# 专业目录解析
# --------------------------------------------------------------------------- #
COLLEGE_RE = re.compile(r"^(\d{3})\s*([\u4e00-\u9fa5A-Za-z（）()、·\-]{2,30})$")
PROG_RE = re.compile(r"^([01]\d{5})\s*(.+)$")
DIR_RE = re.compile(r"^(\d{2})\s*(.+)$")
EXAM_PREFIX = "①②③④⑤⑥"
EXAM_SPLIT = re.compile(r"(?=[①②③④⑤⑥])")


def split_exam_subjects(text):
    """'①101思想政治理论②201英语（一）③301数学（一）④841通信专业基础'
    -> ['101 思想政治理论', '201 英语（一）', '301 数学（一）', '841 通信专业基础']"""
    out = []
    for chunk in EXAM_SPLIT.split((text or "").strip()):
        chunk = chunk.strip()
        if not chunk or chunk[0] not in EXAM_PREFIX:
            continue
        body = chunk[1:].strip()
        m = re.match(r"^(\d{3})\s*(-{0,2})\s*(.*)$", body)
        if m:
            code, dash, name = m.group(1), m.group(2), m.group(3).strip()
            if not name and dash:
                name = "无"
            out.append({"code": code, "name": name, "text": f"{code} {name}".strip()})
        elif body:
            out.append({"code": None, "name": body, "text": body})
    return out


def parse_catalog(html):
    """返回 {college_code: {name, programs:[...]}}，只保留目标学院。

    原始 HTML 的「院系所」「专业」两列都用 rowspan 合并；先用
    expand_table() 把合并单元格向下填满，此时每行都是规整的
    [院系所, 专业, 研究方向, 学习方式, 考试科目]，再按
    (学院, 专业) 是否变化来切分专业块、把同一专业的多条研究方向合并。
    """
    tables = html_tables(html)
    if not tables:
        return {}
    best = max(tables, key=lambda t: len(t))
    matrix = expand_table(best)
    if len(matrix) < 2:
        return {}
    out = {}
    prev = (None, None)
    program = None
    for row in matrix[1:]:
        if len(row) < 5:
            row = list(row) + [""] * (5 - len(row))
        c0, c1 = row[0].strip(), row[1].strip()
        mcol = COLLEGE_RE.match(c0)
        if not mcol:
            continue
        college_code, college_name = mcol.group(1), c0
        mprog = PROG_RE.match(c1)
        if not mprog:
            continue
        key = (college_code, mprog.group(1))
        if key != prev:
            prev = key
            program = None
            if college_code == TARGET_COLLEGE:
                program = {
                    "program_code": mprog.group(1),
                    "program_name": mprog.group(2).strip(),
                    "directions": [],
                    "study_mode": row[3].strip(),
                    "exam_subjects_raw": row[4].strip(),
                }
                blk = out.setdefault(college_code, {"name": college_name, "programs": []})
                blk["programs"].append(program)
        if program is not None:
            d = row[2].strip()
            if d and d not in program["directions"]:
                program["directions"].append(d)
            if not program["exam_subjects_raw"] and row[4].strip():
                # 极少数专业把「考试科目」放在研究方向首行之后
                program["exam_subjects_raw"] = row[4].strip()
    return out


# --------------------------------------------------------------------------- #
# PDF 解析
# --------------------------------------------------------------------------- #
def pdf_pages(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    return [(pg.extract_text() or "").replace("\u3000", " ") for pg in r.pages]


NUM_PAIR = re.compile(r"(?<![\d])(\d{1,3})\s+(\d{1,3})(?![\d])")


def parse_scale(pages):
    """2026 招生章程附件：拟招生人数 / 硕士推免人数。

    pypdf 抽出的行里，某一专业的两个数字单独占一行，出现在该专业最后一条
    「主校区」方向行之后（秦皇岛/佛山等异地校区各自单列）。因此取
    「专业代码出现之后、同专业下一个校区行之前」的孤立数字行。
    """
    full = "\n".join(pages)
    out = {}
    for code in TARGET_CODES:
        rec = {"program_code": code}
        for m in re.finditer(re.escape(code), full):
            seg = full[m.start(): m.start() + 900]
            # 只取主校区（专业代码首次出现的那一段）
            main = seg.split("【")[0]
            hit = None
            for para in NUM_PAIR.finditer(main):
                hit = para
                break
            if hit:
                rec["plan"] = int(hit.group(1))
                rec["recommend"] = int(hit.group(2))
                rec["raw"] = WS.sub(" ", main)[:220]
                break
        out[code] = rec
    return out


def parse_duration(pages):
    """学制表：'017 计算机科学与工程学院 081000 信息与通信工程 全日制 3'"""
    out = {}
    for pg in pages:
        for code in TARGET_CODES:
            for m in re.finditer(re.escape(code), pg):
                seg = WS.sub(" ", pg[m.start(): m.start() + 160])
                dm = re.search(r"全日制\s*(\d)\s*年?", seg)
                if dm and code not in out:
                    out[code] = {"duration_years": int(dm.group(1)), "raw": seg[:120]}
    return out


def parse_tuition(pages):
    """学费表：'017 计算机科学与工程学院 081000 信息与通信工程 全日制 8000元/年.生'"""
    out = {}
    for pg in pages:
        for code in TARGET_CODES:
            for m in re.finditer(re.escape(code), pg):
                seg = WS.sub(" ", pg[m.start(): m.start() + 200])
                tm = re.search(r"(\d{3,6})\s*元\s*/\s*年", seg)
                if tm and code not in out:
                    out[code] = {"tuition": f"{tm.group(1)}元/年·生", "amount": int(tm.group(1)),
                                 "raw": seg[:140]}
    return out


# --------------------------------------------------------------------------- #
def retest_stats(path):
    """解析 017 计算机科学与工程学院 2026 年复试名单 PDF。

    该 PDF 的第 1 页是「2026 年硕士研究生分专业统考招生计划」表（**统考**名额，
    即从拟招生人数中扣除推免之后的数），第 2 页起是复试名单（每人政治/外语/
    业务课一/业务课二/初试总分）。

    重要：这里统计出来的是**进入复试名单者**的初试总分，不是学校公布的复试分数线，
    也不是录取最低分，因此只放进 extracted.retest_list_2026，绝不写进 score_line_*。
    """
    from pypdf import PdfReader
    plan_re = re.compile(
        r"^(\d{3})\s+(\S+?)\s+([0-9A-Z]{6})\s+(\S+?)\s+(\S+?)\s+(全日制|非全日制)\s+(\d+)$")
    row_re = re.compile(
        r"^(1\d{14})\s+(\S+)\s+(\d{3})\s+(\S+?)\s+(\d{6})\s+(\S+?)\s+"
        r"(\d{2}|【[^】]+】)\s+(\S.*?)\s+(\d{2,3})\s+(\d{2,3})\s+(\d{2,3})\s+(\d{2,3})\s+(\d{2,3})$")
    plans, by_major = [], {}
    reader = PdfReader(str(path))
    for page in reader.pages:
        text = (page.extract_text() or "").replace("\u3000", " ")
        for line in text.splitlines():
            line = line.strip()
            m = plan_re.match(line)
            if m:
                plans.append({"college": m.group(1) + m.group(2), "major_code": m.group(3),
                              "major": m.group(4), "direction": m.group(5),
                              "mode": m.group(6), "plan": int(m.group(7))})
                continue
            m = row_re.match(line)
            if m:
                by_major.setdefault(m.group(5), []).append(
                    {"id": m.group(1), "zz": int(m.group(9)), "wy": int(m.group(10)),
                     "k1": int(m.group(11)), "k2": int(m.group(12)),
                     "total": int(m.group(13))})
    stats = {}
    for code, rows in sorted(by_major.items()):
        totals = sorted(r["total"] for r in rows)
        stats[code] = {
            "count": len(rows), "min_total": totals[0], "max_total": totals[-1],
            "min_5_totals": totals[:5],
            "min_single": {k: min(r[k] for r in rows) for k in ("zz", "wy", "k1", "k2")},
            "note": ("进入复试名单者的初试总分统计（第一志愿），不是学校公布的复试分数线，"
                     "也不能当作录取最低分。"),
        }
    return {
        "applicable_year": 2026,
        "source": {"title": "东北大学2026年硕士研究生招生考试复试工作安排（含复试名单）",
                   "page_url": RETEST_PAGE, "attachment_url": RETEST_PDF,
                   "pdf_pages": len(reader.pages), "source_level": "official"},
        "plan_table_017": [p for p in plans if p["college"].startswith(TARGET_COLLEGE)],
        "by_major_017": {k: v for k, v in stats.items() if k in TARGET_CODES},
    }


def retest_reference_017(path):
    """解析 017 计算机科学与工程学院 2026 年硕士研究生复试参考信息 PDF。

    这份件是本校唯一能把「复试考什么」落到具体专业的官方件：每行给出
    复试方式及分数组成（专业机试 40 分 + 综合面试 60 分）、专业素质和能力
    考核内容参考书目、复试比例（120%）、复试名单/拟录取名单确定规则、
    同等学力加试科目及参考书目。文末还给出全校总成绩折算办法，
    即 初试 65% + 复试 35%（对应 SPEC §6 的 re_exam_ratio(6) 与 score_weight(7)）。
    """
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    full = "\n".join((p.extract_text() or "").replace("\u3000", " ")
                     for p in reader.pages)
    fm = re.search(r"初试成绩满分为\s*500\s*分的专业[^\n]*", full)
    formula = fm.group(0).strip() if fm else ""
    score_weight = ""
    wm = re.search(r"初试总分\s*[÷/]\s*(\d+)\s*×\s*(\d+)%\s*\+\s*复试总分\s*[÷/]\s*\d+\s*×\s*(\d+)%",
                   full)
    if wm:
        score_weight = (f"初试 {wm.group(2)}% + 复试 {wm.group(3)}%"
                        f"（总成绩=初试总分÷{wm.group(1)}×{wm.group(2)}%+复试总分×{wm.group(3)}%）")

    code_re = re.compile(r"(?<![\d])([01]\d{5})(?!\d)")
    marks = [(m.start(), m.group(1)) for m in code_re.finditer(full)]
    by_major = {}
    for i, (pos, code) in enumerate(marks):
        if code not in TARGET_CODES:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(full)
        seg = full[pos:end]
        ratio = re.search(r"(?<![\d])(\d{2,3})\s*%", seg)
        method = re.search(r"专业机试\s*(\d+)\s*分\s*综合面试\s*(\d+)\s*分", seg)
        books = [{"title": t.strip(), "publisher": p.strip()}
                 for t, p in re.findall(
                     r"[\[［]\d[\]］]\s*《([^》]+)》([^\n〔〕\[\]［］]*)", seg)]
        addi = [{"subject": s.strip(), "reference_book": b.strip()}
                for s, b in re.findall(
                    r"[\[［]\d[\]］]\s*([^\n《]{2,30}?)\s*\n\s*参考书目[：:]\s*《([^》]+)》", seg)]
        am = re.search(r"按报考志愿[^\n]*", seg)
        by_major[code] = {
            "re_exam_ratio": (ratio.group(1) + "%") if ratio else None,
            "re_exam_method": (f"专业机试{method.group(1)}分 + 综合面试{method.group(2)}分")
                              if method else None,
            "re_exam_content_reference_books": books,
            "additional_exam_subjects": [a["subject"] for a in addi],
            "additional_exam_reference_books": addi,
            "admit_list_rule": re.sub(r"\s+", "", am.group(0)) if am else "",
            "raw": seg[:600],
        }
    return {
        "applicable_year": 2026,
        "source": {
            "title": "017 计算机科学与工程学院 2026年硕士研究生复试参考信息",
            "page_url": "https://yz.neu.edu.cn/2025/1018/c5933a293857/page.htm",
            "attachment_url": REF017_PDF,
            "pdf_pages": len(reader.pages),
            "source_level": "official",
        },
        "score_formula": formula,
        "score_weight": score_weight,
        "by_major": by_major,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="重新下载原始页/PDF")
    args = ap.parse_args()

    if args.download:
        from crawler import http
        for _y, url, name in CATALOG_PAGES:
            dest = os.path.join(SEARCH_DIRS[0], os.path.basename(name))
            os.makedirs(SEARCH_DIRS[0], exist_ok=True)
            raw = http.fetch_binary(url, delay=1.2)
            with open(dest, "wb") as f:
                f.write(raw)
            print("saved", dest, len(raw))

    history = []
    for year, url, name in CATALOG_PAGES:
        path = find(name)
        if not path:
            print(f"  ! 缺 {name}，跳过 {year}")
            continue
        parsed = parse_catalog(read(path))
        coll = parsed.get(TARGET_COLLEGE)
        if not coll:
            print(f"  ! {year} 未找到 {TARGET_COLLEGE} 学院")
            continue
        progs = []
        for p in coll["programs"]:
            if p["program_code"] not in TARGET_CODES:
                continue
            progs.append({
                "program_code": p["program_code"],
                "program_name": p["program_name"],
                "admission_scale": None,
                "recommend_scale": None,
                "degree_type": ("全日制学术学位" if p["program_code"].startswith("081")
                                else "全日制专业学位"),
                "study_mode": p["study_mode"] or None,
                "duration_years": None,
                "tuition": None,
                "exam_subjects": split_exam_subjects(p["exam_subjects_raw"]),
                "exam_subjects_raw": p["exam_subjects_raw"],
                "re_exam_subjects": None,
                "directions": p["directions"],
                "notes": None,
            })
        history.append({
            "applicable_year": year,
            "source": {"title": f"东北大学{year}年硕士研究生招生专业目录",
                       "page_url": url, "source_level": "official"},
            "college_name": coll["name"],
            "programs": progs,
        })
        print(f"  ok {year} {coll['name']} 命中专业 {len(progs)}: "
              f"{[(p['program_code'], len(p['exam_subjects'])) for p in progs]}")

    if not history:
        print("没有解析到任何年份，退出")
        return 1

    history.sort(key=lambda h: h["applicable_year"], reverse=True)
    cur = history[0]

    # 人数 / 学制 / 学费：按招生年度分别解析，再写回该年度的 history 条目。
    # 2027 只发布了学制/学费明细表（拟招生人数表未发布），2026 三张表都有。
    YEAR_ASSETS = [
        # (年度, 附件所在页, 拟招生人数 pdf, 学制 pdf, 学费 pdf, 学制附件名, 学费附件名)
        (2026, PLAN_PAGE, SCALE_PDF, XUEZHI_PDF, XUEFEI_PDF,
         "e38b7eb1-254a-49b8-9df5-9e8d76f778bc.pdf",
         "bbe89af6-1d4a-4cf5-aa5c-8cd14ffc8043.pdf"),
        (2027, GUIDE_2027_PAGE, None, XUEZHI_PDF_2027, XUEFEI_PDF_2027,
         XUEZHI_ATT_2027, XUEFEI_ATT_2027),
    ]
    for year, page, scale_pdf, xz_pdf, xf_pdf, xz_att, xf_att in YEAR_ASSETS:
        h = next((x for x in history if x["applicable_year"] == year), None)
        if h is None:
            continue
        scale, duration, tuition = {}, {}, {}
        for label, path, fn in (("拟招生人数", scale_pdf, parse_scale),
                                ("学制", xz_pdf, parse_duration),
                                ("学费", xf_pdf, parse_tuition)):
            if not path:
                continue
            p = find(path)
            if not p:
                print(f"  ! 缺 {path}（{year} {label}）")
                continue
            res = fn(pdf_pages(p))
            print(f"  {year} {label}: " + json.dumps(res, ensure_ascii=False)[:300])
            if label == "拟招生人数":
                scale = res
            elif label == "学制":
                duration = res
            else:
                tuition = res
        for prog in h["programs"]:
            code = prog["program_code"]
            if code in scale and "plan" in scale[code]:
                prog["admission_scale"] = scale[code]["plan"]
                prog["recommend_scale"] = scale[code]["recommend"]
                prog["admission_scale_source"] = {
                    "title": f"东北大学{year}年硕士研究生招生专业目录（拟招生人数表）",
                    "url": page,
                    "attachment": "106a5be3-929b-4d8c-81b3-4debc9b8cae2.pdf",
                    "note": "表列名：学院码及名称/专业代码及名称/研究方向代码及名称/学习方式/"
                            "拟招生人数/硕士推免人数/备注；表内数字为「主校区」合计，"
                            "【秦皇岛】【佛山】等异地校区单列。",
                    "raw": scale[code].get("raw", ""),
                }
            if code in duration:
                prog["duration_years"] = duration[code]["duration_years"]
                prog["duration_source"] = {
                    "title": f"东北大学{year}年硕士研究生招生专业学制情况汇总表",
                    "url": page,
                    "attachment": xz_att,
                    "raw": duration[code].get("raw", ""),
                }
            if code in tuition:
                prog["tuition"] = tuition[code]["tuition"]
                prog["tuition_source"] = {
                    "title": f"东北大学{year}年硕士研究生招生专业学费标准明细表",
                    "url": page,
                    "attachment": xf_att,
                    "raw": tuition[code].get("raw", ""),
                }

    # 复试参考信息（017 学院）：复试比例、复试方式与分数、加试科目、总成绩折算
    ref017 = None
    refpath = find(REF017_NAME) or os.path.join(SEARCH_DIRS[-1], REF017_NAME)
    if os.path.exists(str(refpath)):
        ref017 = retest_reference_017(refpath)
        print("  017 复试参考信息:", json.dumps(
            {k: (v["re_exam_ratio"], v["re_exam_method"],
                 [b["title"] for b in v["re_exam_content_reference_books"]],
                 v["additional_exam_subjects"])
             for k, v in ref017["by_major"].items()}, ensure_ascii=False))
        for prog in cur["programs"]:
            ref = ref017["by_major"].get(prog["program_code"])
            if not ref:
                continue
            prog["re_exam_ratio"] = ref["re_exam_ratio"]
            prog["re_exam_subjects"] = ref["re_exam_method"]
            prog["re_exam_content_reference_books"] = ref["re_exam_content_reference_books"]
            prog["additional_exam_subjects"] = ref["additional_exam_subjects"]
            prog["additional_exam_reference_books"] = ref["additional_exam_reference_books"]
            prog["admit_list_rule"] = ref["admit_list_rule"]
            prog["re_exam_source"] = ref017["source"]
        score_weight = ref017["score_weight"]
    else:
        score_weight = ""
        print("  ! 缺 017 复试参考信息 PDF，跳过", refpath)

    # 2026 的人数/学制/学费属于 2026 招生年度；当前目录为 2027，故归入 history[2026]
    missing = [
        "score_line_total", "score_line_single", "line_vs_national",
        "admission_ratio", "admitted_min_score",
        "recommend_ratio", "reference_books_count", "discipline_rating", "has_phd",
        "accepts_cross_major", "policy_change_flag",
    ]
    unknowns = [
        "复试基本分数线（初试成绩基本要求）历年整页均为 jpg 图片，正文无文字与表格，"
        "未经 OCR 无法取得数值；对应图片 URL 已登记在 data/schools/neu.json 的 "
        "attachments 中，数值留 null。作为可追溯的替代证据，本文件另给 "
        "extracted.retest_list_2026 —— 2026 年 017 学院复试名单 PDF 中"
        "进入复试者的初试总分统计（081000 最低 331、085402 最低 367），"
        "它不是官方分数线，不得填进 score_line_total / admitted_min_score。",
        "复试笔试科目：需人工比对「复试参考信息」页每个学院的附件 PDF，"
        "本次已配对到 017 计算机科学与工程学院的 2026 年复试参考信息 PDF 并解析"
        "（见 extracted.retest_reference_2026.by_major），re_exam_subjects 填的是该件"
        "原文的「复试方式及分数组成」（专业机试 + 综合面试），不是笔试科目名；"
        "005 等其他学院未配对。",
        "2027 年拟招生人数：《2027年硕士研究生招生章程》附件尚未发布拟招生人数表，"
        "故 admission_scale 仅能给出 2026 年度的值并已标注 metric_years；"
        "2026 年的**统考**招生计划（扣推免后）见 extracted.retest_list_2026.plan_table_017。",
    ]

    # 顶层 programs = 最新一年（2027）的专业目录；人数/学制/学费来自 2026 年度，
    # 逐字段标注取值年份（metric_years），避免把不同年份的数据混成一个数。
    retest = None
    rp = os.path.join(RETEST_PDF_DIR, RETEST_PDF_NAME)
    if os.path.exists(rp):
        retest = retest_stats(rp)
        print("  2026 统考招生计划(017):", json.dumps(
            [(p["major_code"], p["direction"], p["plan"])
             for p in retest["plan_table_017"]], ensure_ascii=False))
        print("  2026 进复试初试总分:", json.dumps(
            {k: (v["count"], v["min_total"], v["max_total"])
             for k, v in retest["by_major_017"].items()}, ensure_ascii=False))
    else:
        print("  ! 缺 2026 复试名单 PDF，跳过", rp)

    metric_years = {"exam_subjects": cur["applicable_year"]}
    if ref017:
        metric_years["re_exam_ratio"] = 2026
        metric_years["re_exam_subjects"] = 2026
        metric_years["score_weight"] = 2026
    # 本年度（2027）自己就有的维度直接用本年度年份标注
    for key in ("duration_years", "tuition", "admission_scale"):
        if cur["programs"] and all(p.get(key) is not None for p in cur["programs"]):
            metric_years[key] = cur["applicable_year"]
    src2026 = next((h for h in history if h["applicable_year"] == 2026), None)
    if src2026:
        for prog in cur["programs"]:
            ref = next((p for p in src2026["programs"]
                        if p["program_code"] == prog["program_code"]), None)
            if not ref:
                continue
            for key, year in (("admission_scale", 2026), ("recommend_scale", 2026),
                              ("duration_years", 2026), ("tuition", 2026)):
                # 只在本年度没有该维度时才回填上年数据，避免用 2026 覆盖 2027 的实测值
                if prog.get(key) is None and ref.get(key) is not None:
                    prog[key] = ref[key]
                    metric_years[key] = year
            for key in ("admission_scale_source", "duration_source", "tuition_source"):
                if not prog.get(key) and ref.get(key):
                    prog[key] = ref[key]

    doc = {
        "school_id": "neu",
        "school_name": "东北大学",
        "applicable_year": cur["applicable_year"],
        "college_code": TARGET_COLLEGE,
        "college_name": cur["college_name"],
        "metric_years": metric_years,
        "source": {
            "title": cur["source"]["title"],
            "page_url": cur["source"]["page_url"],
            "source_level": "official",
            "site_render": "server-side HTML（静态，无需 JS）",
            "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        },
        "extracted": {
            "college_pages": [],
            "college_note": (
                "东北大学 081000 信息与通信工程 / 085402 通信工程（含宽带网络、移动通信等）"
                "均挂靠在 017 计算机科学与工程学院，不在 005 信息科学与工程学院。"
                "原始 HTML 表中「院系所」列使用 rowspan 合并，直接拍平会导致学院归属错位，"
                "本文件用 rowspan 展开后的矩阵解析。同一专业代码的多条研究方向已合并为一条。"
            ),
            "programs": cur["programs"],
            "reference_books": {"by_exam_code": {}, "titles": [], "count": 0},
            "retest_list_2026": retest,
            "retest_reference_2026": ref017,
            "score_formula": (score_weight or None),
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
