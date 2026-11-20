# Keitaro wrapper — тестовое задание ETOLEGKO

Обёртка над [Keitaro](https://keitaro.io) Admin API: создание кампаний с гео-потоком
и редактор офферов с «верными цифрами» из отчётов трекера.

- Backend: FastAPI + SQLAlchemy 2.0 (async) + Alembic + Pydantic v2 + httpx
- Frontend (план): серверный HTML без сборки
- Хранение: источник истины — Keitaro, локальная БД хранит журнал операций,
  кэш справочников и настройки

## Статус

Проект в разработке; готовые части отмечены в таблице.

| Шаг | Состояние |
|---|---|
| Разведка Admin API, дамп инстанса, контракт создания кампании | готово |
| Скелет приложения, `/health`, настройки, Docker, тесты | готово |
| Клиент Keitaro с обработкой ошибок и ретраями | готово |
| Таблицы, журнал операций и миграции Alembic | готово |
| `POST /api/campaigns` (создаватель) | готово |
| `GET /api/campaigns/{id}`, офферы и цифры (редактор, backend) | готово |
| UI создаватора и редактора | следующий шаг |

## Требования

- Python 3.12–3.13 (на 3.14 нет wheels `asyncpg`) и [uv](https://docs.astral.sh/uv/)
- Docker и Docker Compose — для запуска с PostgreSQL

Зависимости зафиксированы в `uv.lock`: в `pyproject.toml` только нижние границы,
а `uv sync` (он же `make install`) ставит проверенные версии из lock-файла —
установка воспроизводима, без «внезапных» мажорных обновлений.

## Запуск локально

```bash
cp .env.example .env      # и заполнить: ключ и адрес Keitaro
make install              # uv sync
docker compose up -d db   # PostgreSQL для миграций и журнала операций
make migrate              # alembic upgrade head
make run                  # http://127.0.0.1:8000
```

`make run` без Postgres тоже работает для `/health`, но ручки, которые пишут
журнал операций (шаги 4–5), без миграций вернут 503 с подсказкой
«выполните make migrate» — сначала `make migrate`.

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
docker compose build app
docker compose up -d db
docker compose run --rm app alembic upgrade head   # миграции до старта приложения
make up        # app + PostgreSQL (порт хоста 5433, чтобы не мешать локальному Postgres)
make logs
make down
```

Контейнер `app` запускается с двумя воркерами uvicorn (`--workers 2`) и
перезапускается при падении (`restart: unless-stopped`). Живость проверяется
`HEALTHCHECK` из `Dockerfile` — он дёргает `GET /health`; в compose healthcheck
отдельно не дублируется, а наследуется из образа. С неверным `.env` контейнер
падает на старте и уходит в перезапуск — причина видна в `docker compose logs app`.

## Тесты и линтер

```bash
make test      # uv run pytest -q
make lint      # uv run ruff check .
```

Тесты не ходят в сеть: ответы Keitaro замоканы `respx`, а БД — файл SQLite во
временном каталоге `tmp_path` (Postgres для тестов не нужен). Настройки
поднимаются в изолированном окружении (`tests/conftest.py` гасит `KEITARO_*`
и сбрасывает кэш). Отдельный тест прогоняет `alembic upgrade head` → `downgrade
base` на SQLite и сверяет таблицы и колонки с моделями, чтобы миграция не
разошлась со схемой. Ручки создания и редактора проверяются с двойником
Keitaro (`tests/stub_keitaro.py`): сверяются точная форма payload'ов обоих
потоков, компенсация, веса 50/50 и 34/33/33, цифры из мока отчёта, повтор с
тем же ключом и коды ошибок.

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
| `KEITARO_TIMEOUT` | нет | таймаут одного запроса в секундах, по умолчанию `10` (было 30 — веб-ручка ждала слишком долго) |
| `KEITARO_RETRIES` | нет | число повторов сверх первой попытки, по умолчанию `1` (допустимо 0–5) |
| `DATABASE_URL` | нет | строка подключения SQLAlchemy, по умолчанию локальный Postgres на 5433 |
| `LOG_LEVEL` | нет | `critical`…`debug`, по умолчанию `info`; применяется к логированию приложения |

Отсутствие обязательной переменной, пустой ключ или `http://` в адресе инстанса —
понятная ошибка на старте (с перечнем полей), а не падение в рантайме на середине
запроса и не `401` от Keitaro.

## Создание кампании

`POST /api/campaigns` создаёт кампанию и два потока по контракту из
`docs/campaign-create.md`:

1. поток `regular/redirect` с единственным фильтром `country = <geo>` (accept)
   редиректит на Google (`KEITARO_GOOGLE_URL`);
2. поток `default/landings` без фильтров отдаёт трафик на оффер со `share=100`.
   Если Keitaro уже создал `default`-поток сам, он обновляется через `PUT`,
   чтобы не появилось два дефолтных.

Домен, группа и источник берутся из `KEITARO_*` и проверяются по кэшу
справочников (при промахе кэш обновляется из Keitaro). Оффер читается
напрямую: он должен существовать и быть `active`.

```bash
curl -s localhost:8000/api/campaigns \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{"name": "MX test", "geo": "MX", "offer_id": 18813}'
```

```json
{
  "campaign_id": 215000,
  "alias": "mx-test-a1b2c3",
  "name": "MX test",
  "geo": "MX",
  "offer_id": 18813,
  "streams": [
    {"id": 1, "name": "Google (MX)", "type": "regular", "schema": "redirect", "position": 1},
    {"id": 2, "name": "Offers", "type": "default", "schema": "landings", "position": 2}
  ],
  "idempotency_key": "demo-1",
  "replayed": false
}
```

Поля запроса: `name` (1–255), `geo` (ISO-2, приводится к верхнему регистру),
`offer_id` (положительный). Ответ — `201`; повтор с тем же `Idempotency-Key`
возвращает сохранённый результат с `replayed: true` и кодом `200`, не создавая
вторую кампанию. Без заголовка сервер генерирует ключ и возвращает его в теле
и в заголовке `Idempotency-Key`.

Компенсация: если после создания кампании упал поток, кампания архивируется
(`DELETE /campaigns/{id}`), в журнал пишется ошибка; если архив не удался, в
тексте ошибки есть подсказка удалить кампанию вручную. Если кампания и потоки
уже созданы, а упала запись журнала, кампания **не** удаляется — в журнале и
логе остаётся просьба проверить её в Keitaro.

| Код | Когда |
|---|---|
| 201 / 200 | кампания создана / повтор с тем же ключом |
| 409 | ключ уже использован с другим телом, операция ещё выполняется или прошлая попытка упала |
| 422 | гео не ISO-2, оффер не найден/неактивен, домен/группа/источник из `.env` отсутствует в Keitaro, Keitaro отклонил payload |
| 502 | Keitaro недоступен, отклонил ключ или вернул неожиданный ответ (подробности в логе) |
| 503 | не накатаны миграции или недоступна БД |

## Редактор кампании

| Ручка | Назначение |
|---|---|
| `GET /api/campaigns` | список кампаний (заодно обновляет кэш для селектов) |
| `GET /api/offers` | список офферов (кэш для селекта «добавить оффер») |
| `GET /api/campaigns/{id}?interval=all_time` | потоки, офферы, веса и цифры |
| `POST /api/campaigns/{id}/offers` | добавить оффер `{"offer_id": 18813}` |
| `DELETE /api/campaigns/{id}/offers/{offer_id}` | удалить оффер |

Правила весов: после добавления/удаления доли офферов пересчитываются поровну
и всегда дают в сумме 100% — два оффера 50/50, три 34/33/33. Позиции — порядок
в потоке, 1..N. Редактор меняет **default**-поток (или единственный поток с
офферами, если default нет — так бывает на живом инстансе). Последний оффер
удалить нельзя: поток остался бы без трафика (422).

Поток отправляется в `PUT` целиком (`StreamUpdate`): `triggers`, `landings`,
`weight`, `state`, `comments`, фильтры сохраняются, даже если редактор меняет
только `offers`. Живой Keitaro отдаёт `action_options` как `null`, объект или
список, а `country.payload` — списком или строкой (`"RU AU"`); обе формы
поддерживаются и возвращаются без изменений.

Цифры — из `POST /report/build` с `dimensions=["campaign_id","offer"]`:
клики/конверсии по каждому офферу и итог кампании (включая редиректы с
`offer=null`). `interval` — диапазон Keitaro (`all_time`, `today`,
`7_days_ago`, `1_month_ago`, …), по умолчанию `all_time`. Если отчёт упал или
не уложился в таймаут, кампания всё равно возвращается: `stats_available=false`,
цифры нулевые, причина в логе. На медленном инстансе поднимите
`KEITARO_TIMEOUT` (например, до 60): `GET /campaigns/{id}/streams` и отчёт
иногда отвечают дольше 10 секунд.

```bash
curl -s localhost:8000/api/campaigns/215000
curl -s -X POST localhost:8000/api/campaigns/215000/offers \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: add-1' \
  -d '{"offer_id": 18813}'
curl -s -X DELETE localhost:8000/api/campaigns/215000/offers/18813 \
  -H 'Idempotency-Key: remove-1'
```

Мутации идемпотентны по `Idempotency-Key` (как создание): повтор возвращает
сохранённый результат с `replayed: true` и кодом 200, не трогая Keitaro.
В UI шага 6 ключ надо генерировать на действие, иначе сетевой ретрай после
успешного `PUT` покажет 404/409.

## Оговорки по безопасности (локальный запуск)

- Логин и пароль БД `keitaro/keitaro` захардкожены в `docker-compose.yml` — это
  dev-окружение; порт 5433 опубликован только на машине разработчика.
- `KEITARO_API_KEY` попадает в контейнер через `env_file: .env`, поэтому виден
  в `docker inspect` и в `/proc/1/environ` внутри контейнера. Для боевого
  окружения — Docker secrets или переменные оркестратора, а не `env_file`.
- Сам ключ лежит в `.env` с правами `600` и в git не попадает (`.gitignore`);
  шаблон `.env.example` пустых значений не содержит.
- `KEITARO_BASE_URL` обязан быть `https://` (исключение — loopback для локального
  мока): по http заголовок `Api-Key` ушёл бы открытым текстом.

## Структура

```
src/keitaro_wrapper/
  main.py           сборка FastAPI-приложения, старт и валидация настроек
  config.py         настройки (pydantic-settings)
  logging_setup.py  применение LOG_LEVEL
  api/router.py     корневой роутер
  api/routes/       эндпоинты
  services/         бизнес-сценарии: создаватель кампаний
  keitaro/          клиент Admin API, DTO и доменные ошибки
  db/               модели, сессии, журнал операций, кэш справочников
migrations/         env.py и версии миграций Alembic
tests/              pytest
```

`services/` знает про Keitaro и БД, но не про HTTP: ручка только вызывает
сценарий и переводит доменные ошибки в коды ответа.

## Миграции

```bash
make migrate        # uv run alembic upgrade head
make migrate-down   # откат последней миграции
```

URL берётся из `DATABASE_URL`/`.env`, дефолт — Postgres из compose. Ключи
Keitaro для миграций не нужны: `migrations/env.py` не требует `KEITARO_API_KEY`.
В Docker миграции запускаются отдельной командой (файлы `alembic.ini` и
`migrations/` включены в образ):

```bash
docker compose run --rm app alembic upgrade head
```

Приложение не мигрирует БД само и не проверяет соединение на старте: движок
ленивый, поэтому `/health` и чтение Keitaro работают и без Postgres. Если схема
не накатана или БД недоступна, ручки с журналом отвечают **503** с текстом
«Локальная база не готова: выполните `make migrate` …» (`OperationalError`,
`ProgrammingError`, `ConnectionError` и таймаут пула перехватываются в
`api/errors.py`), а подробности попадают в лог приложения.
