# Keitaro Admin API — справочник (сгенерирован)

Источник: `https://admin-api.docs.keitaro.io/openapi.json`
Версия спецификации: OpenAPI 3.0.0, API `v1`.
Всего операций: **119**, путей: **87**, схем: **69**.

> Файл пересобирается командой `python3 scripts/gen_api_docs.py`. Руками не правим.

## Доступ и авторизация

- Базовый путь: `/admin_api/v1` (то есть `https://<instance>/admin_api/v1/...`).
- Заголовок `Api-Key: <ключ>` либо `Authorization: Bearer <ключ>`.
- Ключ создаётся в админке: *Maintenance → Users → Admin API keys*.
- Ошибки приходят как `{"error": "<текст>"}` с HTTP 400/401/402/404/422/500.

Секция `#/` в адресе админки (`?object=adminApi#/`) — это Swagger UI, который открывает ту же спецификацию, что и `openapi.json`.

## Эндпоинты

### Campaigns

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/campaigns` | Get all campaigns | `offset`, `limit` |  |
| `POST` | `/campaigns` | Create campaign |  | `CampaignRequest + CampaignCreateRequired` |
| `GET` | `/campaigns/{id}` | Get campaign | `id` |  |
| `PUT` | `/campaigns/{id}` | Update campaign | `id` | `CampaignRequest` |
| `DELETE` | `/campaigns/{id}` | Move campaign to archive | `id` |  |
| `POST` | `/campaigns/{id}/clone` | Clone Campaign | `id` |  |
| `POST` | `/campaigns/{id}/disable` | Disable Campaign | `id` |  |
| `POST` | `/campaigns/{id}/enable` | Enable Campaign | `id` |  |
| `POST` | `/campaigns/{id}/restore` | Restore Campaign | `id` |  |
| `POST` | `/campaigns/{id}/update_costs` | Update costs for campaign | `id` | `CampaignCostRequest` |
| `POST` | `/campaigns/clean_archive` | Clean archive |  |  |
| `GET` | `/campaigns/deleted` | Get deleted campaigns |  |  |

### Offers

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/offers` | Get offers |  |  |
| `POST` | `/offers` | Create offer |  | `OfferRequest + OfferCreateRequired` |
| `GET` | `/offers/{id}` | Get offer | `id` |  |
| `PUT` | `/offers/{id}` | Update offer | `id` | `OfferRequest` |
| `POST` | `/offers/{id}/add_file` | Create file | `id`, `path` |  |
| `DELETE` | `/offers/{id}/archive` | Delete offer | `id` |  |
| `POST` | `/offers/{id}/clone` | Clone offer | `id` |  |
| `GET` | `/offers/{id}/download` | Download | `id` |  |
| `GET` | `/offers/{id}/get_file` | Read File | `id`, `path` |  |
| `GET` | `/offers/{id}/get_structure` | Get file structure | `id` |  |
| `DELETE` | `/offers/{id}/remove_file` | Delete file | `id`, `path` |  |
| `POST` | `/offers/{id}/restore` | Restore offer | `id` |  |
| `PUT` | `/offers/{id}/update_file` | Upload file | `id`, `path`, `data` |  |
| `POST` | `/offers/clean_archive` | Clean archive |  |  |
| `GET` | `/offers/deleted` | Get deleted offers |  |  |

### Domains

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/domains` | Get the list of domains |  |  |
| `POST` | `/domains` | Create domain |  | `DomainRequest + DomainCreateRequired` |
| `GET` | `/domains/{id}` | Get domain | `id` |  |
| `PUT` | `/domains/{id}` | Update domain | `id` | `DomainRequest` |
| `DELETE` | `/domains/{id}` | Move Domain to Archive | `id` |  |
| `POST` | `/domains/{id}/check` | Update Domain Status | `id` |  |
| `POST` | `/domains/{id}/restore` | Restore an Archived Domain | `id` |  |
| `POST` | `/domains/clean_archive` | Clean archive |  |  |
| `GET` | `/domains/deleted` | Get deleted domains |  |  |
| `GET` | `/domains/ip` | Retrieve server ip |  |  |
| `POST` | `/domains/register` | Register the domain |  | `DomainRequest + DomainCreateRequired` |

### Groups

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/groups` | Get groups | `type` |  |
| `POST` | `/groups` | Create croup |  | `GroupRequest + GroupCreateRequired` |
| `PUT` | `/groups/{id}` | Update Group | `id` | `GroupRequest` |
| `DELETE` | `/groups/{id}/delete` | Delete Group | `id` |  |

