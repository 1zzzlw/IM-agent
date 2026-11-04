"""验证内部 JSON，最多一次格式修复，不用默认剧情掩盖失败。"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import ValidationError

from app.core.errors import ChatError


def text_content(response):
    if isinstance(response.content, str):
        return response.content
    return "".join(p.get("text", "") for p in response.content if isinstance(p, dict))


async def structured(model, schema, instruction, context):
    messages = [
        SystemMessage(
            content=instruction
            + "\n只输出符合以下 JSON Schema 的 JSON 对象，不输出解释或思维过程。字段使用严格约定的英文枚举；非 nullable 字段不得输出 null，可省略有默认值的字段：\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        ),
        HumanMessage(content=json.dumps(context, ensure_ascii=False, default=str)),
    ]
    for attempt in range(2):
        try:
            response = await model.ainvoke(messages)
        except Exception as exc:
            chain = []
            cause = exc
            while cause is not None and len(chain) < 5:
                chain.append(type(cause).__name__)
                cause = cause.__cause__
            logger.warning(
                "模型请求未完成 schema={} exception_types={}", schema.__name__, chain
            )
            raise
        text = text_content(response).strip()
        if text.startswith("```") and "\n" in text:
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            errors = [
                {"field": list(e["loc"]), "type": e["type"]}
                for e in exc.errors(include_input=False, include_context=False)
            ]
            logger.warning(
                "模型格式校验未通过 schema={} fields={}", schema.__name__, errors
            )
            if attempt:
                raise ChatError(
                    "MODEL_OUTPUT_INVALID",
                    "当前模型未按要求返回结构化结果，请更换模型或重试",
                    502,
                ) from exc
            messages.extend(
                [
                    AIMessage(content=text),
                    HumanMessage(
                        content="上次 JSON 未通过校验。只修正格式与以下字段，不改变事实，不添加没有依据的情节；再输出完整 JSON："
                        + json.dumps(errors, ensure_ascii=False)
                    ),
                ]
            )
