# AGENTS.md

This file provides guidance to agents working with code in this repository.

## Что это

Python-конвейер, который тянет данные Transfermarkt через локальный
[transfermarkt-api](https://github.com/Churikov0112/transfermarkt-api) (FastAPI на :8000), скачивает
фото, чистит и собирает `prepared_*.json` для Flutter-игры football_collection и канон для
GameplayFootball. Единый канон и конвейер — [[данные-из-transfermarkt]] вики GameplayFootball.

**Актуальная картина проекта живёт в вики: `docs/wiki/index.md` — начинать оттуда.**

## Состояние проекта

**Ветка `main` сломана** (в т.ч. баны TM): задержки по умолчанию 0, воркеры 8–20, статичный UA
Chrome 91 (2021), голый `requests` без TLS-fingerprint, ретраится только 503, 429 молча глотается;
`tm_999` неидемпотентен — перезаписывает собственные входные файлы. Ветка `fix` намечает решение:
rate-limit ≤2 rps, curl_cffi, SQLite-resume-кэш, идемпотентность. **Новый код писать в духе `fix`,
не в `main`.**

## Ядро архитектуры

- Общий HTTP-слой — `tm_common.py` (`create_session`, `request_with_retries`, `get_soup`,
  `get_json`, `extract_id_from_url`, `load_json`/`save_json`).
- Скрипты по порядку: `tm_1_teams_2` (сборные+игроки+тренеры) → `tm_1_2_filter_coaches_by_teams`
  → `tm_2_players_profiles_2` → `tm_3_legends_profiles` → `tm_4_market_values_2` →
  `tm_4_2_filter_market_values_by_players_profiles` → `tm_5_clubs_2` → `tm_5_2_filter_clubs_by_players_profiles`
  → `tm_6_load_images_2` → `tm_7_compress_images_2` → `tm_999_prepare_data`.
- Скрейпер ходит на `http://localhost:8000` (API), на TM напрямую — только `tm_1` (страницы
  сборных/сотрудников) и `tm_6` (фото).
- Промежуточные файлы: `tm_teams.json`, `tm_players_urls.json`, `tm_players_profiles.json`,
  `tm_players_market_values.json`, `tm_legends_profiles.json`, `tm_clubs.json`,
  `tm_coach_profiles.json`, `tm_clubs_urls.json` + папки `*_faces/`, `flags/`, `logos/`.
- Финальные `prepared_*` — вход для football_collection (см. её [[данные]]).

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
python tm_1_teams_2.py
python tm_2_players_profiles_2.py
# ... по порядку до tm_999
```

Параметры — через env (`TM_*_WORKERS`, `TM_*_REQUEST_DELAY`, `TM_*_MAX_RETRIES`, см. файлы).

## Устройство вики

- `docs/wiki/` — **текущее состояние**, одна страница на подсистему, кросс-ссылки вида
  `[[имя-страницы]]`, каталог в `docs/wiki/index.md`. Обновляй после правки кода.
- `docs/wiki/глоссарий.md` — имена предметной области. `CONTEXT.md` в корне — только указатель.
- `log.md` (корень) — append-only хронология. Записи `## [YYYY-MM-DD] тип | описание`.

**Правило: после любого содержательного изменения обнови соответствующую страницу вики — не
создавай новый датированный документ.**

## Workflow

- Код и комментарии — на английском; вики и этот файл — на русском.
- **Лимит запросов к TM — не более 2/сек.** При бане (429/блокировка) — остановиться, не
  молотить ретраями.
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