"""pytest 夹具：离线模式 + 临时数据库 + 应用实例。

跑法（必须在仓库根目录）：
    $env:MOCK_LLM = "1"
    & "D:\\dsh environment\\dsh yield\\.venvs\\edu-agent\\Scripts\\python.exe" -m pytest tests -q
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 必须在 import backend 之前设好：离线模式 + 独立临时库（不污染 data/app.db）
os.environ["MOCK_LLM"] = "1"
os.environ.setdefault("LAB_ADAPTER", "mock")
_tmp_dir = Path(tempfile.mkdtemp(prefix="edu_agent_test_"))
os.environ["EDU_AGENT_DB"] = str(_tmp_dir / "test.db")

# Windows 控制台是 GBK：不重设编码的话中文断言信息会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
