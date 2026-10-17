"""院校数据加载与指标抽取（SPEC §9.1 / §7.4 N4）。

原则（答辩红线）：**没有一个数字是编的**。
每条指标都必须能给出 `source_index` → 指向该校 `sources[]` 里的一条官方来源；
拿不到的维度写 `null` 并进 `unknowns`，**绝不推测填写**。
跨来源互相矛盾时不静默覆盖，而是记录到 `data_conflicts[]`。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .. import config
from ..core import subjects as subject_table

SCHOOLS_DIR = config.DATA_DIR / "schools"
META_PATH = SCHOOLS_DIR / "school_meta.json"

# 招生专业代码 → 复试线所在的"专业学位/学术学位"类别关键词（显式覆盖，优先）
PROGRAM_CATEGORY_HINTS = {
    "0810": "工学[08]",
    "0811": "工学[08]",
    "0812": "工学[08]",
    "0854": "电子信息",
    "0855": "机械",
    "0856": "材料与化工",
    "0859": "土木水利",
}

# 学术学位代码前两位 = 学科门类（教育部学科目录），用于兜底推导
_DISCIPLINE_BY_PREFIX = {
    "01": "哲学[01]",
    "02": "经济学[02]",
    "03": "法学[03]",
    "04": "教育学[04]",
    "05": "文学[05]",
    "06": "历史学[06]",
    "07": "理学[07]",
    "08": "工学[08]",
    "09": "农学[09]",
    "10": "医学[10]",
    "11": "军事学[11]",
    "12": "管理学[12]",
    "13": "艺术学[13]",
    "14": "交叉学科[14]",
}


def category_hint(code: str | None) -> str | None:
    """专业代码 → 复试线表里的行关键词。

    实测（吉大 / 东北大学）复试线表按**学科门类**分行（`工学[08]`、`经济学[02]`…），
    专业学位另起一张表并按类别名分行（`电子信息[0854]、机械[0855]…`）。
    旧实现只硬编码了通信/控制几个代码，导致 080800 电气工程、085801、
    120400 这类专业取不到复试线（如实返回 null，但本可拿到）。
    规则：①显式覆盖（最长前缀优先）②学术学位按代码前两位取门类
    ③判不出来返回 None，由调用方如实写进 unknowns（不猜）。
    """
    if not code:
        return None
    text = str(code).strip()
    for prefix in sorted(PROGRAM_CATEGORY_HINTS, key=len, reverse=True):
        if text.startswith(prefix):
            return PROGRAM_CATEGORY_HINTS[prefix]
    if len(text) >= 2 and text[:2].isdigit():
        return _DISCIPLINE_BY_PREFIX.get(text[:2])
    return None


def _read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _year(value) -> int | None:
    """数据年份归一化。

    **不吃页面发布日**：调用方传入的应是标题/正文推断出的 `data_year`。
    不同抓取批次的 `data_year` 可能是字符串（如 "2027"），这里统一成 int，
    否则排序与比较会静默出错（实测踩过一次）。
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = _YEAR_RE.search(str(value))
    return int(match.group(0)) if match else None


@lru_cache(maxsize=1)
def _meta() -> dict:
    if META_PATH.exists():
        return _read_json(META_PATH)
    return {}


@lru_cache(maxsize=1)
def load_all() -> dict[str, dict]:
    """扫描 data/schools/，合并成 school_id -> 学校对象。

    - `<id>.json`             原始抓取记录（含 tables/attachments）
    - `<id>_catalog*.json`    PDF 附件解析出的专业级数据
    - `school_meta.json`      人工整理的城市/层次等元信息（B 级数据）
    """
    schools: dict[str, dict] = {}

    for path in sorted(SCHOOLS_DIR.glob("*.json")):
        name = path.name
        if name == "school_meta.json":
            continue
        try:
            data = _read_json(path)
        except (OSError, ValueError):
            continue
        school_id = data.get("school_id")
        if not school_id:
            continue
        school = schools.setdefault(school_id, {
            "school_id": school_id,
            "school_name": data.get("school") or data.get("school_name") or school_id,
            "records": [],
            "catalogs": [],
            "raw_files": [],
        })
        if "records" in data:
            for rec in data["records"]:
                rec["data_year"] = _year(rec.get("data_year"))
                rec["published"] = rec.get("published")
            school["records"].extend(data["records"])
            school["crawled_at"] = data.get("generated_at")
        elif "extracted" in data:
            data["applicable_year"] = _year(data.get("applicable_year"))
            data["_file"] = name
            school["catalogs"].append(data)
        school["raw_files"].append(name)

    for school_id, school in schools.items():
        meta = _meta().get(school_id, {})
        school["meta"] = meta
        school["city"] = meta.get("city")
        school["province"] = meta.get("province")
        school["tags"] = meta.get("tags", [])
        school["records"].sort(key=lambda r: (r.get("category") or "", -(r.get("data_year") or 0)))
        school["records"], school["duplicate_records"] = _dedupe_records(school["records"])
        school["catalogs"] = _dedupe_catalogs(school["catalogs"])
        school["sources"] = _build_sources(school)
    return schools


