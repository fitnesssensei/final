"""Тесты TTL кэша справочников и ретенции журнала операций.

Оба механизма — обслуживание локальной БД: TTL закрывает устаревшие селекты
(новый оффер не появлялся без перезапуска), ретенция не даёт журналу расти
бесконечно. Тесты без сети и без ожидания реального времени: часы подменяются.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from keitaro_wrapper.db import Base, OperationLog, OperationType, create_session_factory
from keitaro_wrapper.db.operations import prune_operations
from keitaro_wrapper.main import create_app, lifespan, prune_old_operations
from keitaro_wrapper.services import reference
from tests.stub_keitaro import StubKeitaro

# --- TTL справочников -------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Управляемые часы: `clock[0] += 61` сдвигает время без реального ожидания."""
    now = [1000.0]
    monkeypatch.setattr(reference, "_clock", lambda: now[0])
    return now


async def test_cache_is_not_refreshed_inside_ttl(session: AsyncSession, clock: list[float]) -> None:
    """Внутри TTL повторный рендер селектов не ходит в Keitaro."""
    stub = StubKeitaro()
    reference.configure_cache_ttl(60)

    await reference.creator_options(session, stub)
    calls_after_first = len(stub.calls)
    await reference.creator_options(session, stub)

    assert calls_after_first > 0
    assert len(stub.calls) == calls_after_first


async def test_cache_is_refreshed_after_ttl_expires(
    session: AsyncSession, clock: list[float]
) -> None:
    """TTL истёк — справочники перечитываются, иначе новый оффер не появится в селекте."""
    stub = StubKeitaro()
    reference.configure_cache_ttl(60)
    await reference.creator_options(session, stub)
    calls_after_first = len(stub.calls)

    clock[0] += 61
    await reference.creator_options(session, stub)

    assert len(stub.calls) > calls_after_first


async def test_zero_ttl_keeps_old_behaviour(session: AsyncSession, clock: list[float]) -> None:
    """TTL=0 (и дефолт в тестах) — кэш обновляется только когда пуст."""
    stub = StubKeitaro()
    reference.configure_cache_ttl(0)

    await reference.creator_options(session, stub)
    calls_after_first = len(stub.calls)
    clock[0] += 10_000
    await reference.creator_options(session, stub)

    assert len(stub.calls) == calls_after_first


async def test_explicit_refresh_updates_ttl_marker(
    session: AsyncSession, clock: list[float]
) -> None:
    """`?refresh=1` сдвигает срок свежести: следующий рендер не идёт в Keitaro."""
    stub = StubKeitaro()
    reference.configure_cache_ttl(60)

    await reference.campaign_options(session, stub, refresh=True)
    calls_after_refresh = len(stub.calls)
    await reference.campaign_options(session, stub)

    assert len(stub.calls) == calls_after_refresh


# --- ретенция журнала -------------------------------------------------------------


async def test_prune_removes_only_old_operations(session: AsyncSession) -> None:
    """Старые записи удаляются, свежие остаются: идемпотентность повторов цела."""
    now = datetime.now(UTC).replace(tzinfo=None)
    session.add_all(
        [
            OperationLog(
                idempotency_key="old",
                operation_type=OperationType.CAMPAIGN_CREATE.value,
                payload={},
                created_at=now - timedelta(days=30),
            ),
            OperationLog(
                idempotency_key="fresh",
                operation_type=OperationType.CAMPAIGN_CREATE.value,
                payload={},
                created_at=now,
            ),
        ]
    )
    await session.commit()

    removed = await prune_operations(session, older_than_days=7)
    keys = (await session.scalars(select(OperationLog.idempotency_key))).all()

    assert removed == 1
    assert keys == ["fresh"]


async def test_prune_with_zero_days_keeps_everything(session: AsyncSession) -> None:
    """`OPERATIONS_RETENTION_DAYS=0` — чистка выключена (журнал не теряется)."""
    session.add(
        OperationLog(
            idempotency_key="ancient",
            operation_type=OperationType.CAMPAIGN_CREATE.value,
            payload={},
            created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3650),
        )
    )
    await session.commit()

    removed = await prune_operations(session, older_than_days=0)
    count = await session.scalar(select(func.count()).select_from(OperationLog))

    assert removed == 0
    assert count == 1


# --- связка со стартом приложения -------------------------------------------------


def set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Валидное окружение для `lifespan` с точечными подменами."""
    env = {
        "KEITARO_BASE_URL": "https://tlgk.host",
        "KEITARO_API_KEY": "test-key",
        "KEITARO_DOMAIN_ID": "1813",
        "KEITARO_GROUP_ID": "10332",
        "KEITARO_SOURCE_ID": "836",
    }
    for name, value in {**env, **overrides}.items():
        monkeypatch.setenv(name, value)


async def test_lifespan_prunes_journal_and_configures_ttl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """На старте: старые записи журнала удалены, TTL справочников взят из настроек."""
    db_path = tmp_path / "lifespan.db"
    set_env(
        monkeypatch,
        DATABASE_URL=f"sqlite+aiosqlite:///{db_path}",
        OPERATIONS_RETENTION_DAYS="7",
        REFERENCE_CACHE_TTL="120",
    )
    # Схему и старую запись готовим до старта: приложение миграции не делает
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with create_session_factory(engine)() as session:
        session.add(
            OperationLog(
                idempotency_key="stale",
                operation_type=OperationType.CAMPAIGN_CREATE.value,
                payload={},
                created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30),
            )
        )
        await session.commit()
    await engine.dispose()

    app = create_app()
    async with lifespan(app):
        async with app.state.session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(OperationLog))
        assert count == 0
        assert reference._cache_ttl == 120


async def test_lifespan_survives_unavailable_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """БД недоступна — приложение стартует, чистка журнала только предупреждает."""
    # Каталог не существует: драйвер падает на подключении
    set_env(monkeypatch, DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/missing/x.db")

    app = create_app()
    with caplog.at_level("WARNING", logger="keitaro_wrapper.main"):
        async with lifespan(app):
            pass

    assert any("чистка пропущена" in record.message for record in caplog.records)


async def test_prune_old_operations_is_noop_when_disabled(tmp_path: Path) -> None:
    """`days=0` — функция выходит сразу и даже не открывает сессию."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'noop.db'}")
    factory = create_session_factory(engine)
    try:
        await prune_old_operations(factory, days=0)
    finally:
        await engine.dispose()
