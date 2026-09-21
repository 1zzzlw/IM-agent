from typing import Any
from app.integrations.vector_store.client import chroma_client
from chromadb.api.models.Collection import Collection
from app.core.config import config


class ProjectCodeStore:
    def __init__(self) -> None:
        self.collection: Collection | None = None

    def initialize(self) -> None:
        if self.collection is not None:
            return

        client = chroma_client.get_client()

        self.collection = client.get_or_create_collection(
            name=config.rag.collection_name,
            metadata={
                "hnsw:space": "cosine",
            },
        )

    def get_collection(self) -> Collection:
        if self.collection is None:
            raise Exception("ChromaDB 尚未初始化")

        return self.collection

    def close(self) -> None:
        if self.collection is not None:
            self.collection = None

    def upsert_chunks(
            self,
            *,
            ids: list[str],
            documents: list[str],
            embeddings: list[list[float]],
            metadatas: list[dict[str, Any]],
    ) -> None:
        collection = self.get_collection()

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def search(
            self,
            *,
            query_embedding: list[float],
            user_id: str,
            workspace_id: str,
            limit: int = 5,
    ) -> dict[str, Any]:
        collection = self.get_collection()

        return collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where={
                "$and": [
                    {"user_id": {"$eq": user_id}},
                    {"workspace_id": {"$eq": workspace_id}},
                ]
            },
            include=[
                "documents",
                "metadatas",
                "distances",
            ],
        )

    def delete_file(
            self,
            *,
            user_id: str,
            workspace_id: str,
            relative_path: str,
    ) -> None:
        collection = self.get_collection()

        collection.delete(
            where={
                "$and": [
                    {"user_id": {"$eq": user_id}},
                    {"workspace_id": {"$eq": workspace_id}},
                    {"relative_path": {"$eq": relative_path}},
                ]
            }
        )

    def delete_workspace(
            self,
            *,
            user_id: str,
            workspace_id: str,
    ) -> None:
        collection = self.get_collection()

        collection.delete(
            where={
                "$and": [
                    {"user_id": {"$eq": user_id}},
                    {"workspace_id": {"$eq": workspace_id}},
                ]
            }
        )


project_code_store = ProjectCodeStore()
