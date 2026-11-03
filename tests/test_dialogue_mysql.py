"""显式启用：IMAGENT_TEST_MYSQL=1 python -m unittest discover -s tests。
使用独立随机测试库、真实 MySQL 约束和本机 OpenAI SSE 服务；不写入用户数据库。
"""

import asyncio
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pymysql
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import config
from app.core.errors import ChatError
from app.api.ai_message import router as message_router
from app.api.model_config import router as config_router
from app.integrations.database.mysql import mysql_service
from app.integrations.database.message_repository import (
    begin_turn,
    complete_turn,
)
from app.schemas.message import AIMessageRequest
from app.services.ai_message_service import prepare_reply, stream_reply


@unittest.skipUnless(
    os.environ.get("IMAGENT_TEST_MYSQL") == "1", "仅显式启用真实 MySQL 测试"
)
class DialogueMysqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_name = "lumina_test_" + uuid4().hex
        cls.original_database = config.database.name
        cls.admin = pymysql.connect(
            host=config.database.host,
            port=config.database.port,
            user=config.database.user,
            password=config.database.password,
            charset="utf8mb4",
            autocommit=True,
        )
        with cls.admin.cursor() as cursor:
            cursor.execute(
                "CREATE DATABASE "
                + cls.database_name
                + " CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        mysql_service.close()
        config.database.name = cls.database_name
        mysql_service.initialize()
        ddl = Path("docs/sql/imagent_init.sql").read_text(encoding="utf-8")
        ddl = "\n".join(
            line for line in ddl.splitlines() if not line.lstrip().startswith("--")
        )
        connection = mysql_service.connection()
        try:
            with connection.cursor() as cursor:
                for statement in ddl.split(";"):
                    if statement.strip():
                        cursor.execute(statement)
            connection.commit()
        finally:
            connection.close()
        cls.requests = []
        owner = cls

        class ModelHandler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                last = body["messages"][-1]["content"]
                if last == "upstream-error":
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        b'{"error":{"message":"fixture-secret-key must never leak"}}'
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                response = "你好，" + last + "。"
                for delta in (
                    {
                        "role": "assistant",
                        "reasoning_content": "internal-reasoning-must-not-leak",
                    },
                    {"content": response[:3]},
                    {"content": response[3:]},
                ):
                    event = {
                        "id": "fixture-chat",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "fixture-model",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": None}
                        ],
                    }
                    self.wfile.write(
                        (
                            "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                        ).encode()
                    )
                    self.wfile.flush()
                self.wfile.write(
                    b'data: {"id":"fixture-chat","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                )

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        mysql_service.close()
        config.database.name = cls.original_database
        # 只允许删除由本类随机创建的测试库。
        assert (
            cls.database_name.startswith("lumina_test_")
            and len(cls.database_name) == 44
        )
        with cls.admin.cursor() as cursor:
            cursor.execute("DROP DATABASE " + cls.database_name)
        cls.admin.close()

    def setUp(self):
        self.user = "test-" + uuid4().hex
        self.app = FastAPI()
        self.app.include_router(message_router)
        self.app.include_router(config_router)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.url = "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
        self.configuration = self.add_config(self.user)

    def add_config(self, user):
        response = self.client.post(
            "/ai-message/config/add_model",
            json={
                "userId": user,
                "configName": "本机测试模型",
                "providerName": "custom",
                "modelName": "fixture-model",
                "baseUrl": self.url,
                "modelTemperature": 0.5,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["configId"]

    def send(self, content="你好", message_id=None, config_id=None, **extra):
        payload = {
            "id": message_id or str(uuid4()),
            "conversationId": str(uuid4()),
            "userId": self.user,
            "role": "user",
            "messageType": 1,
            "content": content,
            "config": {
                "providerName": "custom",
                "configId": config_id or self.configuration,
                "apiKey": "fixture-secret-key",
                "modelName": "untrusted-client-model",
                "baseUrl": "http://wrong.invalid/v1",
            },
            **extra,
        }
        return self.client.post("/ai-message/sendMessage", json=payload)

    @staticmethod
    def events(response):
        return [
            (block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
            for block in response.text.split("\n\n")
            if block.startswith("event: ")
        ]

    def history(self, **extra):
        response = self.client.get(
            "/ai-message/loadMessage", params={"userId": self.user, **extra}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_real_stream_context_persistence_replay_and_pagination(self):
        client_id = str(uuid4())
        start_count = len(self.requests)
        response = self.send("我的名字是小林", client_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = self.events(response)
        self.assertEqual(
            [event for event, _ in events],
            [
                "run.started",
                "message.delta",
                "message.delta",
                "message.completed",
                "done",
            ],
        )
        self.assertNotIn("internal-reasoning", response.text)
        self.assertNotIn("fixture-secret-key", response.text)
        self.assertEqual(self.requests[-1]["model"], "fixture-model")
        history = self.history()
        self.assertEqual(
            [message["role"] for message in history], ["user", "assistant"]
        )
        self.assertTrue(history[0]["createdAt"].endswith("Z"))
        world = history[0]["conversationId"]
        self.assertEqual(history[1]["conversationId"], world)
        replay = self.send("我的名字是小林", client_id)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(self.events(replay)[0][1]["replayed"])
        self.assertEqual(len(self.requests), start_count + 1)
        self.assertEqual(len(self.history()), 2)
        conflict = self.send("另一个内容", client_id)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "MESSAGE_ID_CONFLICT")
        second = self.send("还记得我的名字吗")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertIn(
            "我的名字是小林",
            json.dumps(self.requests[-1]["messages"], ensure_ascii=False),
        )
        history = self.history()
        self.assertEqual([message["seq"] for message in history], ["1", "2", "3", "4"])
        self.assertEqual(len(self.history(limit=2)), 2)
        self.assertEqual(
            [message["seq"] for message in self.history(beforeSeq=3)], ["1", "2"]
        )
        self.assertEqual(
            len(
                mysql_service.select(
                    "SELECT id FROM imagent_world WHERE user_id=%s", (self.user,)
                )
            ),
            1,
        )
        self.assertEqual(
            mysql_service.select(
                "SELECT COUNT(*) n FROM imagent_observation WHERE world_id=%s", (world,)
            )[0]["n"],
            6,
        )
        self.assertEqual(
            mysql_service.select(
                "SELECT COUNT(*) n FROM imagent_client_event WHERE user_id=%s",
                (self.user,),
            )[0]["n"],
            4,
        )
        tasks = mysql_service.select(
            "SELECT payload_json,status FROM imagent_background_task WHERE user_id=%s",
            (self.user,),
        )
        self.assertTrue(all(task["status"] == "succeeded" for task in tasks))
        self.assertNotIn("fixture-secret-key", json.dumps(tasks))

    def test_failed_reply_keeps_user_message_and_retry_reuses_it(self):
        client_id = str(uuid4())
        response = self.send("upstream-error", client_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events(response)[-2][0], "run.failed")
        self.assertNotIn("fixture-secret-key", response.text)
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[0]["role"], "user")
        self.send("upstream-error", client_id)
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(
            mysql_service.select(
                "SELECT attempts FROM imagent_background_task WHERE user_id=%s",
                (self.user,),
            )[0]["attempts"],
            2,
        )

    def test_generated_client_id_can_replay_without_reinitializing_character(self):
        payload = {
            "userId": self.user,
            "content": "没有指定消息 ID",
            "config": {
                "configId": self.configuration,
                "apiKey": "fixture-secret-key",
            },
        }
        response = self.client.post("/ai-message/sendMessage", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        started = self.events(response)[0][1]
        self.assertTrue(started["clientMessageId"])
        self.assertEqual(self.history()[0]["clientMessageId"], started["clientMessageId"])
        payload["id"] = started["clientMessageId"]
        replay = self.client.post("/ai-message/sendMessage", json=payload)
        self.assertTrue(self.events(replay)[0][1]["replayed"])
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(
            self.events(replay)[0][1]["conversationId"], started["conversationId"]
        )

    def test_config_crud_binding_inheritance_and_user_isolation(self):
        rows = self.client.get(
            "/ai-message/config/getConfig", params={"userId": self.user}
        ).json()
        self.assertEqual(rows[0]["id"], self.configuration)
        self.assertNotIn("apiKey", rows[0])
        self.assertEqual(
            self.client.put(
                "/ai-message/config/switchModel",
                json={"userId": self.user, "configId": self.configuration},
            ).status_code,
            200,
        )
        status = self.client.get(
            "/ai-message/status", params={"userId": self.user}
        ).json()
        self.assertTrue(status["available"])
        self.assertTrue(status["requiresClientKey"])
        self.assertFalse(status["upstreamVerified"])
        self.assertEqual(
            self.client.delete(
                "/ai-message/config/deleteModel/" + self.configuration,
                params={"userId": self.user},
            ).status_code,
            409,
        )
        other = "other-" + uuid4().hex
        self.assertEqual(
            self.client.put(
                "/ai-message/config/switchModel",
                json={"userId": other, "configId": self.configuration},
            ).status_code,
            404,
        )
        response = self.client.post(
            "/ai-message/sendMessage",
            json={
                "userId": other,
                "content": "hello",
                "config": {
                    "configId": self.configuration,
                    "apiKey": "fixture-secret-key",
                },
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get("/ai-message/loadMessage", params={"userId": other}).json(),
            [],
        )
        self.assertEqual(
            mysql_service.select(
                "SELECT COUNT(*) n FROM imagent_world WHERE user_id=%s", (other,)
            )[0]["n"],
            0,
        )
        changed = {
            "userId": self.user,
            "configId": self.configuration,
            "configName": "更新名称",
            "providerName": "custom",
            "modelName": "fixture-model",
            "baseUrl": self.url,
        }
        self.assertEqual(
            self.client.put("/ai-message/config/updateModel", json=changed).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(
                "/ai-message/config/switchModel",
                json={"userId": self.user, "configId": None},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(
                "/ai-message/config/deleteModel/" + self.configuration,
                params={"userId": self.user},
            ).status_code,
            200,
        )
        self.assertEqual(self.send(config_id=self.configuration).status_code, 404)

    def test_model_switch_preserves_character_world_and_context(self):
        self.assertEqual(self.history(), [])
        ready = self.client.get(
            "/ai-message/status", params={"userId": self.user}
        )
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(
            mysql_service.select(
                "SELECT COUNT(*) n FROM imagent_world WHERE user_id=%s", (self.user,)
            )[0]["n"],
            0,
        )
        self.assertEqual(self.send("切换前的共同对话").status_code, 200)
        world_id = self.history()[0]["conversationId"]
        second_config = self.add_config(self.user)
        switched = self.client.put(
            "/ai-message/config/switchModel",
            json={"userId": self.user, "configId": second_config},
        )
        self.assertEqual(switched.status_code, 200)
        response = self.send(
            "切换后继续聊", config={"providerName": "default", "apiKey": "fixture-secret-key"}
        )
        self.assertEqual(self.events(response)[-1], ("done", {"success": True}))
        history = self.history()
        self.assertEqual(len(history), 4)
        self.assertTrue(all(item["conversationId"] == world_id for item in history))
        self.assertIn(
            "切换前的共同对话", json.dumps(self.requests[-1]["messages"], ensure_ascii=False)
        )

    def test_busy_turn_lease_recovery_and_stale_result_rejection(self):
        first_id, second_id = str(uuid4()), str(uuid4())
        first = begin_turn(self.user, first_id, "one", self.configuration, 180)
        with self.assertRaises(ChatError) as busy:
            begin_turn(self.user, second_id, "two", self.configuration, 180)
        self.assertEqual(busy.exception.code, "DIALOGUE_BUSY")
        self.assertEqual(len(self.history()), 1)
        mysql_service.update(
            "UPDATE imagent_background_task SET lease_until=DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 SECOND) WHERE id=%s",
            (first["task_id"],),
        )
        recovered = begin_turn(self.user, first_id, "one", self.configuration, 180)
        with self.assertRaises(ChatError):
            complete_turn(self.user, first, "过期回复")
        complete_turn(self.user, recovered, "恢复完成")
        self.assertEqual(len(self.history()), 2)

    def test_cancelled_stream_has_no_fake_assistant_record_and_releases_turn(self):
        async def check():
            body = AIMessageRequest(
                user_id=self.user,
                content="cancelled",
                config={"configId": self.configuration, "apiKey": "fixture-secret-key"},
            )
            turn, model = await prepare_reply(body)
            stream = stream_reply(turn, model)
            await anext(stream)
            await stream.aclose()
            return turn

        turn = asyncio.run(check())
        self.assertEqual(len(self.history()), 1)
        task = mysql_service.select(
            "SELECT status,error_code FROM imagent_background_task WHERE id=%s",
            (turn["task_id"],),
        )[0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error_code"], "RUN_CANCELLED")
        self.assertEqual(self.send("下一条").status_code, 200)

    @unittest.skipUnless(
        os.environ.get("IMAGENT_TEST_LIVE_MODEL") == "1", "仅显式启用真实模型小样本"
    )
    def test_live_default_model_two_turn_dialogue(self):
        first = self.send(
            "请记住本轮暗号：棉花云。只回复记住了。", config={"providerName": "default"}
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(
            self.events(first)[-1], ("done", {"success": True}), first.text
        )
        second = self.send(
            "刚才的暗号是什么？只回复暗号。", config={"providerName": "default"}
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(
            self.events(second)[-1], ("done", {"success": True}), second.text
        )
        history = self.history()
        self.assertEqual(len(history), 4)
        self.assertIn("棉花云", history[-1]["content"])
        print(
            json.dumps(
                {
                    "realModel": True,
                    "turns": 2,
                    "persistedMessages": len(history),
                    "contextRecovered": True,
                },
                ensure_ascii=False,
            )
        )

    def test_history_filters_hidden_messages_and_other_worlds(self):
        self.assertEqual(self.send("只属于本人的对话").status_code, 200)
        first = self.history()
        other = "other-" + uuid4().hex
        self.assertEqual(
            self.client.get("/ai-message/loadMessage", params={"userId": other}).json(),
            [],
        )
        mysql_service.update(
            "UPDATE imagent_message_delivery d JOIN imagent_world w ON w.id=d.world_id SET d.hidden_at=UTC_TIMESTAMP(6) WHERE d.message_id=%s AND d.viewer_actor_id=w.user_actor_id",
            (first[-1]["id"],),
        )
        self.assertEqual(len(self.history()), 1)
        # 模拟管理员归档并允许新建；旧世界不混入当前角色历史。
        mysql_service.update(
            "UPDATE imagent_world SET status='archived' WHERE id=%s",
            (first[0]["conversationId"],),
        )
        mysql_service.update(
            "UPDATE imagent_user_space SET active_world_id=NULL WHERE user_id=%s",
            (self.user,),
        )
        self.assertEqual(self.send("新的当前世界").status_code, 200)
        current = self.history()
        self.assertEqual(len(current), 2)
        self.assertNotEqual(current[0]["conversationId"], first[0]["conversationId"])
        self.assertNotIn(
            "只属于本人的对话",
            json.dumps(self.requests[-1]["messages"], ensure_ascii=False),
        )

    def test_public_default_credentials_and_disabled_config(self):
        public_id = str(uuid4())
        mysql_service.update(
            "INSERT INTO imagent_model_config (id,name,provider,model_name,base_url,credential_ref,parameters_json,capabilities_json) VALUES (%s,'公共默认','custom','fixture-model',%s,'env:LUMINA_TEST_KEY','{}','{}')",
            (public_id, self.url),
        )
        mysql_service.update(
            "INSERT INTO imagent_model_binding (scope_key,slot,config_id) VALUES ('system','dialogue',%s)",
            (public_id,),
        )
        try:
            with patch.dict(os.environ, {"LUMINA_TEST_KEY": "fixture-secret-key"}):
                anonymous_status = self.client.get("/ai-message/status")
                self.assertTrue(anonymous_status.json()["available"], anonymous_status.text)
                response = self.send("继承公共默认", config={"providerName": "default"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.events(response)[-1], ("done", {"success": True}))
            # 个人绑定停用后必须提示；显式解除个人覆盖后才能继承公共默认。
            self.client.put(
                "/ai-message/config/switchModel",
                json={"userId": self.user, "configId": self.configuration},
            )
            changed = {
                "userId": self.user,
                "configId": public_id,
                "configName": "篡改公共配置",
                "providerName": "custom",
                "modelName": "fixture-model",
                "baseUrl": self.url,
            }
            self.assertEqual(
                self.client.put(
                    "/ai-message/config/updateModel", json=changed
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.delete(
                    "/ai-message/config/deleteModel/" + public_id,
                    params={"userId": self.user},
                ).status_code,
                404,
            )
            mysql_service.update(
                "UPDATE imagent_model_config SET status='disabled' WHERE id=%s",
                (self.configuration,),
            )
            self.assertEqual(self.send().status_code, 404)
            unavailable = self.client.get(
                "/ai-message/status", params={"userId": self.user}
            ).json()
            self.assertFalse(unavailable["available"])
            self.assertEqual(unavailable["code"], "MODEL_CONFIG_NOT_FOUND")
            inherited = self.send("不能静默换模型", config={"providerName": "default"})
            self.assertEqual(inherited.status_code, 404)
        finally:
            mysql_service.delete(
                "DELETE FROM imagent_model_binding WHERE scope_key='system' AND slot='dialogue'",
                (),
            )
