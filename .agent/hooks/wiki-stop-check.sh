#!/usr/bin/env bash
# Stop-проверка (Claude Code: Stop; в opencode прямого аналога нет — заменяется
# периодическим прогоном скилла wiki-lint). Если за сессию менялся код подсистем,
# описанных вики, а docs/wiki — нет, один раз возвращает агента дописать вики.
# Второй Stop проходит всегда.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root" || exit 0

sid=$(jq -r '.session_id // .sessionID // "default"' 2>/dev/null) || sid="default"
sentinel="/tmp/wiki-stopped-$sid"
[ -f "$sentinel" ] && exit 0          # уже срабатывал в этой сессии — не зацикливаемся

# файлы, изменённые за сессию: незакоммиченные + коммиты после старта сессии
changed=$(git status --porcelain 2>/dev/null | awk '{print $NF}')
base=$(cat "/tmp/wiki-head-$sid" 2>/dev/null || true)
if [ -n "$base" ] && git cat-file -e "$base^{commit}" 2>/dev/null; then
  changed="$changed"$'\n'$(git diff --name-only "$base" HEAD 2>/dev/null || true)
fi
[ -z "${changed//[[:space:]]/}" ] && exit 0

# ── Код, который вики реально описывает. Сузить под каталоги проекта. ──────────
# Широкая регулярка ловит конфиги и одноразовые скрипты — хук начнёт срабатывать
# на правки, которым вики не нужна, и его перестанут читать.
CODE_RE='^tm_[0-9a-z_]*\.py$|^tm_common\.py$'
# ──────────────────────────────────────────────────────────────────────────────

code=$(printf '%s\n' "$changed" | grep -E "$CODE_RE" | sort -u)
[ -z "$code" ] && exit 0

wiki=$(printf '%s\n' "$changed" | grep -E '^docs/wiki/' | sort -u)
[ -n "$wiki" ] && exit 0              # вики тронута — всё в порядке

touch "$sentinel"
list=$(printf '%s\n' "$code" | head -8 | sed 's/^/  - /')

jq -n --arg r "За сессию менялся код, а docs/wiki — нет:
$list

Правило из AGENTS.md: обнови страницу(ы) вики для затронутой подсистемы — не создавай новый датированный документ. Если менялся порог, поправь docs/wiki/константы.md; если что-то стало (не)подтверждено — docs/wiki/открытые-вопросы.md.

Если обновление вики в этот раз не нужно (тривиальная правка, эксперимент, откат) — просто скажи это одной строкой и завершай. Повторно этот хук за сессию не сработает." \
  '{decision:"block",reason:$r}'
