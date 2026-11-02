import re
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
    summary_mode: Literal["concise", "standard", "detailed"] | None = None

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


MISSING_VALUES = {
    "",
    "-",
    "--",
    "na",
    "n a",
    "none",
    "null",
    "nil",
    "unknown",
    "unspecified",
    "unassigned",
    "tbd",
    "tba",
    "t b d",
    "to be determined",
    "to be decided",
    "to be confirmed",
    "not specified",
    "not provided",
    "not assigned",
    "not available",
    "not stated",
    "not mentioned",
}
FIELD = r"(?:owner|assignee|deadline|due date|due|responsible person)"
EMPTY = r"(?:not specified|not provided|not assigned|not stated|not mentioned|not available|unspecified|unassigned|unknown|n/a|tbd|none)"
# Only a whole clause about a field is removed, so ordinary task wording is left alone.
PLACEHOLDER = re.compile(
    rf"(?:{FIELD}\s*(?:is\s*|:\s*|-\s*)?{EMPTY}|no {FIELD}(?:\s+(?:assigned|specified|provided|given|set|stated))?)",
    re.I,
)
NOT_A_VERB = {"a", "an", "the", "this", "that", "these", "those", "our", "their", "all", "each", "any"}


def present(value):
    """The value when it is real; missing-value placeholders become None."""
    if value is None:
        return None
    text = value.strip()
    plain = re.sub(r"[^\w\s]", " ", text.lower())
    plain = " ".join(plain.split())
    if plain in MISSING_VALUES or plain.replace(" ", "") in MISSING_VALUES:
        return None
    if PLACEHOLDER.fullmatch(text.strip(" .;,()[]-")):
        return None
    return text


def clean_task(task):
    text = re.sub(rf"[\s.;,(\[-]*\(?{PLACEHOLDER.pattern}\)?[\s.;,)\]]*(?=$|[.;,(\[])", "", task, flags=re.I)
    previous = None
    while text != previous:
        previous = text
        text = re.sub(rf"[\s.;,(\[-]*\(?{PLACEHOLDER.pattern}\)?[\s.;,)\]]*$", "", text, flags=re.I)
    return text.strip().rstrip(".;, ")


def spoken_action(item: ActionItem) -> str:
    """One natural sentence. Owner and deadline appear only when they are real."""
    task, owner, deadline = clean_task(item.task), present(item.owner), present(item.deadline)
    if owner:
        lead = re.compile(rf"^{re.escape(owner)}\s+(?:to|will|should|must)\s+", re.I)
        duty = lead.sub("", task)
        duty = re.sub(r"^to\s+", "", duty, flags=re.I)
        first = duty.split()[0] if duty.split() else ""
        verb = (
            first
            and first.lower() not in NOT_A_VERB
            and not first.isupper()
            and not first.lower().endswith(("ing", "ed"))
        )
        task = f"{owner} will {duty[0].lower() + duty[1:]}" if verb else f"{owner} will handle this: {duty}"
    if deadline and deadline.lower().removeprefix("by ") not in task.lower():
        joined = re.match(r"(?:by|before|on|until|in|at|within|next)\b", deadline, re.I)
        task = (
            f"{task} {deadline}"
            if joined and not deadline.lower().startswith("next")
            else f"{task} by {deadline}"
        )
    return task + "."


def spoken_text(content: dict) -> str:
    m = Minutes.model_validate(content)
    parts = [m.title, m.executive_summary]
    for label, items in [
        ("Discussion points", m.discussion_points),
        ("Decisions", m.decisions),
        ("Unresolved issues", m.unresolved_issues),
        ("Follow-up", m.follow_up),
    ]:
        items = [item for item in items if present(item)]
        if items:
            parts.append(label + ". " + ". ".join(items))
    if m.action_items:
        parts.append("Action items. " + " ".join(spoken_action(a) for a in m.action_items))
    return "\n\n".join(parts)
