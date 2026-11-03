from dataclasses import dataclass
from typing import TypedDict
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


class DialogueState(TypedDict):
    messages: list[BaseMessage]


@dataclass
class DialogueContext:
    model: BaseChatModel
