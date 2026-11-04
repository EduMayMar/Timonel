# macOS

## What you need

- **Homebrew** ([brew.sh](https://brew.sh)) is the easiest way to get the rest.
- **Python 3.11+** and **pipx**: `brew install python pipx` then `pipx ensurepath`.

## Install Timonel

```bash
pipx install git+https://github.com/EduMayMar/Timonel
timonel start
```

`timonel start` uses Homebrew for Git, GitHub CLI and Node.js, and npm for the agent CLIs.

A single-file binary for Apple silicon, `timonel-macos-arm64`, is attached to each
[release](https://github.com/EduMayMar/Timonel/releases). Check it with
`shasum -a 256 timonel-macos-arm64` against `SHA256SUMS`, then
`chmod +x timonel-macos-arm64 && mv timonel-macos-arm64 ~/.local/bin/timonel`. Because it is not
notarized, macOS may block the first run; allow it in System Settings → Privacy & Security, or use
pipx instead. Intel Macs: there is no binary; use pipx.
