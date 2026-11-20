# Что реально можно спарсить с `tlgk.host` (проверено 19.11.2026)

Проверка выполнена обычными GET-запросами без авторизации, чтобы понять границу
доступа. Ключи и пароли не запрашивались и не применялись.

## Результаты проверки

| Запрос | Ответ | Вывод |
|---|---|---|
| `GET https://tlgk.host/admin/?object=adminApi` | `403`, HTML-страница логина (`appTranslation`, `login.js`) | админка закрыта аутентификацией |
| `GET https://tlgk.host/admin_api/v1/campaigns` | `401` `{"error":"Invalid API key"}` | данные инстанса только с Admin API key |
| `GET https://tlgk.host/admin_api/v1/openapi.json` | `404` | инстанс не отдаёт спецификацию публично |
| `GET https://tlgk.host/admin/assets/app.js` | `200`, 2 341 042 байта | публичный SPA-бандл, документации Admin API внутри нет |
| `GET https://tlgk.host/admin/assets/login.js` | `200`, 1 392 097 байт | код экрана логина |
| `GET https://admin-api.docs.keitaro.io/openapi.json` | `200`, OpenAPI 3.0.0, 87 путей / 119 операций / 69 схем | публичный референс Admin API |

Итог по вопросу «спарсить всё из `?object=adminApi#/`»:

- **Документация (Swagger) — да.** Страница `?object=adminApi#/` — это Swagger UI
  поверх спецификации Admin API. Та же спецификация лежит публично на
  `admin-api.docs.keitaro.io/openapi.json`, поэтому её можно скачать и разобрать
  без доступа к инстансу. Уже разобрана: `docs/keitaro-api.md`.
- **Данные инстанса (`tlgk.host`) — только с ключом.** Кампании, потоки, офферы,
  домены, группы, источники, лендинги, партнёрские сети, пользователи, отчёты,
  логи кликов и конверсий отдаются исключительно по заголовку `Api-Key`
  (или `Authorization: Bearer`). Без ключа — `401`, без логина в админку — `403`.
  Обходить аутентификацию не нужно и не планируется: весь тот же набор данных
  доступен легально через Admin API.

## Что нужно, чтобы выгрузить все данные

1. `KEITARO_BASE_URL=https://tlgk.host` — адрес инстанса.
2. `KEITARO_API_KEY` — Admin API key из *Maintenance → Users → Admin API keys*.
   Достаточно прав на чтение; для создаватора/редактора нужны права на запись.
3. Для записи: `KEITARO_DOMAIN_ID`, `KEITARO_GROUP_ID`, `KEITARO_SOURCE_ID`.

Значения хранятся только в локальном `.env` (он в `.gitignore`), в репозиторий
попадает лишь `.env.example` с именами переменных.

## Готовые инструменты в репозитории

| Файл | Назначение |
|---|---|
| `docs/keitaro-api.md` | справочник по Admin API: все 119 операций, поля ключевых схем, авторизация |
| `scripts/gen_api_docs.py` | пересобирает справочник из публичного `openapi.json` |
| `scripts/keitaro_dump.py` | полная выгрузка данных инстанса в JSON + `_manifest.json` |
| `scripts/keitaro_probe.sh` | быстрая проверка ключа и счётчиков справочников |

Запуск:

```bash
cp .env.example .env      # вписать KEITARO_BASE_URL и KEITARO_API_KEY
scripts/keitaro_probe.sh  # проверить доступ
python3 scripts/keitaro_dump.py
# результат: data/keitaro/entities/*.json, campaigns/<id>/streams.json,
#            meta/*.json, logs/*.json, reports/*.json, _manifest.json
```

Дополнительно: `--include-deleted` (архив), `--only offers,campaigns`,
`--streams-for 12,34`, `--logs clicks,conversions --from 2026-11-01 --to 2026-11-19`,
`--report-json body.json` (тело `POST /report/build`).

## Что показал живой инстанс (проверено тем же ключом)

- Ключ рабочий, но с ограниченными правами: `users`, `landing_pages`,
  `affiliate_networks` отвечают `403 You have no permission to access this page`.
  Остальное читается.
- `GET /groups` **требует** обязательный параметр `type` (без него
  `400 Param 'type' is required`). Доступные значения на инстансе:
  `campaigns` (22 группы), `offers` (2), `domains` / `landings` / `flows` (0).
  Неизвестный `type` возвращает пустой список, а не ошибку.
- Объёмы: 65 кампаний, 1 оффер, 1 домен, 1 источник трафика, 125 потоков
  (в 3 кампаниях потоков нет), 61 оффер внутри потоков.
- Метаданные: 63 фильтра потоков (фильтр `country` присутствует), 3 типа потоков,
  3 схемы потоков, 18 действий.
- `POST /report/build` работает с `dimensions: ["campaign", "stream", "offer"]`
  и `measures: ["clicks", "conversions"]`, диапазон `{"interval": "all_time"}`.
  Строки с `"offer": null` — это клики, ушедшие не на оффер (например, редирект
  на Google из гео-потока). Это ключ к «верным цифрам» в редакторе.
- Структура потоков совпадает с ТЗ: поток 1 — `regular` / `redirect` с фильтром
  `country` в режиме `accept`; поток 2 — `default` / `landings` с оффером.

Реальные названия кампаний и офферов остаются только в `data/` (в `.gitignore`),
в документацию и репозиторий они не переносятся.

## Ограничения

- Права ключа ограничивают набор доступных сущностей: при `403` на конкретный
  эндпоинт скрипт продолжает работу, помечает запись как `forbidden`
  в `_manifest.json` и не считает это сбоем.
- Публичная спецификация отстаёт от живого инстанса: `/groups` требует `type`,
  `dimensions`/`measures` отчётов в спеке не перечислены (рабочие значения
  зафиксированы выше).
- Если заказчик даёт только логин в админку без API-ключа, парсить HTML панели
  технически можно, но это нестабильно (React SPA, Cloudflare) и не требуется
  заданием — правильнее запросить Admin API key.
