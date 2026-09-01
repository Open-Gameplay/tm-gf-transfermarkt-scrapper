# Handoff: конвейер данных GF (open-football + TM-ростеры)

Датированный handoff-пакет для свежей сессии. Обновлён 2026-08-31. Актуальное состояние — в
`docs/wiki/конвейер.md` (обновлено), хронология — `log.md`.

---

# Промпт для новой сессии

Ты работаешь с экосистемой из 4 репозиториев (Windows, PowerShell):

- `C:\Users\Egor\Desktop\projects\transfermarkt_scrapper` — **твой главный объект** (ветка `scraper-v2`).
  Вики: `docs/wiki/index.md` → `docs/wiki/конвейер.md`, `docs/wiki/данные.md`.
- `C:\Users\Egor\Desktop\projects\transfermarkt-api` — форк FastAPI-обёртки над TM (порт 8000),
  ветка `main` (curl_cffi + `imageUrl` в составах клубов).
- `C:\Users\Egor\Desktop\projects\GameplayFootball` — 3D-игра (GF), ветка `squads-update`. Канон-схема —
  `docs/wiki/данные-из-transfermarkt`.
- `C:\Users\Egor\Desktop\projects\football_collection` — Flutter-потребитель `prepared_*.json`.

Запуск API:
```
& "C:\Users\Egor\Desktop\projects\transfermarkt-api\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Скрейпер: `.venv\Scripts\python` внутри `transfermarkt_scrapper`.

## Состояние (что уже сделано)

**Стратегия данных**: все атрибуты игроков доступны напрямую с TM через **tmapi JSON API**
(`tmapi.transfermarkt.technology`) — не блокируется, Reverse-engineered из JS-бандлов TM.
open-football-database **отложена в долгий ящик** (может перестать поддерживаться, TM доступен
напрямую).

**Уже стянуто с TM:**
- **Составы клубов: 3 949 клубов, 107 454 игрока** (все лиги из индекса `/wettbewerbe/national/`).
  В состав входят: id, name, position, DOB, age, nationality[], height, foot, joinedOn, signedFrom,
  contract, marketValue, status, **imageUrl** (фото).
- **Профили клубов: только 198** (Tier-1: цвета, стадион, лига, лого) — остальных нет.
- **Сборные: 19** (top-20: profile + roster).
- **Тренеры: 19** (top-20).
- tmapi profile (Rabiot, Mbappe, Lewa, Haaland): ✅ тестировано, работает без бана.

**Инструменты в репо (`scraper-v2`):** `config.py`, `client.py` (curl_cffi, адаптивная скорость,
circuit breaker, resume-кэш, джиттер, веса, severity-403), `cache.py`, `fetch/`, `build_canon.py`,
`crawl.py --scope pilot|smoke|tier1`, `probe.py`, `session_viewer.py` (веб-дашборд, есть таблица
по лигам), `fetch_all_rosters.py` (составы всех лиг), `fetch_all_club_profiles.py` (профили клубов),
`fetch_all_national_teams.py` (все сборные), `fetch_player_extras.py` (stats/injuries/achievements),
`download_images.py` (фото/логотипы/эмблемы). Тесты: pytest (51).

**transfermarkt-api (форк):** эндпоинты переписаны на tmapi/ceapi JSON:
- `app/services/players/profile.py` → `tmapi/player/{id}`
- `app/services/players/market_value.py` → `tmapi/player/{id}/market-value-history`
- `app/services/players/stats.py` → `ceapi/performance-game/{id}`

**Лимиты (probe):** HTML 2 rps sustained (профили страниц игроков блокируются сильнее), CDN 15 rps
(не ограничен, проверено до 20). Скорость самонастраивается. tmapi JSON — не блокируется.

**API-форк — доступные эндпоинты (все через JSON, не блокируются):**
- `/clubs/{id}/players` — составы клубов (imageUrl, height, foot, marketValue)
- `/clubs/{id}/profile` — профили клубов (цвета, стадион, лига, лого)
- `/national-teams/most-valuable` — все сборные (пагинация внутри API)
- `/national-teams/{id}/profile` + `/players` — профиль и состав сборной
- `/players/{id}/profile` (tmapi) — height, foot, position, outfitter, imageUrl
- `/players/{id}/market_value` (tmapi) — история стоимости
- `/players/{id}/stats` (ceapi) — статистика по сезонам
- `/players/{id}/injuries` — травмы
- `/players/{id}/achievements` — титулы
- `/players/{id}/transfers` — трансферы

**GF SQLite схема (из `src/menu/mainmenu.cpp:343-403`):**
```sql
regions(id INTEGER PK, name VARCHAR(64))
countries(id INTEGER PK, region_id INTEGER, name VARCHAR(64))
leagues(id INTEGER PK, country_id INTEGER, name VARCHAR(64), logo_url VARCHAR(512))
teams(id INTEGER PK, league_id INTEGER, name VARCHAR(64), logo_url VARCHAR(512),
      kit_url VARCHAR(512), formation_xml TEXT, formation_factory_xml TEXT,
      tactics_xml TEXT, tactics_factory_xml TEXT, shortname VARCHAR(3),
      color1 VARCHAR(16), color2 VARCHAR(16))
