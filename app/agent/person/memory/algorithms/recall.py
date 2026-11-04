"""首版记忆评分可独立替换；权重是工程选择，不是心理学定律。"""

import math
import re
from datetime import datetime, timezone


def tokens(text):
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words.update(chinese[i : i + 2] for i in range(max(0, len(chinese) - 1)))
    return words


def strength(cognition, created_at, last_accessed_at=None, now=None):
    now = now or datetime.now(timezone.utc)
    origin = last_accessed_at or created_at
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=timezone.utc)
    days = max(0, (now - origin).total_seconds() / 86400)
    half_life = (
        7
        + 173 * float(cognition.get("importance", 0.5))
        + 15 * min(4, int(cognition.get("recalls", 0)))
    )
    return math.exp(-math.log(2) * days / half_life)


def rank(query, memories, semantic=None, limit=8):
    terms, ranked = tokens(query), []
    for row in memories:
        if row["status"] not in ("active", "faded") or not row.get("content"):
            continue
        cognition = row["cognition"]
        relevance = max(
            len(terms & tokens(row["content"])) / max(1, len(terms)),
            (semantic or {}).get(row["id"], 0),
        )
        if relevance <= 0:
            continue
        heat = strength(cognition, row["created_at"], row.get("last_accessed_at"))
        ranked.append(
            (
                0.7 * relevance
                + 0.15 * float(cognition.get("importance", 0.5))
                + 0.15 * heat,
                row,
            )
        )
    return [
        row for _, row in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]
    ]
