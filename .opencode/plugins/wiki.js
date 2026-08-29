// Обвязка контура вики под opencode (адаптировано для этого проекта).
// Плагин в .opencode/plugins/wiki.js. Скрипты bash (логика) — в .agent/hooks/.
// События и сигнатуры плагинов: https://opencode.ai/docs/plugins/
//
// Механики из SKILL.md отображаются на события opencode так, как позволяет его
// плагинный API:
//   - подсказка «прочитай страницу вики» → tool.execute.before для edit/write;
//   - оглавление вики на старте сессии   → session.created (журнал; в контекст
//     модели надёжнее всего попадает через AGENTS.md, который указывает на вики);
//   - stop-проверка вики                  → в opencode блокирующего аналога нет,
//     заменяется периодическим прогоном скилла wiki-lint.
// Скрипты ожидают на stdin JSON и возвращают JSON во «вложенной» обвязке (формат
// Claude Code). Этот плагин извлекает сообщение и пишет его в журнал.
//
// Windows-адаптация: bash не гарантирован в PATH — берём bash из Git Bash
// (C:\Program Files\Git\bin\bash.exe), если `bash` не найден. Корень проекта
// выводится из местоположения самого плагина (.opencode/plugins/…).
import { execSync } from "node:child_process"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, "..", "..")
const HOOKS_DIR = join(ROOT, ".agent", "hooks")

function bashBin() {
  const candidates = [
    process.env.GIT_BASH || "",
    "C:/Program Files/Git/bin/bash.exe",
    "C:/Program Files (x86)/Git/bin/bash.exe",
  ]
  for (const c of candidates) {
    if (!c) continue
    try {
      execSync(`"${c}" --version`, { stdio: "ignore" })
      return c
    } catch {
      /* try next */
    }
  }
  return "bash" // fallback: rely on PATH
}

const BASH = bashBin()

function run(script, input) {
  try {
    const out = execSync(`"${BASH}" "${join(HOOKS_DIR, script)}"`, {
      input: JSON.stringify(input),
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "ignore"],
      env: { ...process.env, HOME: process.env.USERPROFILE ?? process.env.HOME },
    })
    return JSON.parse(out)
  } catch {
    return null
  }
}

function message(res) {
  return res?.hookSpecificOutput?.additionalContext ?? null
}

export const WikiPlugin = async ({ client }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "edit" && input.tool !== "write") return
      const f = output.args?.filePath
      if (!f) return
      const hint = run("wiki-hint.sh", { tool_input: { file_path: f } })
      const text = message(hint)
      if (text) await client.app.log({ body: { level: "info", message: text } })
    },
    "session.created": async (input) => {
      const toc = run("wiki-toc.sh", { session_id: input.session?.id ?? "default" })
      const text = message(toc)
      if (text) await client.app.log({ body: { level: "info", message: text } })
    },
  }
}
