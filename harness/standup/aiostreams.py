#!/usr/bin/env python3
"""Configure AIOStreams for a round and mint its URL. Clears its `shakedown`.

    python3 harness/standup/aiostreams.py --base http://127.0.0.1:3010

AIOStreams splits its configuration three ways, and the round needs all three:

  * **the news account** is global, owned by the administrator rather than the
    user, and goes to `PUT /api/v1/dashboard/usenet/providers` behind a
    dashboard session. `AIOSTREAMS_AUTH=user:pass` is what that session logs in
    with.
  * **the indexers** are per-user: one `newznab` preset per indexer inside the
    user config, each carrying its own url and api key.
  * **the streaming service** is also per-user. The preset refuses to load
    without one of the usenet services, and the choice matters: `stremio_nntp`
    hands NNTP server details to the *player* -- a Stremio V5 desktop feature
    that streams nothing through the addon and would measure nothing here. The
    one that makes AIOStreams a usenet-backed addon in its own right is
    `aiostreams`, its built-in engine, authorised by the same `user:pass` pair.

The user is created by `POST /api/v1/user`, which answers with a uuid and an
**encrypted password**; both are path segments of the manifest URL. That URL
cannot be written from a template, which is why `harness/targets.py` carried
this target as `shakedown` until it had been stood up once.

The config also has to carry `accessKey`, the value of `CONFIG_ACCESS_KEY`
(`ADDON_PASSWORD` is the deprecated spelling and is migrated into it at boot).
Without it every write is refused as `ADDON_PASSWORD_INVALID`, which reads like
a wrong password rather than a missing field.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


class Session:
    """A dashboard session. The dashboard answers 302 to /login without one."""

    def __init__(self, base):
        self.base = base
        self.cookie = ""

    def call(self, path, method="GET", data=None, timeout=40):
        headers = {"Content-Type": "application/json", "User-Agent": common.USER_AGENT}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, headers=headers,
                                         data=body, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as problem:
            return problem.code, problem.read().decode("utf-8", "replace")
        cookies = response.headers.get_all("Set-Cookie") or []
        if cookies:
            self.cookie = "; ".join(c.split(";", 1)[0] for c in cookies)
        return response.status, response.read().decode("utf-8", "replace")

    def login(self, user, password):
        status, body = self.call("/api/v1/auth/login", "POST",
                                 {"username": user, "password": password})
        if status != 200:
            raise SystemExit(f"dashboard login failed: http {status}: {body[:200]}\n"
                             f"the container needs AIOSTREAMS_AUTH={user}:<pass>")


def set_provider(session, account, connections):
    provider = {
        "id": "bench",
        "name": "bench",
        "host": account["host"],
        "port": account["port"],
        "username": account["user"],
        "password": account["password"],
        "tls": account["tls"],
        "maxConnections": connections,
        "priority": 0,
        "enabled": True,
    }
    status, body = session.call("/api/v1/dashboard/usenet/providers", "PUT",
                                {"providers": [provider]})
    if status != 200:
        raise SystemExit(f"cannot set the usenet provider: http {status}: {body[:400]}")
    print(f"usenet provider set, maxConnections={connections}")


def user_config(indexers, access_key, auth_token):
    presets = []
    for indexer in indexers:
        presets.append({
            "type": "newznab",
            "instanceId": indexer["label"],
            "enabled": True,
            "options": {
                # the label, not the real name: whatever a target prints into a
                # stream description ends up in a published result file
                "name": indexer["label"],
                "api": {"url": common.api_url(indexer), "apiKey": indexer["api_key"]},
                "services": ["aiostreams"],
            },
        })
    return {
        "accessKey": access_key,
        "sortCriteria": {"global": []},
        "formatter": {"id": "torrentio"},
        "presets": presets,
        "services": [
            {"id": "aiostreams", "enabled": True,
             "credentials": {"aiostreamsAuth": auth_token}},
        ],
    }


def create_user(session, config, password):
    status, body = session.call("/api/v1/user", "POST",
                                {"config": config, "password": password})
    if status not in (200, 201):
        raise SystemExit(f"cannot create the user config: http {status}\n"
                         f"{(json.loads(body).get('error') or {}).get('message', body)[:900]}")
    data = json.loads(body)["data"]
    return data["uuid"], data["encryptedPassword"]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:3010")
    parser.add_argument("--user", default="bench")
    parser.add_argument("--password", default="sabround1pass",
                        help="AIOSTREAMS_AUTH and CONFIG_ACCESS_KEY carry this value")
    parser.add_argument("--config-password", default="round1pass",
                        help="the user config's own password, a path segment of its URL")
    parser.add_argument("--connections", type=int, default=15)
    args = parser.parse_args()

    account = common.account()
    indexers = common.parity_indexers()
    print(f"parity indexers: {[i['label'] for i in indexers]}")

    session = Session(args.base)
    session.login(args.user, args.password)
    set_provider(session, account, args.connections)

    config = user_config(indexers, args.password, f"{args.user}:{args.password}")
    uuid, encrypted = create_user(session, config, args.config_password)

    manifest = f"{args.base}/stremio/{uuid}/{encrypted}/manifest.json"
    stream = f"{args.base}/stremio/{uuid}/{encrypted}/stream/{{type}}/{{id}}.json"
    common.record_endpoint("aiostreams", manifest, stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
