# Security

Timonel starts programs that can edit your code and run commands. These are the defences and their
limits.

## Threat model

| Asset | Threat | Defence |
|---|---|---|
| Your working copy | An agent makes a bad or malicious change | Edits happen in a separate git worktree on branch `timonel/<id>`. Nothing is merged without `timonel apply` or the *Apply* button. |
| Files outside the repository | An agent writes a path with `..` or a symlink pointing outside | Every changed path is checked after each attempt; escaping paths and outward symlinks reject the attempt. Agents also run with their own sandbox flags (`--sandbox workspace-write`, `--permission-mode acceptEdits`). |
| API keys | Keys leak to logs, the UI or another agent | Keys come only from environment variables or the OS keyring (`keyring` extra). `env_from` gives a key to one agent's process and removes it from every other agent and from verification commands. API key variables declared by any connector (`OPENAI_API_KEY`, `GROQ_API_KEY`…) are removed from every CLI agent unless that agent lists the name in `env_pass` (Claude Code gets `ANTHROPIC_API_KEY`, Gemini CLI gets `GEMINI_API_KEY`). Provider keys read from the keyring are added to the redaction list. Outputs, errors, task text and events are redacted before they are stored or shown. Keys are never written to files. |
| Router configuration | Repository content or agent output changes routing | Your user file is read from your configuration directory. A repository's `.timonel.yaml` is ignored until you run `timonel trust`, which prints the whole file, lists every command and every agent, verification, notification, path and privacy setting it changes, and stores the SHA-256 of exactly the bytes it showed. A project file can never set an agent's `provider`, `env_from`, `env_pass` or install metadata, so it cannot send your keys elsewhere or swap an installer; any later change to the file revokes the trust until you review it again. Classifier, reviewer and planner answers are parsed as data and validated against fixed schemas; they can change the tier and the task text, never agents, flags or commands. |
| Your machine | Shell injection through prompts | No `shell=True` anywhere. Commands are argument lists. Placeholders are filled once and never re-expanded. On Windows, npm `.cmd` shims are resolved to `node script.js`; any other batch shim refuses arguments containing cmd.exe metacharacters. |
| Your machine | A planted `claude.cmd` or `git.exe` in the repository | On Windows, executables are only accepted from folders listed in `PATH`, never from the current directory (`NoDefaultCurrentDirectoryInExePath` is also set). |
| Your machine | Agent-controlled code runs during verification | Verification runs the project's own tests, which the agent may have edited. Timonel runs them with a minimal environment (`PATH`, home, temp and locale variables, plus names you list in `execution.verify_env`), so API keys and tokens in your shell are not visible to them. They are not sandboxed: only run Timonel on repositories and tasks you trust, as you would run their tests yourself. |
| Git metadata | An agent rewrites the worktree's `.git` pointer to run commands through git configuration | Before every git command in a worktree, Timonel checks that `.git` is still a file pointing outside the worktree to git's `worktrees/` folder; git is always called with `core.fsmonitor` and hooks disabled. |
| Your machine | Permission bypass flags | Flags such as `--dangerously-skip-permissions`, `--yolo`, `--allow-all`, `--allow-tool=shell` or `approval_policy=never` are rejected by the schema unless they are in `dangerous_args`, the agent sets `allow_dangerous: true`, and you pass `--dangerous` and type `yes`. The web UI and MCP server never enable them. |
| Your machine | An agent runs arbitrary shell commands | Defaults keep shells out of reach where the CLI allows it: Copilot always gets `--deny-tool=shell`, Claude Code runs in `plan` or `acceptEdits` mode (no unapproved Bash), Codex runs in its `read-only` or `workspace-write` sandbox. `agy` permissions are unverified. |
| Read-only tasks | An "explain" task edits files | `explain` and `review` always use each agent's read-only arguments, even if the classifier claims otherwise. |

## Web UI

The UI can launch agents with write access, so it is locked down:

