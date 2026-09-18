#!/usr/bin/env bash
# Create clean, reproducible run directories for the current upstream builds.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${RUN:-$HOME/stremio-addon-bench/run}"
BUILD_MANIFEST="${BUILD_MANIFEST:-$HOME/sab/versions-round3-build.json}"
ZURG_BINARY="${ZURG_BINARY:?set ZURG_BINARY to the zurg artifact shared with the mount round}"
CONNS="${CONNS:-10}"
AIOSTREAMS_BOOT_CONNS="${AIOSTREAMS_BOOT_CONNS:-$((CONNS > 1 ? CONNS - 1 : 1))}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RETIRE_ROOT="${RETIRE_ROOT:-$RUN/.retired/$STAMP}"

image() {
  local target="$1"
  local commit
  commit="$(jq -r ".commits.$target" "$BUILD_MANIFEST")"
  printf '%s:%s-%s' "$target" "$(jq -r .round "$BUILD_MANIFEST")" "$commit"
}

retire() {
  local path="$1" destination
  [[ -e "$path" ]] || return 0
  destination="$RETIRE_ROOT/${path#"$RUN"/}"
  mkdir -p "$(dirname "$destination")"
  mv "$path" "$destination"
}

for state in data nzbs logs target.log target.pid; do
  retire "$RUN/zurg/$state"
done

mkdir -p "$RUN"/{zurg,stremthru,streamnzb,aiostreams,comet}
for target in stremthru streamnzb aiostreams comet; do
  retire "$RUN/$target/data"
  mkdir -p "$RUN/$target/data"
done

install -m 0755 "$ZURG_BINARY" "$RUN/zurg/zurg"
python3 "$ROOT/harness/standup/zurg.py" --dir "$RUN/zurg" --connections "$CONNS"

STREMTHRU_IMAGE="$(image stremthru)"
STREAMNZB_IMAGE="$(image streamnzb)"
AIOSTREAMS_IMAGE="$(image aiostreams)"
COMET_IMAGE="$(image comet)"

cat > "$RUN/stremthru/compose.yml" <<YAML
services:
  stremthru:
    image: $STREMTHRU_IMAGE
    container_name: sab-stremthru
    ports: ["8484:8080"]
    environment:
      # Only the Usenet addon and its required services belong in this
      # benchmark. The default feature set also starts torrent/hash-list
      # importers, which consumed a core and hundreds of MiB during a real
      # round without contributing to the measured request path.
      STREMTHRU_FEATURE: "newz,stremio_newz,vault"
      STREMTHRU_VAULT_SECRET: "sab-round-vault-secret-not-an-account"
      STREMTHRU_NEWZ_MAX_CONNECTION_PER_STREAM: "$CONNS"
      STREMTHRU_NEWZ_SEGMENT_CACHE_SIZE: "4GB"
      STREMTHRU_BASE_URL: "http://127.0.0.1:8484"
      STREMTHRU_LOG_LEVEL: "INFO"
      STREMTHRU_AUTH_ADMIN: "bench"
      STREMTHRU_AUTH: "bench:sabround1pass"
    volumes: ["./data:/app/data"]
    restart: "no"
YAML

cat > "$RUN/streamnzb/compose.yml" <<YAML
services:
  streamnzb:
    image: $STREAMNZB_IMAGE
    container_name: sab-streamnzb
    ports: ["7000:7000"]
    env_file: [.env]
    environment:
      ADDON_PORT: "7000"
      ADDON_BASE_URL: "http://127.0.0.1:7000"
      LOG_LEVEL: "INFO"
      ADMIN_USERNAME: "bench"
    volumes: ["./data:/app/data"]
    restart: "no"
YAML

cat > "$RUN/aiostreams/compose.yml" <<YAML
services:
  aiostreams:
    image: $AIOSTREAMS_IMAGE
    container_name: sab-aiostreams
    ports: ["3010:3000"]
    environment:
      PORT: "3000"
      BASE_URL: "http://127.0.0.1:3010"
      SECRET_KEY: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      ADDON_PASSWORD: "sabround1pass"
      LOG_LEVEL: "info"
      AIOSTREAMS_AUTH: "bench:sabround1pass"
      CONFIG_ACCESS_KEY: "sabround1pass"
    volumes: ["./data:/app/data"]
    restart: "no"
YAML

