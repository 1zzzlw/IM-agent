from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from app.core.config import config


class EmbeddingFactory:
    @staticmethod
    def create() -> Embeddings:
        return OpenAIEmbeddings(
            model=config.embedding.name,
            api_key=config.embedding.api_key,
            base_url=config.embedding.base_url,
        )

embedding_factory = EmbeddingFactory()