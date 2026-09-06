#!/usr/bin/env bash
# The advertised entry point for a round. Everything is in harness/round.py;
# this exists so docs/running.md's `./harness/round.sh` is a real command.
set -euo pipefail
exec python3 "$(dirname "$0")/round.py" "$@"
