"""HTML 解析层：正文提取、链接提取、表格提取。只用标准库。

关键用途：把官方页面里的「分数线表」「招生专业目录表」变成结构化二维数组。
"""
import html as H
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

_DROP = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")
_WS = re.compile(r"[ \t\u3000]+")


_PUNCT_OR_DIGIT = re.compile(r"[，。；：、！？（）《》\d]")


def html_to_text(html: str, drop_nav: bool = True) -> str:
    """去标签得到可读正文。drop_nav 会丢掉导航类短行（无标点、无数字、≤6 字）。"""
    h = _DROP.sub(" ", html or "")
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", h)
    h = _TAG.sub(" ", h)
    h = H.unescape(h)
    h = _WS.sub(" ", h)
    keep = []
    for ln in (s.strip() for s in h.split("\n")):
        if not ln:
            continue
        if drop_nav and len(ln) <= 6 and not _PUNCT_OR_DIGIT.search(ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def guess_year(title: str, published: str = "") -> str:
    """从标题里提取「数据年份」（如 2026年复试线 → 2026）。

    比发布日期可靠 —— 部分站点列表的日期字段存在错录，
    而标题里的年份是数据的实际归属年份。
    """
    m = re.search(r"(20\d{2})\s*年", title or "")
    if m:
        return m.group(1)
    return (published or "")[:4]


class _TableParser(HTMLParser):
    """把 <table> 拍平成 [[cell, cell], ...]。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._depth = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._depth.append([])
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table" and self._depth:
            self.tables.append(self._depth.pop())
        elif tag == "tr" and self._row is not None:
            if self._depth:
                self._depth[-1].append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def extract_tables(html: str, min_rows: int = 2, min_cols: int = 2):
    """提取有意义的数据表，过滤掉纯排版用的表格。"""
    p = _TableParser()
    try:
        p.feed(html or "")
    except Exception:
        pass
    out = []
    for t in p.tables:
        rows = [r for r in t if any(c.strip() for c in r)]
        if len(rows) < min_rows:
            continue
        width = max(len(r) for r in rows)
        if width < min_cols:
            continue
        out.append([r + [""] * (width - len(r)) for r in rows])
    return out


_LINK = re.compile(r'(?is)<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>')


def extract_links(html: str, base_url: str, keyword_filter=None):
    """提取链接，返回 [{title, url}]。keyword_filter 是关键词列表。"""
    results, seen = [], set()
    for m in _LINK.finditer(html or ""):
        href, inner = m.group(1).strip(), m.group(2)
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        title = " ".join(H.unescape(_TAG.sub(" ", inner)).split())
        if not title or len(title) < 2:
            continue
        if keyword_filter and not any(k in title for k in keyword_filter):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        results.append({"title": title, "url": url})
    return results


_DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"),
    re.compile(r"(20\d{2})[-/年.](\d{1,2})"),
]


def guess_date(text: str):
    """从标题或日期字符串里抠出日期。"""
    for pat in _DATE_PATTERNS:
        m = pat.search(text or "")
        if m:
            parts = [int(g) for g in m.groups()]
            if len(parts) == 3:
                return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
            return f"{parts[0]:04d}-{parts[1]:02d}"
    return ""


def clean_cell(s: str) -> str:
    return " ".join((s or "").split())
