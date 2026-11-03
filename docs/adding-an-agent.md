# Adding a new agent

An agent is data, not code. If the CLI can run one prompt non-interactively and exit, you can add it
in your `routes.yaml`.

## 1. Read the real help

Install the CLI and read `--help` and its official docs. Write down:

- how to run one prompt and exit, and whether the prompt can come from stdin;
- how to choose the model and the reasoning effort;
- how to restrict it to read-only and how to allow edits without prompting;
- the output format (plain text, one JSON object, or JSON lines) and where cost is reported;
- how to check the login and the version;
- what it prints when a usage limit is hit.

If a flag only appears in blog posts or cheat sheets, mark the agent `verified: false`.

## 2. Describe it

```yaml
agents:
  mycli:
    command: mycli
    billing: subscription
    verified: false
    parser: text
    prompt_via: arg
    base_args: ["run", "--no-color"]
    read_args: ["--read-only"]
    write_args: ["--allow-edits"]
    model_args: ["--model", "{model}"]
    effort_args: []
    prompt_args: ["--prompt", "{prompt}"]
    known_models: [small, large]
    rate_limit_patterns: ["out of credits"]
    detect:
      version: ["--version"]
      auth: ["whoami"]
```

Prefer `prompt_via: stdin` when the CLI supports it: long prompts stay out of the process list, and on
Windows a batch-file shim cannot misread them.

If the CLI only prints output when it sees a terminal, add `pty: true` (and keep `prompt_via: arg`).

If it needs a secret, use `env_from` so the key is read from your environment or keyring and given
only to this process:

```yaml
    env_from:
      MYCLI_TOKEN: MYCLI_API_KEY
```

## 3. Put it on a ladder

```yaml
tiers:
  1:
    - {agent: mycli, model: small, effort: ""}
    - {agent: copilot, model: auto, effort: low}
```

Remember that `tiers:` replaces the default tiers completely; copy the tiers you want to keep.

## 4. Check it

```bash
timonel validate
timonel doctor
timonel doctor --smoke
timonel plan "explain README.md"
```

`--smoke` runs a real, tiny, read-only prompt per agent and reports the exit code, whether the output
parsed and the first line of the reply.

## 5. Contribute it

If the agent is useful to others, add it to `src/timonel/default_routes.yaml`, add a row to
[agents.md](agents.md) with the version you read, and make sure the fake CLI in
`tests/fakes/fake_agent.py` can emulate its output format so the engine tests cover it.
