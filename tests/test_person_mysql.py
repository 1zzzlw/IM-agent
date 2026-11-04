import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from test_dialogue_mysql import MysqlFixture

from app.agent.person.retrieval.agent import retrieve
from app.api.admin import router as admin_router
from app.api.person import router as person_router
from app.core.errors import ChatError
from app.integrations.database.memory_repository import (
    list_memories,
    pending_observations,
    save_batch,
)
from app.integrations.database.message_repository import (
    append_expression,
    begin_turn,
    settle_turn,
)
from app.integrations.database.mysql import mysql_service
from app.integrations.database.character_repository import checked_world
from app.integrations.database.person_store import enqueue, snapshot
from app.integrations.database.state_commit import commit_appraisal
from app.integrations.database.user_space_repository import user_transaction
from app.schemas.person import (
    Appraisal,
    Candidate,
    CandidateBatch,
    CandidateRequest,
    EventExperience,
    LifeImpact,
    LifeProposal,
    MemoryBatch,
    MemoryItem,
    SelectCandidateRequest,
)
from app.integrations.database.creation_repository import save_candidates, select_candidate
from app.integrations.database.life_repository import commit_life_event


class PersonMysqlTests(MysqlFixture):
    def setUp(self):
        super().setUp()
        self.app.include_router(person_router)
        self.app.include_router(admin_router)
        from fastapi.responses import JSONResponse

        @self.app.exception_handler(ChatError)
        async def handler(_request, exc):
            return JSONResponse(
                status_code=exc.status,
                content={"detail": {"code": exc.code, "message": exc.message}},
            )

    def create_character(self):
        item = Candidate(
            name="林夏",
            introduction="住在海边城市的学生",
            appearance="短发",
            personality="慢热、讲理、偶尔固执",
            interests="画画",
            background="与家人住在海边",
            knowledge="大学通识与绘画",
            past=["曾和朋友阿宁一起去海边画画"],
            goals=["完成一幅海边画作"],
            companions=[
                {"name": "阿宁", "description": "外向而直率", "relation": "朋友"}
            ],
        )
        body = CandidateRequest(user_id=self.user, wish="喜欢画画的普通人")
        candidates = save_candidates(
            body,
            CandidateBatch(candidates=[item, item.model_copy(update={"name": "林秋"})]),
        )
        return select_candidate(
            SelectCandidateRequest(
                user_id=self.user, candidate_id=candidates["candidates"][0]["id"]
            )
        )["worldId"]

    def test_creation_unique_public_projection_and_seeds(self):
        wid = self.create_character()
        public = self.client.get(
            "/ai-message/person", params={"userId": self.user}
        ).json()
        self.assertEqual(public["worldId"], wid)
        self.assertNotIn("personality", json.dumps(public))
        self.assertNotIn("interests", json.dumps(public))
        self.assertNotIn("海边画画", json.dumps(public, ensure_ascii=False))
        self.assertEqual(
            len(
                mysql_service.select(
                    "SELECT id FROM imagent_actor WHERE world_id=%s", (wid,)
                )
            ),
            3,
        )
        self.assertEqual(
            len(
                mysql_service.select(
                    "SELECT id FROM imagent_goal WHERE world_id=%s", (wid,)
                )
            ),
            1,
        )
        snap = snapshot(self.user)
        self.assertEqual(len(list_memories(wid, snap["world"]["primary_actor_id"])), 1)
        self.assertEqual(
            pending_observations(wid, snap["world"]["primary_actor_id"]), []
        )

    def test_appraisal_cas_idempotency_and_no_whole_snapshot_overwrite(self):
        self.create_character()
        snap = snapshot(self.user)
        appraisal = Appraisal(
            emotion={"name": "生气", "valence": -0.7, "reason": "被误解"},
            relationship_label="刚认识",
        )
        first = commit_appraisal(self.user, snap, appraisal, "same-impact")
        second = commit_appraisal(self.user, snap, appraisal, "same-impact")
        self.assertEqual(first, second)
        with self.assertRaises(ChatError) as error:
            commit_appraisal(self.user, snap, appraisal, "another-impact")
        self.assertEqual(error.exception.code, "STATE_CHANGED")
        self.assertEqual(snapshot(self.user)["emotion"]["name"], "生气")

    def test_phone_unavailable_saves_without_reading_or_model_call(self):
        wid = self.create_character()
        mysql_service.update(
            "UPDATE imagent_actor_state SET context_json=JSON_SET(context_json,'$.data.phone_accessible',FALSE) WHERE world_id=%s",
            (wid,),
        )
        before = len(self.requests)
        response = self.send("你人呢")
        self.assertEqual(self.events(response)[-1], ("done", {"success": True}))
        self.assertEqual(len(self.requests), before)
        self.assertEqual(len(self.history()), 1)
        snap = snapshot(self.user)
        observations = mysql_service.select(
            "SELECT o.id FROM imagent_observation o JOIN imagent_experience e ON e.id=o.experience_id WHERE o.world_id=%s AND o.observer_actor_id=%s AND e.kind='utterance'",
            (wid, snap["world"]["primary_actor_id"]),
        )
        self.assertEqual(observations, [])

    def test_memory_sources_recall_superseding_and_user_isolation(self):
        wid = self.create_character()
        turn = begin_turn(
            self.user, str(uuid4()), "我喜欢茉莉花茶", self.configuration, 180
        )
        settle_turn(self.user, turn)
        snap = snapshot(self.user)
        actor = snap["world"]["primary_actor_id"]
        sources = pending_observations(wid, actor)
        item = MemoryItem(
            content="用户说自己喜欢茉莉花茶", source_ids=[sources[-1]["id"]]
        )
        save_batch(self.user, wid, actor, sources, MemoryBatch(memories=[item]))
        result = asyncio.run(retrieve(self.user, wid, actor, "喜欢喝什么茶"))
        self.assertTrue(result["found"])
        self.assertIn("茉莉花茶", result["memories"][0]["content"])
        self.assertFalse(
            asyncio.run(retrieve(self.user, wid, str(uuid4()), "茉莉花茶"))["found"]
        )
        mysql_service.update(
            "UPDATE imagent_memory SET status='forgotten',content=NULL WHERE id=%s",
            (result["memories"][0]["id"],),
        )
        self.assertFalse(
            asyncio.run(retrieve(self.user, wid, actor, "茉莉花茶"))["found"]
        )
        later = begin_turn(
            self.user, str(uuid4()), "你还记得我吗", self.configuration, 180
        )
        self.assertNotIn(
            "我喜欢茉莉花茶",
            json.dumps(later["history"], ensure_ascii=False, default=str),
        )
        forged = item.model_copy(update={"source_ids": [str(uuid4())]})
        with self.assertRaises(ValueError):
            save_batch(self.user, wid, actor, sources, MemoryBatch(memories=[forged]))

    def test_life_event_cancels_retrieval_preserves_filler_and_rejects_late_result(
        self,
    ):
        wid = self.create_character()
        turn = begin_turn(
            self.user, str(uuid4()), "记得我们上次吵架吗", self.configuration, 180
        )
        append_expression(self.user, turn, "等我想想", "waiting:" + turn["task_id"])
        snap = snapshot(self.user)
        key = str(uuid4())
        with user_transaction(self.user) as cursor:
            world = checked_world(cursor, self.user, wid)
            enqueue(cursor, world, "life", key, {})
            cursor.execute(
                "UPDATE imagent_background_task SET status='running',lease_owner='test',lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 180 SECOND) WHERE world_id=%s AND dedupe_key=%s",
                (wid, key),
            )
            cursor.execute(
                "SELECT * FROM imagent_background_task WHERE world_id=%s AND dedupe_key=%s",
                (wid, key),
            )
            task = cursor.fetchone()
        result = {
            "proposal": LifeProposal(
                action="event",
                title="爸爸请帮忙搬东西",
                activity="搬东西",
                participant_ids=[snap["world"]["primary_actor_id"]],
                phone_accessible=False,
            ).model_dump(),
            "experience": EventExperience(
                summary="听到爸爸叫自己去帮忙，把手机留在房间"
            ).model_dump(),
            "impact": LifeImpact(
                appraisal=Appraisal(decision="leave", emotion={"name": "平静"})
            ).model_dump(),
        }
        self.assertTrue(commit_life_event(self.user, task, snap, result))
        with self.assertRaises(ChatError):
            append_expression(
                self.user, turn, "过期的回忆答案", "late:" + turn["task_id"]
            )
        self.assertEqual(
            [m["content"] for m in self.history()], ["记得我们上次吵架吗", "等我想想"]
        )
        self.assertFalse(snapshot(self.user)["context"]["phone_accessible"])

    def test_settings_credentials_admin_boundary(self):
        self.create_character()
        settings = self.client.put(
            "/ai-message/person/settings",
            json={"userId": self.user, "lifeWindowMinutes": 30, "onlineVisible": False},
        )
        self.assertEqual(settings.status_code, 200)
        self.assertFalse(settings.json()["presenceVisibility"]["online"])
        denied = self.client.get(
            "/ai-message/admin/person", params={"userId": self.user}
        )
        self.assertEqual(denied.status_code, 403)
        wrong_user = self.client.put(
            "/ai-message/person/credential",
            json={
                "userId": "other",
                "configId": self.configuration,
                "apiKey": "never-stored",
            },
        )
        self.assertEqual(wrong_user.status_code, 404)

    def test_only_dialogue_can_be_bound_and_old_fixed_bindings_are_ignored(self):
        from app.services.model_config_service import configured_slots, resolve_model

        server_slots = configured_slots()
        server_memory = resolve_model(self.user, slot="memory")
        for slot in (
            "memory",
            "retrieval",
            "event",
            "vision",
            "image",
            "asr",
            "tts",
            "audio_emotion",
        ):
            response = self.client.put(
                "/ai-message/person/bindings",
                json={
                    "userId": self.user,
                    "slot": slot,
                    "configId": self.configuration,
                },
            )
            self.assertEqual(response.status_code, 422, response.text)
            mysql_service.update(
                "INSERT INTO imagent_model_binding (scope_key,owner_user_id,slot,config_id) VALUES (%s,%s,%s,%s)",
                ("user:" + self.user, self.user, slot, self.configuration),
            )
        self.assertEqual(
            self.client.get(
                "/ai-message/person/bindings", params={"userId": self.user}
            ).json(),
            {},
        )
        self.assertEqual(configured_slots(), server_slots)
        self.assertEqual(resolve_model(self.user, slot="memory"), server_memory)
        with self.assertRaises(ChatError) as error:
            resolve_model(self.user, slot="tts")
        self.assertEqual(error.exception.code, "CAPABILITY_NOT_CONFIGURED")
        # 废弃的个人固定能力绑定不再阻止删除对应的个人连接。
        deleted = self.client.delete(
            "/ai-message/config/deleteModel/" + self.configuration,
            params={"userId": self.user},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

    def test_dialogue_model_switch_keeps_retrieval_memory_event_and_media_fixed(self):
        from app.core.config import ModelSlotConfig, config
        from app.services.model_config_service import resolve_model

        body = {"userId": self.user, "slot": "dialogue", "configId": self.configuration}
        self.assertEqual(
            self.client.put("/ai-message/person/bindings", json=body).status_code, 200
        )
        self.client.put(
            "/ai-message/person/credential",
            json={
                "userId": self.user,
                "configId": self.configuration,
                "apiKey": "fixture-secret-key",
            },
        )
        config.models.tts = ModelSlotConfig(model="fixed-voice")
        slots = ("dialogue", "memory", "retrieval", "event", "tts")
        before = {slot: resolve_model(self.user, slot=slot) for slot in slots}
        response = self.send("切换前")
        self.assertEqual(self.events(response)[-1], ("done", {"success": True}))
        self.assertEqual(
            [r["model"] for r in self.requests[-3:]],
            ["fixture-fixed", "fixture-model", "fixture-model"],
        )
        other = self.add_config(self.user)
        mysql_service.update(
            "UPDATE imagent_model_config SET model_name='switched-chat' WHERE id=%s",
            (other,),
        )
        self.assertEqual(
            self.client.put(
                "/ai-message/person/credential",
                json={
                    "userId": self.user,
                    "configId": other,
                    "apiKey": "fixture-secret-key",
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(
                "/ai-message/person/bindings", json={**body, "configId": other}
            ).status_code,
            200,
        )
        after = {slot: resolve_model(self.user, slot=slot) for slot in slots}
        self.assertEqual(after["dialogue"].model_name, "switched-chat")
        for slot in slots[1:]:
            self.assertEqual(before[slot], after[slot])
        response = self.send("切换后", config={"providerName": "default"})
        self.assertEqual(self.events(response)[-1], ("done", {"success": True}))
        self.assertEqual(
            [r["model"] for r in self.requests[-3:]],
            ["fixture-fixed", "switched-chat", "switched-chat"],
        )

    def test_basic_character_first_full_creation_keeps_history_and_world(self):
        self.send("认识之前已说过的话")
        previous = self.history()[0]["conversationId"]
        wid = self.create_character()
        self.assertEqual(wid, previous)
        self.assertIn("认识之前已说过的话", self.history()[0]["content"])
        self.assertFalse(
            self.client.get("/ai-message/person", params={"userId": self.user}).json()[
                "requiresCreation"
            ]
        )
        with self.assertRaises(ChatError):
            self.create_character()

    def test_moment_visibility_and_interaction_idempotency(self):
        from app.integrations.database.moment_repository import feed, interact, publish
        from app.schemas.person import InteractionRequest

        wid = self.create_character()
        with user_transaction(self.user) as cursor:
            world = checked_world(cursor, self.user, wid)
            hidden = publish(cursor, world, "不对用户公开", [], "private")
            visible = publish(
                cursor, world, "今天画完云了", [world["user_actor_id"]], "visible"
            )
        self.assertEqual([m["id"] for m in feed(self.user)], [visible])
        request = InteractionRequest(
            user_id=self.user, request_id=str(uuid4()), kind="comment", content="真好看"
        )
        interact(self.user, visible, request)
        interact(self.user, visible, request)
        self.assertEqual(len(feed(self.user)[0]["interactions"]), 1)
        with self.assertRaises(ChatError):
            interact(self.user, hidden, request)

    def test_call_uses_only_played_segments_and_invalidates_old_epoch(self):
        from app.integrations.database.call_repository import (
            call_action,
            claim_voice_turn,
            heard,
            start_session,
            store_audio_output,
        )
        from app.schemas.call import CallAction, CallRequest

        self.create_character()
        call = start_session(
            CallRequest(user_id=self.user, client_call_id=str(uuid4())),
            snapshot(self.user),
            Appraisal(),
            True,
        )
        identity = call["id"]
        segments = [
            {"id": str(uuid4()), "text": "已经听到的句子", "assetId": str(uuid4())},
            {"id": str(uuid4()), "text": "尚未播出的尾句", "assetId": str(uuid4())},
        ]
        store_audio_output(self.user, identity, 0, segments)
        heard(
            identity,
            CallAction(user_id=self.user, epoch=0, segment_id=segments[0]["id"]),
        )
        heard(
            identity,
            CallAction(user_id=self.user, epoch=0, segment_id=segments[0]["id"]),
        )
        call_action(identity, CallAction(user_id=self.user))
        with self.assertRaises(ChatError):
            heard(
                identity,
                CallAction(user_id=self.user, epoch=0, segment_id=segments[1]["id"]),
            )
        rows = mysql_service.select(
            "SELECT parts_json FROM imagent_message WHERE call_id=%s", (identity,)
        )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("尚未播出", str(rows))
        claim_voice_turn(self.user, identity, 1)
        with self.assertRaises(ChatError):
            claim_voice_turn(self.user, identity, 1)

    def test_agreed_activity_uses_real_duration_not_instant_completion(self):
        from app.schemas.person import ActivityPlan

        self.create_character()
        snap = snapshot(self.user)
        proposal = Appraisal(
            activity_plan=ActivityPlan(
                title="一起散步",
                starts_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                duration_minutes=30,
                mutually_agreed=True,
            )
        )
        commit_appraisal(self.user, snap, proposal, "shared-walk")
        row = mysql_service.select(
            "SELECT * FROM imagent_life_event WHERE world_id=%s", (snap["world"]["id"],)
        )[0]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(
            (row["scheduled_end_at"] - row["scheduled_start_at"]).total_seconds(), 1800
        )
        self.assertIsNone(row["ended_at"])

    def test_four_agents_execute_together_with_persistent_sources_and_events(self):
        from types import SimpleNamespace

        from app.agent.event.graph import event_graph
        from app.agent.event.state import EventContext
        from app.agent.person.coordinator import dialogue_graph
        from app.agent.person.memory.agent import consolidate
        from app.agent.person.state import DialogueContext

        self.create_character()
        owner = self

        class Model:
            calls = []

            async def ainvoke(self, messages):
                system = messages[0].content
                self.calls.append(system)
                if "JSON Schema" in system:
                    context = json.loads(messages[1].content)
                    if '"title": "RetrievalDecision"' in system:
                        response = {"needed": True, "query": "海边画画"}
                    elif '"title": "MemoryBatch"' in system:
                        response = {
                            "memories": [
                                {
                                    "content": "用户说自己喜欢茉莉花茶",
                                    "source_ids": [context["sources"][0]["id"]],
                                    "kind": "fact",
                                    "epistemic_type": "reported",
                                }
                            ]
                        }
                    elif '"title": "LifeProposal"' in system:
                        response = {
                            "action": "event",
                            "title": "为画作整理草稿",
                            "activity": "画画",
                            "participant_ids": [context["actors"][0]["id"]],
                            "duration_minutes": 10,
                        }
                    elif '"title": "EventExperience"' in system:
                        response = {"summary": "开始整理画作草稿，没有提前完成绘画"}
                    elif '"title": "LifeImpact"' in system:
                        response = {
                            "appraisal": {
                                "emotion": {"name": "专注", "reason": "开始画画"},
                                "decision": "reply",
                            },
                            "moment_text": "开始整理海边的草稿。",
                            "audience_ids": [context["user_actor_id"]],
                        }
                    else:
                        response = {
                            "emotion": {"name": "开心", "reason": "聊起了共同兴趣"},
                            "decision": "reply",
                            "relationship_label": "刚认识",
                        }
                    return SimpleNamespace(
                        content=json.dumps(response, ensure_ascii=False)
                    )
                return SimpleNamespace(content="我记得和阿宁去海边画画的那次。")

        model = Model()

        async def run():
            turn = begin_turn(
                owner.user,
                str(uuid4()),
                "我喜欢茉莉花茶。你去海边画过画吗？",
                owner.configuration,
                180,
            )
            emitted = []

            async def emit(reply):
                emitted.append(reply)

            result = await dialogue_graph.ainvoke(
                {},
                context=DialogueContext(
                    model=model,
                    user_id=owner.user,
                    turn=turn,
                    emit=emit,
                    models={"retrieval": model},
                ),
            )
            owner.assertEqual(result["outcome"], "replied")
            owner.assertEqual(len(emitted), 1)
            owner.assertTrue(result["retrieval"]["found"])
            await consolidate(owner.user, snapshot(owner.user), model)
            snap = snapshot(owner.user)
            found = await retrieve(
                owner.user,
                snap["world"]["id"],
                snap["world"]["primary_actor_id"],
                "茉莉花茶",
            )
            owner.assertTrue(found["found"])
            event = await event_graph.ainvoke(
                {"snapshot": snap, "finishing": False},
                context=EventContext(model=model),
            )
            with user_transaction(owner.user) as cursor:
                world = checked_world(cursor, owner.user, snap["world"]["id"])
                enqueue(cursor, world, "life", "graph-test", {})
                cursor.execute(
                    "UPDATE imagent_background_task SET status='running',lease_owner='event-test',lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 180 SECOND) WHERE kind='life' AND user_id=%s AND dedupe_key='graph-test'",
                    (owner.user,),
                )
                cursor.execute(
                    "SELECT * FROM imagent_background_task WHERE kind='life' AND user_id=%s AND dedupe_key='graph-test'",
                    (owner.user,),
                )
                task = cursor.fetchone()
            commit_life_event(owner.user, task, snap, event)
            owner.assertTrue(
                mysql_service.select(
                    "SELECT id FROM imagent_life_event WHERE world_id=%s AND status='running'",
                    (snap["world"]["id"],),
                )
            )
            owner.assertTrue(
                mysql_service.select(
                    "SELECT id FROM imagent_moment WHERE world_id=%s",
                    (snap["world"]["id"],),
                )
            )

        asyncio.run(run())
        self.assertTrue(any('"title": "MemoryBatch"' in call for call in model.calls))
        self.assertTrue(any('"title": "LifeImpact"' in call for call in model.calls))

    def test_background_claim_lease_and_restart_do_not_generate_gap_events(self):
        from app.integrations.database.task_repository import (
            claim,
            finish,
            resume_worlds,
            schedule_lives,
        )

        self.create_character()
        snap = snapshot(self.user)
        with user_transaction(self.user) as cursor:
            world = checked_world(cursor, self.user, snap["world"]["id"])
            enqueue(cursor, world, "life", "due-life", {})
        task = None
        for _ in range(50):
            candidate = claim()
            if candidate is None:
                break
            if candidate["user_id"] == self.user and candidate["kind"] == "life":
                task = candidate
                break
            finish(candidate)
        self.assertIsNotNone(task)
        self.assertEqual(task["attempts"], 1)
        finish(task, "temporary", True)
        saved = mysql_service.select(
            "SELECT * FROM imagent_background_task WHERE id=%s", (task["id"],)
        )[0]
        self.assertEqual(saved["status"], "queued")
        mysql_service.update(
            "UPDATE imagent_world SET last_tick_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 7 DAY) WHERE id=%s",
            (snap["world"]["id"],),
        )
        before = mysql_service.select(
            "SELECT COUNT(*) AS n FROM imagent_experience WHERE world_id=%s",
            (snap["world"]["id"],),
        )[0]["n"]
        resume_worlds()
        after = mysql_service.select(
            "SELECT COUNT(*) AS n FROM imagent_experience WHERE world_id=%s",
            (snap["world"]["id"],),
        )[0]["n"]
        self.assertEqual(before, after)
        moved = mysql_service.select(
            "SELECT run_after FROM imagent_background_task WHERE id=%s", (task["id"],)
        )[0]["run_after"]
        self.assertGreater((moved - saved["run_after"]).total_seconds(), 6 * 86400)
        schedule_lives()

    def test_admin_public_model_idempotency_and_secret_projection(self):
        import os
        from unittest.mock import patch

        request = {
            "requestId": str(uuid4()),
            "reason": "测试公共方案",
            "slot": "memory",
            "configName": "后台记忆模型",
            "providerName": "custom",
            "modelName": "fixture-model",
            "baseUrl": self.url,
            "apiKey": "public-fixture-secret",
        }
        with patch.dict(os.environ, {"IMAGENT_ADMIN_TOKEN": "test-admin-only"}):
            first = self.client.put(
                "/ai-message/admin/model-default",
                json=request,
                headers={"X-Agent-Admin-Token": "test-admin-only"},
            )
            self.assertEqual(first.status_code, 200, first.text)
            second = self.client.put(
                "/ai-message/admin/model-default",
                json=request,
                headers={"X-Agent-Admin-Token": "test-admin-only"},
            )
            self.assertEqual(first.json(), second.json())
        rows = mysql_service.select(
            "SELECT parameters_json,result_json FROM imagent_admin_operation WHERE request_key=%s",
            (request["requestId"],),
        )
        self.assertNotIn(request["apiKey"], str(rows))

    def test_vector_store_real_index_uses_owner_scope_and_database_validity(self):
        import os
        from pathlib import Path
        from unittest.mock import patch

        if not os.environ.get("IMAGENT_RUNTIME_DIR"):
            self.skipTest("向量目录验证需显式提供独立运行目录")
        from chromadb import PersistentClient

        from app.embedding.service import embedding_service
        from app.integrations.vector_store.client import chroma_client
        from app.integrations.vector_store.memory_store import search, sync_memory

        class Embedding:
            def embed_query(self, text):
                return [1.0, 0.0, 0.0, 0.0] if "海边" in text else [0.0, 1.0, 0.0, 0.0]

        self.create_character()
        snap = snapshot(self.user)
        world, actor = snap["world"]["id"], snap["world"]["primary_actor_id"]
        client = PersistentClient(
            path=str(Path(os.environ["IMAGENT_RUNTIME_DIR"]) / "test-chroma")
        )
        with (
            patch.object(chroma_client, "client", client),
            patch.object(embedding_service, "model", Embedding()),
        ):
            row = list_memories(world, actor)[0]
            sync_memory(row)
            found, mode = search(world, actor, "海边")
            self.assertEqual(mode, "semantic")
            self.assertIn(row["id"], found)
            self.assertEqual(search(world, str(uuid4()), "海边")[0], {})
            mysql_service.update(
                "UPDATE imagent_memory SET status='forgotten',content=NULL,revision=revision+1,index_status='pending' WHERE id=%s",
                (row["id"],),
            )
            result = asyncio.run(retrieve(self.user, world, actor, "海边"))
            self.assertFalse(result["found"])
