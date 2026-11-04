"""人物感知的纯规则：只判断"实际获知到什么程度"，不读库、不调模型。

四种结果对应架构设计 4.4 的判断链：
- read：手机可用、没有睡眠/静音/现场障碍，正文可以进入人物上下文；
- notification：只察觉到"有消息"，正文不进入评价，只写一条通知类观察；
- unnoticed：手机不在身边、睡眠且免打扰/静音，消息先保存、等待之后再看；
- privacy_available=False 也会降级为 notification：周围有人，不方便查看正文。

放在 core 而不是 agent：数据层（接收消息时）和后台任务都要用同一份规则，
它没有任何依赖，属于共享的确定性规则。睡眠唤醒用消息 ID 做稳定随机，
避免同一消息重试时不断"抽到醒来"。
"""

import hashlib


def perceive(context, routine, contact_policy, message_id):
    if not context.get("phone_accessible", True):
        return "unnoticed"
    if routine.get("sleeping", False):
        if contact_policy.get("do_not_disturb", False) or context.get(
            "phone_silent", False
        ):
            return "unnoticed"
        # 固定消息的随机结果，不让重试不断抽到“醒来”。
        sample = (
            int(hashlib.sha256(message_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        )
        if sample > 0.15:
            return "notification"
    if not context.get("privacy_available", True):
        return "notification"
    return "read"