def _record_richness(record: dict) -> tuple:
    """同一条记录可能来自多份文件，用这个指标挑信息最全的那条。"""
    tables = record.get("tables") or []
    return (
        len(_score_line_tables(record)),
        sum(len(t) for t in tables),
        len(str(record.get("content") or "")),
        len(record.get("attachments") or []),
    )


def _dedupe_records(records: list[dict]) -> tuple[list[dict], int]:
    """按 `(category, url)` 去重，保留信息最全的一条，返回 (记录, 丢弃条数)。

    实测：`neu.json`（抓取器产出，分数线页 0 表）与 `neu_score_line.json`
    （人工转录，带表）会带出同一 URL 的两条记录。不去重会让 `/meta/sources`
    的总记录数虚高，并把"最终用哪条"交给文件遍历顺序决定。
    """
    best: dict[tuple, dict] = {}
    dropped = 0
    for rec in records:
        key = (str(rec.get("category") or ""), str(rec.get("url") or ""))
        taken = best.get(key)
        if taken is None:
            best[key] = rec
            continue
        dropped += 1
        if _record_richness(rec) > _record_richness(taken):
            best[key] = rec
    return list(best.values()), dropped


def _catalog_richness(cat: dict) -> tuple:
    """给同一学院的多份目录排序，挑信息最全的那份（实测抓到过重复文件）。"""
    extracted = cat.get("extracted") or {}
    programs = extracted.get("programs") or []
    scales = [_scale(p.get("admission_scale")) for p in programs]
    return (
        1 if any(s is not None for s in scales) else 0,
        1 if extracted.get("reference_books") else 0,
        1 if extracted.get("tuition_rows_2027") or extracted.get("tuition_table_raw_rows") else 0,
        len([v for v in extracted.values() if v not in (None, "", [], {})]),
        len(programs),
    )


def _dedupe_catalogs(catalogs: list[dict]) -> list[dict]:
    """同一学校的同一学院可能有多份目录文件（并发抓取留下的重复）。

    按 `college_code` 去重，保留信息更全的一份，并把重复文件名记进
    `duplicate_files`，便于答辩时说明"数据管道会报重复而不是静默取一个"。
    """
    best: dict[str, dict] = {}
    for cat in catalogs:
        key = str(cat.get("college_code") or cat.get("college_name") or "")
        taken = best.get(key)
        if taken is None:
            best[key] = cat
            continue
        if _catalog_richness(cat) > _catalog_richness(taken):
            cat.setdefault("duplicate_files", [])
            cat["duplicate_files"] = list(cat.get("duplicate_files") or []) + \
                list(taken.get("duplicate_files") or []) + [taken.get("_file") or "?"]
            best[key] = cat
        else:
            taken.setdefault("duplicate_files", [])
            taken["duplicate_files"] = list(taken.get("duplicate_files") or []) + [cat.get("_file") or "?"]
    return sorted(best.values(), key=lambda c: str(c.get("college_code") or ""))


def _build_sources(school: dict) -> list[dict]:
    """把"抓取记录 + 附件 PDF"统一编成可被 source_index 引用的来源清单。"""
    sources: list[dict] = []
    for rec in school["records"]:
        sources.append({
            "title": rec.get("title") or "",
            "url": rec.get("url") or "",
            "applicable_year": rec.get("data_year"),
            "category": rec.get("category"),
            "level": rec.get("level") or "official",
            "fetched_at": rec.get("fetched_at"),
        })
    for cat in school["catalogs"]:
        src = cat.get("source") or {}
        sources.append({
            "title": src.get("title") or "招生专业目录附件",
            "url": src.get("attachment_download") or src.get("page_url") or "",
            "applicable_year": cat.get("applicable_year"),
            "category": "program_catalog_pdf",
            "level": src.get("source_level") or "official",
            "fetched_at": src.get("fetched_at"),
            "attachment_wbfileid": src.get("attachment_wbfileid"),
        })
    return sources


def school_ids() -> list[str]:
    return sorted(load_all().keys())


def get(school_id: str) -> dict | None:
    return load_all().get(school_id)


def crawl_time() -> str | None:
    """所有数据集里最新的抓取时间（`GET /meta/sources` 用）。"""
    times = [
        rec.get("fetched_at")
        for school in load_all().values()
        for rec in school.get("records", [])
        if rec.get("fetched_at")
    ]
    return max(times) if times else None


def display_name(school_id: str) -> str:
    school = get(school_id)
    return school["school_name"] if school else school_id


def sources(school_id: str) -> list[dict]:
    school = get(school_id)
    return list(school["sources"]) if school else []


def _source_index_of(school: dict, predicate) -> int | None:
    for idx, src in enumerate(school["sources"]):
        if predicate(src):
            return idx
    return None


# --------------------------------------------------------------------------
# 专业选择
# --------------------------------------------------------------------------

def all_programs(school_id: str) -> list[dict]:
    school = get(school_id) or {}
    out: list[dict] = []
    for cat in school.get("catalogs", []):
        extracted = cat.get("extracted") or {}
        for program in extracted.get("programs") or []:
            item = dict(program)
            item["college_code"] = cat.get("college_code")
            item["college_name"] = cat.get("college_name")
            item["applicable_year"] = cat.get("applicable_year")
            item["_catalog"] = cat
            out.append(item)
    return out


_KEYWORDS = ("通信", "信息", "信号", "电子", "控制", "计算机", "人工智能")