- It listens on `127.0.0.1` only.
- Each launch creates a random 256-bit URL token. It works once: the server exchanges it for a
  different random session value in an `HttpOnly`, `SameSite=Strict` cookie whose name includes the
  port, and redirects to a URL without the token. Responses send `Referrer-Policy: no-referrer`.
- Every request must carry a `Host` header of `127.0.0.1:<port>` or `localhost:<port>`, which blocks
  DNS rebinding.
- Every state-changing request must come from the same origin (`Origin` header), carry the CSRF token
  embedded in the page in `X-CSRF-Token`, and use `Content-Type: application/json`.
- No CORS headers are ever sent. `OPTIONS` and `DELETE` are refused.
- A strict Content Security Policy allows only the page's own nonce-tagged script and style;
  `frame-ancestors 'none'` and `X-Frame-Options: DENY` prevent framing.
- The page builds the DOM with `textContent`; agent output, diffs and task text are never interpreted
  as HTML.
- Edited routes sent by the page are validated on the server: agents must exist in your configuration
  and billing is taken from the configuration, not from the browser.
- Request bodies are limited to 1 MB.

Another local user or process running as you can still reach `127.0.0.1`; without the session it gets
`401`. Treat the printed URL like a password and close the server when you are done. Stopping the
server with Ctrl+C cancels running agents and waits for them to stop.

- The *Setup* page only runs package-manager installs that need no administrator rights, after a
  confirmation that shows the exact command. Steps that need `sudo`/UAC or a downloaded script are only
  shown, with a copy button, to run yourself. Logins and API keys are never handled in the browser: the
  page shows the terminal command (`codex login`, `timonel keys set NAME`).
- The *Settings* page writes your user file after server-side validation and keeps a `.bak` copy. That
  file can define commands (hooks, agents), so the page is as powerful as editing the file yourself.

## Installer (`timonel start`)

- It never elevates privileges. It only runs a command by itself when the program is the package
  manager itself (`winget`, `brew`, `npm`…); anything else, including `sudo`, is printed for you to run.
- It never pipes a download into a shell. When a vendor only offers an install script, Timonel downloads
  it, shows it (terminal control characters removed, and says how many lines it did not show and
  where the full file is) with its SHA-256, and asks before running it. `--yes` never runs a script.
- Official package managers come first (winget, Homebrew, npm…). Each command is explained, with its
  approximate size, before you choose install, skip, explain or quit.
- `--dry-run` shows everything and changes nothing. `--yes` only applies to components you list with
  `--allow`.
- Every executed action is appended to `audit.log` in the data directory with the command, exit code
  and how to undo it. Completed installs are kept in `start-state.json`, so an interrupted run resumes; skipped steps are offered again next time.

## Accounts and API keys

Timonel never asks for, sees or stores the passwords or tokens of your accounts. Logins run the
vendor's official command in your terminal (`claude`, `codex login`, `gh auth login`…) and Timonel only
reads the resulting status afterwards. API keys you choose to give Timonel are typed with hidden input
and saved in the system keyring (`timonel keys set NAME`), never in YAML, logs or the repository.

## Privacy

- No telemetry. Timonel makes no network request of its own except the ones you ask for: agent CLIs and
  provider APIs you configured, package downloads in `timonel start`, and webhooks you set. A test fails
  if any command opens an unsolicited connection.
- The update check is off by default (`privacy.update_check`).
- Outputs stored in the local ledger are redacted; `privacy.store_outputs: false` stores none.
  `privacy.retention_days` and `timonel purge` delete old runs.

## MCP server

`timonel mcp` speaks JSON-RPC over stdio to the client that started it. It exposes `timonel_plan`,
`timonel_run` and `timonel_status`, only for the repository it was started in. Runs happen in
worktrees; there is no apply tool, so merging always needs a human. Errors are redacted.

## Reporting

See [SECURITY.md](https://github.com/EduMayMar/Timonel/blob/main/SECURITY.md).
