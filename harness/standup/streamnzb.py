#!/usr/bin/env python3
"""Configure streamnzb for a round and mint its URL. Clears its `shakedown`.

    python3 harness/standup/streamnzb.py

streamnzb bootstraps its news account and its indexers straight from the
environment -- `PROVIDER_1_*` and `INDEXER_<n>_*`, which the compose file fills
from the bench account and the parity set -- so unlike the rest of the field it
comes up already pointed at the right catalogue. What the environment cannot
set is everything this script does.

Three of its surfaces are read-only or lie, and each one cost a round's worth
of confusion before it was found:

  * `PUT /api/config` accepts a `streams` key, answers 200, and discards it.
    `config_apply.go` overwrites the submitted streams with the current ones,
    so a size cap written that way reads back as saved and is not applied.
    Stream settings go to `PUT /api/streams/configs` instead.
  * `POST /api/streams` takes a username and ignores every other field, so a
    stream cannot be created already configured. It is created, then set.
  * `playback_startup_timeout_seconds` is capped at 60 by validation. Its
    default of 5 is far too short for a multi-volume RAR, which fails outright
    rather than slowly, so this raises it to the maximum the app allows and
    that maximum is worth stating in the round.

**The size cap this writes does not take effect, and that is the finding.**
`limits.max_size_gb` is set on a filter profile, the profile is bound to the
stream, and both read back. The served list is unchanged: 158 streams for The
Godfather with 129 of them over the cap, measured on a title the instance had
never searched, so it is not a stale cache. A `reject all` rule
(`when: "true"`, `action: "reject"`) leaves the same 157 streams standing, and
`results_mode` has no effect either, so no stream-level setting reaches the
addon's stream list in 5.17.0. It is written anyway -- it costs nothing and a
later version may honour it -- and the round caps at the pick instead, for
every target, which is why docs/design.md's parity rule 2 reads the way it
does.

The cap is in **decimal** GB while the round's cap is 6 GiB, so the number
written is 6.442450944 and not 6. A profile is per content kind; `default` is
the entry the others inherit.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

PROFILE = "bench"


def admin_token(data_dir):
    path = os.path.join(os.path.expanduser(data_dir), "config.json")
    if not os.path.exists(path):
        raise SystemExit(f"no {path}: start the container first, it writes its own config")
    with open(path) as handle:
        return json.load(handle)["admin_token"]


def call(base, token, path, method="GET", data=None, timeout=30):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "User-Agent": common.USER_AGENT}
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(base + path, headers=headers, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as problem:
        return problem.code, problem.read().decode("utf-8", "replace")


def set_limits(base, token, cap_bytes, timeout_s):
    status, body = call(base, token, "/api/config")
    if status != 200:
        raise SystemExit(f"cannot read the config: http {status}: {body[:200]}")
    config = json.loads(body)
    config["playback_startup_timeout_seconds"] = timeout_s
    profiles = [p for p in (config.get("filter_profiles") or []) if p.get("name") != PROFILE]
    profiles.append({"name": PROFILE,
                     "limits": {"default": {"max_size_gb": cap_bytes / 1e9}}})
    config["filter_profiles"] = profiles
    status, body = call(base, token, "/api/config", "PUT", config)
    if status != 200:
        raise SystemExit(f"cannot save the config: http {status}: {body[:300]}")
    print(f"filter profile {PROFILE!r}: max_size_gb={cap_bytes / 1e9:.9f} "
          f"({cap_bytes / 1024 ** 3:.0f} GiB), playback timeout {timeout_s}s")


def bind_stream(base, token, indexers, connections):
    status, body = call(base, token, "/api/streams")
    if status != 200:
        raise SystemExit(f"cannot list streams: http {status}: {body[:200]}")
    streams = json.loads(body)
    if not streams:
        raise SystemExit("streamnzb has no stream to configure")
    entry = streams[0]
    username = entry["username"]
    want = {key: value for key, value in entry.items() if key != "token"}
    want["filter_profile_name"] = PROFILE
    want["indexer_selections"] = [i["label"] for i in indexers]
    want["provider_selections"] = ["bench"]
    want["provider_connection_limits"] = {"bench": connections}
    status, body = call(base, token, "/api/streams/configs", "PUT", {username: want})
    if status != 200:
        raise SystemExit(f"cannot save stream config: http {status}: {body[:300]}")

    status, body = call(base, token, "/api/streams")
    back = [s for s in json.loads(body) if s["username"] == username][0]
    if back.get("filter_profile_name") != PROFILE:
        raise SystemExit("the stream did not keep its filter profile. Do not measure this: "
                         "the indexer selection lives in the same object")
    print(f"stream {username!r} bound to profile {PROFILE!r}, "
          f"indexers {back.get('indexer_selections')}")
    return back["token"]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:7000")
    parser.add_argument("--data-dir", default="~/stremio-addon-bench/run/streamnzb/data")
    parser.add_argument("--connections", type=int, default=15)
    parser.add_argument("--playback-timeout-s", type=int, default=60,
                        help="60 is the maximum this app's own validation allows")
    args = parser.parse_args()

    with open(common.TITLES) as handle:
        cap_bytes = json.load(handle)["playable_cap_bytes"]
    indexers = common.parity_indexers()
    print(f"parity indexers: {[i['label'] for i in indexers]}")

    token = admin_token(args.data_dir)
    set_limits(args.base, token, cap_bytes, args.playback_timeout_s)
    stream_token = bind_stream(args.base, token, indexers, args.connections)

    manifest = f"{args.base}/{stream_token}/manifest.json"
    stream = f"{args.base}/{stream_token}/stream/{{type}}/{{id}}.json"
    common.record_endpoint("streamnzb", manifest, stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