def pick_program(school_id: str, major_hint: str | None) -> tuple[dict | None, str]:
    """挑出与用户方向最接近的招生专业。

    返回 `(program|None, method)`；`method` ∈ `keyword` / `default` / `none`。
    **匹配不到时退回目录里的第一个专业并标注 `default`**，让前端知道这是兜底而非精确命中。

    为什么不能"第一个名字里带关键词的就返回"：实测哈工程的 `081000 信息与通信工程`
    同时出现在 `005 水声工程学院`（19 人）与 `008 信息与通信工程学院/集成电路学院`（90 人）下，
    按文件顺序会挑到水声学院。所以命中专业名之后，还要看**学院名是否也命中方向**，
    再按招生规模取大的那个。
    """
    programs = all_programs(school_id)
    if not programs:
        return None, "none"
    hint = (major_hint or "").strip()
    keywords = [k for k in ([hint] if hint else []) + list(_KEYWORDS) if k]
    matched: list[tuple[int, int, int, int, dict]] = []
    for order, keyword in enumerate(keywords):
        for program in programs:
            if not keyword or keyword not in (program.get("program_name") or ""):
                continue
            name_hit = 1 if hint and hint in (program.get("program_name") or "") else 0
            college_hit = 1 if hint and hint in str(program.get("college_name") or "") else 0
            scale = _scale(program.get("admission_scale")) or 0
            matched.append((name_hit, college_hit, scale, -order, program))
    if matched:
        best = max(matched, key=lambda item: item[:4])
        program = best[4]
        program["_pick_note"] = (
            f"按方向『{hint or '未给出'}』命中 {program.get('college_name')} 的 "
            f"{program.get('program_code')} {program.get('program_name')}（招生 {best[2]} 人）"
        )
        return program, "keyword"
    programs[0]["_pick_note"] = "用户方向未命中任何专业名，按目录顺序兜底取第一个专业（default）"
    return programs[0], "default"


# --------------------------------------------------------------------------
# 复试线抽取
# --------------------------------------------------------------------------

_HEADER_WORDS = ("学科门类", "专业学位名称", "考试类型", "专业名称")


def _num(cell) -> int | None:
    if cell is None:
        return None
    text = str(cell).replace(",", "")
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def _looks_like_score_line_table(table: list[list]) -> bool:
    """表头是否是复试线表（含「总分」或「学科门类+分」）。

    放宽 `len(t) > 2` 的原因：东北大学的分数线表是「表头 + 工学行 + 备注行」共 3 行，
    而某些学校/年份只有「表头 + 1 行数据」= 2 行，旧过滤会把整张表丢掉。
    """
    if len(table) < 2:
        return False
    head = " ".join(str(c) for c in table[0])
    return "总分" in head or ("门类" in head and "分" in head)


def _score_line_tables(record: dict) -> list[list[list]]:
    tables = [t for t in (record.get("tables") or []) if t and len(t) >= 2]
    strict = [t for t in tables if _looks_like_score_line_table(t)]
    if strict:
        return strict
    # 表头识别不出来时退回旧口径，避免把原本能用的表筛掉
    return [t for t in tables if len(t) > 2]


_COLUMN_PATTERNS = (
    ("total", ("总分",)),
    ("politics", ("政治", "政治或综合")),
    ("foreign", ("外语", "外国语")),
    ("biz1", ("业务课一", "专业课一", "数学")),
    ("biz2", ("业务课二", "专业课二")),
    ("single100", ("满分=100", "满分＝100", "单科(满分=100")),
    ("single150", ("满分>100", "满分＞100", "满分>10", "单科(满分>100")),
)


def _map_columns(table: list[list]) -> dict[str, int]:
    """按表头识别列含义。

    实测三所学校的复试线表列数不同：吉大是
    `学科门类(专业)名称|总分|政治|外语|业务课一|业务课二`，
    东北大学是 `…|总分|单科(满分=100分)|单科(满分>100分)`。
    按列号硬编码会把"单科>100"读成外语，所以这里读表头。
    """
    if not table:
        return {}
    header = [str(c) for c in table[0]]
    if not any("总分" in c for c in header):
        return {}
    cols: dict[str, int] = {}
    for key, patterns in _COLUMN_PATTERNS:
        for index, cell in enumerate(header):
            if any(pattern in cell for pattern in patterns):
                cols.setdefault(key, index)
                break
    return cols


