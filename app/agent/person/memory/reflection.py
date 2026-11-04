"""成长建议的首版提交约束，避免一轮话就整体重写人格。"""

from datetime import datetime, timedelta


def growth_update(profile, growth, source_ids, now):
    if not set(growth.source_ids) <= source_ids or len(set(growth.source_ids)) < 3:
        raise ValueError("成长需要三个不同的实际观察作为依据")
    last = profile.get("last_growth_at")
    if last and now - datetime.fromisoformat(last) < timedelta(days=7):
        return None
    return {**profile, "development": growth.note, "last_growth_at": now.isoformat()}
