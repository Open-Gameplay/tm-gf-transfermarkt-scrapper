# Handoff: конвейер данных GF (open-football + TM-ростеры)

Датированный handoff-пакет для свежей сессии. Создан 2026-08-31. Актуальное состояние — в
`docs/wiki/конвейер.md` (обновлено), хронология — `log.md`.

---

# Промпт для новой сессии

Ты работаешь с экосистемой из 4 репозиториев (Windows, PowerShell):

- `C:\Users\Egor\Desktop\projects\transfermarkt_scrapper` — **твой главный объект** (ветка `scraper-v2`).
  Вики: `docs/wiki/index.md` → `docs/wiki/конвейер.md`, `docs/wiki/данные.md`.
- `C:\Users\Egor\Desktop\projects\transfermarkt-api` — форк FastAPI-обёртки над TM (порт 8000),
  ветка `main` (curl_cffi + `imageUrl` в составах клубов).
- `C:\Users\Egor\Desktop\projects\GameplayFootball` — 3D-игра (GF), ветка `squads-update`. Канон-схема —
  `docs/wiki/данные-из-transfermarkt.md`.
- `C:\Users\Egor\Desktop\projects\football_collection` — Flutter-потребитель `prepared_*.json`.

Запуск API:
```
& "C:\Users\Egor\Desktop\projects\transfermarkt-api\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Скрейпер: `.venv\Scripts\python` внутри `transfermarkt_scrapper`.

## Состояние (что уже сделано)

**Стратегия данных изменилась**: TM-страницы игроков (`/profil/spieler/`, `/marktwertverlauf/`) TM
блокирует по объёму per-IP (~15-20 запросов → бан на минуты; проверено на двух IP, даже с VPN и
ротацией fingerprint). Поэтому источник атрибутов для GF — **open-football-database**
(https://github.com/ZOXEXIVO/open-football-database): поддерживаемый TM-производный датасет
(сезон 2025/26, git, 59k игроков, 69 стран). У каждого игрока: позиции с уровнями (FM-коды),
CA/PA, value, ноги, контракт, карьера, `ids.transfermarkt.com`. **Роста/фото нет.**

**Уже стянуто с TM (не заблокированные пути):**
- **Составы клубов: 3949 клубов, 107454 игрока** (все лиги из индекса `/wettbewerbe/national/`).
  В состав входят: id, name, position, dateOfBirth, age, nationality[], height, foot, joinedOn,
  signedFrom, contract, marketValue, status, **imageUrl** (фото — добавлено в API-форке, парсится
  из страницы состава, профиль не нужен).
- **Профили клубов: только 198** (Tier-1: цвета, стадион, лига, лого) — остальных нет.
- Логотипы/эмблемы: 198 клубных + 19 сборных (Tier-1), лежат в `data/images/`.

**Инструменты в репо (`scraper-v2`):** `config.py`, `client.py` (curl_cffi, адаптивная скорость,
circuit breaker, resume-кэш, джиттер, веса, severity-403), `cache.py`, `fetch/`, `build_canon.py`,
`crawl.py --scope pilot|smoke|tier1`, `probe.py`, `session_viewer.py` (веб-дашборд, есть таблица
по лигам), `load_openfootball.py` (join open-football + TM-ростеры по TM-id), `fetch_all_rosters.py`
(составы всех лиг). Тесты: pytest (51).

**Лимиты (probe):** HTML 2 rps sustained (профили страниц игроков блокируются сильнее), CDN 15 rps
(не ограничен, проверено до 20). Скорость самонастраивается.

## Что делать дальше (по порядку)

1. **`fetch_all_rosters.py --refresh`** — добор `imageUrl` в составы, закэшированные до фикса фото
   (~1900 клубов; скрипт сам пропускает те, где фото уже есть). ~30-40 мин, фон, ресумаблно.
2. **Новый `fetch_all_club_profiles.py`** — `/clubs/{id}/profile` для всех 3949 клубов (цвета,
   стадион, лига, лого). Профиль клуба — **не заблокированный** путь. Использовать списки клубов
   из кэша (`/competitions/{id}/clubs`). ~3949 запросов, фон, ресумаблно.
3. **`download_images.py`**: `download_faces_from_cache()` (лица из составов, CDN-бакет, уже
   написана) + логотипы всех клубов после п.2.
4. **`load_openfootball.py`** — прокинуть `imageUrl` (из TM-ростеров) в слитый датасет; прогнать по
   **всем странам**, отчитаться о покрытии рост/фото.
5. **Собрать канон для GF** из слитых данных (open-football атрибуты + TM рост/фото/клуб/сборная),
   затем конвертер в GF SQLite (base_stat из value/CA-PA, profile_xml по роли, формации).
6. **Закрепить open-football-database**: сейчас клон в
   `C:\Users\Egor\AppData\Local\Temp\opencode\football_projects\open-football-database` (временный).
   Сделать сабмодулем или задокументировать путь `OF_DATABASE_PATH`. Данные — git, обновление = pull.

## Ограничения/ловушки

- **Профили игроков и истории стоимости TM — заблокированы**: не ходить туда. Фото из составов,
  атрибуты из open-football.
- **TM-id есть не у всех игроков open-football** (Испания ~95%, Англия топ-лига 66%, нижние
  дивизионы 0-38%) — для остальных рост не джойнится; решить: фаззи-матч по имени+дате или дефолт.
- **Рост/вес**: рост — из TM-ростеров (есть), вес — эвристика (в TM и open-football его нет).
- `session_viewer.py` — перезапустить, если упал; лог-путь передаётся `--log`.
- Крол состов уже завершён (269 лиг) — вьюер по лигам покажет 269/269.
- Фото игроков в GF не обязательны (3D); нужны коллекционке; источник при желании — DF11 Faces.
- Вики и `log.md` обновлять после содержательных правок (правила в AGENTS.md).

## Критерии готовности сессии

1. Все составы с `imageUrl` (после `--refresh`).
2. Профили всех клубов (цвета/лого/стадион/лига) в кэше; логотипы скачаны.
3. `load_openfootball` по всем странам даёт слитый датасет с ростом и фото; отчёт по покрытию.
4. Канон GF собран (или хотя бы дизайн конвертера зафиксирован).
5. `log.md` + вики + `открытые-вопросы.md` обновлены.