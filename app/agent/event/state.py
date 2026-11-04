from dataclasses import dataclass
from typing import Any, TypedDict


class EventState(TypedDict, total=False):
    snapshot: dict
    proposal: dict
    experience: dict
    impact: dict
    finishing: bool


@dataclass
class EventContext:
    model: Any