def extract_score_line(school_id: str, program: dict | None) -> tuple[dict | None, dict | None, str]:
    """从最新的复试线记录里抽出该校/该专业对应的总分与单科线。

    返回 `(found|None, source_record|None, note)`：
    - `found = {"total":..., "single":..., "politics":..., "foreign":..., "biz1":..., "biz2":...,
      "row_label":..., "year":...}`
    - 抽不到就返回 `None`，由调用方放进 `unknowns`（不猜）
    """
    school = get(school_id)
    if not school:
        return None, None, "学校不存在"
    records = [r for r in school["records"] if r.get("category") == "score_line"]
    if not records:
        return None, None, "没有复试线记录"
    # 同年有多条记录时，优先用真的带表格的那条（实测有学校把分数线做成了图片，正文没有数字）
    records.sort(key=lambda r: (-(r.get("data_year") or 0), 0 if _score_line_tables(r) else 1))
    record = next((r for r in records if _score_line_tables(r)), records[0])

    code = (program or {}).get("program_code") or ""
    degree = (program or {}).get("degree_type") or ""
    wants_professional = "专业" in degree or code.startswith("085")
    category_key = category_hint(code)

    tables = _score_line_tables(record)
    academic = tables[0] if tables else []
    professional = tables[1] if len(tables) > 1 else []

    target_table = professional if (wants_professional and professional) else academic
    row = _find_row(target_table, category_key)
    if row is None and category_key:
        row = _find_row(professional, category_key) or _find_row(academic, category_key)
    if row is None:
        if not tables:
            return None, record, "复试线页面的正文里没有可解析的数字表格（该页把分数线做成了图片）"
        return None, record, f"复试线表中没有匹配『{category_key or code}』的行"

    cols = _map_columns(target_table)

    def pick(*names: str, pos: int | None = None) -> int | None:
        for name in names:
            index = cols.get(name)
            if index is not None and index < len(row):
                got = _num(row[index])
                if got is not None:
                    return got
        if pos is not None and pos < len(row):
            return _num(row[pos])
        return None

    total = pick("total", pos=1)
    politics = pick("politics", pos=2)
    foreign = pick("foreign", pos=3)
    biz1 = pick("biz1", pos=4)
    biz2 = pick("biz2", pos=5)
    single100 = pick("single100")
    single150 = pick("single150")
    singles = [v for v in (politics, foreign, biz1, biz2, single100, single150) if v]
    return {
        "total": total,
        "single": min(singles) if singles else None,
        "politics": politics,
        "foreign": foreign,
        "biz1": biz1,
        "biz2": biz2,
        "single_100": single100,
        "single_150": single150,
        "row_label": str(row[0]),
        "year": record.get("data_year"),
        "columns": cols,
    }, record, "ok"


def _find_row(table: list[list], keyword: str | None):
    """在复试线表里找学科门类那一行。

    实测东北大学的表对同一门类有两行：`工学[08] | 工学照顾专业*、冶金工程…` 与
    `工学[08] | 其他学科专业`。**照顾专业是例外行**，不能当成该门类的通用线，
    所以优先取"其他学科专业/各学科专业"那行。
    """
    if not table or not keyword:
        return None
    base = keyword.split("[")[0]
    matches = [row for row in table if row and base and base in str(row[0])]
    if not matches:
        # 退化匹配：只比 [08] 这类方括号代码（必须整段相等，否则 [08] 会误配 [0854]）
        code_match = re.search(r"\[(\d+)\]", keyword)
        if code_match:
            want = code_match.group(1)
            matches = [
                row
                for row in table
                if row and re.search(r"\[\s*" + want + r"\s*\]", str(row[0]))
            ]
    if not matches:
        return None
    for row in matches:
        text = " ".join(str(c) for c in row)
        if "照顾" in text:
            continue
        if "其他" in text or "各学科专业" in text:
            return row
    for row in matches:
        if "照顾" not in " ".join(str(c) for c in row):
            return row
    return matches[0]


def score_line_history(school_id: str, program: dict | None, limit: int = 3) -> list[dict]:
    """近三年复试线（趋势展示用，SPEC §5.3 P4 阶段要用）。"""
    school = get(school_id)
    if not school:
        return []
    records = [r for r in school["records"] if r.get("category") == "score_line"]
    records.sort(key=lambda r: (-(r.get("data_year") or 0), 0 if _score_line_tables(r) else 1))
    out: list[dict] = []
    seen: set[int] = set()
    for record in records:
        year = record.get("data_year")
        if not year or year in seen or not _score_line_tables(record):
            continue
        code = (program or {}).get("program_code") or ""
        category_key = category_hint(code)
        tables = _score_line_tables(record)
        for table in tables[:2]:
            row = _find_row(table, category_key)
            if row is None:
                continue
            cols = _map_columns(table)
            total_index = cols.get("total", 1)
            seen.add(year)
            out.append({
                "year": year,
                "total": _num(row[total_index]) if total_index < len(row) else None,
                "row_label": str(row[0]),
                "source_index": _source_index_of(
                    school, lambda s, u=record.get("url"): s["url"] == u
                ),
            })
            break
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# 学费 / 学制
# --------------------------------------------------------------------------

_FEE_RE = re.compile(r"([\d.,]+)\s*(万元|万|元)")
_PER_YEAR_RE = re.compile(r"元\s*/\s*生?\s*[·・]?\s*年|元/年|每年")
_YEARS_RE = re.compile(r"^\s*(\d+)\s*年\s*$")
# 实测三种写法：吉大"学术型硕士生"、哈工程"学术学位硕士"、部分学校"专业型硕士"
_MASTER_LABEL_RE = re.compile(r"(?:学术|专业)(?:学位|学位型|型|学[位型])?硕士")
# 具体管理类专业学位行（不是通用培养类型的学费行）。实测吉大 085402 通信工程
# 曾因 "公共管理(MPA)专业学位硕士研究生（非全日制）" 含「专业」二字而被当成专硕学费，
# 算成 69,000；哈工程「专业学位硕士[全日制MBA除外]」则是**通用行**，不能排除。
_MANAGEMENT_RE = re.compile(
    r"工商管理|公共管理|工程管理|图书情报|高级工商管理|会计|审计|MBA|MPA|MEM|MLIS|EMBA",
    re.IGNORECASE,
)


