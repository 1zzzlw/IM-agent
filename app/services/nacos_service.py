import logging

from v2.nacos import (
    ClientConfigBuilder,
    DeregisterInstanceParam,
    GRPCConfig,
    NacosNamingService,
    RegisterInstanceParam,
)

from config import config

logger = logging.getLogger(__name__)


class NacosService:
    def __init__(self) -> None:
        self._client = None

    async def register(self) -> None:
        client_config = (
            ClientConfigBuilder()
            .server_address(config.nacos.server_addr)
            .log_level("INFO")
            .grpc_config(GRPCConfig(grpc_timeout=5000))
            .build()
        )

        self._client = await NacosNamingService.create_naming_service(client_config)

        success = await self._client.register_instance(
            request=RegisterInstanceParam(
                service_name=config.nacos.service_name,
                group_name=config.nacos.group_name,
                ip=config.nacos.register_ip,
                port=config.nacos.register_port,
                weight=1.0,
                cluster_name=config.nacos.cluster_name,
                metadata={
                    "framework": "fastapi",
                    "protocol": "http",
                    "version": "1.0.0",
                },
                # 表示这个服务实例是否启用
                enabled=True,
                # 表示注册时声明该实例当前是否健康
                healthy=True,
                # 表示是否注册为临时实例，这是三个参数中最重要的
                ephemeral=True
            )
        )

        if not success:
            raise RuntimeError("注册 im-agent 到 Nacos 失败")

        logger.info(
            "Nacos 注册成功: service=%s, address=%s:%s",
            config.nacos.service_name,
            config.nacos.register_ip,
            config.nacos.register_port,
        )

    async def deregister(self) -> None:
        if self._client is None:
            return

        try:
            await self._client.deregister_instance(
                request=RegisterInstanceParam(
                    service_name=config.nacos.service_name,
                    group_name=config.nacos.group_name,
                    ip=config.nacos.register_ip,
                    port=config.nacos.register_port,
                    cluster_name=config.nacos.cluster_name,
                    ephemeral=True
                )
            )

            logger.info("Nacos 实例注销成功: %s", config.nacos.service_name)
        finally:
            await self._client.shutdown()
            self._client = None


nacos_service = NacosService()