cat > "$RUN/comet/compose.yml" <<YAML
services:
  comet:
    image: $COMET_IMAGE
    container_name: sab-comet
    init: true
    ports: ["8091:8000"]
    env_file: [.env]
    volumes: ["./data:/app/data"]
    tmpfs:
      - /tmp:size=64m,mode=1777
      - /run/comet/usenet:size=16m,mode=0700
    restart: "no"
YAML

ROOT="$ROOT" RUN="$RUN" CONNS="$CONNS" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["ROOT"], "harness", "standup"))
import common
account = common.account()
indexers = common.parity_indexers()
lines = [
    "PROVIDER_1_NAME=bench",
    f"PROVIDER_1_HOST={account['host']}",
    f"PROVIDER_1_PORT={account['port']}",
    f"PROVIDER_1_USERNAME={account['user']}",
    f"PROVIDER_1_PASSWORD={account['password']}",
    f"PROVIDER_1_CONNECTIONS={os.environ['CONNS']}",
    f"PROVIDER_1_SSL={'true' if account['tls'] else 'false'}",
    f"PROVIDER_1_TLS={'true' if account['tls'] else 'false'}",
]
for number, indexer in enumerate(indexers, 1):
    lines.extend([
        f"INDEXER_{number}_NAME={indexer['label']}",
        f"INDEXER_{number}_URL={common.api_url(indexer)}",
        f"INDEXER_{number}_API_KEY={indexer['api_key']}",
    ])
path = os.path.join(os.environ["RUN"], "streamnzb", ".env")
with open(path, "w") as handle:
    handle.write("\n".join(lines) + "\n")
os.chmod(path, 0o600)
print(f"streamnzb environment: {len(indexers)} parity indexers, {os.environ['CONNS']} connections")

server = [{
    "name": "bench",
    "host": account["host"],
    "port": int(account["port"]),
    "tls_mode": "implicit" if account["tls"] else "plaintext",
    "username": account["user"],
    "password": account["password"],
    "connections": int(os.environ["CONNS"]),
    "priority": 0,
    "backup": False,
    "pipeline": 16,
}]
lines = [
    "FASTAPI_HOST=0.0.0.0",
    "FASTAPI_PORT=8000",
    "FASTAPI_WORKERS=1",
    "DATABASE_TYPE=sqlite",
    "DATABASE_PATH=data/comet.db",
    "ANIME_MAPPING_ENABLED=False",
    "USENET_ENABLED=True",
    "USENET_ENGINE_ENABLED=True",
    "USENET_ENGINE_REQUIRED=True",
    "USENET_NATIVE_ACCESS_TOKEN=sabround1token",
    f"USENET_NATIVE_SERVERS={json.dumps(server, separators=(',', ':'))}",
]
path = os.path.join(os.environ["RUN"], "comet", ".env")
with open(path, "w") as handle:
    handle.write("\n".join(lines) + "\n")
os.chmod(path, 0o600)
print(f"comet environment: {len(indexers)} parity indexers, {os.environ['CONNS']} connections")
PY

wait_http() {
  local url="$1"
  for _ in $(seq 1 90); do
    curl -sS --max-time 3 -o /dev/null "$url" && return 0
    sleep 2
  done
  echo "never became ready: $url" >&2
  return 1
}

docker compose -f "$RUN/stremthru/compose.yml" up -d
wait_http http://127.0.0.1:8484/
python3 "$ROOT/harness/standup/stremthru.py" --connections "$CONNS"
docker compose -f "$RUN/stremthru/compose.yml" stop

docker compose -f "$RUN/streamnzb/compose.yml" up -d
wait_http http://127.0.0.1:7000/
python3 "$ROOT/harness/standup/streamnzb.py" --connections "$CONNS"
docker compose -f "$RUN/streamnzb/compose.yml" stop

docker compose -f "$RUN/aiostreams/compose.yml" up -d
wait_http http://127.0.0.1:3010/api/v1/status
python3 "$ROOT/harness/standup/aiostreams.py" --connections "$AIOSTREAMS_BOOT_CONNS"
docker compose -f "$RUN/aiostreams/compose.yml" stop

docker compose -f "$RUN/comet/compose.yml" up -d
wait_http http://127.0.0.1:8091/
python3 "$ROOT/harness/standup/comet.py" --base http://127.0.0.1:8091
docker compose -f "$RUN/comet/compose.yml" stop

echo "prepared zurg, stremthru, streamnzb and comet at $CONNS configured connections"
echo "prepared aiostreams at $AIOSTREAMS_BOOT_CONNS startup connections; the round raises it to $CONNS after boot"