### Landing pages

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/landing_pages` | Get landing pages |  |  |
| `POST` | `/landing_pages` | Create landing page |  | `LandingRequest + LandingCreateRequired` |
| `GET` | `/landing_pages/{id}` | Get Landing Page | `id` |  |
| `PUT` | `/landing_pages/{id}` | Update Landing Page | `id` | `LandingRequest` |
| `DELETE` | `/landing_pages/{id}` | Archive Landing Page | `id` |  |
| `POST` | `/landing_pages/{id}/add_file` | Create a File | `id`, `path` |  |
| `PUT` | `/landing_pages/{id}/clone` | Clone Landing Page | `id` |  |
| `GET` | `/landing_pages/{id}/download` | Download | `id` |  |
| `GET` | `/landing_pages/{id}/get_file` | Read File | `id`, `path` |  |
| `GET` | `/landing_pages/{id}/get_structure` | Get Files Structure | `id` |  |
| `DELETE` | `/landing_pages/{id}/remove_file` | Remove file | `id`, `path` |  |
| `POST` | `/landing_pages/{id}/restore` | Restore landing page | `id` |  |
| `PUT` | `/landing_pages/{id}/update_file` | Save File Data | `id`, `path`, `data` |  |
| `POST` | `/landing_pages/clean_archive` | Clean archive |  |  |
| `GET` | `/landing_pages/deleted` | Get deleted landing pages |  |  |

### Affiliate networks

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/affiliate_networks` | List all affiliate networks |  |  |
| `POST` | `/affiliate_networks` | Create an affiliate network |  | `AffiliateNetworkRequest` |
| `GET` | `/affiliate_networks/{id}` | Retrieve Affiliate network details | `id` |  |
| `PUT` | `/affiliate_networks/{id}` | Update Affiliate network | `id` | `AffiliateNetworkPut` |
| `DELETE` | `/affiliate_networks/{id}` | Move affiliate network to the archive | `id` |  |
| `POST` | `/affiliate_networks/{id}/clone` | Clone affiliate network | `id` |  |
| `POST` | `/affiliate_networks/{id}/restore` | Restore affiliate network | `id` |  |
| `POST` | `/affiliate_networks/clean_archive` | Clean archive |  |  |
| `GET` | `/affiliate_networks/deleted` | Get deleted affiliate networks |  |  |

### Reports

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `POST` | `/report/build` | Build custom report |  | `ReportsRequest` |
| `GET` | `/report/labels` | Get Labels | `campaign_id`, `label_name`, `ref_name` |  |
| `POST` | `/report/labels` | Update Labels |  | `LabelRequestPost` |

### Clicks

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `POST` | `/clicks/log` | Get clicks |  | `ClickRequest` |
| `POST` | `/clicks/update_costs` | Update costs in bulk |  | `ClicksUpdateCostsRequest` |

### Conversions

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `POST` | `/conversions/log` | Get conversions |  | `ConversionRequest` |

### Users

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/users` | Get users |  |  |
| `POST` | `/users` | Create user |  | `UserRequest` |
| `GET` | `/users/{id}` | Get user | `id` |  |
| `PUT` | `/users/{id}` | Update user | `id` | `UserRequestUpdate` |
| `DELETE` | `/users/{id}` | Delete user | `id` |  |
| `PUT` | `/users/{id}/access` | Update access | `id` | `UserRequestAccess` |
| `POST` | `/users/clean_archive` | Clean archive |  |  |

### Logs

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/logs/{type}` | Get log | `type`, `limit`, `offset`, `query` |  |
| `DELETE` | `/logs/{type}` | Clean log | `type` |  |
| `POST` | `/logs/types` | Get log types |  |  |

