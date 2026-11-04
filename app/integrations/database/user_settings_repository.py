"""用户空间设置：生活推进窗口、状态可见性、通知偏好。

这些设置只影响展示与推进节奏，不改变人格或记忆；
角色状态本身在 actor_state，二者不要混写。
"""

from app.core.serialization import dump, load
from app.integrations.database.user_space_repository import user_transaction


def get_settings(user_id):
    with user_transaction(user_id) as cursor:
        cursor.execute(
            "SELECT settings_json FROM imagent_user_space WHERE user_id=%s", (user_id,)
        )
        return load(cursor.fetchone()["settings_json"])


def save_settings(body):
    settings = {
        "schemaVersion": 1,
        "lifeWindowMinutes": body.life_window_minutes,
        "presenceVisibility": {
            "online": body.online_visible,
            "read": body.read_visible,
            "typing": body.typing_visible,
        },
        "notifications": {"muted": body.muted},
    }
    with user_transaction(body.user_id) as cursor:
        cursor.execute(
            "UPDATE imagent_user_space SET settings_json=%s,settings_version=settings_version+1 WHERE user_id=%s",
            (dump(settings), body.user_id),
        )
    return settings
