"""哈尔滨工程大学研究生招生信息网适配器。

站点形态（2026-10 实测）：
    * 服务端渲染的静态 HTML + **西安博达 CMS**，纯 HTTP 抓取即可，无需浏览器内核。
    * 内容页有两种 URL 形态，必须同时放行，否则会漏掉近两年的关键页面：
        - 2026 及以后：  /<yyyy>/<mmdd>/c<栏目id>a<文章id>/page.htm
        - 2025 及以前：  /info/<栏目id>/<文章id>.htm
    * **关键数据（招生人数、初试科目、初试科目代码、复试笔试科目、学制）不在 HTML
      里，在「招生专业目录」PDF 附件里**（见 docs/REVIEW.md §3.2）。附件通常以
      `/_upload/article/files/...pdf` 直链给出，也可能是博达 `download.jsp` 形式
      （**不以 .pdf 结尾**），所以 parse_detail 必须同时匹配两者。

栏目（实测可用）：
    招生简章   /3279/list.htm      含「2027/2026 年硕士研究生招生专业目录」PDF 附件
    考试大纲   /3262/list.htm      含「初试科目考试大纲」页面（内含各科目大纲 PDF）
    招生信息   /3277/list.htm
    复试录取   /3278/list.htm
"""
import re
from urllib.parse import urljoin

from .base import Source
from .. import extract

# 两种内容页形态
CONTENT_URL = r"(?:/\d{4}/\d{4}/c\d+a\d+/page\.htm|/info/\d+/\d+\.htm)"

# 附件：直链 PDF/DOC，或博达 CMS download.jsp（不带 .pdf 后缀）
ATTACH_RE = re.compile(
    r"""(?is)<a\b[^>]*?href=["']([^"']*(?:/_upload/article/files/[^"']+\.(?:pdf|docx?|xlsx?)"""
    r"""|download\.jsp\?[^"']+))["'][^>]*>(.*?)</a>"""
)

# 详情页正文里的发布日期。研招网主站是「发布时间：YYYY-MM-DD」，学院二级站
# （icec.hrbeu.edu.cn）是 `<span class="arti-update">时间：YYYY-MM-DD</span>`，
# 两种都要认，否则学院页的 published 会落成空串。
PUB_RES = [
    re.compile(r"""发布时间\s*[:：]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"""),
    re.compile(r"""class=["']arti[-_]update["'][^>]*>\s*(?:发布时间|时间)\s*[:：]\s*"""
               r"""(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"""),
]


def parse_attachments(html, base_url):
    """抽取附件链接；返回 [{title, url, kind}]。博达 CMS 的 download.jsp 归为 pdf 可能性最高。"""
    out, seen = [], set()
    for m in ATTACH_RE.finditer(html or ""):
        href = m.group(1)
        title = re.sub(r"\s+", " ", extract._TAG.sub("", m.group(2))).strip()
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        kind = "pdf" if ".pdf" in href.lower() else ("doc" if re.search(r"\.docx?$", href, re.I) else "cms_attachment")
        out.append({"title": title or href.rsplit("/", 1)[-1], "url": url, "kind": kind})
    return out


