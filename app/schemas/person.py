"""模型输出与公开请求分别建模；模型不能选择用户或世界。"""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import ApiModel
from app.schemas.message import AgentConfigRequest


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Emotion(InternalModel):
    name: str = Field(default="平静", max_length=40)
    valence: float = Field(default=0, ge=-1, le=1)
    arousal: float = Field(default=0.3, ge=0, le=1)
    reason: str = Field(default="", max_length=500)


class RetrievalDecision(InternalModel):
    needed: bool = False
    query: str = Field(default="", max_length=500)
    waiting_text: str = Field(default="", max_length=40)
    local_tool: Literal["none", "clock", "list_files", "read_text"] = "none"
    path: str = Field(default="", max_length=500)


class GoalChange(InternalModel):
    goal_id: str
    progress: float = Field(ge=0, le=1)
    status: Literal["active", "paused", "succeeded", "failed", "abandoned"] = "active"
    reason: str = Field(max_length=500)


class ActivityPlan(InternalModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=1, le=1440)
    mutually_agreed: bool = False

    @field_validator("starts_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("约定时间必须包含时区")
        return value


class Appraisal(InternalModel):
    emotion: Emotion = Field(default_factory=Emotion)
    decision: Literal["reply", "silence", "delay", "leave"] = "reply"
    tendency: str = Field(default="自然地交流", max_length=800)
    reason: str = Field(default="", max_length=800)
    intentional_lie: bool = False
    medium: Literal["text", "image", "audio"] = "text"
    media_prompt: str = Field(default="", max_length=1000)
    relationship_label: str = Field(default="刚认识", max_length=64)
    relationship_reason: str = Field(default="", max_length=500)
    trust_delta: float = Field(default=0, ge=-0.15, le=0.15)
    closeness_delta: float = Field(default=0, ge=-0.1, le=0.1)
    display_status: str = Field(default="在线", max_length=40)
    display_visible: bool = True
    do_not_disturb: bool = False
    goal_changes: list[GoalChange] = Field(default_factory=list, max_length=4)
    activity_plan: ActivityPlan | None = None
    new_goals: list[str] = Field(default_factory=list, max_length=2)


class MemoryItem(InternalModel):
    content: str = Field(min_length=1, max_length=1500)
    kind: Literal[
        "fact", "experience", "inference", "knowledge", "reflection", "appointment"
    ] = "experience"
    epistemic_type: Literal["observed", "reported", "inferred"] = "reported"
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    source_ids: list[str] = Field(min_length=1, max_length=30)
    supersedes_id: str | None = None


class Growth(InternalModel):
    note: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(min_length=3, max_length=20)


class MemoryBatch(InternalModel):
    memories: list[MemoryItem] = Field(default_factory=list, max_length=16)
    growth: Growth | None = None


class Companion(InternalModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=500)
    relation: str = Field(max_length=64)
    age: int = Field(default=22, ge=0, le=120)
    birthday: str = Field(default="06-15", pattern=r"^\d{2}-\d{2}$")


class Candidate(InternalModel):
    name: str = Field(min_length=1, max_length=100)
    introduction: str = Field(max_length=500)
    appearance: str = Field(max_length=800)
    personality: str = Field(max_length=1000)
    interests: str = Field(max_length=500)
    background: str = Field(max_length=1500)
    knowledge: str = Field(max_length=600)
    timezone: str = "Asia/Shanghai"
    age: int = Field(default=22, ge=18, le=100)
    birthday: str = Field(default="06-15", pattern=r"^\d{2}-\d{2}$")
    voice: str = Field(default="alloy", max_length=100)
    past: list[str] = Field(default_factory=list, max_length=6)
    goals: list[str] = Field(default_factory=list, max_length=3)
    companions: list[Companion] = Field(default_factory=list, max_length=6)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("birthday")
    @classmethod
    def valid_birthday(cls, value):
        from datetime import datetime

        datetime.strptime("2000-" + value, "%Y-%m-%d")
        return value


class CandidateBatch(InternalModel):
    candidates: list[Candidate] = Field(min_length=2, max_length=3)


class EventDialogue(InternalModel):
    speaker_id: str
    text: str = Field(min_length=1, max_length=500)


class LifeProposal(InternalModel):
    action: Literal["continue", "event"] = "continue"
    title: str = Field(default="", max_length=200)
    reason: str = Field(default="", max_length=800)
    participant_ids: list[str] = Field(default_factory=list, max_length=8)
    duration_minutes: int = Field(default=15, ge=1, le=1440)
    activity: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=120)
    phone_accessible: bool = True
    phone_silent: bool = False
    privacy_available: bool = True
    sleeping: bool = False
    perceived_by_primary: bool = True


class EventExperience(InternalModel):
    summary: str = Field(min_length=1, max_length=1600)
    dialogue: list[EventDialogue] = Field(default_factory=list, max_length=12)
    new_companion: Companion | None = None
    relation_changes: list[dict[str, str]] = Field(default_factory=list, max_length=4)
    life_changes: dict[Literal["residence", "occupation", "education"], str] = Field(
        default_factory=dict, max_length=3
    )


class LifeImpact(InternalModel):
    appraisal: Appraisal = Field(default_factory=Appraisal)
    proactive_message: str = Field(default="", max_length=1500)
    moment_text: str = Field(default="", max_length=1500)
    audience_ids: list[str] = Field(default_factory=list, max_length=12)
    comments: list[EventDialogue] = Field(default_factory=list, max_length=6)
    image_prompt: str = Field(default="", max_length=1000)


class CandidateRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    wish: str = Field(min_length=1, max_length=3000)
    meeting_mode: str = Field(default="刚加上好友", max_length=64)
    voice: str = Field(default="alloy", max_length=100)
    config: AgentConfigRequest = Field(default_factory=AgentConfigRequest)


class SelectCandidateRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=36, max_length=36)


class UserSettings(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    life_window_minutes: int = Field(default=60, ge=5, le=1440)
    online_visible: bool = True
    read_visible: bool = True
    typing_visible: bool = True
    muted: bool = False


class InteractionRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=36, max_length=36)
    kind: Literal["like", "comment"]
    content: str = Field(default="", max_length=2000)
    active: bool = True


class CredentialRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    config_id: str
    api_key: str = Field(min_length=1, max_length=4096, repr=False)


class SlotRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    slot: Literal["dialogue"]
    config_id: str | None = None
