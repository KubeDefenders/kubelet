#!/bin/bash
# Shared helpers for repository scripts

if [ -z "${REPO_ROOT:-}" ]; then
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

SCRIPTS_ROOT="$REPO_ROOT/scripts"
