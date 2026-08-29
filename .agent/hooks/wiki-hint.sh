#!/usr/bin/env bash
# Хук на редактирование файла (Claude Code: PreToolUse Edit|Write; opencode:
# tool.execute.before). По пути правимого файла подсказывает страницу вики.
# Вход: JSON на stdin. Выход: JSON с additionalContext (или ничего).
# Обвязка на stdout — формат Claude Code; при переносе в другой инструмент
# оберни ту же логику в его протокол (см. opencode.wiki.plugin.js).
set -uo pipefail

f=$(jq -r '.tool_input.file_path // .file_path // empty' 2>/dev/null) || exit 0
[ -z "$f" ] && exit 0

b=$(basename "$f")
pages=""

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

# ── Глоссарий: запись в корневой CONTEXT.md уводится на страницу вики. ─────────
# Некоторые инструменты/скиллы дописывают термины по имени CONTEXT.md напрямую;
# без этой подсказки словарь расщепится на два.
if [ "$b" = "CONTEXT.md" ] && [ -f "$root/docs/wiki/глоссарий.md" ]; then
  jq -n --arg c "CONTEXT.md в этом проекте — только указатель. Глоссарий живёт в docs/wiki/глоссарий.md: термины читаются и дописываются там, в формате \`**Термин**:\` + определение + \`_Avoid_:\`." \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$c}}'
  exit 0
fi
# ──────────────────────────────────────────────────────────────────────────────

# ── Карта «файл → страницы вики». Заполнить под проект. ────────────────────────
# Ключ — имя файла или glob; значение — имена страниц через пробел, без .md.
# Пополнять по мере появления подсистем; страница без записи здесь не подскажется.
case "$b" in
  # Общий HTTP-слой
  tm_common.py)
    pages="конвейер" ;;
  # Скрипты конвейера
  tm_*.py)
    pages="конвейер" ;;
  # Вспомогательные
  extract_*.py)
    pages="данные" ;;
  *) ;;
esac
# ──────────────────────────────────────────────────────────────────────────────

[ -z "$pages" ] && exit 0

msg="Правится $b. Актуальное описание этой подсистемы — в вики (читай ПЕРЕД правкой, если ещё не читал):"
for p in $pages; do
  [ -f "$root/docs/wiki/$p.md" ] && msg="$msg"$'\n'"  - docs/wiki/$p.md"
done
msg="$msg"$'\n'"Пороги и константы не выводи из прозы — они в docs/wiki/константы.md."
msg="$msg"$'\n'"После правки обнови затронутые страницы вики (правило в AGENTS.md)."

jq -n --arg c "$msg" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$c}}'
