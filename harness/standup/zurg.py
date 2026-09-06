#!/usr/bin/env python3
"""Write zurg's bench configuration and record its URL. Clears its `shakedown`.

    python3 harness/standup/zurg.py --dir ~/stremio-addon-bench/run/zurg

zurg is the only target in the field that is a binary rather than a container,
and the only one whose configuration is a single file. That makes it the
easiest to stand up and the easiest to get subtly wrong, because two of its
mistakes are silent:

  * **an indexer entry with an empty `api_key` is dropped without an error.**
    The only sign is the startup line, which reads `with 2 indexer(s)` when you
    configured three. This writes the count it expects so the check is
    mechanical.
  * **`/version` is not a route.** Liveness has to be probed through the
    manifest, which is also the only thing that proves the token is right.

The addon token is generated on first boot and printed once. It is pinned here
instead, so the manifest URL is known before the process starts and a round
does not depend on scraping a log line.

No `max_size_gb`: the round caps at the pick for every target alike, so that
`n_streams` and `picked_rank` compare unfiltered rankings rather than
configurations. See docs/design.md, parity rule 2.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

TOKEN = "sabround1zurgaddontoken00000000ff"


def config_yaml(account, indexers, port, connections, token):
    lines = [
        "# Written by harness/standup/zurg.py for the addon benchmark.",
        "# The news account comes from the bench account file; the indexers are",
        "# the measured parity set. Neither is typed in here by hand.",
        "zurg: v1",
        f"port: {port}",
        "rclone_enabled: false",
        "check_for_changes_every_secs: 15",
        "retain_folder_name_extension: true",
        "providers:",
        "  - type: nzb",
        "    nntp:",
        f"      host: {account['host']}",
        f"      port: {account['port']}",
        f"      tls: {'true' if account['tls'] else 'false'}",
        f"      username: {account['user']}",
        f"      password: {account['password']}",
        f"      connections: {connections}",
        "stremio:",
        "  enabled: true",
        f"  token: {token}",
        "  indexers:",
    ]
    for indexer in indexers:
        lines.append(f"    - name: {indexer['label']}")
        lines.append(f"      url: {indexer['url'].rstrip('/')}")
        lines.append(f"      api_key: {indexer['api_key']}")
        api_path = (indexer.get("api_path") or "/api").strip()
        if api_path != "/api":
            lines.append(f"      api_path: {api_path}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="~/stremio-addon-bench/run/zurg")
    parser.add_argument("--port", type=int, default=9998)
    parser.add_argument("--connections", type=int, default=15)
    parser.add_argument("--token", default=TOKEN)
    args = parser.parse_args()

    directory = os.path.expanduser(args.dir)
    os.makedirs(directory, exist_ok=True)
    account = common.account()
    indexers = common.parity_indexers()
    print(f"parity indexers: {[i['label'] for i in indexers]}")

    path = os.path.join(directory, "config.yml")
    with open(path, "w") as handle:
        handle.write(config_yaml(account, indexers, args.port, args.connections, args.token))
    os.chmod(path, 0o600)
    print(f"wrote {path} ({len(indexers)} indexers, {args.connections} connections)")

    binary = os.path.join(directory, "zurg")
    if not os.path.exists(binary):
        print(f"\nno binary at {binary} yet. Build it from the commit the round is "
              f"measuring and copy it in:\n"
              f"  go build -o zurg.new ./cmd/zurg && mv zurg.new {binary}\n"
              f"A round must state which commit it measured, so build from a clean "
              f"checkout rather than a worktree with local changes.")

    base = f"http://127.0.0.1:{args.port}"
    common.record_endpoint("zurg",
                           f"{base}/stremio/{args.token}/manifest.json",
                           f"{base}/stremio/{args.token}/stream/{{type}}/{{id}}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
