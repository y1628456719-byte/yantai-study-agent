"""请求体模型（SPEC §7）。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProfileIn(BaseModel):
    """用户档案。N1 结构化字段与 N29 阶段字段**并存**（非破坏性）。"""

    model_config = ConfigDict(extra="ignore")

    # 原契约字段
    exam_year: int | None = None
    major: str | None = None
    background: str | None = None
    preferred_regions: list[str] | None = None
    daily_minutes: int | None = Field(default=None, ge=1, le=1440)
    preferences: str | None = None

    # N1：把自由文本拆成可引用的结构化字段（保留原字段并存）
    undergrad_school: str | None = None
    undergrad_tier: str | None = None
    gpa_band: str | None = None
    english_level: str | None = None
    # N8 硬规则的输入：用户目标分（可选；不传则按候选院校复试线相对分档）
    target_score: int | None = Field(default=None, ge=100, le=500)
    study_status: str | None = None

    # N29 / N30
    phase: str | None = None
    phase_confidence: str | None = None
    known_facts: dict | None = None
    is_cross_major: bool | None = None
    undergrad_major: str | None = None
    target_major: str | None = None


class ProfilePatchIn(BaseModel):
    """部分更新：只传要改的字段。"""

    model_config = ConfigDict(extra="ignore")

    exam_year: int | None = None
    major: str | None = None
    background: str | None = None
    preferred_regions: list[str] | None = None
    daily_minutes: int | None = Field(default=None, ge=1, le=1440)
    preferences: str | None = None
    undergrad_school: str | None = None
    undergrad_tier: str | None = None
    gpa_band: str | None = None
    english_level: str | None = None
    target_score: int | None = Field(default=None, ge=100, le=500)
    study_status: str | None = None
    phase: str | None = None
    is_cross_major: bool | None = None
    undergrad_major: str | None = None
    target_major: str | None = None
    known_facts: dict | None = None


class SessionCreateIn(BaseModel):
    profile: ProfileIn = Field(default_factory=ProfileIn)


class TargetIn(BaseModel):
    school_id: str
    user_note: str | None = None          # N9


class ShortlistIn(BaseModel):
    """N10：备选清单（先缩小到 2 所）。"""

    school_ids: list[str]


class MaterialIn(BaseModel):
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_url: str | None = None
    is_demo: bool = False
    school_id: str | None = None          # N12
    subject: str | None = None            # N12，建议用 subject_code


class LabStartIn(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    material_id: str | None = None        # N34：允许为 null → 无资料诊断路径
    topic: str | None = None
    mode: str | None = None               # material | self_assessment


class LabAnswerIn(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    question_id: str
    answer: str


class LabActionIn(BaseModel):
    """队友实验室 lab.v2 的额外动作透传（additive，SPEC 只要求 start/answer）。"""

    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    action: str                                    # start|submit|feedback|time|continue|stop
    question_id: str | None = None                 # submit 必填
    answer: str | None = None                      # submit 必填
    feedback: str | None = None                    # feedback 必填：unclear|too_easy|guessed|new_example
    minutes: int | None = Field(default=None, ge=1, le=1440)   # time 必填


class PlanReviseIn(BaseModel):
    daily_minutes: int = Field(ge=1, le=1440)


class AgentHistoryItem(BaseModel):
    """Agent 入口的历史对话（前端把最近几轮带上，便于"再答一次"不丢上下文）。"""

    model_config = ConfigDict(extra="ignore")

    role: str = "user"
    content: str = ""


class AgentIn(BaseModel):
    """P0-1：Agent 入口（**additive**，SPEC 的 12 个端点一个都没动）。

    用户只给一句话；Agent 自己决定：建档 / 追问 / 推荐 / 开小测 / 改计划。
    """

    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1)
    session_id: str | None = None            # 不给就由 Agent 自己 create_session
    history: list[AgentHistoryItem] = Field(default_factory=list)