class HEU(Source):
    school_id = "heu"
    school_name = "哈尔滨工程大学"
    base_url = "https://yzb.hrbeu.edu.cn/"

    def sections(self):
        return {
            # 注意：本站列表页会把近期文章镜像到多个栏目下（同一 URL 出现在 c3262/c3277/c3278），
            # 所以只放行标题能唯一标识数据的那几类，避免同类页面抓成重复记录；
            # collect() 内部按 URL 去重，多栏目同时配置也不会产生重复记录。
            #
            # **不设 program_catalog 栏目**：/3277/list1.htm 确实列出了 2016–2026 各年
            # 「统考硕士招生专业目录」，但 2024/2025/2026 那几篇的匿名访问全部被 CAS
            # 统一身份认证拦截（302 → cas.hrbeu.edu.cn/cas/login，回来的只有十几字登录页），
            # 唯一能匿名打开的「2027年专业目录」页是个只有标题的壳页（171 字、0 表、0 附件）。
            # 真正带数据的专业目录 PDF 挂在「招生简章」页的附件里，所以专业目录数据由
            # admission_guide 与 college_notice 两个栏目负责，这里再抓只会落进壳页/登录页。
            "admission_guide": {
                "label": "招生简章（含招生专业目录 PDF 附件）",
                # list3 才有「2026 年硕士研究生招生简章」（其附件是 2026 年专业目录 PDF，
                # 而 2026/2025/2024 的专业目录正文页匿名访问已被 CAS 拦截，只能从简章取）
                "path": ["/3279/list.htm", "/3279/list3.htm", "/3277/list1.htm"],
                "keywords": ["招生简章", "招生章程"],
                "url_pattern": CONTENT_URL,
                "exclude": ["博士", "推免", "夏令营", "卓越工程师", "非全日制"],
            },
            "exam_syllabus": {
                "label": "考试大纲（初试科目）",
                "path": ["/3262/list.htm", "/3262/list1.htm"],
                "keywords": ["考试大纲"],
                "url_pattern": CONTENT_URL,
            },
            # 信通学院二级站（icec.hrbeu.edu.cn）是另一个域名，base.pick_links 默认
            # 只收主域名链接，所以这里显式给 hosts 白名单。
            # **这里才有信息与通信工程方向最具体的两类证据**：
            #   1) 学院「复试工作办法」页 —— 附件含 2026 年第一志愿复试名单 PDF
            #      （每人单科分数 + 初试总分），是本站唯一能拿到分数的地方
            #      （哈工程研招网不公布普通计划专业复试线）；
            #   2) 「初试科目考试大纲」页 —— 含《2027 年电路、信号与系统（考试大纲）》
            #      PDF，是「④815 电路、信号与系统」这个科目代码的官方考纲证据。
            "college_notice": {
                "label": "信通学院通知公告（复试办法 / 初试科目考试大纲）",
                "path": ["https://icec.hrbeu.edu.cn/4658/list.htm",
                         "https://icec.hrbeu.edu.cn/4658/list2.htm",
                         "https://icec.hrbeu.edu.cn/4658/list3.htm",
                         "https://icec.hrbeu.edu.cn/4658/list4.htm",
                         "https://icec.hrbeu.edu.cn/4658/list5.htm"],
                "hosts": ["icec.hrbeu.edu.cn"],
                "keywords": ["复试工作办法", "复试名单", "考试大纲", "复试、加试科目",
                             "复试工作实施细则"],
                "url_pattern": CONTENT_URL,
                "exclude": ["博士", "推免", "夏令营", "调剂", "拟录取"],
                # 2026 年的复试工作办法页（其附件是本站唯一的分数原始件）不在
                # /4658 任何列表页上，只能按已知 URL 直接登记。
                "seeds": [
                    {"title": "信息与通信工程学院2026年硕士研究生复试工作办法",
                     "url": "https://icec.hrbeu.edu.cn/2026/0323/c4658a346971/page.htm"},
                    {"title": "2027年全国硕士研究生招生考试内容范围说明（电路、信号与系统）",
                     "url": "https://icec.hrbeu.edu.cn/2026/1008/c4658a352763/page.htm"},
                ],
            },
        }

    def parse_detail(self, html, url):
        """正文 + 数据表 + 附件。附件是本校全部关键数据的载体，必须带上。"""
        m = None
        for rx in PUB_RES:
            m = rx.search(html or "")
            if m:
                break
        return {
            "content": extract.html_to_text(html)[:20000],
            "tables": extract.extract_tables(html)[:6],
            "attachments": parse_attachments(html, url),
            "published": "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else "",
        }

    def collect(self, categories=None, limit=5, verbose=True):
        """在基类之上增加两层过滤。

        1. **丢弃被统一身份认证拦截的页面**：本站 2025/2024 的专业目录正文页匿名
           访问会被 302 到 `cas.hrbeu.edu.cn/cas/login`，详情页拿回来只有十几字的
           登录页。此时 rec.url 是重定向后的最终 URL，域名不再是本站 —— 直接丢弃，
           绝不让登录页混进 `data/schools/heu.json`。
        2. 提示无法判定数据年份的条目（哈工程列表页会重复出现同一篇文章的多个年份
           入口，标题里的年份是判年的唯一可靠依据）。
        """
        records = super().collect(categories=categories, limit=limit, verbose=verbose)
        base_host = re.sub(r"^https?://", "", self.base_url).split("/")[0]
        # 合法的「非主域名」= 各 section 用 hosts 显式声明的学院二级站域名。
        extra_hosts = set()
        for spec in self.sections().values():
            extra_hosts.update(spec.get("hosts") or [])
        kept = []
        for rec in records:
            host = re.sub(r"^https?://", "", rec.url or "").split("/")[0]
            if host != base_host and host not in extra_hosts:
                if verbose:
                    print(f"    ! 丢弃（被登录页拦截）{rec.title[:32]} -> {host}")
                continue
            kept.append(rec)
        if verbose:
            for rec in kept:
                if not rec.data_year:
                    print(f"    ! {rec.title[:40]} 无年份，data_year 可能不准")
        return kept
