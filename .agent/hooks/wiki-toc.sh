#!/usr/bin/env bash
# Старт сессии (Claude Code: SessionStart; opencode: session.created — см.
# opencode.wiki.plugin.js): впрыскивает компактное оглавление вики и запоминает
# HEAD сессии. Оглавление генерируется из docs/wiki/index.md, поэтому не устаревает.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
idx="$root/docs/wiki/index.md"
[ -f "$idx" ] || exit 0

# запомнить HEAD на старте сессии — stop-проверка сравнит с ним, чтобы увидеть
# коммиты, сделанные за сессию (а не только незакоммиченные правки)
sid=$(jq -r '.session_id // .sessionID // "default"' 2>/dev/null) || sid="default"
git -C "$root" rev-parse HEAD > "/tmp/wiki-head-$sid" 2>/dev/null || true
rm -f "/tmp/wiki-stopped-$sid" 2>/dev/null || true

PY="python3"
python3 --version >/dev/null 2>&1 || PY="python"

toc=$(PYTHONIOENCODING=utf-8 $PY - "$idx" <<'PY'
import re, sys
rows = []
for line in open(sys.argv[1], encoding='utf-8'):
    m = re.match(r'^\|\s*\[\[([^\]]+)\]\]\s*\|\s*(.+?)\s*\|\s*$', line)
    if m:
        rows.append(f"  {m.group(1)}.md — {m.group(2)}")
print("\n".join(rows))
PY
) || exit 0

[ -z "$toc" ] && exit 0

msg="Вики проекта (docs/wiki/, живое состояние — читать вместо вывода из кода/прозы):
$toc
Каталог: docs/wiki/index.md · Датированные первоисточники (не редактировать): docs/reports/"

jq -n --arg c "$msg" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$c},suppressOutput:true}'
