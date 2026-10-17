"""站点适配器基类与数据记录结构。

一条 Record = 一条可溯源的数据。url 与 fetched_at 必须保留 —— 这是本产品
「判断依据可追溯」的基础，也是答辩时能当场抽查的凭证。
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from .. import extract, http

CST = timezone(timedelta(hours=8))

NAV_WORDS = {"首页", "下页", "尾页", "上页", "上一页", "下一页", "更多", "返回",
             "联系我们", "通知公告", "录取公示", "招生简章", "分数线", "硕士招生",
             "博士招生", "招生宣传", "直播回放", "招宣动态", "院系纵览", "博士报名",
             "港澳台招生", "招生公告"}

# 列表项标题常带 "18 2026.03 " 这类"日 年.月"前缀，去掉它
_TITLE_PREFIX = re.compile(r"^\s*(?:\d{1,2}\s+)?\d{4}\s*[.\-/年]\s*\d{1,2}\s*")


def now_iso():
    return datetime.now(CST).isoformat(timespec="seconds")


def clean_title(title: str) -> str:
    return _TITLE_PREFIX.sub("", (title or "").strip()).strip()


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url or "").split("/")[0]


class Record:
    def __init__(self, school_id, school, category, title, url,
                 published="", content="", tables=None, attachments=None):
        self.school_id = school_id
        self.school = school
        self.category = category
        self.title = title
        self.url = url
        self.published = published
        self.data_year = extract.guess_year(title, published)
        self.content = content
        self.tables = tables or []
        self.attachments = attachments or []
        self.fetched_at = now_iso()
        self.raw_html = ""

    def key(self):
        return hashlib.md5(self.url.encode("utf-8")).hexdigest()[:12]

    def to_dict(self):
        return {
            "school_id": self.school_id,
            "school": self.school,
            "category": self.category,
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "data_year": self.data_year,
            "fetched_at": self.fetched_at,
            "content": self.content,
            "tables": self.tables,
            "attachments": self.attachments,
        }


class Source:
    """一所学校 = 一个子类。子类只需声明 sections，必要时覆盖 parse_detail。"""

    school_id = ""
    school_name = ""
    base_url = ""

    def __init__(self, delay=1.0):
        self.delay = delay

    def sections(self):
        """返回 {category: {label, path, keywords, url_pattern, exclude}}"""
        raise NotImplementedError

    def parse_detail(self, html, url):
        """默认解析：正文 + 数据表。子类可覆盖以抽取更精细的字段。"""
        return {
            "content": extract.html_to_text(html)[:20000],
            "tables": extract.extract_tables(html)[:6],
        }

    def pick_links(self, html, list_url, spec):
        """从列表页里挑出真正的「内容条目」，滤掉导航、页脚、外链。"""
        pattern = spec.get("url_pattern")
        exclude = spec.get("exclude") or []
        # 默认只收本校主域名的链接；spec 里显式给了 hosts 时，改用该白名单
        # （用于「学院二级站与研招网不同域名」的情形，例如哈工程的信通学院站）。
        allowed_hosts = set(spec.get("hosts") or [_host(self.base_url)])
        picked = []
        for link in extract.extract_links(html, list_url, keyword_filter=spec.get("keywords")):
            title, url = clean_title(link["title"]), link["url"]
            if url.rstrip("/") == list_url.rstrip("/"):
                continue
            if _host(url) not in allowed_hosts:
                continue
            if title in NAV_WORDS or len(title) < 8:
                continue
            if pattern and not re.search(pattern, url):
                continue
            if any(k in title for k in exclude):
                continue
            picked.append({"title": title, "url": url,
                           "published": extract.guess_date(link["title"])})
        return picked

    def collect(self, categories=None, limit=5, verbose=True):
        records = []
        for cat, spec in self.sections().items():
            if categories and cat not in categories:
                continue
            # path 可以是单个路径，也可以是多个列表页（用于翻页 / 历年栏目），
            # 这样一所学校的历史年份能一并入库。既有单路径适配器行为不变。
            paths = spec["path"]
            if isinstance(paths, str):
                paths = [paths]
            links, seen_urls = [], set()
            for path in paths:
                list_url = urljoin(self.base_url, path)
                if verbose:
                    print(f"  [{spec['label']}] 列表页 {list_url}")
                try:
                    html, _status, final = http.fetch(list_url, delay=self.delay)
                except http.FetchError as e:
                    print(f"    x {e}")
                    continue
                if not isinstance(html, str):
                    print("    x 列表页返回的不是 HTML")
                    continue
                for lk in self.pick_links(html, final, spec):
                    if lk["url"] in seen_urls:
                        continue
                    seen_urls.add(lk["url"])
                    links.append(lk)
            # 已知内容页可以直接登记（有些关键页不在列表页里：学院的「复试工作办法」
            # 只挂在二级站的另一栏目，翻遍列表也找不到）。spec["seeds"] =
            # [{"title": ..., "url": ...}]，仍是官方 URL，同样要过详情页。
            for seed in spec.get("seeds") or []:
                url = seed["url"] if isinstance(seed, dict) else seed
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                links.append({"title": (seed.get("title") if isinstance(seed, dict) else "")
                              or url,
                              "url": url, "published": ""})
            if verbose:
                print(f"    命中 {len(links)} 个内容条目，取前 {min(limit, len(links))} 条")

            for link in links[:limit]:
                try:
                    d_html, _s, d_url = http.fetch(link["url"], delay=self.delay)
                except http.FetchError as e:
                    print(f"    x {link['title'][:22]} — {e}")
                    continue
                if not isinstance(d_html, str):
                    print(f"    x {link['title'][:22]} — 非 HTML（可能是附件）")
                    continue
                parsed = self.parse_detail(d_html, d_url)
                rec = Record(
                    school_id=self.school_id,
                    school=self.school_name,
                    category=cat,
                    title=link["title"],
                    url=d_url,
                    # 列表页标题常不含日期（博达 CMS 把日期放在独立 span 里），
                    # 子类的 parse_detail 可从详情页正文「发布时间：YYYY-MM-DD」补出。
                    published=parsed.get("published") or link["published"],
                    content=parsed.get("content", ""),
                    tables=parsed.get("tables", []),
                    attachments=parsed.get("attachments", []),
                )
                rec.raw_html = d_html
                records.append(rec)
                if verbose:
                    print(f"      ok {rec.title[:40]}  "
                          f"({rec.published or '无日期'} | {len(rec.content)} 字 | {len(rec.tables)} 表)")
        # 同一内容页可能同时命中多个栏目（本站列表页会把同一篇文章镜像到多个栏目，
        # 例如东北大学的「复试参考信息」既出现在简章栏目也出现在复试栏目）。
        # 按 URL 去重，保留附件/正文信息量最大的那一条，避免交付 JSON 出现重复条目。
        by_url = {}
        for rec in records:
            prev = by_url.get(rec.url)
            if prev is None or len(rec.attachments) > len(prev.attachments):
                by_url[rec.url] = rec
        return list(by_url.values())
