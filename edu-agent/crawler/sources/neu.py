"""东北大学研究生招生信息网（yz.neu.edu.cn）适配器。

站点形态（2026-10 实测）：
    * 服务端渲染的静态 HTML + 西安博达 CMS，纯 HTTP 抓取即可。
    * 内容页 URL：/<yyyy>/<mmdd>/c<栏目id>a<文章id>/page.htm
      （栏目 id 与文章 id 不一定属于同一栏目，例如分数线的文章挂在 c5932/c5933 下，
       所以 url_pattern 只校验「/年/月日/c…a…/page.htm」这一形态，不做栏目白名单。）
    * **注意**：短时间内高频访问会被跳到 `pass.neu.edu.cn/tpass/login` 统一身份
      认证（返回「未认证授权的服务」）。抓取必须串行 + 间隔 ≥1s；命中登录页时
      按普通抓取失败处理并稍后重试，不要解析其内容。

数据落点（实测）：
    分数线      https://yz.neu.edu.cn/2026/0313/c5932a451030/page.htm
                **历年「初试成绩基本要求」整页都是图片（jpg）**，正文无文字表格，
                只能用 OCR 才能取数；本适配器把原图 URL 与比例尺信息保留在
                attachments 里，把「未取得数值」写进 content 备注，不臆造分数。
    招生简章    https://yz.neu.edu.cn/2025/1009/c5933a293424/page.htm
                附件：《2026 年硕士研究生招生专业学费统计表》PDF（学制/学费）、
                招生专业目录 PDF（拟招生人数 / 硕士推免人数）
    专业目录    https://yz.neu.edu.cn/2025/1009/c5933a293436/page.htm
                **HTML 内联表格**（不是 PDF！）—— 350 行，含学院/专业/研究方向/
                学习方式/初试科目（①-④ 与科目代码）。这是本校最重要的可解析源。
"""
import re
from urllib.parse import urljoin

from .base import Source
from .. import extract

CONTENT_URL = r"/\d{4}/\d{4}/c\d+a\d+/page\.htm"

ATTACH_RE = re.compile(
    r"""(?is)<a\b[^>]*?href=["']([^"']*(?:/_upload/article/files/[^"']+\.(?:pdf|docx?|xlsx?)"""
    r"""|download\.jsp\?[^"']+))["'][^>]*>(.*?)</a>"""
)
# 分数线页的正文配图（整页图片即数据本体）
IMAGE_RE = re.compile(r"""(?is)<img\b[^>]*?src=["']([^"']*/_upload/article/images/[^"']+)["']""")
# 详情页正文里的「发布时间：YYYY-MM-DD」（列表页标题不含日期）
PUB_RE = re.compile(r"""发布时间\s*[:：]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})""")


def parse_attachments(html, base_url):
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
    # 分数线类页面：把正文大图也登记为附件，注明它是「数据本体（图片，未经 OCR）」
    for m in IMAGE_RE.finditer(html or ""):
        url = urljoin(base_url, m.group(1))
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": "正文数据图（分数线原文，图片形式，未 OCR）", "url": url, "kind": "image"})
    return out


class NEU(Source):
    school_id = "neu"
    school_name = "东北大学"
    base_url = "https://yz.neu.edu.cn/"

    def sections(self):
        return {
            "score_line": {
                "label": "复试基本分数线（初试成绩基本要求）",
                # list.htm 与 list1.htm 内容相同（伪分页）；list2/list3 才有历年条目
                "path": ["/sszs/list.htm", "/sszs/list2.htm", "/sszs/list3.htm"],
                "keywords": ["初试成绩基本要求", "分数线", "基本要求"],
                "url_pattern": CONTENT_URL,
                "exclude": ["博士"],
            },
            "admission_guide": {
                "label": "招生简章 / 招生专业目录",
                "path": "/5933/list.htm",
                "keywords": ["招生章程", "招生简章", "专业目录"],
                "url_pattern": CONTENT_URL,
                "exclude": ["博士", "推免", "单独考试", "骨干", "退役大学生士兵"],
            },
            # 复试阶段的页面：**这里才有可文本抽取的分数数据**
            # —— 「复试工作安排（含复试名单）」正文是一张统考招生计划表，附件里是
            #    各学院复试名单 PDF（含每人政治/外语/业务课一/业务课二/初试总分）；
            #    而「初试成绩基本要求」那个官方分数线页整页是图片，取不到数。
            "retest_notice": {
                "label": "复试工作安排 / 复试参考信息",
                "path": ["/sszs/list.htm", "/sszs/list2.htm", "/5932/list.htm",
                         "/5932/list2.htm"],
                "keywords": ["复试工作安排", "复试名单", "复试参考信息"],
                "url_pattern": CONTENT_URL,
                "exclude": ["博士", "推免", "单独考试", "退役大学生士兵", "骨干"],
                # 这三页只在列表页的深处/另一栏目，按 limit 抓不到，但它们是本校唯一
                # 能把「复试笔试科目」落到具体学院的官方件（每学院一份 PDF 附件），
                # 以及唯一含考生分数的清单（复试名单 PDF）。按已知 URL 直接登记。
                "seeds": [
                    {"title": "东北大学2026年硕士研究生招生考试复试工作安排（含复试名单）",
                     "url": "https://yz.neu.edu.cn/2026/0314/c5932a451100/page.htm"},
                    {"title": "东北大学2026年硕士研究生复试参考信息（005 信息科学与工程学院 / 017 计算机科学与工程学院）",
                     "url": "https://yz.neu.edu.cn/2025/1018/c5933a293857/page.htm"},
                    {"title": "东北大学2025年硕士研究生复试参考信息",
                     "url": "https://yz.neu.edu.cn/2025/0122/c5933a277277/page.htm"},
                ],
            },
        }

    def parse_detail(self, html, url):
        m = PUB_RE.search(html or "")
        return {
            "content": extract.html_to_text(html)[:20000],
            "tables": extract.extract_tables(html)[:6],
            "attachments": parse_attachments(html, url),
            "published": "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else "",
        }
