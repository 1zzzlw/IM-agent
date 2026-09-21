from pathlib import Path
from chromadb import PersistentClient
from app.core.config import config
from app.core.paths import RESOURCES_DIR


class ChromaClient:
    def __init__(self) -> None:
        self.client: PersistentClient | None = None

    def initialize(self) -> None:
        if self.client is not None:
            return

        # 创建 ChromaDB 的持久化存储目录
        persist_dir = Path(RESOURCES_DIR, config.rag.persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)

        self.client = PersistentClient(path=str(persist_dir))

    def get_client(self) -> PersistentClient:
        if self.client is None:
            raise Exception("ChromaDB 尚未初始化")

        return self.client

    def close(self) -> None:
        if self.client is not None:
            # Chroma 通常不需要手动关闭每一条连接，close() 主要是用于清理当前对象引用
            self.client = None


chroma_client = ChromaClient()
