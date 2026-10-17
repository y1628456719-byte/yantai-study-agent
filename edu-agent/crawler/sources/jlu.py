"""吉林大学研究生招生信息网适配器。

该站是 .htm 静态页 + 服务端渲染，可纯 HTTP 抓取，无需浏览器内核。
内容页 URL 固定为 /info/<栏目id>/<文章id>.htm —— 用它把导航链接滤掉。

栏目：
    分数线    /sszs/fsx.htm
    招生简章  /sszs/zsjz.htm
"""
from .base import Source

CONTENT_URL = r"/info/\d+/\d+\.htm"


class JLU(Source):
    school_id = "jlu"
    school_name = "吉林大学"
    base_url = "https://yzb.jlu.edu.cn/"

    def sections(self):
        return {
            "score_line": {
                "label": "分数线",
                "path": "/sszs/fsx.htm",
                "keywords": ["成绩基本要求", "分数线", "复试"],
                "url_pattern": CONTENT_URL,
            },
            "admission_guide": {
                "label": "招生简章",
                "path": "/sszs/zsjz.htm",
                "keywords": ["简章", "专业目录", "招生章程", "招生专业"],
                "url_pattern": CONTENT_URL,
                "exclude": ["博士", "推免"],
            },
        }
