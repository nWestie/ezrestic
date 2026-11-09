#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'Usage: source %s\n' "$0" >&2
    exit 1
fi

password_file="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../secrets" && pwd)/restic_pass"

export RESTIC_PASSWORD="$(< "$password_file")"
