# FAQ

**Does Timonel include an AI model?**
No. It drives the coding agents you install (Claude Code, Codex, Copilot, Gemini CLI…) or model APIs
you configure, and decides which one to use for each task.

**Does it cost money?**
Timonel is free and MIT-licensed. The agents and APIs it calls are billed by their vendors under your
own account. Subscriptions are tried before pay-per-use APIs at the same level, and budgets can cap
spending.

**Can it read my passwords or tokens?**
No. Logins happen in each vendor's official tool. API keys you choose to give it go to your system
keyring. See [security and privacy](security.md).

**Does it send data anywhere?**
Only to the agents and APIs you configured, the package registries during `timonel start`, and
webhooks you set. There is no telemetry; a test enforces it.

**Will it change my code without asking?**
Work happens in a separate git worktree and branch. Nothing reaches your branch until you run
`timonel apply` or press *Apply* in the UI.

**My favourite agent is not supported.**
Add it with `timonel agents add`, or contribute a connector: [adding an agent](adding-an-agent.md).

**Is it allowed by the agents' terms of service?**
Timonel runs the official CLIs the way you would run them yourself. Read
[limits and terms](limits-and-terms.md) and each vendor's terms; you are responsible for using your
accounts within them.