### Bot list

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/botlist` | Retrieve rows from the bot list |  |  |
| `PUT` | `/botlist` | Update the bot list |  | `BotListRequest` |
| `DELETE` | `/botlist` | Clear the bot list |  |  |
| `POST` | `/botlist/add` | Add IPs to the bot list |  | `BotListRequest` |
| `POST` | `/botlist/exclude` | Remove IPs from the bot list |  | `BotListRequest` |

### Clean stats

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `POST` | `/clicks/clean` | Clean stats |  | `CleanRequest` |

### Flows (Streams)

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/campaigns/{id}/streams` | Get flows | `id` |  |
| `GET` | `/stream_filters` | Get available flow filters |  |  |
| `GET` | `/stream_schemas` | Get available flow schemas |  |  |
| `GET` | `/stream_types` | Get list of flows types |  |  |
| `GET` | `/stream/{id}/events` | Get flow events | `id` |  |
| `GET` | `/streams_actions` | Get available flows actions |  |  |
| `POST` | `/streams` | Create flow |  | `StreamRequest` |
| `GET` | `/streams/{id}` | Get flow | `id` |  |
| `PUT` | `/streams/{id}` | Update flow | `id` | `StreamRequestPut` |
| `DELETE` | `/streams/{id}` | Delete flow | `id` |  |
| `POST` | `/streams/{id}/disable` | Disable flow | `id` |  |
| `POST` | `/streams/{id}/enable` | Enable flow | `id` |  |
| `POST` | `/streams/{id}/restore` | Restore an Archived Flow | `id` |  |
| `POST` | `/streams/clean_archive` | Clean archive |  |  |
| `GET` | `/streams/deleted` | Get deleted flows |  |  |
| `GET` | `/streams/search` | Search in flows | `query` |  |

