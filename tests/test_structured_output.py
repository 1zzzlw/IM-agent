import unittest
from types import SimpleNamespace

from app.core.errors import ChatError
from app.llm.structured import structured
from app.schemas.person import RetrievalDecision


class StructuredOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_bounded_repair_retains_contract_and_context(self):
        class Model:
            calls = []

            async def ainvoke(self, messages):
                self.calls.append(messages.copy())
                return SimpleNamespace(
                    content='{"needed":"sometimes"}'
                    if len(self.calls) == 1
                    else '{"needed":true,"query":"旧争执"}'
                )

        model = Model()
        result = await structured(
            model, RetrievalDecision, "按需回忆", {"message": "还记得上次吗"}
        )
        self.assertTrue(result.needed)
        self.assertEqual(len(model.calls), 2)
        self.assertIn("还记得上次吗", model.calls[1][1].content)

    async def test_invalid_twice_fails_without_fake_result(self):
        class Model:
            count = 0

            async def ainvoke(self, messages):
                self.count += 1
                return SimpleNamespace(content="not JSON")

        model = Model()
        with self.assertRaises(ChatError) as caught:
            await structured(model, RetrievalDecision, "判断", {})
        self.assertEqual(caught.exception.code, "MODEL_OUTPUT_INVALID")
        self.assertEqual(model.count, 2)
