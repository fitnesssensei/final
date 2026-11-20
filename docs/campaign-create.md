# Создание кампании: проверенный контракт `tlgk.host`

Всё ниже проверено на живом инстансе 19.11.2026 (GET-запросы и один пробный
`POST` с невалидным телом — без создания сущностей).

## Права на запись

`POST /campaigns` с `{"alias": "", "name": ""}` возвращает
`422 {"name": ["Name is required"], "alias": ["Alias is required"]}`.
Это значит: ключ имеет права на создание (не `403`), а валидация отдаёт ошибки
по полям — идеальный способ проверять права, ничего не создавая.

Ещё один нюанс: `POST` с пустым объектом `{}` даёт
`400 Invalid request body, expected JSON object`, поэтому в клиенте отправляем
`{}` только когда это осмысленно, а обязательные поля заполняем всегда.

## `POST /admin_api/v1/campaigns`

Обязательны `alias` и `name`. Рабочее тело (совпадает с UI-кампанией на этом
инстансе):

```json
{
  "alias": "mx-test-a1b2c3",
  "name": "MX test",
  "type": "position",
  "state": "active",
  "domain_id": 1813,
  "group_id": 10332,
  "traffic_source_id": 836
}
```

response `200` — полный объект кампании с `id`, `token`, `created_at`.

- `group_id` в спецификации запроса описан как `string`, в ответе и в UI — число;
  на практике принимается число.
- `domain_id` / `traffic_source_id` — числа, берутся из справочников.
- `type`: `position` (обычная ротация) или `weight` (сплит-тест потоков).

## `POST /admin_api/v1/streams`

Поток 1 — гео → Google:

```json
{
  "campaign_id": 215000,
  "name": "Google (MX)",
  "type": "regular",
  "schema": "redirect",
  "action_type": "http",
  "action_payload": "https://www.google.com",
  "collect_clicks": true,
  "position": 1,
  "filters": [{"name": "country", "mode": "accept", "payload": ["MX"]}]
}
```

Поток 2 — оффер:

```json
{
  "campaign_id": 215000,
  "name": "Offers",
  "type": "default",
  "schema": "landings",
  "action_type": "http",
  "action_payload": "",
  "collect_clicks": true,
  "position": 2,
  "offers": [{"offer_id": 18813, "share": 100}]
}
```

- `OfferStreamRequest.required = ["offer_id", "share"]` — `share` обязателен.
- `action_type` берётся из `GET /streams_actions`; поле называется `key`
  (`http`, `meta`, `js`, ...), для редиректа нужен `http`.
- `schema`: `landings` | `redirect` | `action` (совпадает с реальными потоками).
- После создания кампании Keitaro может сам завести `default`-поток: его нужно
  переиспользовать через `PUT /streams/{id}`, иначе получится два дефолтных.
- `GET /campaigns/{id}/streams` — верификация результата.

## Откат и идемпотентность

- `DELETE /campaigns/{id}` — кампания уходит в архив (не удаляется физически),
  вернуть: `POST /campaigns/{id}/restore`.
- Уникальность `alias` гарантирует, что повторный запуск создаватора не
  перезапишет чужую кампанию; при сбое на шаге потоков создаватор архивирует
  уже созданную кампанию (флаг `--keep-on-error` отключает это поведение).

## Реализация

> Статус: сценарий реализован в API — `POST /api/campaigns`
> (`services/campaigns.py`), включая идемпотентность через `operation_log`,
> компенсацию и кэш справочников. Живое создание на инстансе **не выполнялось**
> — по решению владельца ключа; проверены чтением (домен, группа, источник и
> оффер существуют) и моками. Первый реальный `POST` остаётся за вами
> (Swagger, `curl` из README или скрипт без `--dry-run`).

На инстансе параллельно работают другие тестировщики (кампании
`test_*_wrapper_*` появляются в течение дня), поэтому `alias` генерируется
со случайным суффиксом: чужие кампании не перезаписываются.

`scripts/keitaro_create_campaign.py`:

```bash
python3 scripts/keitaro_create_campaign.py \
    --name "MX test" --geo MX --offer-id 18813 --dry-run   # только payload'ы
python3 scripts/keitaro_create_campaign.py \
    --name "MX test" --geo MX --offer-id 18813             # создание
```

Значения по умолчанию для домена, группы и источника берутся из `.env`
(`KEITARO_DOMAIN_ID=1813`, `KEITARO_GROUP_ID=10332`,
`KEITARO_SOURCE_ID=836`, единственные доступные на инстансе домен и источник).

## Важный нюанс по данным

Гео из задания (`--geo`) управляет только фильтром первого потока. Страна самого
оффера задаётся в оффере: у единственного доступного оффера `18813` стоит `PE`,
поэтому при `--geo MX` трафик вне Мексики уйдёт на перуанский оффер. Это
поведение ТЗ, но при демонстрации стоит проговаривать явно.
