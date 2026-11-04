"""可重建索引，失败时保留正文并明确退回关键词召回。"""

import hashlib

from app.core.config import config
from app.embedding.service import embedding_service
from app.integrations.database.mysql import mysql_service
from app.integrations.vector_store.client import chroma_client


def embedding_space():
    return (
        "person_memory_"
        + hashlib.sha256(
            (config.embedding.name + config.embedding.base_url).encode()
        ).hexdigest()[:16]
    )


def collection():
    return chroma_client.get_client().get_or_create_collection(
        name=embedding_space(), metadata={"hnsw:space": "cosine"}
    )


def sync_memory(row):
    try:
        col = collection()
        if row["status"] not in ("active", "faded") or not row.get("content"):
            col.delete(ids=[row["id"]])
        else:
            vector = embedding_service.embed_query(row["content"])
            col.upsert(
                ids=[row["id"]],
                embeddings=[vector],
                metadatas=[
                    {
                        "world": row["world_id"],
                        "owner": row["owner_actor_id"],
                        "revision": int(row["revision"]),
                    }
                ],
            )
        mysql_service.update(
            "UPDATE imagent_memory SET index_status='ready',indexed_revision=%s,embedding_space=%s WHERE id=%s AND revision=%s",
            (row["revision"], col.name, row["id"], row["revision"]),
        )
    except Exception:
        mysql_service.update(
            "UPDATE imagent_memory SET index_status='failed' WHERE id=%s AND revision=%s",
            (row["id"], row["revision"]),
        )


def search(world_id, actor_id, query):
    try:
        result = collection().query(
            query_embeddings=[embedding_service.embed_query(query)],
            n_results=12,
            where={"$and": [{"world": world_id}, {"owner": actor_id}]},
            include=["distances", "metadatas"],
        )
        return {
            mid: (max(0, 1 - distance), metadata["revision"])
            for mid, distance, metadata in zip(
                result["ids"][0], result["distances"][0], result["metadatas"][0]
            )
        }, "semantic"
    except Exception:
        return {}, "lexical"
