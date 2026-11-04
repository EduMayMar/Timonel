# Windows 11

## What you need

- **Python 3.11+**: `winget install --id Python.Python.3.12 -e`, or the installer from
  [python.org](https://www.python.org/downloads/windows/) with "Add python.exe to PATH" ticked.
- **pipx** (isolated installs per user): `py -m pip install --user pipx` then `py -m pipx ensurepath`.
  Close and reopen the terminal afterwards.

`timonel start` installs the rest for you (Git, GitHub CLI, Node.js, agent CLIs) with **winget**,
**Scoop** or **Chocolatey**, whichever you have, preferring winget.

## Install Timonel

```powershell
pipx install git+https://github.com/EduMayMar/Timonel
timonel start
```

Or download `timonel-windows-x64.exe` from the [releases page](https://github.com/EduMayMar/Timonel/releases),
check it against `SHA256SUMS` (`Get-FileHash timonel-windows-x64.exe`), rename it to `timonel.exe` and
put it in a folder on your `PATH`.

## Things that are different on Windows

- After the assistant installs something, it re-reads `PATH` from the registry, so the new command
  works without restarting the terminal. If a command is still "not found", open a new terminal.
- Some winget installs show a Windows confirmation (UAC). Timonel never elevates by itself: accept it
  only if the command is the one it showed you.
- Timonel only runs programs from folders in `PATH`, never from the current folder.
- Desktop notifications use a PowerShell balloon; enable them with
  `timonel config set notifications.desktop true`.
- Known limitation: on Windows a crashed agent's child processes are stopped with the agent, but
  detecting orphans that escape the process tree is not supported.
