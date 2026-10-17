"""生成 `data/schools/neu.json`（抓取记录集合）。

内容全部来自已抓取的官方页面/附件，逐条记录来源 URL：
  1) score_line  2026 / 2025 / 2024 进入复试的初试成绩基本要求（官方原文为图片，数值经视觉转录）
  2) admission_guide  2027 学费标准明细表 / 2027 学制汇总表 / 2026 招生规模 / 2026 复试参考信息

表格列顺序说明：backend/services/school_data.py 的 extract_score_line() 读 row[1] 作总分、
row[2..3] 作单科，因此分数线表按「学科门类 | 总分 | 单科（满分=100分）| 单科（满分>100分）| 学科专业」
排列；官方原文列序写在 content 里。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "data" / "raw" / "_probe"
OUT = ROOT / "data" / "schools" / "neu.json"

CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST).replace(microsecond=0).isoformat()

SCORE_HEADER = ["学科门类", "总分", "单科（满分=100分）", "单科（满分>100分）", "学科专业"]
# 专业学位表（tables[1]）：后端 extract_score_line() 对 085xxx 专业会优先取 tables[1]，
# 并用 PROGRAM_CATEGORY_HINTS 给出的类别名（电子信息/机械/材料与化工/土木水利）做行匹配。
PRO_HEADER = ["专业学位类别", "总分", "单科（满分=100分）", "单科（满分>100分）", "学科专业"]
LAYOUT_NOTE = (
    "表列顺序按后端 backend/services/school_data.py 的 extract_score_line() 读取约定排列"
    "（row[1]=总分、row[2]/row[3]=单科）；官方原文列序为"
    "「学科门类 | 学科专业 | 总分 | 单科（满分=100分）| 单科（满分>100分）」，原文各门类数值见 content。"
)

SCORE_LINES = [
    {
        "year": 2026,
        "page_url": "https://yz.neu.edu.cn/2026/0313/c5932a451030/page.htm",
        "published": "2026-03-13",
        "image": "https://yz.neu.edu.cn/_upload/article/images/f1/54/0091a805493ba6f5ecb544942c95/2dbe5708-041e-4dda-ae66-949332c54785.png",
        "general": [315, 45, 75],
        "zhaogu": "285/40/60",
        "rows": [
            "哲学[01] 各学科专业 340 / 50 / 90",
            "经济学[02] 各学科专业 345 / 50 / 85",
            "法学[03] 各学科专业 350 / 50 / 90",
            "教育学[04] 体育学[0403]、体育[0452] 315 / 40 / 150",
            "文学[05] 各学科专业 360 / 50 / 90",
            "理学[07] 各学科专业 315 / 45 / 80",
            "工学[08] 工学照顾专业*、冶金工程[085603]、矿业工程[085705] 285 / 40 / 60",
            "工学[08] 其他学科专业 315 / 45 / 75",
        ],
        "extra": (
            "该页正文共 4 张图片，本记录转录第 1 张（一、普通计划表）与第 2 张（普通计划续表及脚注），"
            "第 3、4 张未转录。图片 URL 的响应头 Content-Type 为 image/png，实际字节为 JPEG。"
        ),
        "extra_rows_note": (
            "续表转录（第 2 张正文图 "
            "https://yz.neu.edu.cn/_upload/article/images/f1/54/0091a805493ba6f5ecb544942c95/807a507d-8972-4ce1-a99d-418744140630.png ）：\n"
            "管理学[12] 工商管理[1251] 146 / 35 / 70\n"
            "管理学[12] 公共管理[1252] 168 / 39 / 78\n"
            "管理学[12] 会计[1253] 230 / 55 / 110\n"
            "管理学[12] 工程管理[125601] 166 / 39 / 78\n"
            "管理学[12] 工业工程与管理[125603]、物流工程与管理[125604] 230 / 55 / 110\n"
            "管理学[12] 其他学科专业 345 / 50 / 85\n"
            "艺术学[13] 各学科专业 355 / 45 / 80\n"
            "交叉学科[14] 设计学[1403] 350 / 45 / 80\n"
            "交叉学科[14] 智能科学与技术[1405] 315 / 45 / 75\n"
            "第 2 张图脚注：★工学照顾专业：力学[0801]、冶金工程[0806]、动力工程及工程热物理[0807]、"
            "地质资源与地质工程[0818]、矿业工程[0819]。"
        ),
        "professional": {
            "rows": [
                ["电子信息[0854]、机械[0855]、土木水利[0859]、资源与环境[0857]、能源动力[0858]", 315, 45, 75,
                 "官方原文『其他学科专业』口径"],
            ],
            "note": (
                "本表按官方该行『工学[08] | 其他学科专业 | 315 / 45 / 75』派生，数值未做任何换算；"
                "官方原文中只有『其他学科专业』这一合并口径，未逐类别列出工学专业学位行。"
                "冶金工程[085603]、矿业工程[085705] 属工学照顾专业（285/40/60），故不在本表；"
                "材料与化工[0856] 亦未列入——该类别含照顾专业 085603，而后端按类别名匹配无法区分 085601 与 085603，"
                "列入会给出错误的 315（此处宁缺勿错）。"
            ),
        },
        "images": [
            {"title": "正文数据图（分数线原文）",
             "url": "https://yz.neu.edu.cn/_upload/article/images/f1/54/0091a805493ba6f5ecb544942c95/2dbe5708-041e-4dda-ae66-949332c54785.png",
             "kind": "image"},
            {"title": "正文数据图 2（普通计划续表、工学照顾专业脚注）",
             "url": "https://yz.neu.edu.cn/_upload/article/images/f1/54/0091a805493ba6f5ecb544942c95/807a507d-8972-4ce1-a99d-418744140630.png",
             "kind": "image"},
        ],
    },
    {
        "year": 2025,
        "page_url": "https://yz.neu.edu.cn/2025/0311/c5932a278797/page.htm",
        "published": "2025-03-11",
        "image": "https://yz.neu.edu.cn/_upload/article/images/45/ac/f1bc21fe4c64ad3a69f9ced3da03/000f99ee-39e1-44a8-b9a1-7d11e0861e96.jpg",
        "general": [310, 45, 75],
        "zhaogu": "280/40/60",
        "rows": [
            "哲学[01] 各学科专业 335 / 50 / 90",
            "经济学[02] 各学科专业 345 / 50 / 85",
            "法学[03] 各学科专业 350 / 50 / 90",
            "教育学[04] 体育学[0403]、体育[0452] 315 / 40 / 150",
            "文学[05] 各学科专业 355 / 50 / 90",
            "理学[07] 各学科专业 315 / 45 / 80",
            "工学[08] 工学照顾专业*、冶金工程[085603]、矿业工程[085705] 280 / 40 / 60",
            "工学[08] 其他学科专业 310 / 45 / 75",
        ],
        "extra": (
            "该页正文共 5 张图片，本次转录第 1 张（一、普通计划表）；"
            "原图 6735x4762，读取用缩略图 2435x1722。脚注星号对应专业在图内字号很小，未能确认其清单。"
        ),
        "extra_rows_note": (
            "本次只转录第 1 张图；第 1 张图末尾到『工学[08] 其他学科专业 310 / 45 / 75』。"
            "管理学[12]、艺术学[13]、交叉学科[14] 等后续行位于其余 4 张图中，本次未转录（未取得数值，故不列出）。"
        ),
        "professional": {
            "rows": [
                ["电子信息[0854]、机械[0855]、土木水利[0859]、资源与环境[0857]、能源动力[0858]", 310, 45, 75,
                 "官方原文『其他学科专业』口径"],
            ],
            "note": (
                "本表按官方该行『工学[08] | 其他学科专业 | 310 / 45 / 75』派生，数值未做任何换算。"
                "冶金工程[085603]、矿业工程[085705] 属工学照顾专业（280/40/60），故不在本表；"
                "材料与化工[0856] 未列入，理由同 2026 年记录（085603 为照顾专业，后端按类别名无法区分）。"
            ),
        },
    },
    {
        "year": 2024,
        "page_url": "https://yz.neu.edu.cn/2024/0314/c5932a243913/page.htm",
        "published": "2024-03-14",
        "image": "https://yz.neu.edu.cn/_upload/article/images/ec/2f/d05b6b1446fb8d24421124f54e3c/9b1e7824-fbdc-4a08-b067-ba17182fa55a.jpg",
        "general": [315, 45, 75],
        "zhaogu": "290/40/60",
        "section_title": "一、学术学位",
        "rows": [
            "哲学[01] 340 / 50 / 90",
            "经济学[02] 355 / 55 / 90",
            "法学[03] 360 / 55 / 90",
            "教育学[04] 体育学[0403] 320 / 45 / 150",
            "文学[05] 外国语言文学[0502] 365 / 55 / 90",
            "理学[07] 320 / 50 / 80",
            "工学[08]（不含工学照顾专业）315 / 45 / 75",
            "工学照顾专业* 290 / 40 / 60",
            "管理学[12] 355 / 55 / 90",
            "艺术学[13] 365 / 45 / 90",
            "交叉学科[14] 365 / 45 / 90",
        ],
        "extra": (
            "该页正文共 4 张图片，本记录转录第 1 张（一、学术学位表）与第 2 张（二、专业学位表），第 3、4 张未转录；"
            "图片脚注：*工学照顾专业：力学[0801]、冶金工程[0806]、动力工程及工程热物理[0807]、"
            "地质资源与地质工程[0818]、矿业工程[0819]。"
        ),
        "general_label": "不含工学照顾专业",
        "professional": {
            "rows": [
                ["应用统计[0252]、国际商务[0254]", 355, 55, 90, "全国统考"],
                ["体育[0452]", 320, 45, 150, "全国统考"],
                ["翻译[0551]", 365, 55, 90, "全国统考"],
                ["电子信息[0854]、机械[0855]、材料工程[085601]、化学工程[085602]、环境工程[085701]、"
                 "安全工程[085702]、能源动力[0858]、土木水利[0859]", 315, 45, 75, "全国统考"],
                ["冶金工程[085603]、矿业工程[085705]", 290, 40, 60, "全国统考"],
                ["音乐[1352]、设计[1357]", 365, 45, 90, "全国统考"],
                ["会计[1253]", 240, 60, 120, "全国统考"],
                ["工业工程与管理[125603]、物流工程与管理[125604]", 240, 60, 120, "全国统考"],
                ["工商管理[1251]、公共管理[1252]、工程管理[125601]", None, None, None,
                 "官方原文为合并单元格，未给出数值：全国硕士研究生招生考试复试基本分数线（A类考生）。"
                 "之后学院公布各专业具体复试分数线。"],
            ],
            "note": (
                "本表为官方第 2 张图「二、专业学位」原文转录（"
                "https://yz.neu.edu.cn/_upload/article/images/ec/2f/d05b6b1446fb8d24421124f54e3c/"
                "8cf38b3e-8395-42b8-9e11-5d97ad0ce8c8.jpg ），数值逐格照抄。"
                "工商管理[1251]、公共管理[1252]、工程管理[125601] 三行在原文中与说明文字合并为一个单元格，图内无数值。"
            ),
        },
        "images": [
            {"title": "正文数据图（一、学术学位）",
             "url": "https://yz.neu.edu.cn/_upload/article/images/ec/2f/d05b6b1446fb8d24421124f54e3c/9b1e7824-fbdc-4a08-b067-ba17182fa55a.jpg",
             "kind": "image"},
            {"title": "正文数据图 2（二、专业学位）",
             "url": "https://yz.neu.edu.cn/_upload/article/images/ec/2f/d05b6b1446fb8d24421124f54e3c/8cf38b3e-8395-42b8-9e11-5d97ad0ce8c8.jpg",
             "kind": "image"},
        ],
    },
]


def score_record(spec: dict) -> dict:
    total, s1, s2 = spec["general"]
    year = spec["year"]
    pro = spec.get("professional")
    section = spec.get("section_title", "一、普通计划")
    content = (
        f"东北大学{year}年硕士研究生招生考试考生进入复试的初试成绩基本要求。\n"
        f"官方原文以图片形式发布，{spec['image']} ，表格数值经视觉识别转录；"
        f"工学照顾专业为 {spec['zhaogu']}，此处表格只保留通用工学行。\n"
        f"{spec['extra']}\n"
        f"{section}（门类/专业 | 总分 | 单科（满分=100分）| 单科（满分>100分））：\n"
        + "\n".join(spec["rows"])
        + (f"\n{spec['extra_rows_note']}" if spec.get("extra_rows_note") else "")
        + (
            "\ntables[1] 为专业学位表：" + pro["note"]
            if pro
            else ""
        )
        + f"\n{LAYOUT_NOTE}\n"
        f"发布日期 {spec['published']} 取自官方页面 URL 日期段（/{year}/{spec['published'][5:7]}{spec['published'][8:10]}/）。"
    )
    tables = [
        [
            SCORE_HEADER,
            ["工学[08]", total, s1, s2, spec.get("general_label", "其他学科专业")],
            ["备注", f"工学照顾专业*、冶金工程[085603]、矿业工程[085705] 为 {spec['zhaogu']}；本表仅保留通用工学行", "", "", ""],
        ]
    ]
    if pro:
        tables.append([PRO_HEADER, *pro["rows"], ["备注", pro["note"], "", "", ""]])
    return {
        "school_id": "neu",
        "school": "东北大学",
        "category": "score_line",
        "title": f"东北大学{year}年硕士研究生招生考试考生进入复试的初试成绩基本要求",
        "url": spec["page_url"],
        "published": spec["published"],
        "data_year": year,
        "fetched_at": NOW,
        "level": "official",
        "transcribed_from_image": True,
        "content": content,
        "tables": tables,
        "attachments": spec.get("images")
        or [{"title": "正文数据图（分数线原文）", "url": spec["image"], "kind": "image"}],
    }


def guide_records(side: dict) -> list[dict]:
    rows = side.get("rows") or {}
    src = side.get("sources") or {}
    dur_rows = rows.get("duration") or []
    fee_rows = rows.get("tuition") or []
    scale = side.get("scale") or {}

    scale_rows = []
    for key, g in scale.items():
        if g.get("plan") is not None:
            value, basis = g["plan"], "专业级合并单元格"
        elif g.get("pair_sum"):
            value, basis = g["pair_sum"][0], "各研究方向行加总：" + "、".join(f"{a}/{b}" for a, b in g["pairs"])
        else:
            value, basis = None, "原表未给出数值"
        rec = g.get("recommend")
        if g.get("plan") is None and g.get("pair_sum"):
            rec = g["pair_sum"][1]
        scale_rows.append([g["college_code"], g.get("college_name") or "", g["program_code"],
                           g.get("program_name") or "", value, rec, basis])

    return [
        {
            "school_id": "neu",
            "school": "东北大学",
            "category": "admission_guide",
            "title": "东北大学2027年硕士研究生招生专业学费标准明细表",
            "url": "https://yz.neu.edu.cn/2026/0930/c5933a480167/page.htm",
            "published": "2026-09-30",
            "data_year": 2027,
            "fetched_at": NOW,
            "level": "official",
            "content": (
                "来源：东北大学2027年全国硕士研究生招生章程（页面 https://yz.neu.edu.cn/2026/0930/c5933a480167/page.htm ）"
                "的附件 PDF《2027年硕士招生专业学费标准明细表》"
                "（https://yz.neu.edu.cn/_upload/article/files/7c/2a/7e2da1164b4b91f7358d35de7ef0/ad11c8dc-9d3a-4bd9-a9ee-06834729942c.pdf "
                "，88639 字节，sha256 前缀 ac1386dac88d027f，与本页附件逐字节一致）。\n"
                "原表列：学院码 | 学院名称 | 专业代码 | 专业名称 | 学习方式 | 学费标准。"
                "学费标准单位为「元/年.生」或「元/生」（非全日制按全程计），原表如此，未做换算。\n"
                "原表末尾备注：低空技术与工程、城乡规划为新增专业，学费标准最终以辽宁省发展和改革委员会审批为准。\n"
                "注：本表没有「学术型硕士/专业学位硕士」这类培养类型列，因此后端 extract_tuition() "
                "（按 row[1] 匹配培养类型）不会命中本表，NEU 的学费以本记录与 neu_catalog_*.json 的 "
                "programs[].tuition 字段为准。"
            ),
            "tables": [[["学院码", "学院名称", "专业代码", "专业名称", "学习方式", "学费标准"]] + fee_rows],
            "attachments": [
                {"title": "2027年硕士招生专业学费标准明细表", "kind": "pdf",
                 "url": "https://yz.neu.edu.cn/_upload/article/files/7c/2a/7e2da1164b4b91f7358d35de7ef0/ad11c8dc-9d3a-4bd9-a9ee-06834729942c.pdf"},
            ],
        },
        {
            "school_id": "neu",
            "school": "东北大学",
            "category": "admission_guide",
            "title": "东北大学2027年硕士研究生招生专业学制情况汇总表",
            "url": "https://yz.neu.edu.cn/2026/0930/c5933a480167/page.htm",
            "published": "2026-09-30",
            "data_year": 2027,
            "fetched_at": NOW,
            "level": "official",
            "content": (
                "来源：东北大学2027年全国硕士研究生招生章程的附件 PDF《东北大学2027年硕士研究生招生专业学制情况汇总表》"
                "（https://yz.neu.edu.cn/_upload/article/files/7c/2a/7e2da1164b4b91f7358d35de7ef0/fbd72bb5-da62-41ab-935c-65bf68fb6a91.pdf "
                "，66210 字节，sha256 前缀 e6eda276be3d6f56）。\n"
                "原表列：学院码 | 学院名称 | 专业代码 | 专业名称 | 学习方式 | 学制（年）。PDF 共 4 页，每页重复同一表头。\n"
                "同一学制已按专业代码写入 neu_catalog_*.json 的 programs[].duration_years。"
            ),
            "tables": [[["学院码", "学院名称", "专业代码", "专业名称", "学习方式", "学制（年）"]] + dur_rows],
            "attachments": [
                {"title": "东北大学2027年硕士研究生招生专业学制情况汇总表", "kind": "pdf",
                 "url": "https://yz.neu.edu.cn/_upload/article/files/7c/2a/7e2da1164b4b91f7358d35de7ef0/fbd72bb5-da62-41ab-935c-65bf68fb6a91.pdf"},
            ],
        },
        {
            "school_id": "neu",
            "school": "东北大学",
            "category": "admission_guide",
            "title": "东北大学2026年硕士研究生招生专业目录（含拟招生人数）",
            "url": "https://yz.neu.edu.cn/2025/1009/c5933a293424/page.htm",
            "published": "2025-10-09",
            "data_year": 2026,
            "fetched_at": NOW,
            "level": "official",
            "content": (
                "来源：东北大学2026年全国硕士研究生招生章程（含拟招生人数）（页面 "
                "https://yz.neu.edu.cn/2025/1009/c5933a293424/page.htm ）的附件1 PDF"
                "（https://yz.neu.edu.cn/_upload/article/files/70/ba/c702279f4bdabfe25fca5b1e6e11/106a5be3-929b-4d8c-81b3-4debc9b8cae2.pdf "
                "，175384 字节，sha256 前缀 ead2118437856f4e，与本页附件逐字节一致），20 页。\n"
                "原表列为「拟招生人数」「硕士推免人数」，两者均为纵向合并单元格（一个专业可能只出现在其中一个数字行）；"
                "PDF 抽取时页码会与表格单元格黏连（如「17 5006 资源与土木工程学院」= 上页页脚 17/50 + 本页 006 学院），"
                "解析脚本 scripts/_neu_side_pdfs.py 已按正则拆分。\n"
                "「数据来源」列：专业级合并单元格直接取值；无合并单元格的专业由各研究方向行加总（括号内为各行原始值）。\n"
                "已知官方表内部矛盾：017 计算机科学与工程学院 085402 通信工程专业级单元格为 25/7，"
                "而其研究方向 06【秦皇岛】=53/3、07【佛山】=5/0，合计 58/3 大于专业级单元格值；"
                "本记录与 neu_catalog_017.json 均保留官方专业级单元格值（25/7），不做调整。\n"
                "注意：该人数是 2026 年数据；2027 年专业目录页面不含「拟招生人数」列，"
                "故 neu_catalog_*.json 的 admission_scale 引用的是本 2026 年数据，并在每条 program 上标注 admission_scale_source_year=2026。"
            ),
            "tables": [[["学院码", "学院名称", "专业代码", "专业名称", "拟招生人数", "硕士推免人数", "数据来源"]] + scale_rows],
            "attachments": [
                {"title": "东北大学2026年硕士研究生招生专业目录（含拟招生人数）", "kind": "pdf",
                 "url": "https://yz.neu.edu.cn/_upload/article/files/70/ba/c702279f4bdabfe25fca5b1e6e11/106a5be3-929b-4d8c-81b3-4debc9b8cae2.pdf"},
            ],
        },
        {
            "school_id": "neu",
            "school": "东北大学",
            "category": "admission_guide",
            "title": "东北大学2026年硕士研究生招生考试复试参考信息（005 信息科学与工程学院 / 017 计算机科学与工程学院）",
            "url": "https://yz.neu.edu.cn/2025/1018/c5933a293857/page.htm",
            "published": "2025-10-18",
            "data_year": 2026,
            "fetched_at": NOW,
            "level": "official",
            "content": (
                "来源：东北大学2026年硕士研究生招生考试复试参考信息（页面 https://yz.neu.edu.cn/2025/1018/c5933a293857/page.htm ）"
                "附件列表中的两个学院 PDF：\n"
                "005 信息科学与工程学院 "
                "https://yz.neu.edu.cn/_upload/article/files/1f/0d/b7d07d3c48029807e784d5331a44/c371ec37-32ba-4f91-8668-ad5756aa8098.pdf （85662 字节，3 页）\n"
                "017 计算机科学与工程学院 "
                "https://yz.neu.edu.cn/_upload/article/files/1f/0d/b7d07d3c48029807e784d5331a44/ecdc4ae2-dd60-440d-b8ab-9c860f438488.pdf （86900 字节，3 页）\n"
                "原表列：专业 | 研究方向 | 复试方式及分数组成 | 专业素质和能力考核内容参考 | 复试比例 | "
                "复试名单确定规则 | 拟录取名单确定规则 | 同等学力加试科目及参考书目。两学院所有专业复试比例均为 120%。\n"
                "原表页脚：总成绩折算办法：初试成绩满分为500分的专业，考生总成绩=初试总分÷5×65%+复试总分÷1×35%；"
                "初试成绩满分为300分的专业，考生总成绩=初试总分÷3×65%+复试总分÷1×35%。\n"
                "同源正文（两页共有）：跨学科专业报考我校的考生无需加试。以同等学力身份报考并参加复试的考生，"
                "在复试中须加试两门与报考专业相关的本科主干课程。加试方式为笔试，加试不合格者不予录取。\n"
                "各专业复试方式与参考书目已写入 neu_catalog_005.json / neu_catalog_017.json 的 programs[].re_exam_* 字段。"
                "本记录只覆盖 005 与 017 两个学院（其余 20 个附件未解析）。"
            ),
            "tables": [[
                ["学院", "专业代码", "专业名称", "复试方式及分数组成", "复试比例", "同等学力加试科目"],
                *[[cc, pc, name, info["method"], info["ratio"] or "", "、".join(info["equal_ability"])]
                  for (cc, pc), (name, info) in RE_EXAM_ROWS],
            ]],
            "attachments": [
                {"title": "005 信息科学与工程学院 2026年硕士研究生复试参考信息", "kind": "pdf",
                 "url": "https://yz.neu.edu.cn/_upload/article/files/1f/0d/b7d07d3c48029807e784d5331a44/c371ec37-32ba-4f91-8668-ad5756aa8098.pdf"},
                {"title": "017 计算机科学与工程学院 2026年硕士研究生复试参考信息", "kind": "pdf",
                 "url": "https://yz.neu.edu.cn/_upload/article/files/1f/0d/b7d07d3c48029807e784d5331a44/ecdc4ae2-dd60-440d-b8ab-9c860f438488.pdf"},
            ],
        },
    ]


def collect_re_exam_rows() -> list:
    """把 _re_exam_map.json 与 catalog 里的专业名对上，供 admission_guide 表格使用。"""
    rx = json.loads((ROOT / "data" / "raw" / "neu" / "_re_exam_map.json").read_text(encoding="utf-8"))
    names: dict[tuple[str, str], str] = {}
    for cc in ("005", "017"):
        path = ROOT / "data" / "schools" / f"neu_catalog_{cc}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data["extracted"]["programs"]:
            names[(cc, p["program_code"])] = p["program_name"]
    out = []
    for key, info in rx.items():
        cc, pc = key.split("|")
        out.append(((cc, pc), (names.get((cc, pc), ""), info)))
    return out


def merge_records(mine: list[dict], existing: list[dict]) -> tuple[list[dict], list[str]]:
    """把本脚本的记录并进磁盘上已有的 neu.json，不丢弃其他来源的记录。

    **为什么必须合并**：`data/schools/neu.json` 会被其他写入者重写（实测 2026-10-16T21:35:10
    有一次非本脚本的写入，含 2023/2024 分数线记录与 retest_notice）。直接覆盖会删掉它们；
    而不写入又会让本脚本带表格的分数线记录失效——`extract_score_line()` 取
    `next(r for r in records if _score_line_tables(r))`，同一年的无表格记录会被跳过，
    于是后端会退回去读 `data/schools/neu_score_line.json` 里那张 9 行表。
    规则：同 URL 的 score_line 记录以本脚本为准（本脚本的带图片转录与分列表），其余原样保留。
    """
    log: list[str] = []
    mine_score_urls = {r["url"] for r in mine if r.get("category") == "score_line"}
    merged: list[dict] = []
    seen: set[tuple] = set()
    for r in existing:
        if r.get("category") == "score_line" and r.get("url") in mine_score_urls:
            log.append(f"replace score_line {r.get('data_year')} {r.get('url')}")
            continue
        k = (r.get("category"), r.get("url"), str(r.get("data_year")))
        if k in seen:
            log.append(f"drop duplicate {k}")
            continue
        seen.add(k)
        merged.append(r)
    for r in mine:
        k = (r.get("category"), r.get("url"), str(r.get("data_year")))
        if k in seen:
            log.append(f"keep existing {k}")
            continue
        seen.add(k)
        merged.append(r)
        log.append(f"add {k}")
    return merged, log


if __name__ == "__main__":
    side = json.loads((PROBE / "_neu_side.json").read_text(encoding="utf-8"))
    RE_EXAM_ROWS = collect_re_exam_rows()
    mine = [score_record(s) for s in SCORE_LINES] + guide_records(side)
    payload: dict = {}
    if OUT.exists():
        payload = json.loads(OUT.read_text(encoding="utf-8")) or {}
    records, log = merge_records(mine, payload.get("records") or [])
    if OUT.exists():
        # 该文件有多个写入者（见 merge_records 的说明），覆盖前留一份可回溯的备份
        backup = PROBE.parent / "neu" / "_backup_neu_before_merge.json"
        backup.write_bytes(OUT.read_bytes())
        print("backup ->", backup, OUT.stat().st_size, "B")
    payload.update({
        "school_id": "neu",
        "school": "东北大学",
        "count": len(records),
        "generated_at": NOW,
        "records": records,
    })
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("mine", len(mine), "merged", len(records), "->", OUT)
    for line in log:
        print("  ", line)
    for r in records:
        n = sum(len(t) for t in (r.get("tables") or []))
        print(r["category"], r["data_year"], str(r["title"])[:40], "table rows", n)
