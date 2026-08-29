# AGENTS.md

This file provides guidance to agents working with code in this repository.

## Что это

Python-конвейер, который тянет данные Transfermarkt через локальный
[transfermarkt-api](https://github.com/Churikov0112/transfermarkt-api) (FastAPI на :8000), скачивает
фото, чистит и собирает `prepared_*.json` для Flutter-игры football_collection и канон для
GameplayFootball. Единый канон и конвейер — [[данные-из-transfermarkt]] вики GameplayFootball.

**Актуальная картина проекта живёт в вики: `docs/wiki/index.md` — начинать оттуда.**

## Состояние проекта

**Ветка `scraper-v2` — актуальная.** Переписанный скрейпер по дизайну [[конвейер]]: `config.py`,
`client.py` (curl_cffi, глобальный token-bucket HTML 8 rps / CDN 15 rps, weight-aware для
market_value, circuit breaker,
ретраи с Retry-After), `cache.py` (SQLite resume-кэш всех статусов), `fetch/` (competitions →
clubs → rosters → profiles/market_values → coaches), `build_canon.py` + `canon_schema.json`,
`download_images.py`, `crawl.py`, `probe.py`. Лимиты установлены probe-экспериментом 2026-08-29,
пилот собран и валидирован (детали — [[конвейер]], [[данные]]).

Ветки `main`/`fix` — старый конвейер `tm_1…tm_999` (сломан: баны TM, неидемпотентный `tm_999`);
не развивать. Новый код писать в структуре `scraper-v2`.

## Ядро архитектуры

- Общий HTTP-слой — `client.py` (`Client.request`: кэш → circuit gate → token bucket → ретраи;
  бакеты `html` и `cdn` отдельно; curl_cffi с fallback на requests). Resume-кэш — `cache.py`.
- Fetch-модули — `fetch/`; оркестратор — `crawl.py --scope pilot|tier1 [--spot N] [--images]`.
- Скрейпер ходит на `http://127.0.0.1:8000` (API); напрямую на TM — только `/mitarbeiter/`
  (`fetch/coaches.py`), ceapi-fallback (`fetch/market_values.py`) и CDN-картинки (`download_images.py`).
- Выход — канон-JSON в `data/canon/` (`build_canon.py`, валидация `canon_schema.json`).
- Старый конвейер `tm_*.py` заморожен (см. ветки `main`/`fix`).

### Известные баги main (чинить при переписывании)

- Ретраи: в fetch-функциях перекрыт общий набор на `{503}` — 429/5xx не ретраятся, данные тихо
  теряются.
- `tm_999` перезаписывает входные файлы — второй прогон разрушает данные (профили без `profile`
  ключа → пустые записи).
- `tm_998` избыточен при верном порядке; `tm_4_2`/`tm_5_2` хрупки к формату после `tm_999`.
- Возможный `TypeError` в `max(entry.get('marketValue', 0))` при `null` в истории.
- `tm_7`: комментарий «тёмно-синий фон», а `BACKGROUND_COLOR = (255,255,255)` — белый.

## Сборка / запуск

```bash
# нужен поднятый transfermarkt-api на :8000 (см. его AGENTS.md)
.venv\Scripts\python crawl.py --scope pilot            # пилот (PSG, Real, Франция)
.venv\Scripts\python crawl.py --scope tier1            # полный Tier-1
.venv\Scripts\python crawl.py --scope pilot --spot 10 --images
.venv\Scripts\python probe.py --html | --cdn           # перезамер лимитов (осторожно!)
.venv\Scripts\python -m pytest                         # 30 тестов HTTP-слоя (без сети)
```

Зависимости: `requirements.txt` (`requests`, `beautifulsoup4`, `lxml`, `curl_cffi`, `jsonschema`,
`pytest`, `pytest-timeout`; уже в `.venv`). Параметры — через env `TM_*` (лимиты, кэш, пути — см.
`config.py`).

## Устройство вики

- `docs/wiki/` — **текущее состояние**, одна страница на подсистему, кросс-ссылки вида
  `[[имя-страницы]]`, каталог в `docs/wiki/index.md`. Обновляй после правки кода.
- `docs/wiki/глоссарий.md` — имена предметной области. `CONTEXT.md` в корне — только указатель.
- `log.md` (корень) — append-only хронология. Записи `## [YYYY-MM-DD] тип | описание`.

**Правило: после любого содержательного изменения обнови соответствующую страницу вики — не
создавай новый датированный документ.**

## Workflow

- Код и комментарии — на английском; вики и этот файл — на русском.
- **Лимиты к TM: HTML 8 rps, CDN 15 rps** (установлены probe 2026-08-29, см. [[конвейер]]).
  При бане (429/403/блок-страница) — остановиться, не молотить ретраями; circuit breaker сам
  даст паузу, заблокированные URL кэшируются.
- Три хука (.agent/hooks/ + плагин opencode в .opencode/plugins/) поддерживают контур вики.

## Working principles

## 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

## 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

## 3. Surgical Changes
Touch only what you must. Match existing style. Remove only what YOUR changes made unused.

## 4. Goal-Driven Execution
Define success criteria. Loop until verified.