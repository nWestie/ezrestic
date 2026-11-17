# ezrestic Copilot Instructions

## Project Purpose

ezrestic is a small Python library and CLI around the Restic command-line
tool. It simplifies configuring and running backups to one or more
repositories and parses Restic JSON output into typed Python objects.

The preferred library API is repository-oriented:

```python
repository.backup(source_dir)
repository.get_stats()
repository.forget()
repository.prune()
```

Keep the project small and direct. Do not introduce abstractions, frameworks,
or new dependencies without a concrete need.

## Architecture

### Runtime objects

- `EZRestic` is the low-level Restic process wrapper. It owns the configured
  Restic executable path and executes commands.
- `Repository` owns a repository locator, its password-file path, and a
  reference to an `EZRestic` instance. Repository operations should be methods
  on `Repository`, not functions requiring the repository and client to be
  passed repeatedly.
- `Source` owns a source directory, destination `Repository` objects,
  optional pre/post shell actions, and source-relative ignore files.
- `BackupList` owns the configured sources and the shared `EZRestic` instance.
- Use frozen dataclasses where practical. Prefer immutable collections such as
  tuples for constructed repository collections.
- Avoid global mutable state. A shared `EZRestic` instance is dependency
  sharing, not global state, and should be constructed explicitly.
- Because the program is short-lived and single purpose, state is stored in frozen dataclasses whenever possible. 
- Results are requested as JSON from Restic operations, and dedicated result
  types parse that output into dataclasses. Repository methods own command
  construction, execution, and multi-command orchestration; result types own
  output decoding, schema validation, normalization, and typed construction.
  Keep `from_json()` for decoded JSON-shaped data and use `from_output()` (or
  `from_outputs()` for a result requiring multiple commands) for CLI output.
- A nonzero exit result returned by Restic must raise `ResticError` (or its
  relevant subclass), preserving the Restic error details.
- Invalid or malformed successful CLI output must raise `ResticParseError`,
  which inherits from `ResticError`.
- Failure to start or invoke the Restic executable should raise
  `ResticCommandError`, also inheriting from `ResticError`. Do not hide these
  failures as parse errors or return success-shaped fallback values.

### Configuration boundary

`BackupList.from_config_file()` is the single configuration boundary. It is
responsible for reading TOML, validating required fields, resolving paths, and
constructing `EZRestic`, `Repository`, and `Source` objects.

Runtime objects should receive already-resolved values. Keep configuration-specific helper methods on `BackupList` unless the project grows enough to justify a separate loader.

### Path rules

All relative paths in the configuration are relative to the configuration file
unless an explicit exception is listed below:

- `restic_pass_file`: relative to the config file.
- Non-default `restic_path`: relative to the config file.
- `source_dir`: relative to the config file.
- Local relative repository destinations: relative to the config file.
- Absolute repository destinations: unchanged.
- Remote repository locators containing a backend scheme remain unchanged.
  Examples include `rclone:opcbackup:immich` and
  `sftp:user@example.com:/repo`.
- `ignore_file`: relative to the resolved `source_dir`.
- Pre/post action command arguments: preserve unchanged. Run the commands with
  `source_dir` as the working directory.

Keep general relative-path handling in `BackupList._resolve_relative_to()` and
repository-local-vs-remote handling in `BackupList._resolve_repo_locator()`.
Do not resolve remote locators as filesystem paths.

### Credentials and shell helpers

Password files belong to `Repository`, not to global state. Do not expose
secrets in logs, error messages, tests, or committed configuration.

The optional shell helpers under `util/` source the repository's
`secrets/restic_pass` into the standard `RESTIC_PASSWORD` environment variable.
They must resolve that file relative to the helper's own location rather than
the caller's working directory.

## Coding and Change Guidelines

- Target Python 3.11 or newer.
- Dependencies are managed through UV. use it to call the app as an executable and run tests
- Preserve type safety and use existing project patterns.
- Make surgical changes and avoid unrelated cleanup.
- Surface invalid configuration and command failures explicitly; do not silently
  fall back to success-shaped values.
- Prefer standard-library solutions. The runtime package has no dependencies.
- Keep pre/post action arguments unchanged; do not reinterpret individual
  arguments as config-relative paths.
- When changing a component's structure or public API, update all existing
  tests that exercise that component. Legacy tests are expected to be updated
  when the implementation structure changes; they must not be left failing
  merely because they encode the previous API.
- Add focused tests for configuration and path behavior without invoking the
  Restic executable. Use temporary directories for path-resolution tests.

## Validation

Use the smallest relevant validation command:

```powershell
uv run pytest
```

For a focused test:

```powershell
uv run pytest test_ez_restic.py -k <test-name>
```

For syntax-only validation when appropriate:

```powershell
python -m py_compile src/ezrestic/ez_restic.py
```

The test suite is still incomplete, but incomplete coverage is not a reason to
skip updating affected legacy tests. Prefer focused tests for newly changed
behavior and report unrelated pre-existing failures separately.

## Planned Direction

- Complete the test suite, including invalid-configuration and ignore-file
  cases.
- Add restore functionality from a repository to a user-selected destination
  folder.
- Support restore destination selection through command arguments and
  potentially an interactive CLI flow.
