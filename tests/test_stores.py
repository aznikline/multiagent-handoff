"""Tests for Redis and PostgreSQL store backends."""

from __future__ import annotations

import pytest

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, TaskInfo
from handoff.orchestrator.redis_store import RedisHandoffStore


class TestRedisHandoffStore:
    """Redis store tests using fakeredis."""

    @pytest.fixture
    async def redis_store(self):
        pytest.importorskip("fakeredis")
        from fakeredis.aioredis import FakeRedis

        client = FakeRedis()
        store = RedisHandoffStore(redis_client=client, ttl_seconds=3600)
        yield store
        await client.close()

    @pytest.fixture
    def sample_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-1"),
                handoff_reason=HandoffReason.USER_TRIGGERED,
            ),
            task=TaskInfo(original_task_id="task-1", description="Test"),
        )

    @pytest.mark.asyncio
    async def test_save_and_load(
        self, redis_store: RedisHandoffStore, sample_package: ContextPackage
    ) -> None:
        await redis_store.save(sample_package)
        loaded = await redis_store.load(sample_package.meta.package_id)
        assert loaded is not None
        assert loaded.meta.source.agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_load_missing(self, redis_store: RedisHandoffStore) -> None:
        result = await redis_store.load("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_existing(
        self, redis_store: RedisHandoffStore, sample_package: ContextPackage
    ) -> None:
        await redis_store.save(sample_package)
        deleted = await redis_store.delete(sample_package.meta.package_id)
        assert deleted is True
        assert await redis_store.load(sample_package.meta.package_id) is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, redis_store: RedisHandoffStore) -> None:
        deleted = await redis_store.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_expired_returns_empty(
        self, redis_store: RedisHandoffStore
    ) -> None:
        # Redis handles TTL automatically
        expired = await redis_store.list_expired()
        assert expired == []

    @pytest.mark.asyncio
    async def test_ttl_applied(
        self, redis_store: RedisHandoffStore, sample_package: ContextPackage
    ) -> None:
        await redis_store.save(sample_package)
        key = redis_store._key(sample_package.meta.package_id)
        ttl = await redis_store._redis.ttl(key)
        assert ttl > 0
        assert ttl <= 3600
