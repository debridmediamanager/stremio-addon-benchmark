#!/usr/bin/env bash
# The client plane, for every target, driven from the operator's machine.
#
# It cannot run where the protocol round runs. The bench host has no route to
# the player -- it has no credentials for it -- and the player's Stremio must
# reach each target on its own loopback, because the shell is served from
# https://app.strem.io and a plain-http addon anywhere else is blocked as mixed
# content. So this runs from the machine that can reach both, starts one target
# at a time on the bench host over ssh, forwards it onto the player's loopback,
# drives Stremio, and stops it again.
#
#   ./harness/client-round.sh round1
#   SAB_TARGETS="zurg stremthru" ./harness/client-round.sh round1
set -uo pipefail

round="${1:-round1}"
bench="${SAB_BENCH:-ben@zen}"
bench_host="${SAB_BENCH_HOST:-zen}"
player="${SAB_PLAYER:-ben@windows}"
cdp="${SAB_CDP_PORT:-9223}"
targets="${SAB_TARGETS:-aiostreams stremthru streamnzb zurg}"
here="$(cd "$(dirname "$0")/.." && pwd)"

port_of() { python3 -c "import sys;sys.path.insert(0,'$here/harness');import targets;print(targets.TARGETS['$1']['port'])"; }
launch_of() { python3 -c "import sys;sys.path.insert(0,'$here/harness');import targets;print(targets.TARGETS['$1'].get('launch','compose'))"; }

start_target() {
  if [ "$(launch_of "$1")" = "command" ]; then
    ssh "$bench" "cd ~/stremio-addon-bench/run/$1 && nohup ./zurg --config config.yml > target.log 2>&1 & echo \$! > ~/stremio-addon-bench/run/$1/target.pid" >/dev/null 2>&1
  else
    ssh "$bench" "cd ~/stremio-addon-bench/run/$1 && docker compose up -d" >/dev/null 2>&1
  fi
}
stop_target() {
  if [ "$(launch_of "$1")" = "command" ]; then
    ssh "$bench" "kill \$(cat ~/stremio-addon-bench/run/$1/target.pid 2>/dev/null) 2>/dev/null; rm -f ~/stremio-addon-bench/run/$1/target.pid" >/dev/null 2>&1
  else
    ssh "$bench" "cd ~/stremio-addon-bench/run/$1 && docker compose stop" >/dev/null 2>&1
  fi
}

# the debugger, once for the whole run
pkill -f "ssh -f -N .*-L ${cdp}:127.0.0.1:${cdp}" 2>/dev/null || true
ssh -f -N -o ExitOnForwardFailure=yes -o ConnectTimeout=20 -L "${cdp}:127.0.0.1:${cdp}" "$player" \
  || { echo "! no debugger on the player; is Stremio running with remote debugging?"; exit 1; }
echo "debugger forwarded from $player"

for name in $targets; do
  port="$(port_of "$name")"
  echo "=== $name (port $port) ==="
  start_target "$name"
  # the target has to answer before the player is asked to install it
  for _ in $(seq 1 40); do
    if ssh "$bench" "curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:${port}/" 2>/dev/null | grep -qE '^[2345]'; then break; fi
    sleep 5
  done
  pkill -f "ssh -f -N .*-R ${port}:" 2>/dev/null || true
  if ssh -f -N -o ExitOnForwardFailure=yes -o ConnectTimeout=20 -R "${port}:${bench_host}:${port}" "$player"; then
    python3 -u "$here/harness/client.py" --target "$name" --round "$round" \
      --addon-host 127.0.0.1 --fetch-host "$bench_host" --cdp-port "$cdp"
  else
    echo "! could not forward $port onto the player's loopback; $name has no client rows"
  fi
  pkill -f "ssh -f -N .*-R ${port}:" 2>/dev/null || true
  stop_target "$name"
done
echo "client plane done: python3 harness/report.py --round $round"
