# Distribution manifests

Templates for winget, Scoop and Homebrew. They point at the single-file binaries that the `Release`
workflow attaches to each GitHub release, together with `SHA256SUMS`.

## Fill them for a release

```bash
gh release download v0.2.0 --pattern SHA256SUMS
python scripts/fill_manifests.py 0.2.0 SHA256SUMS --out dist/manifests
```

`dist/manifests/` then holds ready files with the version and the real checksums. Nothing here is
published automatically: each channel needs a maintainer to submit it.

## winget

1. Fork [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
2. Copy the three files to `manifests/e/EduMayMar/Timonel/<version>/`.
3. Check them with `winget validate --manifest <folder>` and `winget install --manifest <folder>` on
   Windows, then open a pull request there.

## Scoop

Publish a bucket repository (for example `EduMayMar/scoop-timonel`) with `timonel.json` at its root.
Users then run:

```powershell
scoop bucket add timonel https://github.com/EduMayMar/scoop-timonel
scoop install timonel
```

`checkver` and `autoupdate` let `scoop` maintainers' tooling bump the version from new releases.

## Homebrew

Publish a tap repository named `EduMayMar/homebrew-timonel` with `Formula/timonel.rb`. Users then run:

```bash
brew install EduMayMar/timonel/timonel
```

Check it with `brew audit --strict --formula timonel` and `brew test timonel`. The binary is built on
the `macos-latest` runner (Apple silicon); Intel Macs should install with `pipx install timonel`.
