import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.ai_message import router
from app.core.errors import ChatError


class ChatAPITests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    @patch(
        "app.api.ai_message.prepare_reply",
        side_effect=ChatError("MODEL_NOT_CONFIGURED", "请先配置模型", 503),
    )
    def test_missing_configuration_returns_structured_error(self, prepare):
        response = self.client.post(
            "/ai-message/sendMessage", json={"userId": "user-test", "content": "你好"}
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "MODEL_NOT_CONFIGURED")

    @patch(
        "app.api.ai_message.agent_status",
        return_value={"available": True, "code": "READY"},
    )
    def test_status_uses_user_selection(self, status):
        response = self.client.get("/ai-message/status", params={"userId": "user-test"})
        self.assertTrue(response.json()["available"])
        status.assert_called_once_with("user-test")

    @patch("app.api.ai_message.select_ai_messages", return_value=[])
    def test_history_cursor_validation(self, select):
        response = self.client.get(
            "/ai-message/loadMessage",
            params={"userId": "user-test", "beforeSeq": "100", "limit": 20},
        )
        self.assertEqual(response.json(), [])
        select.assert_called_once_with(user_id="user-test", limit=20, before_seq=100)
        self.assertEqual(
            self.client.get(
                "/ai-message/loadMessage", params={"userId": "user-test", "limit": 0}
            ).status_code,
            422,
        )

    def test_rejects_blank_messages_forged_role_and_unsupported_media(self):
        for extra in (
            {"content": " "},
            {"role": "assistant"},
            {"messageType": 2},
            {"id": "not-a-uuid"},
            {"content": "a" * 10001},
        ):
            with self.subTest(extra=extra):
                response = self.client.post(
                    "/ai-message/sendMessage",
                    json={"userId": "user-test", "content": "hello", **extra},
                )
                self.assertEqual(response.status_code, 422)

    @patch("app.api.ai_message.select_ai_messages", return_value=[])
    def test_gateway_identity_cannot_query_another_user(self, select):
        response = self.client.get(
            "/ai-message/loadMessage",
            params={"userId": "other-user"},
            headers={"user-info": '{"id":"logged-in-user"}'},
        )
        self.assertEqual(response.status_code, 403)
        select.assert_not_called()