### Third-party integrations

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/integrations/facebook` | Get Facebook integrations |  |  |
| `POST` | `/integrations/facebook` | Create Facebook Integration |  | `FacebookRequest` |
| `GET` | `/integrations/facebook/{id}` | Get Facebook integration | `id` |  |
| `PUT` | `/integrations/facebook/{id}` | Update Facebook integration | `id` | `FacebookRequest` |
| `DELETE` | `/integrations/facebook/{id}` | Delete Facebook integration | `id` |  |
| `GET` | `/integrations/facebook/{id}/campaign` | Get assigned campaigns. | `id` |  |
| `POST` | `/integrations/facebook/{id}/campaign` | Assign to campaign | `id` | `object` |
| `DELETE` | `/integrations/facebook/{id}/campaign` | Dissociate campaign | `id` | `object` |

### Traffic sources

| Метод | Путь | Назначение | Параметры | Тело запроса |
|---|---|---|---|---|
| `GET` | `/traffic_sources` | Get traffic sources |  |  |
| `POST` | `/traffic_sources` | Create traffic source |  | `SourceRequest` |
| `GET` | `/traffic_sources/{id}` | Get traffic source | `id` |  |
| `PUT` | `/traffic_sources/{id}` | Update traffic source | `id` | `SourceRequest` |
| `DELETE` | `/traffic_sources/{id}` | Delete traffic source | `id` |  |
| `POST` | `/traffic_sources/{id}/clone` | Clone trafic source | `id` |  |
| `POST` | `/traffic_sources/clean_archive` | Clean archive |  |  |

## Ключевые схемы

### Campaign

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `id` | `integer` |  |  |
| `alias` | `string` |  | Leave empty to generate automatically. |
| `type` | `string (position, weight)` |  | Set 'weight' to enable split-testing the flows. |
| `name` | `string` |  |  |
| `cookies_ttl` | `integer` |  |  |
| `position` | `integer` |  |  |
| `state` | `string (active, disabled, deleted)` |  |  |
| `cost_type` | `string (CPM, CPC, CPUC, RevShare, CPA, CPS, CPUV, CPV)` |  |  |
| `cost_value` | `number` |  |  |
| `cost_currency` | `string` |  | Leave empty to use tracker currency |
| `group_id` | `integer` |  |  |
| `bind_visitors` | `string (s, sl, slo)` |  | 's' for flows. 'sl' for flows and landing pages. 'slo' for flows, landing pages and offers. |
| `traffic_source_id` | `integer` |  |  |
| `token` | `string` |  |  |
| `cost_auto` | `boolean` |  |  |
| `parameters` | `SourceParameters` |  |  |
| `postbacks` | `S2SPostback[]` |  |  |
| `notes` | `string` |  |  |
| `created_at` | `string` |  |  |
| `updated_at` | `string` |  |  |

### CampaignRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `alias` | `string` |  | Campaign Alias which is Used in URL http://domain.com/ALIAS |
| `type` | `string (position, weight)` |  | Flow Rotation Type |
| `name` | `string` |  | Campaign Name |
| `cookies_ttl` | `integer` |  | When the Click Gets Unique Status Again, in Hours |
| `state` | `string (active, disabled, deleted)` |  | Campaign State |
| `cost_type` | `string (CPC, CPUC, CPM)` |  | Cost Type |
| `cost_value` | `number` |  | Cost Value |
| `cost_currency` | `string` |  | Currency Value EUR/USD/RUB/UAH/GBP. Default Value is Taken from the Tracker's Settings. |
| `cost_auto` | `boolean` |  | Enable Automatic Costs (0/1) |
| `group_id` | `string` |  | Campaign Group ID |
| `bind_visitors` | `string` |  | Bind Visitors Feature (null - disabled/ s - only to streams/ sl - to streams and LPs/ slo — to streams, LPs and offers) |
| `traffic_source_id` | `integer` |  | Traffic source ID |
| `parameters` | `SourceParameters` |  |  |
| `token` | `string` |  | Token to gain access to Click API |
| `domain_id` | `integer` |  | Domain ID |
| `postbacks` | `S2SPostback[]` |  | Campaign S2S postback |
| `notes` | `string` |  | Notes for the campaign |

### CampaignCreateRequired

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| — | — | — | Схема составная, см. описание выше |

### Stream

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `id` | `integer` |  |  |
| `type` | `string` |  |  |
| `name` | `string` |  |  |
| `campaign_id` | `integer` |  |  |
| `position` | `integer` |  |  |
| `weight` | `number` |  |  |
| `action_options` | `object` |  |  |
| `comments` | `string` |  |  |
| `state` | `string` |  |  |
| `updated_at` | `string` |  |  |
| `action_type` | `string` |  |  |
| `action_payload` | `string \| object` |  |  |
| `schema` | `string` |  |  |
| `collect_clicks` | `boolean` |  |  |
| `filter_or` | `boolean` |  |  |
| `filters` | `Filter[]` |  |  |
| `triggers` | `Trigger[]` |  |  |
| `landings` | `LandingStream[]` |  |  |
| `offers` | `OfferStream[]` |  |  |
| `offer_selection` | `string (before_click, after_click)` |  |  |

### StreamObject

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `campaign_id` | `integer` |  | Campaign ID |
| `type` | `string (forced, regular, default)` |  | Flow type |
| `name` | `string` |  | Flow name |
| `position` | `integer` |  | Position of a flow among other flows. |
| `weight` | `number` |  | Flow weight. |
| `action_options` | `object` |  | Action options. |
| `comments` | `string` |  | Comments or notes for the flow |
| `state` | `string (active, disabled, deleted)` |  | State of the flow |
| `action_type` | `string` |  | Action to perform (see 'Retrieve available flow action types') |
| `schema` | `string (landings, redirect, action)` |  |  |
| `collect_clicks` | `boolean` |  | Flow saves clicks (true/false) |
| `filter_or` | `boolean` |  | Use 'OR' operator between filters |
| `filters` | `FilterStreamRequest[]` |  |  |
| `triggers` | `TriggersStreamRequest[]` |  |  |
| `landings` | `LandingStreamRequest[]` |  |  |
| `offers` | `OfferStreamRequest[]` |  |  |

### StreamRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| — | — | — | Схема составная, см. описание выше |

### Filter

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `id` | `integer` |  |  |
| `stream_id` | `integer` |  |  |
| `name` | `string` |  |  |
| `mode` | `string` |  |  |
| `payload` | `string` |  |  |
| `oid` | `string` |  |  |

### FilterStreamRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `id` | `integer` |  | Flow filter ID. Provide it if you update the filter. |
| `name` | `string` | да | Flow filter name, see 'retrive-stream-filters' section |
| `mode` | `string (accept, reject)` | да | Filter mode |
| `payload` | `string[]` |  | Flow payload. This field contains values for filters. For example, for a "keyword" Filter an Array ["value1", "value2"] must be provided |

### Offer

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `id` | `integer` |  |  |
| `name` | `string` |  |  |
| `group_id` | `integer` |  |  |
| `action_type` | `string` |  |  |
| `action_payload` | `string \| object` |  |  |
| `action_options` | `object` |  |  |
| `affiliate_network_id` | `integer` |  |  |
| `payout_value` | `number` |  |  |
| `payout_currency` | `string` |  |  |
| `payout_type` | `string` |  |  |
| `state` | `string` |  |  |
| `created_at` | `string` |  |  |
| `updated_at` | `string` |  |  |
| `payout_auto` | `boolean` |  |  |
| `payout_upsell` | `boolean` |  |  |
| `country` | `string[]` |  |  |
| `notes` | `string` |  |  |
| `affiliate_network` | `string` |  |  |
| `archive` | `string` |  |  |
| `local_path` | `string` |  |  |
| `preview_path` | `string` |  |  |
| `values` | `object[]` |  |  |

### OfferRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `name` | `string` |  | Offer name |
| `group_id` | `integer` |  | Offer group ID |
| `offer_type` | `string` |  | Offer Type ('local'/'external'/'preloaded'/'action') |
| `action_type` | `string` |  | Action or redirect type |
| `action_payload` | `string \| object` |  | Action payload or URL |
| `affiliate_network_id` | `integer` |  | Affiliate network ID |
| `payout_value` | `number` |  | Payout value |
| `payout_currency` | `string` |  | Payout currency |
| `payout_type` | `string (CPA, CPC)` |  | Payout type |
| `state` | `string (active, deleted)` |  | Offer State |
| `payout_auto` | `boolean` |  | If true, offer receives payout value from postback |
| `payout_upsell` | `boolean` |  | Allow offer to get upsells |
| `country` | `string[]` |  | Country codes (i.g, ["US", "DE", "JP"]) |
| `notes` | `string` |  | Notes |
| `archive` | `string` |  | ZIP-file encoded to base64 |
| `conversion_cap_enabled` | `boolean` |  | Turn on that feature if the offer limit conversions per day |
| `daily_cap` | `number` |  | Daily limit of conversions, after which the tracker will send traffic to another offer |
| `conversion_timezone` | `string` |  | Which timezone is being used by the Affiliate network for calculating conversions, e.g. UTC or Europe/Madrid |
| `alternative_offer_id` | `number` |  | Offer id, where to send traffic when daily limit is reached |

### ReportsRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `range` | `RangeRequest` |  |  |
| `dimensions` | `string[]` |  |  |
| `measures` | `string[]` |  |  |
| `filters` | `FilterRequest[]` |  | Filters can contain OR and AND logical operators. |
| `sort` | `SortRequest[]` |  |  |

### RangeRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `from` | `string` |  | Start Date, e.g., '2017-09-10' |
| `to` | `string` |  | End Date, e.g., '2017-09-10' |
| `timezone` | `string` |  | E.g., Europe/Madrid |
| `interval` | `object` |  | One of the intervals can be used: today, yesterday, 7_days_ago, first_day_of_this_week, 1_month_ago, first_day_of_this_month, 1_year_ago, first_day_of_this_year, all_time |

### FilterRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `name` | `string` | да | Name of the field |
| `operator` | `string` | да | One of the available operators ( operators ) |
| `expression` | `string \| number` |  | Expression for the filter |

### SortRequest

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `name` | `string` | да | Column or metric name |
| `order` | `string (ASC, DESC)` | да | Order |

## Что проверить на живом инстансе

Спецификация публичная и может отставать от версии конкретного трекера. Перед реализацией создаватора и редактора снять реальные ответы:

1. `GET /stream_filters` — точные имена фильтров (в т.ч. фильтр по стране).
2. `GET /stream_types`, `GET /streams_actions`, `GET /stream_schemas` — допустимые `type`, `action_type`, `schema` для потоков.
3. `GET /campaigns`, `GET /offers`, `GET /domains`, `GET /groups`, `GET /traffic_sources` — справочники и реальные id для `.env`.
4. `GET /campaigns/{id}/streams` — форма потоков после создания кампании через UI (эталон payload).
5. `POST /report/build` — наборы `dimensions` и `measures` (в спецификации они не перечислены, берутся из UI трекера).

Готовый сборщик этих данных: `python3 scripts/keitaro_dump.py`.
