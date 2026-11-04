"""通用兼容协议适配；生成结果不自动表示已经说出或被听到。"""

import base64

import httpx

from app.core.errors import ChatError


async def speech(connection, text, voice, instructions=""):
    base, key, model = connection
    payload = {"model": model, "voice": voice, "input": text, "response_format": "wav"}
    if instructions:
        payload["instructions"] = instructions
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            base + "/audio/speech",
            headers={"Authorization": "Bearer " + key},
            json=payload,
        )
        if response.is_error:
            raise ChatError(
                "TTS_FAILED", "所选语音模型无法生成当前声线或情绪表现，请检查配置", 502
            )
        return response.content


async def image(connection, prompt, reference=None):
    base, key, model = connection
    async with httpx.AsyncClient(timeout=150) as client:
        if reference:
            blob, mime = reference
            response = await client.post(
                base + "/images/edits",
                headers={"Authorization": "Bearer " + key},
                data={
                    "model": model,
                    "prompt": prompt,
                    "n": "1",
                    "response_format": "b64_json",
                },
                files={"image": ("reference.png", blob, mime)},
            )
        else:
            response = await client.post(
                base + "/images/generations",
                headers={"Authorization": "Bearer " + key},
                json={
                    "model": model,
                    "prompt": prompt,
                    "n": 1,
                    "response_format": "b64_json",
                },
            )
        if response.is_error:
            raise ChatError(
                "IMAGE_GENERATION_FAILED",
                "所选模型无法生成或编辑参考图片，请检查图片能力配置",
                502,
            )
        encoded = response.json()["data"][0].get("b64_json")
        if not encoded:
            raise ChatError(
                "IMAGE_FORMAT_UNSUPPORTED", "当前适配要求图片服务返回 b64_json", 502
            )
        return base64.b64decode(encoded, validate=True)
