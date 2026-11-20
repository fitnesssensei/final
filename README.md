# Keitaro wrapper — тестовое задание ETOLEGKO

Обёртка над [Keitaro](https://keitaro.io) Admin API: создание кампаний с гео-потоком
и редактор офферов с «верными цифрами» из отчётов трекера.

- Backend: FastAPI + SQLAlchemy 2.0 (async) + Alembic + Pydantic v2 + httpx
- Frontend: серверный HTML без сборки
- Хранение: источник истины — Keitaro, локальная БД хранит журнал операций,
  кэш справочников и настройки

## Статус

Проект в разработке; готовые части отмечены в таблице.

| Шаг | Состояние |
|---|---|
| Разведка Admin API, дамп инстанса, контракт создания кампании | готово |
| Скелет приложения, `/health`, настройки, Docker, тесты | готово |
| Клиент Keitaro с обработкой ошибок и ретраями | в работе |
| Таблицы и миграции Alembic | запланировано |
| `POST /api/campaigns` (создаватель) | запланировано |
| `GET/PUT /api/campaigns/{id}` (редактор) | запланировано |
| UI создаватора и редактора | запланировано |

## Требования

- Python 3.12–3.13 (на 3.14 нет wheels `asyncpg`) и [uv](https://docs.astral.sh/uv/)
- Docker и Docker Compose — для запуска с PostgreSQL

## Запуск локально

```bash
cp .env.example .env      # и заполнить: ключ и адрес Keitaro
make install              # uv sync
make run                  # http://127.0.0.1:8000
```

Проверка:

```bash
curl -s localhost:8000/health
# {"status":"ok","version":"0.1.0"}

open http://127.0.0.1:8000/docs
```

`/health` — только живость процесса: настройки он не читает. Обязательные
переменные проверяются на старте (`lifespan`), поэтому с пустым ключом или
`http://` в адресе сервис не поднимется и напишет, какие поля не прошли
валидацию. В Docker это видно как упавший контейнер `app`, а не «зелёный»
healthcheck.

## Запуск в Docker

```bash
make up        # app + PostgreSQL (порт хоста 5433, чтобы не мешать локальному Postgres)
make logs
make down
```

## Тесты и линтер

```bash
make test      # uv run pytest -q
make lint      # uv run ruff check .
```

Тесты детерминированные: сеть только через моки (respx), БД — SQLite в памяти.

## Конфигурация

Все значения читаются из окружения и `.env` (в git не попадает; шаблон —
`.env.example`).

| Переменная | Обязательная | Назначение |
|---|---|---|
| `KEITARO_BASE_URL` | да | адрес инстанса, например `https://example.keitaro.io` |
| `KEITARO_API_KEY` | да | Admin API key, передаётся заголовком `Api-Key` |
| `KEITARO_DOMAIN_ID` | да | домен кампании по умолчанию |
| `KEITARO_GROUP_ID` | да | группа кампаний по умолчанию |
| `KEITARO_SOURCE_ID` | да | источник трафика по умолчанию |
| `KEITARO_GOOGLE_URL` | нет | редирект гео-потока, по умолчанию `https://www.google.com` |
| `DATABASE_URL` | нет | строка подключения SQLAlchemy, по умолчанию локальный Postgres на 5433 |
| `LOG_LEVEL` | нет | `critical`…`debug`, по умолчанию `info`; применяется к логированию приложения |

Отсутствие обязательной переменной, пустой ключ или `http://` в адресе инстанса —
понятная ошибка на старте (с перечнем полей), а не падение в рантайме на середине
запроса и не `401` от Keitaro.

## Структура

```
src/keitaro_wrapper/
  main.py          сборка FastAPI-приложения
  config.py        настройки (pydantic-settings)
  api/router.py    корневой роутер
  api/routes/      эндпоинты
tests/             pytest
```

Слои, которые появятся дальше: `services` (создаватель, редактор) →
`keitaro` (клиент, DTO, ошибки) → `repositories` + `models`.

## Миграции

Появятся вместе с таблицами (`alembic upgrade head` через `make migrate`).
До этого приложению БД не нужна: `/health` работает без неё.
