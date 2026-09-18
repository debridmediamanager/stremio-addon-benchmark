#!/usr/bin/env bash
# Build every Stremio benchmark target from an explicit upstream revision.
#
# The repository checkouts live outside the harness so a run can preserve the
# exact source trees it built. Each checkout is pinned to the requested remote
# ref before any build starts, and the resulting image tag includes the commit.
set -euo pipefail

SRC="${SRC:-$HOME/sab/src}"
CACHE="${CACHE:-$HOME/sab/gomodcache}"
OUT="${OUT:-$HOME/sab}"
ROUND="${ROUND:-round-current}"
BUILD_COMET="${BUILD_COMET:-1}"
STREAMNZB_RELEASE_IMAGE="${STREAMNZB_RELEASE_IMAGE:-ghcr.io/gaisberg/streamnzb:latest}"
export DOCKER_BUILDKIT=1

mkdir -p "$CACHE" "$OUT"

pin() {
  local directory="$1" ref="${2:-refs/remotes/origin/main}"
  git -C "$directory" fetch --quiet origin
  git -C "$directory" checkout --quiet --detach "$ref"
  git -C "$directory" submodule update --init --recursive --depth 1 --quiet 2>/dev/null || true
  git -C "$directory" rev-parse --short=8 HEAD
}

STREMTHRU_COMMIT="$(pin "$SRC/stremthru")"
STREAMNZB_COMMIT="$(pin "$SRC/streamnzb")"
AIOSTREAMS_COMMIT="$(pin "$SRC/AIOStreams")"
COMET_COMMIT="$(pin "$SRC/comet" refs/remotes/origin/feat/usenet)"

echo "PINNED stremthru=$STREMTHRU_COMMIT streamnzb=$STREAMNZB_COMMIT aiostreams=$AIOSTREAMS_COMMIT comet=$COMET_COMMIT"

echo "=== stremthru $STREMTHRU_COMMIT: dashboard ==="
docker run --rm -v "$SRC/stremthru:/app" -w /app node:20-alpine sh -c '
  corepack enable >/dev/null 2>&1
  corepack prepare pnpm@10.17.0 --activate >/dev/null 2>&1
  pnpm install --frozen-lockfile
  pnpm run dash:build'
test -d "$SRC/stremthru/apps/dash/.output/public"
docker build -t "stremthru:${ROUND}-${STREMTHRU_COMMIT}" "$SRC/stremthru"

echo "=== streamnzb $STREAMNZB_COMMIT: exact-revision release image ==="
# StreamNZB's release build embeds the project's TMDB/TVDB metadata fallbacks.
# A source build without those private release inputs boots cleanly but returns
# an empty list before querying an indexer. Require the official image to attest
# the exact checked-out revision, then give it the same immutable round tag.
docker pull "$STREAMNZB_RELEASE_IMAGE"
STREAMNZB_IMAGE_REVISION="$(docker image inspect "$STREAMNZB_RELEASE_IMAGE" \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
[[ "$STREAMNZB_IMAGE_REVISION" == "$(git -C "$SRC/streamnzb" rev-parse HEAD)" ]] || {
  echo "streamnzb release image is $STREAMNZB_IMAGE_REVISION, source is $(git -C "$SRC/streamnzb" rev-parse HEAD)" >&2
  exit 1
}
docker tag "$STREAMNZB_RELEASE_IMAGE" "streamnzb:${ROUND}-${STREAMNZB_COMMIT}"
STREAMNZB_IMAGE_ID="$(docker image inspect "$STREAMNZB_RELEASE_IMAGE" --format '{{.Id}}')"

echo "=== aiostreams $AIOSTREAMS_COMMIT ==="
docker build -t "aiostreams:${ROUND}-${AIOSTREAMS_COMMIT}" "$SRC/AIOStreams"

if [[ "$BUILD_COMET" == 1 ]]; then
  echo "=== comet $COMET_COMMIT ==="
  docker build -t "comet:${ROUND}-${COMET_COMMIT}" "$SRC/comet"
fi

for image in \
  "stremthru:${ROUND}-${STREMTHRU_COMMIT}" \
  "streamnzb:${ROUND}-${STREAMNZB_COMMIT}" \
  "aiostreams:${ROUND}-${AIOSTREAMS_COMMIT}"; do
  docker image inspect "$image" >/dev/null
done
if [[ "$BUILD_COMET" == 1 ]]; then
  docker image inspect "comet:${ROUND}-${COMET_COMMIT}" >/dev/null
fi

jq -n \
  --arg round "$ROUND" \
  --arg built_at "$(date -u +%FT%TZ)" \
  --arg stremthru "$STREMTHRU_COMMIT" \
  --arg streamnzb "$STREAMNZB_COMMIT" \
  --arg streamnzb_image "$STREAMNZB_RELEASE_IMAGE" \
  --arg streamnzb_image_id "$STREAMNZB_IMAGE_ID" \
  --arg aiostreams "$AIOSTREAMS_COMMIT" \
  --arg comet "$COMET_COMMIT" \
  '{round:$round,built_at:$built_at,built_from:"upstream branch heads on the day",commits:{stremthru:$stremthru,streamnzb:$streamnzb,aiostreams:$aiostreams,comet:$comet},artifacts:{streamnzb:{source:$streamnzb_image,image_id:$streamnzb_image_id,revision_verified:true}}}' \
  > "$OUT/versions-${ROUND}-build.json"

cat "$OUT/versions-${ROUND}-build.json"
