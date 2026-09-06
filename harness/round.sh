#!/usr/bin/env bash
# The advertised entry point for a round. Everything is in harness/round.py;
# this exists so docs/running.md's `./harness/round.sh` is a real command.
set -euo pipefail
# -u so a round driven over ssh into a log file shows progress as it
# happens rather than in one block when it finishes
exec python3 -u "$(dirname "$0")/round.py" "$@"
