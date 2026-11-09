# ezrestic Project Status

## Aims of the Project

ezrestic is a small Python wrapper around the Restic command-line tool. Its
purpose is to simplify configuring and running backups across one or more
repositories, while parsing Restic's JSON output into typed Python objects.

It is designed to be usable as a library or a CLI tool.

```python
repository.backup(source_dir)
repository.get_stats()
repository.forget()
repository.prune()
```


## Implementation Strategy

### Runtime model

- `EZRestic` is the low-level Restic CLI wrapper.
- `Repository` owns a repository locator, password file, and reference to
  `EZRestic`.
- `Source` owns a source directory, its destination repositories, optional
  pre/post actions, and source-relative ignore files.
- `BackupList` owns the configured sources and the shared `EZRestic` instance.
- Dataclasses are frozen where practical.

### Configuration loading

`BackupList.from_config_file()` is the configuration boundary and is
responsible for all TOML interpretation and object construction. Do not spread
config-file context into `Source` or `Repository` constructors.

Path rules:

- `restic_pass_file` is relative to the config file.
- A non-default `restic_path` is relative to the config file.
- `source_dir` is relative to the config file.
- Local relative repository destinations are relative to the config file.
- Absolute repository destinations remain unchanged.
- Remote repository locators containing a backend scheme, such as
  `rclone:opcbackup:immich` or `sftp:user@example.com:/repo`, remain unchanged.
- `ignore_file` is relative to the resolved `source_dir`.
- Pre/post action arguments are preserved unchanged; their commands run with
  `source_dir` as the working directory.

The repository path behavior is centralized in
`BackupList._resolve_repo_locator()`. General relative path resolution is
centralized in `BackupList._resolve_relative_to()`.

### Validation and current status

- The project targets Python 3.11 or newer.
- `pytest` is the development test runner.
- Focused tests exist for local and remote repository locator resolution.
- The broader test suite is incomplete and may still contain expectations from
  the previous API, especially around password-file ownership.
- When changing configuration behavior, prefer focused tests that verify the
  resolved values without invoking the Restic executable.

## Planned Features
- support restore with a configurable root
- add --version 
- show help when run without args
- multi-repo status should print as they come in
- write tests:
  - failure cases for bad config
  - ignore files
