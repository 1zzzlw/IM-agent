from redis import Redis, ConnectionPool
from app.core.config import config


class RedisClient:
    def __init__(self):
        self.pool: ConnectionPool | None = None
        self.client: Redis | None = None

    def initialize(self) -> None:
        """创建 Redis 连接池并检查连接。"""
        if self.pool is not None and self.client is not None:
            return

        self.pool = ConnectionPool(
            host=config.redis.host,
            port=config.redis.port,
            db=config.redis.db,
            password=config.redis.password,
            max_connections=config.redis.max_connections,
            socket_timeout=config.redis.socket_timeout,
            socket_connect_timeout=config.redis.socket_connect_timeout,
            decode_responses=config.redis.decode_responses
        )

        # 创建 redis 的客户端
        self.client = Redis(connection_pool=self.pool)

        # 检查连接是否正常
        try:
            self.client.ping()
        except Exception:
            self.pool.disconnect()
            self.pool = None
            self.client = None
            raise Exception("Redis 连接失败")

    def get_client(self) -> Redis:
        """获得redis客户端"""
        if self.client is None:
            raise Exception("Redis 连接池未初始化")

        return self.client

    def close(self) -> None:
        """关闭 Redis 客户端并释放连接池。"""
        client = self.client
        pool = self.pool

        self.client = None
        self.pool = None

        if client is not None:
            client.close()

        if pool is not None:
            pool.disconnect()




def get_redis_client() -> Redis:
    return redis_client.get_client()

redis_client = RedisClient()
