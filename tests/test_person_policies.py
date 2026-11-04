import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from app.agent.person.memory.algorithms.recall import rank, strength
from app.core.perception import perceive
from app.schemas.person import Appraisal, MemoryItem


class PersonPoliciesTests(unittest.TestCase):
    def test_unread_message_content_cannot_change_perception(self):
        self.assertEqual(
            perceive({"phone_accessible": False}, {}, {}, "id"), "unnoticed"
        )
        self.assertEqual(
            perceive({"phone_silent": True}, {"sleeping": True}, {}, "id"), "unnoticed"
        )
        self.assertEqual(
            perceive({}, {"sleeping": True}, {"do_not_disturb": True}, "id"),
            "unnoticed",
        )
        self.assertEqual(
            perceive({"privacy_available": False}, {}, {}, "id"), "notification"
        )
        self.assertEqual(
            perceive({"phone_silent": True}, {"sleeping": False}, {}, "id"), "read"
        )

    def test_waking_randomness_is_stable_for_retry(self):
        self.assertEqual(
            perceive({}, {"sleeping": True}, {}, "id"),
            perceive({}, {"sleeping": True}, {}, "id"),
        )

    def test_forgotten_superseded_and_unrelated_never_recalled(self):
        now = datetime.now(timezone.utc)
        rows = [
            {
                "id": str(i),
                "content": text,
                "status": status,
                "cognition": {"importance": 0.5},
                "created_at": now,
            }
            for i, (text, status) in enumerate(
                [
                    ("喜欢喝茉莉花茶", "active"),
                    ("喜欢喝茉莉花茶", "forgotten"),
                    ("喜欢喝茉莉花茶", "superseded"),
                    ("正在准备考试", "active"),
                ]
            )
        ]
        self.assertEqual([r["id"] for r in rank("喜欢喝什么茶", rows)], ["0"])

    def test_importance_and_rehearsal_slow_fading(self):
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=100)
        self.assertGreater(
            strength({"importance": 0.9}, created, now=now),
            strength({"importance": 0.1}, created, now=now),
        )
        self.assertGreater(
            strength({"recalls": 4}, created, now=now),
            strength({"recalls": 0}, created, now=now),
        )

    def test_model_cannot_write_arbitrary_fields_or_unbounded_relationship(self):
        with self.assertRaises(ValidationError):
            Appraisal.model_validate({"trust_delta": 1})
        with self.assertRaises(ValidationError):
            Appraisal.model_validate({"owner_user_id": "other"})
        with self.assertRaises(ValidationError):
            MemoryItem.model_validate({"content": "x", "source_ids": []})
