# Security policy

Timonel launches coding agents that can modify files. Security reports are taken seriously.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
<https://github.com/EduMayMar/Timonel/security/advisories/new>.
Do not open a public issue. Include the version (`timonel --version`), your OS and the steps to reproduce.
You will get an answer within 7 days.

## Supported versions

Only the latest release receives fixes.

## Scope

In scope: the web UI (authentication, CSRF, DNS rebinding, XSS), command construction and process
launching, secret handling and redaction, path and symlink checks on agent changes, the MCP server.

Out of scope: the behaviour of the third-party agent CLIs themselves, and anything that requires an
attacker who already runs code as your user.

See [docs/security.md](docs/security.md) for the threat model and the defences in place.
