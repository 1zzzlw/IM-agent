import asyncio

from loguru import logger

from app.agent.person.appraisal import appraise
from app.agent.person.memory.agent import consolidate
from app.core.config import config
from app.core.errors import ChatError
from app.core.perception import perceive
from app.core.serialization import data, load
from app.integrations.database.message_repository import begin_turn
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import snapshot
from app.integrations.database.state_commit import commit_appraisal
from app.integrations.database.task_repository import (
    claim,
    finish,
    resume_worlds,
    schedule_lives,
)
from app.llm.factory import create_chat_model
from app.services.ai_message_service import stream_reply
from app.services.life_service import advance_life
from app.services.model_config_service import resolve_model


def background_model(user_id, slot):
    effective = resolve_model(user_id, slot=slot)
    return create_chat_model(effective)


async def execute(task):
    """按任务类型分派后台工作；每种任务自己校验世界与租约，失败交给 worker 退避重试。"""
    user_id = task["user_id"]
    snap = await asyncio.to_thread(snapshot, user_id)
    if not snap or snap["world"]["id"] != task["world_id"]:
        return
    kind = task["kind"]
    if kind == "moment_image":
        from app.integrations.database.moment_repository import attach_image
        from app.services.media_service import generate_image

        payload = load(task["payload_json"])
        asset = await generate_image(user_id, payload["prompt"])
        await asyncio.to_thread(
            attach_image, user_id, task["world_id"], payload["momentId"], asset["id"]
        )
        return
    if kind == "pending_reply":
        relationship = next(
            (
                r
                for r in snap["relations"]
                if r["to_actor_id"] == snap["world"]["user_actor_id"]
            ),
            {},
        )
        if (
            perceive(
                snap["context"],
                snap["routine"],
                data(relationship.get("contact_policy_json")),
                task["id"],
            )
            != "read"
        ):
            raise ChatError("NOT_YET_PERCEIVED", "尚未看到消息")
        payload = load(task["payload_json"])
        pending = await asyncio.to_thread(
            mysql_service.select,
            "SELECT m.id,d.read_at FROM imagent_message m JOIN imagent_message_delivery d ON d.world_id=m.world_id AND d.message_id=m.id WHERE m.world_id=%s AND m.client_message_id=%s AND d.viewer_actor_id=%s",
            (task["world_id"], payload["clientId"], snap["world"]["primary_actor_id"]),
        )
        if not pending or pending[0]["read_at"]:
            return
        active = await asyncio.to_thread(
            mysql_service.select,
            "SELECT id FROM imagent_background_task WHERE world_id=%s AND kind='dialogue' AND status='running' AND lease_until>UTC_TIMESTAMP(6)",
            (task["world_id"],),
        )
        if active:
            raise ChatError("NOT_YET_PERCEIVED", "稍后一起阅读")
        effective = await asyncio.to_thread(resolve_model, user_id)
        model = create_chat_model(effective)
        turn = await asyncio.to_thread(
            begin_turn,
            user_id,
            payload["clientId"],
            payload["content"],
            effective.config_id,
            int(config.chat.reply_timeout) + 30,
            payload.get("assetIds"),
        )
        async for _ in stream_reply(turn, model):
            pass
        return
    model = await asyncio.to_thread(
        background_model, user_id, "memory" if kind == "memory" else "event"
    )
    if kind == "memory":
        await consolidate(user_id, snap, model)
    elif kind == "appointment":
        from app.services.activity_service import arrive

        await arrive(user_id, task, snap, model)
    elif kind == "interaction":
        eid = load(task["payload_json"])["experienceId"]
        sources = await asyncio.to_thread(
            mysql_service.select,
            "SELECT perceived_json FROM imagent_observation WHERE world_id=%s AND experience_id=%s AND observer_actor_id=%s",
            (task["world_id"], eid, snap["world"]["primary_actor_id"]),
        )
        if sources:
            proposal = await appraise(model, snap, load(sources[0]["perceived_json"]))
            await asyncio.to_thread(
                commit_appraisal,
                user_id,
                snap,
                proposal,
                "interaction:" + task["id"],
                experience_id=eid,
            )
    else:
        await advance_life(user_id, model, task)


class BackgroundService:
    def __init__(self):
        self.tasks = []

    async def start(self):
        await asyncio.to_thread(resume_worlds)
        self.tasks = [asyncio.create_task(self.worker()) for _ in range(2)]
        self.tasks.append(asyncio.create_task(self.scheduler()))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def scheduler(self):
        iteration = 0
        while True:
            try:
                await asyncio.to_thread(schedule_lives)
                if iteration % 360 == 0:
                    from app.integrations.database.maintenance_repository import (
                        maintain_world,
                    )

                    worlds = await asyncio.to_thread(
                        mysql_service.select,
                        "SELECT user_id,id FROM imagent_world WHERE status='active'",
                    )
                    for world in worlds:
                        await asyncio.to_thread(
                            maintain_world, world["user_id"], world["id"]
                        )
                iteration += 1
                # 索引失败不影响聊天，后续轮次自动重建。
                from app.integrations.vector_store.memory_store import (
                    embedding_space,
                    sync_memory,
                )

                rows = await asyncio.to_thread(
                    mysql_service.select,
                    "SELECT * FROM imagent_memory WHERE index_status IN ('pending','failed') OR embedding_space IS NULL OR embedding_space<>%s ORDER BY updated_at LIMIT 10",
                    (embedding_space(),),
                )
                for row in rows:
                    await asyncio.to_thread(sync_memory, row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("后台调度暂不可用 error_type={}", type(exc).__name__)
            await asyncio.sleep(10)

    async def worker(self):
        while True:
            task = None
            try:
                task = await asyncio.to_thread(claim)
                if not task:
                    await asyncio.sleep(1)
                    continue
                async with asyncio.timeout(150):
                    await execute(task)
                await asyncio.to_thread(finish, task)
            except asyncio.CancelledError:
                if task:
                    await asyncio.shield(
                        asyncio.to_thread(finish, task, "SERVICE_STOPPING", True)
                    )
                raise
            except Exception as exc:
                if task:
                    code = (
                        exc.code if isinstance(exc, ChatError) else type(exc).__name__
                    )
                    retry = task["attempts"] < 5
                    if code == "NOT_YET_PERCEIVED":
                        # 没看见是人物处境，不按技术重试次数遗弃消息。
                        task["attempts"] = 0
                        await asyncio.to_thread(
                            mysql_service.update,
                            "UPDATE imagent_background_task SET attempts=0 WHERE id=%s AND lease_owner=%s",
                            (task["id"], task["lease_owner"]),
                        )
                        retry = True
                    await asyncio.to_thread(finish, task, code, retry)
                    logger.warning(
                        "后台任务暂未完成 kind={} code={}", task["kind"], code
                    )
                await asyncio.sleep(1)


background_service = BackgroundService()