def _is_specific_management(label: str) -> bool:
    """这一行是不是「某个具体管理类专业学位」的学费行（而不是通用培养类型行）。

    坑：哈工程官方行 `专业学位硕士[全日制MBA除外]` 里的 MBA 是**排除条件**，
    不是这行的项目名——带「除外/不含」的行一律不能当成管理类项目行排除掉。
    """
    if re.search(r"除外|不含|除[^，。]*外", label):
        return False
    return bool(_MANAGEMENT_RE.search(label))


def _years_int(value) -> int | None:
    """学制归一成整数（实测哈工程给的是 3.0 浮点）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def _fee(cell) -> tuple[int | None, bool, str]:
    """把一格学费文本解析成 `(金额, 是否按年, 原文)`。

    实测两种写法都要吃得住：`"16,000"`（单位写在表头的"总金额 元/生"）与
    `"0.8万元/生·年"`（万元、按年收取）。`万元` 一律换算成元。
    """
    text = str(cell or "").strip()
    if not text:
        return None, False, text
    match = _FEE_RE.search(text)
    if match:
        amount = float(match.group(1).replace(",", ""))
        if match.group(2) in ("万元", "万"):
            amount *= 10000
        return int(round(amount)), bool(_PER_YEAR_RE.search(text)), text
    # 无单位的纯数字：吉大的表把单位写在表头（"总金额 元/生"），格子里只有 "16,000"。
    # 要求至少 4 位数字，否则会把"序号 1"当成学费（实测踩过）。
    if re.fullmatch(r"\d[\d,]*", text) and len(text.replace(",", "")) >= 4:
        return int(text.replace(",", "")), False, text
    return None, False, text


def _tuition_candidate(cells: list[str], label_at: int, record: dict, label: str,
                       label_inherited: bool) -> dict | None:
    """从一行里取出金额 / 是否按年 / 学制。"""
    amount, per_year, unit_raw = None, False, ""
    for i, cell in enumerate(cells):
        if i == label_at:
            continue
        got, py, raw = _fee(cell)
        if got:
            amount, per_year, unit_raw = got, py, raw
            break
    if not amount:
        return None
    duration = None
    for cell in cells:
        match = _YEARS_RE.match(cell)
        if match:
            duration = int(match.group(1))
            break
    return {"amount": amount, "per_year": per_year, "unit_raw": unit_raw,
            "duration_years": duration, "year": record.get("data_year"),
            "row_label": label, "label_inherited": label_inherited}


def extract_tuition(school_id: str, program: dict | None) -> tuple[dict | None, dict | None, str]:
    """从招生章程的学费表里抽该培养类型的学费。

    实测各校表格列数/合并单元格错行都不一样，所以**不按固定列号取值**：
    先在行内找到"学术学位硕士/专业学位硕士"这类标签格，再在该行里找
    第一个含金额的格，学制则找 `"3年"` 这种格。

    两处实测坑（都会产出错误数字，故显式处理）：
    1. 合并单元格会让整行丢掉「序号/培养类型」两格，只剩 `['3年','24,000',…]`
       → 这种行按**同表上一行的标签**继承（`label_inherited=True`，只在没有
       明确标签行可用时才采用，并留审计字段）。
    2. 表里同时有「专业学位硕士生」这种通用行和「工商管理(MBA)…」「公共管理(MPA)…」
       这类**具体管理类专业学位**行；后者不是我们要的通用学费（实测吉大 085402
       曾被算成 MPA 非全的 69,000）→ 用 `_MANAGEMENT_RE` 排除。

    另外：同一培养类型常有 2 年 / 3 年两行，**优先取学制与专业目录一致的那一行**。
    """
    school = get(school_id)
    if not school:
        return None, None, "学校不存在"
    records = [r for r in school["records"] if r.get("category") == "admission_guide"]
    records.sort(key=lambda r: (-(r.get("data_year") or 0), 0 if r.get("tables") else 1))
    degree, _src = _degree_type(program or {})
    wants_professional = "专业" in (degree or "")
    want_years = _years_int((program or {}).get("duration_years"))

    labeled: list[tuple[dict, dict]] = []
    inherited: list[tuple[dict, dict]] = []
    for record in records:
        for table in record.get("tables") or []:
            last_label: tuple[str, bool] | None = None
            for row in table:
                if not row:
                    continue
                cells = [str(c) for c in row]
                label_at = next((i for i, c in enumerate(cells) if _MASTER_LABEL_RE.search(c)), None)
                label_inherited = False
                if label_at is None:
                    # 坑 1：整行丢了标签格（首格直接是 "3年"），继承上一行的标签
                    if not (last_label and _YEARS_RE.match(cells[0].strip())):
                        continue
                    label, is_professional = last_label
                    label_at, label_inherited = -1, True
                else:
                    label = cells[label_at]
                    is_professional = "专业" in label
                    last_label = (label, is_professional)
                if _is_specific_management(label):
                    continue
                if is_professional != wants_professional:
                    continue
                cand = _tuition_candidate(cells, label_at, record, label, label_inherited)
                if not cand:
                    continue
                (inherited if label_inherited else labeled).append((cand, record))

    if not (labeled or inherited):
        return None, None, "学费表中没有匹配的培养类型"
    candidates = labeled + inherited
    # 稳定排序：①学制与专业目录一致的行优先（否则会把"2 年 16,000"当成 3 年制的学费）
    # ②有明确标签的行优先于靠"错行继承"猜出来的行
    candidates.sort(key=lambda cr: (
        0 if (want_years and cr[0].get("duration_years") == want_years) else 1,
        1 if cr[0].get("label_inherited") else 0,
    ))
    cand, record = candidates[0]
    return cand, record, "ok"


# --------------------------------------------------------------------------
# 指标包
# --------------------------------------------------------------------------

def metric(value, year=None, source_index=None) -> dict:
    return {"value": value, "year": year, "source_index": source_index}


_POLICY_KEYS = ("policy_notes", "code_policy_2027", "college_note", "college_total_note",
                "duration_note", "notes")


def _policy_text(cat: dict) -> str:
    """把目录里的政策类说明拼成一段原文（不翻译、不改写）。

    实测三所学校的抓取脚本给这些说明起了不同键名（`policy_notes` /
    `code_policy_2027` / `college_note` / `duration_note`），所以按候选键收集，
    值可能是字符串、字符串列表或 `{键: 文本}`。
    """
    extracted = cat.get("extracted") or {}
    items: list[str] = []
    for key in _POLICY_KEYS:
        value = extracted.get(key)
        if isinstance(value, str):
            items.append(value)
        elif isinstance(value, (list, tuple)):
            items.extend(str(item) for item in value if item)
        elif isinstance(value, dict):
            items.extend(str(item) for item in value.values() if isinstance(item, str) and item)
    return " ".join(item for item in items if item)


def _scale(value) -> int | None:
    """招生人数归一化。

    实测三所学校的目录脚本产出了三种写法：
    `57`（整数）、`"59+31/0"`（推免+统考/非全）、
    `{"recommend": 59, "exam": 31, "part_time": 0, "admission_scale": 90, "raw": "59+31/0"}`。
    统一成"该专业招生总人数"这一个整数；确实算不出来时返回 None（不猜）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, dict):
        for key in ("admission_scale", "total", "scale"):
            got = _scale(value.get(key))
            if got is not None:
                return got
        parts = [value.get("recommend"), value.get("exam"), value.get("part_time")]
        nums = [n for n in (_scale(p) for p in parts) if n is not None]
        return sum(nums) if nums else None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text)
    head = text.split("/")[0]
    nums = [int(n) for n in re.findall(r"\d+", head)]
    if not nums:
        nums = [int(n) for n in re.findall(r"\d+", text)]
    return sum(nums[:2]) if nums else None


