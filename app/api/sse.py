import json
from app.domain.entities.ai_stream import AIStreamEvent


def encode_sse(event: AIStreamEvent):
    payload = json.dumps(event.data, ensure_ascii=False)
    return f"event: {event.event}\ndata: {payload}\n\n"
