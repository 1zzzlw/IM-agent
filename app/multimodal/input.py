"""模型只提供感知线索；这里不修改人物状态或记忆。"""

import base64

import httpx
from langchain_core.messages import HumanMessage

from app.core.errors import ChatError
from app.llm.structured import text_content


async def describe_image(model, blob, mime):
    response = await model.ainvoke(
        [
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": "客观描述照片中可见内容、文字和人物外貌。身份、关系及情绪只能作为不确定线索，不臆测照片外的经历。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,"
                            + base64.b64encode(blob).decode()
                        },
                    },
                ]
            )
        ]
    )
    return text_content(response)


async def transcribe_audio(connection, blob, mime, suffix):
    base, key, model = connection
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            base + "/audio/transcriptions",
            headers={"Authorization": "Bearer " + key},
            data={"model": model},
            files={"file": ("recording." + suffix, blob, mime)},
        )
        if response.is_error:
            raise ChatError(
                "ASR_FAILED", "当前模型无法识别这段语音，请检查 ASR 能力配置", 502
            )
        transcript = response.json().get("text", "")
    if not transcript.strip():
        raise ChatError("EMPTY_AUDIO", "未识别到说话内容", 422)
    return transcript


async def audio_emotion(connection, blob, suffix):
    base, key, model = connection
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            base + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "根据这段原始声音描述语速、停顿、笑声或哽咽，以及可能的情绪，保留不确定性，不做诊断。",
                            },
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": base64.b64encode(blob).decode(),
                                    "format": suffix,
                                },
                            },
                        ],
                    }
                ],
            },
        )
        if response.is_error:
            raise ChatError(
                "AUDIO_EMOTION_UNSUPPORTED", "声音情绪模型不支持当前格式或接口", 502
            )
        return response.json()["choices"][0]["message"]["content"]
