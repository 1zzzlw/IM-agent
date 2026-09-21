from langchain_core.embeddings import Embeddings

from app.embedding.factory import embedding_factory


class EmbeddingService:
    def __init__(self) -> None:
        self.model: Embeddings | None = None

    def initialize(self) -> None:
        if self.model is not None:
            return

        self.model = embedding_factory.create()

    def get_model(self) -> Embeddings:
        if self.model is None:
            raise RuntimeError("Embedding 模型尚未初始化")

        return self.model

    def embed_documents(
            self,
            texts: list[str],
    ) -> list[list[float]]:
        return self.get_model().embed_documents(texts)

    def embed_query(
            self,
            text: str,
    ) -> list[float]:
        return self.get_model().embed_query(text)


embedding_service = EmbeddingService()