players(id INTEGER PK, team_id INTEGER, nationalteam_id INTEGER,
        firstname VARCHAR(64), lastname VARCHAR(64), role VARCHAR(32),
        age INTEGER, base_stat FLOAT, profile_xml TEXT, skincolor INTEGER,
        hairstyle VARCHAR(64), haircolor VARCHAR(64), height FLOAT, weight FLOAT,
        formationorder INTEGER, nationalteamformationorder INTEGER)
```

**base_stat**: `NormalizedClamp(CA - 10, 0, 100)` + age boost (18-летние +10%).
CA/PA — из open-football (отложена) или эвристика из marketValue.
**profile_xml**: 22 стата по 9 позиционным архетипам (GK/SW/D/WB/DM/M/AM/F/ST), взвешенное
среднее по позициям игрока. Каждый архетип — 22 хардкод-значения (src/utils.cpp:160).
**формации**: XML `<p1>..<p11>` с координатами (x,y) и ролью (GK/CB/LB/RB/DM/CM/LM/RM/AM/CF).

## Что делать дальше (по порядку)

1. **`fetch_all_rosters.py --refresh`** — добор `imageUrl` в составы, закэшированные до фикса фото
   (~1900 клубов; скрипт сам пропускает те, где фото уже есть). ~30-40 мин, фон, ресумаблно.
2. **Новый `fetch_all_club_profiles.py`** — `/clubs/{id}/profile` для всех 3949 клубов (цвета,
   стадион, лига, лого). Профиль клуба — **не заблокированный** путь. Использовать списки клубов
   из кэша (`/competitions/{id}/clubs`). ~3949 запросов, фон, ресумаблно.
3. **Новый `fetch_all_national_teams.py`** — `/national-teams/most-valuable` (все ID, один запрос)
   + `/national-teams/{id}/profile` + `/national-teams/{id}/players` для каждой.
4. **`fetch_player_extras.py`** — stats/injuries/achievements/transfers для игроков (все 107k,
   weight≥2, очень осторожно: 0.5 rps, мониторинг 403, стоп при бане). Источник — API, не TM
   напрямую.
5. **`download_images.py`**: `download_faces_from_cache()` (лица из составов, CDN-бакет) +
   `download_club_logos_from_cache()` (логотипы из club profiles) + `download_league_logos()`.
6. **`build_gf_database.py`** — конвертер канон-JSON → GF SQLite: regions, countries, leagues,
   teams (color1/color2, formation_xml, shortname), players (role, base_stat, profile_xml,
   height, weight, formationorder). Основан на схеме из `src/menu/mainmenu.cpp:343-403` и
   алгоритмах из `src/utils.cpp` (CalculateStat, GetDefaultProfile, InitDefaultProfiles).

## Ограничения/ловушки

- **Профили игроков HTML — заблокированы**: не ходить на HTML-страницы профилей/стоимостей.
  Вместо этого — tmapi JSON (`tmapi.transfermarkt.technology`), не блокируется.
- **open-football-database отложена**: может перестать поддерживаться. TM доступен напрямую.
- **Player extras (stats/injuries/achievements)** — идут через API, weight≥2. Тестировано на 5
  игроках, работает. Массовый сбор ~535k запросов, ~6 дней на 2 rps.
- `session_viewer.py` — перезапустить, если упал; лог-путь передаётся `--log`.
- Крол состов уже завершён (269 лиг) — вьюер по лигам покажет 269/269.
- Вики и `log.md` обновлять после содержательных правок (правила в AGENTS.md).

## Критерии готовности сессии

1. Все составы с `imageUrl` (после `--refresh`).
2. Профили всех клубов (цвета/лого/стадион/лига) в кэше; логотипы скачаны.
3. Все сборные (profile + roster) в кэше; эмблемы скачаны.
4. ~~`load_openfootball` по всем странам~~ — **ОТЛОЖЕНО** (ofdb в долгий ящик).
5. Конвертер `build_gf_database.py` собирает рабочий GF SQLite из канона.
6. `log.md` + вики + `открытые-вопросы.md` обновлены.
