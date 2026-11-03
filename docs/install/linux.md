# Linux

## What you need

- **Python 3.11+** and **pipx**. On Debian/Ubuntu: `sudo apt install pipx`; on Fedora:
  `sudo dnf install pipx`; on Arch: `sudo pacman -S python-pipx`. Then `pipx ensurepath`.

## Install Timonel

```bash
pipx install git+https://github.com/EduMayMar/Timonel
timonel start
```

`timonel start` detects apt, dnf, pacman and Homebrew on Linux. System packages need `sudo`, and
Timonel never runs `sudo` for you: it prints the exact command, waits while you run it in another
terminal, and then checks the result. Agent CLIs installed with npm need no `sudo` if Node.js was
installed per user (for example with Homebrew or nvm).

A single-file binary, `timonel-linux-x64`, and a container image
(`ghcr.io/edumaymar/timonel`) are published with each
[release](https://github.com/EduMayMar/Timonel/releases).

```bash
docker run --rm -it -v "$PWD:/work" ghcr.io/edumaymar/timonel:latest plan "explain README.md"
```

The image contains Timonel and Git only; agent CLIs and their logins live on your machine, so the image
is mostly useful for `plan`, `config` and CI checks.
