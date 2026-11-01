from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=2000)
    owner: str | None = Field(default=None, max_length=200)
    deadline: str | None = Field(default=None, max_length=200)
    timestamp: float | None = Field(default=None, ge=0)


class Minutes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    executive_summary: str = Field(min_length=1, max_length=16000)
    discussion_points: list[str] = Field(max_length=100)
    decisions: list[str] = Field(max_length=100)
    action_items: list[ActionItem] = Field(max_length=100)
    unresolved_issues: list[str] = Field(max_length=100)
    follow_up: list[str] = Field(max_length=100)


class SummaryRequest(BaseModel):
    detail: Literal["concise", "standard", "detailed"] = "standard"


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_type: Literal["preset", "cloned"] = "preset"
    preset_voice_id: str | None = Field(default=None, min_length=1, max_length=200)
    cloned_voice_id: str | None = Field(default=None, min_length=32, max_length=32)
    # Keep existing clients working while the UI adopts explicit selection.
    voice: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def exclusive_voice(self):
        if self.voice_type == "cloned":
            if not self.cloned_voice_id or self.preset_voice_id is not None or self.voice is not None:
                raise ValueError("Select exactly one cloned voice")
        elif self.cloned_voice_id is not None or (
            self.voice is not None and self.preset_voice_id is not None
        ):
            raise ValueError("Select exactly one preset voice")
        return self


def spoken_text(content: dict) -> str:
    m = Minutes.model_validate(content)
    parts = [m.title, m.executive_summary]
    for label, items in [
        ("Discussion points", m.discussion_points),
        ("Decisions", m.decisions),
        ("Unresolved issues", m.unresolved_issues),
        ("Follow-up", m.follow_up),
    ]:
        if items:
            parts.append(label + ". " + ". ".join(items))
    if m.action_items:
        parts.append(
            "Action items. "
            + ". ".join(
                a.task
                + ". Owner: "
                + (a.owner or "not specified")
                + ". Deadline: "
                + (a.deadline or "not specified")
                for a in m.action_items
            )
        )
    return "\n\n".join(parts)
