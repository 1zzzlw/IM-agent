"""本机即时取消 + 数据库租约校验。迟到结果不能越过发送事务。"""

import asyncio


class RunRegistry:
    def __init__(self):
        self.active = {}

    def register(self, user_id, task_id, order=(0, 0)):
        previous = self.active.get(user_id)
        if previous and previous[2] > order:
            event = asyncio.Event()
            event.set()
            return event
        self.interrupt(user_id)
        event = asyncio.Event()
        self.active[user_id] = (task_id, event, order)
        return event

    def interrupt(self, user_id):
        previous = self.active.get(user_id)
        if previous:
            previous[1].set()

    def release(self, user_id, task_id):
        if self.active.get(user_id, (None,))[0] == task_id:
            self.active.pop(user_id, None)


runs = RunRegistry()
