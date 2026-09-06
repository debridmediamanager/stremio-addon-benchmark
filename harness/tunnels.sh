#!/usr/bin/env bash
# Put every target on the player's loopback, for the client plane.
#
# Stremio's shell is served from https://app.strem.io, so a plain-http addon on
# any other host is blocked as mixed content: it installs, renders no streams,
# and reports nothing. Chromium exempts localhost, so each target is forwarded
# onto the player's own loopback and installed from there.
#
#   ./harness/tunnels.sh up   ben@windows zen
#   ./harness/tunnels.sh down ben@windows
#
# The debugger is forwarded the other way, so the harness can drive the player.
set -euo pipefail

action="${1:-up}"
player="${2:-ben@windows}"
bench="${3:-zen}"
# every target's port, and the CDP port last
ports=(9998 3010 8484 7000)
cdp=9223

case "$action" in
  up)
    for port in "${ports[@]}"; do
      pkill -f "ssh -f -N .*-R ${port}:${bench}:${port}" 2>/dev/null || true
      ssh -f -N -o ExitOnForwardFailure=yes -o ConnectTimeout=20 \
          -R "${port}:${bench}:${port}" "$player" \
        && echo "forwarded ${bench}:${port} -> player 127.0.0.1:${port}" \
        || echo "! could not forward ${port} (is the target running?)"
    done
    pkill -f "ssh -f -N .*-L ${cdp}:127.0.0.1:${cdp}" 2>/dev/null || true
    ssh -f -N -o ExitOnForwardFailure=yes -o ConnectTimeout=20 \
        -L "${cdp}:127.0.0.1:${cdp}" "$player" \
      && echo "debugger on 127.0.0.1:${cdp}"
    ;;
  down)
    for port in "${ports[@]}"; do
      pkill -f "ssh -f -N .*-R ${port}:" 2>/dev/null || true
    done
    pkill -f "ssh -f -N .*-L ${cdp}:127.0.0.1:${cdp}" 2>/dev/null || true
    echo "tunnels closed"
    ;;
  *)
    echo "usage: $0 {up|down} [player-ssh-target] [bench-host]" >&2
    exit 2
    ;;
esac
