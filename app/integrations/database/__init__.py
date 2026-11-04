"""数据访问层：MySQL / 向量库 / Redis 的读写都收在这里。

按职责分四类：
- ``person_store`` / ``message_store``：基础读写，只依赖 core，不依赖业务仓储；
- ``state_commit``：人物状态的唯一提交入口（版本 CAS、幂等、变化流水）；
- ``message_history`` 等查询函数：只读，不产生副作用；
- ``*_repository.py``：面向一个用例的编排（接收消息、生活推进、朋友圈、通话等）。

命名约定：``snapshot`` / ``current`` 读；``append_*`` / ``record_*`` / ``commit_*`` 写。
应用层（api/services/agent）不直接写 SQL；跨表事务的锁顺序见 character_repository 与 state_commit。
"""
