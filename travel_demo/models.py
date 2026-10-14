from typing import Literal
from pydantic import BaseModel, Field


class Settings(BaseModel):
    request_timeout: int = Field(75, ge=10, le=180)
    retries: int = Field(1, ge=0, le=2)
    photo_soft_minutes: float = Field(3.5, ge=0.1, le=30)
    photo_hard_minutes: float = Field(5, ge=0.1, le=60)
    history_turns: int = Field(24, ge=4, le=200)
    recovery_attempts: int = Field(2, ge=0, le=5)


class Emotion(BaseModel):
    emotions: list[str] = Field(default_factory=list)
    intensity: float = Field(0, ge=0, le=1)


class Cue(BaseModel):
    description: str
    visible_details: list[str] = Field(default_factory=list)
    uncertain: list[str] = Field(default_factory=list)


class Reply(BaseModel):
    reply: str = Field(min_length=1)
    action: Literal['acknowledge', 'ask', 'clarify', 'switch', 'close']
    question: str | None = None
    photo_id: str | None = None
    focus: str = ''
    boundary: str = ''


class Node(BaseModel):
    id: str
    date: str | None = None
    topic: str
    people: list[str] = Field(default_factory=list)
    description: str
    source_seqs: list[int] = Field(min_length=1)
    photo_ids: list[str] = Field(default_factory=list)


class Question(BaseModel):
    id: str
    text: str
    status: Literal['candidate', 'asked', 'partial', 'answered', 'declined']
    basis_seqs: list[int] = Field(default_factory=list)


class FragmentPlan(BaseModel):
    id: str
    title: str
    node_ids: list[str] = Field(min_length=1)
    sufficient: bool
    reason: str


class RecordMeta(BaseModel):
    questions: list[Question] = Field(default_factory=list)
    fragments: list[FragmentPlan] = Field(default_factory=list)
    focus: str = ''
    boundaries: list[str] = Field(default_factory=list)
    writing_brief: str = ''
    final_node_ids: list[str] = Field(default_factory=list)


class Record(RecordMeta):
    nodes: list[Node]


class RecordDraft(RecordMeta):
    nodes_markdown: str


class Variant(BaseModel):
    style: Literal['original', 'natural', 'literary']
    text: str = Field(min_length=1)


class Variants(BaseModel):
    variants: list[Variant] = Field(min_length=3, max_length=3)


class Article(BaseModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=8, max_length=80)
    photo_id: str | None = None
    client_submitted_at: float | None = Field(None, ge=0)


class ChoiceRequest(BaseModel):
    style: Literal['original', 'natural', 'literary']
    text: str = Field(min_length=1, max_length=12000)
    revision: int
    request_id: str = Field(min_length=8, max_length=80)


class MetricRequest(BaseModel):
    seq: int
    submitted_at: float
    first_response_at: float
    completed_at: float
