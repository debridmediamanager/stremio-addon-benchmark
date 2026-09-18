#!/usr/bin/env python3
"""Build Comet's configuration URL and record it. Clears its `shakedown`.

    python3 harness/standup/comet.py --base http://127.0.0.1:8091

Comet has no configure API to post to: its whole configuration is the URL. The
segment is base64url of a JSON document -- the current build also accepts a
`z1.`-prefixed compressed form, and the plain one is still read -- so this
builds the document and encodes it. A document Comet rejects does not error:
`config_check` returns nothing and every route answers 200 with a single
stream named `OBSOLETE CONFIGURATION, PLEASE RE-CONFIGURE`, which a harness
reads as a served result unless it looks at the name.

Four things about this target cost a boot each, and all four are environment
rather than configuration:

  * **`ANIME_MAPPING_ENABLED` must be off on a small host.** Comet downloads a
    70 MB anime mapping during worker startup, single worker, preload on. On
    two shared cores that outlasts gunicorn's worker timeout, so the worker is
    killed and restarted forever, and the only symptom is
    `Worker exited with code 1 | error=dependency_failure` with no traceback.
  * **`USENET_ENGINE_ENABLED=true` is what stops the engine starting.** With it
    set, the supervisor gives up after 30 seconds with
    `native.startup.failed | initialization_failure` and `EngineUnavailable`.
    With it *unset*, the same build logs `Native Usenet engine is ready` in
    295 ms. The flag does not mean "use the native engine" -- `USENET_ENABLED`
    does that -- and the round leaves it off.
  * **`tls_mode` is `implicit`, not `tls`.** The only accepted values are
    `implicit`, `starttls` and `plaintext`; anything else fails the whole
    application config, not just the server entry.
  * **The native pool needs `options: {"source": "instance_pool"}`** on the
    playback provider. Without it the document fails validation with `native
    Usenet options must select one valid server source` and you get the
    obsolete-configuration stream, which looks like a working addon.

The operator half -- the news account, in `USENET_NATIVE_SERVERS` -- lives in
the container environment and is written by the compose file, not here.
"""
import argparse
import base64
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def build_config(indexers, access_token):
    accounts, sources = {}, []
    for indexer in indexers:
        account_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                    f"dmm-benchmark:account:{indexer['label']}"))
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"dmm-benchmark:source:{indexer['label']}"))
        url = common.api_url(indexer)
        # the account kind is the *binding* kind, not the source kind: a
        # `newznab` source binds to an `indexer` account. Getting this wrong
        # fails the whole document with "Usenet account kind does not match its
        # binding", which arrives as the obsolete-configuration stream
        accounts[account_id] = {"kind": "indexer", "apiKey": indexer["api_key"], "url": url}
        sources.append({
            "configurationId": source_id,
            # the label, never the real name: it reaches stream descriptions
            "displayName": indexer["label"],
            "kind": "newznab",
            "enabled": True,
            "accountId": account_id,
            # `endpoint`, not `url`: the adapter reads options["endpoint"] and an
            # unknown key is simply absent, which surfaces as an unhandled 500
            # (`Newznab endpoint is invalid`) from the stream route rather than
            # as a configuration error
            "options": {"endpoint": url, "apiKey": indexer["api_key"],
                        "name": indexer["label"]},
        })
    return {
        # 2, not 1: a v1 document validates and then finds nothing, because the
        # discovery sources this builds are only consulted on the current schema
        "schemaVersion": 2,
        "enabledTransports": ["usenet"],
        "discoverySources": sources,
        "playbackProviders": [{
            "configurationId": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                "dmm-benchmark:provider:native")),
            "displayName": "Comet Native Usenet",
            "kind": "comet_native_usenet",
            "enabled": True,
            "accountId": None,
            "options": {"source": "instance_pool"},
        }],
        "accounts": accounts,
        "nativeAccessToken": access_token,
        "debridService": "torrent",
        "debridApiKey": "",
        # uncapped here on purpose: the round caps at the pick, identically for
        # every target. See docs/design.md, parity rule 2
        "maxResultsPerResolution": 0,
        "maxSize": 0.0,
        "resultFormat": ["all"],
        "removeTrash": True,
        "cachedOnly": False,
    }


def encode(config):
    raw = json.dumps(config, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def check(base, segment):
    """A rejected document answers 200. The name is the only tell."""
    status, body, _ = common.request(f"{base}/{segment}/manifest.json", timeout=60)
    if status != 200:
        raise SystemExit(f"manifest answered http {status}: {body[:200]}")
    manifest = json.loads(body)
    name = manifest.get("name", "")
    description = manifest.get("description", "")
    if "OBSOLETE" in description.upper() or name.startswith("❌"):
        raise SystemExit("Comet rejected the configuration and answered with its "
                         "obsolete-configuration manifest. That is a 200, so nothing "
                         "downstream would have noticed.\n"
                         f"  name: {name}\n  description: {description[:160]}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8091")
    parser.add_argument("--access-token", default="sabround1token",
                        help="must equal the container's USENET_NATIVE_ACCESS_TOKEN")
    args = parser.parse_args()

    indexers = common.parity_indexers()
    print(f"parity indexers: {[i['label'] for i in indexers]}")

    segment = encode(build_config(indexers, args.access_token))
    manifest = check(args.base, segment)
    print(f"manifest ok: {manifest.get('name')} v{manifest.get('version')}")

    common.record_endpoint("comet",
                           f"{args.base}/{segment}/manifest.json",
                           f"{args.base}/{segment}/stream/{{type}}/{{id}}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