def _degree_type(program: dict) -> tuple[str | None, str]:
    """培养类型：优先目录里写的，缺失时按专业代码前缀推断（并标注是推断）。"""
    explicit = program.get("degree_type")
    if explicit:
        return str(explicit), "catalog"
    code = str(program.get("program_code") or "")
    if code.startswith(("085", "086", "125", "135", "105")):
        return "专业学位硕士", "code_prefix"
    if re.match(r"^0[1-9]", code):
        return "学术学位硕士", "code_prefix"
    return None, "none"


def _collapse_repeat(text: str) -> str:
    """目录里同一格被重复拼接时（`"A A"`）折半，避免考试科目重复匹配。"""
    stripped = text.strip()
    repeat = re.fullmatch(r"(.+?)\s+\1", stripped)
    if repeat:
        return repeat.group(1).strip()
    half, rest = divmod(len(stripped), 2)
    if rest == 0 and half and stripped[:half] == stripped[half:]:
        return stripped[:half].strip()
    return stripped


def _exam_subject_text(item) -> str:
    """把目录里的考试科目条目统一成一行文本（容错三种写法）。

    实测各校抓取脚本产出过：纯字符串 `"①101 思想政治理论"`、
    字典 `{"code": "101", "name": "思想政治理论"}`、
    以及带序号的两字段列表。这里只做"变成可匹配文本"，不改写内容。
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        parts = [item.get("code"), item.get("name"), item.get("text"), item.get("label")]
        return " ".join(str(p) for p in parts if p).strip()
    if isinstance(item, (list, tuple)):
        return " ".join(str(p) for p in item if p).strip()
    return "" if item is None else str(item).strip()


def build_metrics(school_id: str, program: dict | None = None,
                  major_hint: str | None = None) -> dict:
    """组装 §7.4 N4 的 `metrics` + `unknowns` + `data_conflicts`。"""
    school = get(school_id)
    if not school:
        return {"metrics": {}, "unknowns": [f"学校 {school_id} 不在数据集里"], "data_conflicts": [],
                "sources": [], "program": None}

    if program is None:
        program, _method = pick_program(school_id, major_hint)

    unknowns: list[str] = []
    conflicts: list[dict] = []
    metrics: dict = {}

    def src_of(record) -> int | None:
        if not record:
            return None
        return _source_index_of(school, lambda s, u=record.get("url"): s["url"] == u)

    # --- 复试线 ---
    line, line_record, note = extract_score_line(school_id, program)
    if line and line.get("total") is not None:
        idx = src_of(line_record)
        metrics["score_line_total"] = metric(line["total"], line.get("year"), idx)
        metrics["score_line_single"] = metric(line["single"], line.get("year"), idx)
        metrics["_score_line_row"] = line.get("row_label")
        metrics["_score_line_singles"] = {
            "politics": line.get("politics"),
            "foreign": line.get("foreign"),
            "subject_one": line.get("biz1"),
            "subject_two": line.get("biz2"),
            "single_100": line.get("single_100"),
            "single_150": line.get("single_150"),
        }
        metrics["_score_line_history"] = score_line_history(school_id, program)
    else:
        metrics["score_line_total"] = None
        metrics["score_line_single"] = None
        unknowns.append(f"复试线：{note}")

    # --- 招生规模 / 考试科目 / 参考书 ---
    cat_source_index = _source_index_of(school, lambda s: s.get("category") == "program_catalog_pdf")
    if program:
        year = program.get("applicable_year")
        metrics["admission_scale"] = metric(_scale(program.get("admission_scale")), year, cat_source_index)
        # 目录里的考试科目可能是纯字符串，也可能是 {"code":..,"name":..} 结构
        # （不同学校的抓取脚本写法不一）。统一成"可读文本"再做标准科目匹配，
        # 否则 subjects.match 会在 dict 上调用 .strip() 直接 500。
        exam_raw: list[str] = []
        for item in program.get("exam_subjects") or []:
            text = _collapse_repeat(_exam_subject_text(item))
            if text and text not in exam_raw:  # 实测抓到过同一科目整行重复两次
                exam_raw.append(text)
        codes, unmatched = subject_table.match_many(exam_raw)
        metrics["exam_subjects"] = exam_raw or None
        metrics["exam_subject_codes"] = codes
        if unmatched:
            unknowns.append("考试科目未进标准科目表（无法参与能力匹配）：" + "、".join(unmatched))
        catalog_ex = (program.get("_catalog") or {}).get("extracted") or {}
        books = catalog_ex.get("reference_books") or {}
        book_count = books.get("count") if isinstance(books, dict) else None
        if book_count is None and isinstance(books, list):
            book_count = len(books)
        # 目录明确"无参考书目"时给 0 而不是 None：0 是事实，None 是缺数据
        metrics["reference_books_count"] = metric(int(book_count), year, cat_source_index) \
            if book_count is not None else None
        metrics["duration_years"] = metric(_years_int(program.get("duration_years")), year, cat_source_index)
        degree_type, degree_src = _degree_type(program)
        metrics["degree_type"] = metric(degree_type, year, cat_source_index) if degree_type else None
        metrics["_degree_type_source"] = degree_src
        metrics["_re_exam_subjects"] = program.get("re_exam_subjects")
        metrics["_program_code"] = program.get("program_code")
        metrics["_program_name"] = program.get("program_name")
        metrics["_college_name"] = program.get("college_name")
        metrics["_program_year"] = year
        per_program_tuition = program.get("tuition")
        if per_program_tuition:
            metrics["_tuition_from_catalog"] = per_program_tuition
        # 跨考 / 同等学力：来自目录里的政策声明（原文）
        note_items: list[str] = [str(program.get("notes"))] if program.get("notes") else []
        for cat in school.get("catalogs", []):
            if cat.get("college_code") != program.get("college_code"):
                continue
            text = _policy_text(cat)
            if text:
                note_items.append(text)
        notes = " ".join(item for item in note_items if item)
        if notes:
            metrics["_policy_notes"] = notes
            if "不招收同等学力" in notes:
                metrics["equivalent_requirement"] = metric(notes.strip(), year, cat_source_index)
            if "招收跨学科考生" in notes or "接收跨专业" in notes:
                metrics["accepts_cross_major"] = metric(True, year, cat_source_index)
            elif "不招收跨" in notes:
                metrics["accepts_cross_major"] = metric(False, year, cat_source_index)
    else:
        unknowns.append("没有可用的招生专业目录（无法确认招生规模与考试科目）")

    # --- 学费 / 学制（与目录交叉校验）---
    tuition, tuition_record, tnote = extract_tuition(school_id, program)
    tuition_source = "admission_guide"
    if not (tuition and tuition.get("amount")):
        # 兜底：部分学校（实测东北大学）章程正文没有学费表，学费只写在专业目录的每个专业上
        raw = (program or {}).get("tuition")
        if isinstance(raw, (str, int, float)):
            amount, per_year, unit_raw = _fee(raw)
            if amount:
                tuition = {"amount": amount, "per_year": per_year, "unit_raw": unit_raw,
                           "duration_years": None, "year": (program or {}).get("applicable_year"),
                           "row_label": None}
                tuition_record = None
                tuition_source = "program_catalog"
    if tuition and tuition.get("amount"):
        catalog_years = _years_int((program or {}).get("duration_years"))
        amount = tuition["amount"]
        formula = None
        if tuition.get("per_year") and catalog_years:
            # 章程写的是"元/生·年"，全程花费 = 每年 × 学制，算式写在明面上（SPEC §4.2.4）
            total = amount * catalog_years
            formula = f"{amount:,} 元/年 × {catalog_years} 年 = {total:,} 元（按学制收取）"
            amount = total
        elif tuition.get("per_year"):
            formula = "来源按『元/生·年』计，未注明学制，未换算全程总花费"
        tuition_src = src_of(tuition_record) if tuition_record else cat_source_index
        metrics["tuition"] = metric(amount, tuition.get("year"), tuition_src)
        metrics["_tuition_row"] = tuition.get("row_label")
        metrics["_tuition_raw"] = tuition.get("unit_raw")
        metrics["_tuition_formula"] = formula
        metrics["_tuition_source"] = tuition_source
        metrics["_tuition_per_year"] = tuition["amount"] if tuition.get("per_year") else \
            amount // max(1, catalog_years or _years_int(tuition.get("duration_years")) or 1)
        if catalog_years and tuition.get("duration_years") and catalog_years != tuition["duration_years"]:
            conflicts.append({
                "dimension": "duration_years",
                "catalog_value": catalog_years,
                "catalog_source_index": cat_source_index,
                "tuition_table_value": tuition["duration_years"],
                "tuition_source_index": src_of(tuition_record),
                "note": "招生专业目录标注的学制与招生章程学费表标注的学制不一致，需人工核对（本系统不做静默取值）",
            })
    else:
        metrics["tuition"] = None
        unknowns.append(f"学费：{tnote}")

    # --- 地理（B 级：人工整理的元信息）---
    if school.get("city"):
        metrics["city"] = school["city"]
    else:
        metrics["city"] = None
        unknowns.append("城市：元信息表 school_meta.json 里没有该校条目")

    # --- 明确无法获取的维度（诚实边界，SPEC §4.8）---
    # 逐维度的机器可读缺失清单由顶层 `dimension_missing`（N32）给出；
    # 这里的 `unknowns` 面向"人"阅读，所以按原因分组而不是一维一条。
    metrics["line_vs_national"] = _line_vs_national(metrics.get("score_line_total"))
    if metrics["line_vs_national"] is None:
        unknowns.append("复试线−国家线差值：缺少可核验的国家线数据源，未做推算")
    for code in ("admission_ratio", "recommend_ratio", "re_exam_ratio", "score_weight",
                 "accepts_transfer", "admitted_min_score", "undergrad_requirement",
                 "past_papers_public", "living_cost", "distance_from_home",
                 "regional_industry", "graduate_destination", "alumni_records"):
        metrics.setdefault(code, None)
    for code in ("scholarship", "discipline_rating", "has_phd", "advisor_count"):
        metrics.setdefault(code, None)
    for code in ("protects_first_choice", "score_deflation"):
        metrics.setdefault(code, None)
    unknowns.append(
        "官方未公布/未抓取（不作为推荐排序依据）：报录比、推免占比、复试差额比例、初试复试权重、"
        "调剂政策、拟录取最低分、本科背景限制、真题公开情况、生活成本、与常驻地距离、"
        "区域产业、毕业生去向、学长学姐录取记录"
    )
    unknowns.append("需人工整理的 B 级数据（当前为 null）：奖助学金覆盖、学科评估等级、博士点、导师数量")
    unknowns.append(
        "属于判断而非事实、且无官方数据源（本系统不生成此类结论）：是否保护一志愿、是否压分"
    )

    return {
        "metrics": metrics,
        "unknowns": unknowns,
        "data_conflicts": conflicts,
        "sources": sources(school_id),
        "program": program,
    }


def _line_vs_national(score_line_total) -> dict | None:
    """SPEC §4.8(4)：线−国家线必须有算术依据。缺国家线就返回 None，不推算。"""
    national_path = config.DATA_DIR / "national_lines.json"
    if not national_path.exists() or not score_line_total:
        return None
    try:
        table = _read_json(national_path)
    except (OSError, ValueError):
        return None
    year = score_line_total.get("year")
    value = score_line_total.get("value")
    entry = None
    for item in (table.get("lines") or []):
        if item.get("year") == year and item.get("discipline") == "工学":
            entry = item
            break
    if not entry or entry.get("a_class") is None or value is None:
        return None
    return {
        "value": value - int(entry["a_class"]),
        "year": year,
        "national_line": int(entry["a_class"]),
        "formula": f"{value} − {entry['a_class']}（{year}年工学A类国家线）",
        "source_index": entry.get("source_index"),
        "source_url": entry.get("source_url"),
    }


_DIMENSION_LABELS_CN = {
    "admission_ratio": "报录比",
    "recommend_ratio": "推免占比",
    "re_exam_ratio": "复试差额比例",
    "score_weight": "初试/复试权重",
    "accepts_transfer": "调剂政策",
    "admitted_min_score": "拟录取最低分",
    "past_papers_public": "真题公开情况",
    "scholarship": "奖助学金覆盖",
    "discipline_rating": "学科评估等级",
    "has_phd": "博士点",
    "advisor_count": "导师信息",
    "living_cost": "生活成本",
    "distance_from_home": "与常驻地距离",
    "regional_industry": "区域产业",
    "protects_first_choice": "是否保护一志愿",
    "score_deflation": "是否压分",
    "undergrad_requirement": "本科背景限制",
    "graduate_destination": "毕业生去向",
    "alumni_records": "学长学姐录取记录",
}


def subject_table_label(code: str) -> str:
    return _DIMENSION_LABELS_CN.get(code, code)
