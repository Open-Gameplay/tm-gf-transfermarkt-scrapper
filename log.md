# Хронология

Append-only хронология проекта (паттерн LLM-Wiki): вехи, деплои, решения, опровергнутые гипотезы.
Дописывать в конец; старые записи не редактировать и не удалять. Парсится: `grep "^## \[" log.md | tail -5`.

Типы: `session` (итог сессии работы агента), `milestone` (веха), `deploy` (релиз), `decision`
(решение), `rejected` (опровергнутая гипотеза), `fix` (исправление).

## [2026-08-29] milestone | scraper v2 реализован и пропилотирован (ветка scraper-v2)

По handoff-пакету `docs/reports/2026-08-29-handoff-scraper-v2.md` реализован переписанный скрейпер
по дизайну [[конвейер]]: `config.py`, `client.py` (curl_cffi/requests, глобальный token-bucket
html/cdn, circuit breaker, ретраи с Retry-After, resume-кэш всех статусов), `cache.py` (SQLite
`requests(url,status,payload,fetched_at)`), `fetch/` (competitions/clubs/rosters/profiles/
market_values/coaches), `build_canon.py` + `canon_schema.json`, `download_images.py`, `crawl.py`,
`probe.py`.

**Лимиты (probe 2026-08-29, до массового сбора):** HTML через API — чисто на 2.0 rps sustained и
3.0 rps burst, безопасно **1.5 rps**; CDN (img.a.transfermarkt.technology) — чисто на 20 rps,
безопасно **10 rps** (гипотеза «CDN не ограничен как HTML» подтверждена). 429/Retry-After не
наблюдались. Результаты — в [[конвейер]] (раздел «Лимиты»).

**Находки:** API-эндпоинт `market_value` TM 403-ит (голый requests/статичный UA Chrome 113 на
`/marktwertverlauf/` и `ceapi/.../graph/`); добавлен прямой fallback на ceapi через curl_cffi
(Chrome 131) — пробивает. Отдельные страницы игроков TM 403 даже curl_cffi (0 байт, похоже на
honeypot) — пропускаются и кэшируются. `/players/{id}/market_value` = 2 запроса к TM (страница +
график), фактор ×2 при бюджетировании.

**Пилот (PSG + Real Madrid + Франция):** канон `data/canon/` собран и валидирован по схеме
(69 игроков, 2 клуба, 2 лиги, 1 сборная, 1 тренер, 5 историй стоимости); фото скачаны. Повторный
прогон — 0.6 с, ноль сетевых запросов (resume-кэш, идемпотентно). Ветка `scraper-v2` от `main`.

## [2026-08-29] session | см. milestone выше