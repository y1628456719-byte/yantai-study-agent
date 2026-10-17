"""读取环境变量配置。"""
import os
from pathlib import Path


def _load_dotenv():
    """极简 .env 加载，避免额外依赖。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 已存在的真实环境变量优先，不被 .env 覆盖
        os.environ.setdefault(key, value)


_load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
SCHOOLS_DIR = DATA_DIR / "schools"
MATERIAL_DIR = DATA_DIR / "materials"
DB_PATH = Path(os.environ.get("EDU_AGENT_DB") or (DATA_DIR / "app.db"))
FRONTEND_DIR = ROOT_DIR / "frontend"
SUBJECTS_PATH = DATA_DIR / "subjects.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SCHOOLS_DIR.mkdir(parents=True, exist_ok=True)
MATERIAL_DIR.mkdir(parents=True, exist_ok=True)

LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat").strip()
MOCK_LLM = os.environ.get("MOCK_LLM", "0").strip() in ("1", "true", "True", "yes")
MAX_AGENT_STEPS = int(os.environ.get("MAX_AGENT_STEPS", "8"))

# SPEC §11：超时 60~120s，最多 2 次退避重试
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "120"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))

# 是否向模型请求 `response_format={"type":"json_object"}`。
# 部分聚合网关不支持该参数（会 400），此时置 0 —— 我们本来就靠 `_extract_json` 容错解析。
LLM_JSON_MODE = os.environ.get("LLM_JSON_MODE", "1").strip().lower() not in ("0", "false", "no", "off")

# SPEC §9.1：实验室适配入口；失败不得静默降级
LAB_ADAPTER = os.environ.get("LAB_ADAPTER", "mock").strip().lower()
LAB_TIMEOUT = float(os.environ.get("LAB_TIMEOUT", "120"))

# 队友 C「盲点实验室」升级包（vendored 在 vendor/blindspot_lab/）：
# mock = 用他们包内 MockModel（离线、无需密钥，is_demo 恒为 true）；live = 调真实模型；replay = 用回放记录
LAB_BLINDSPOT_MODE = os.environ.get("LAB_BLINDSPOT_MODE", "mock").strip().lower()

# 推荐接口在“已配置密钥但模型调用失败”时的行为：
# 0 = 返回 MODEL_UNAVAILABLE（SPEC §14.6）；1 = 降级为规则文本并如实标记 model_used=false
RECOMMEND_DEGRADE = os.environ.get("RECOMMEND_DEGRADE", "0").strip() in ("1", "true", "True", "yes")
