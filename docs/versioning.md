# Versioning and releases

Timonel follows [Semantic Versioning](https://semver.org/):

- **Major** (`2.0.0`): a configuration key is removed or changes meaning without an automatic
  migration, a command or flag is removed, or an error code changes meaning.
- **Minor** (`1.3.0`): new commands, options, connectors or languages. Renamed configuration keys are
  migrated automatically and keep working for at least one minor release.
- **Patch** (`1.2.4`): fixes, connector flag updates and translations.

Before 1.0.0, minor releases may include breaking changes; they are listed under **Changed** in the
changelog.

Stable parts: error codes (`TMN-xxxx`) are never reused, the JSON Schema is published at
`schema/timonel.schema.json`, and connector files carry their own verification date.

## Making a release

1. Move the `Unreleased` entries in `CHANGELOG.md` under the new version and date.
2. Update `version` in `pyproject.toml`; commit.
3. Create a GitHub release with tag `vX.Y.Z`. GitHub drafts the notes from merged pull requests,
   grouped by label (`.github/release.yml`).
4. The `Release` workflow builds the wheel, the three binaries and `SHA256SUMS`, publishes to PyPI
   (trusted publishing) and pushes the container image.
5. Fill the winget, Scoop and Homebrew manifests (`packaging/README.md`) and submit them.
