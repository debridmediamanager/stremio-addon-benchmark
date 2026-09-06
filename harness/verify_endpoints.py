#!/usr/bin/env python3
"""Confirm every target's addon endpoint against a running instance.

Four of the five endpoints in targets.py are written from documentation rather
than read from a live server, and three of those embed a per-install segment
(a uuid, an encrypted password, base64 config) that is minted at configure time
and cannot be written from a template. A round must not discover that as a row
of zeros, so it refuses to start while any enabled target is still `shakedown`
and this is what clears the flag.

    python3 harness/verify_endpoints.py                 # every target
    python3 harness/verify_endpoints.py --only zurg

Reads config/endpoints.local.json for the minted URLs:

    {"zurg": {"manifest": "http://127.0.0.1:9998/stremio/abc123/manifest.json"}}

A pass means the URL answered 200 with something that is actually a Stremio
manifest. It does not mean the addon works, and it is not a measurement.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, "config", "endpoints.local.json")
TIMEOUT = 20

# what the census learned the hard way. Identifying as a benchmark is honest
# and it is also the only string one of the indexers will serve
USER_AGENT = "usenet-addon-census/1 (benchmark; contact via github.com/debridmediamanager)"

REQUIRED = ["id", "resources", "types"]


def local_urls():
    if not os.path.exists(LOCAL):
        return {}
    with open(LOCAL) as handle:
        return json.load(handle)


def check(name, url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = response.read().decode("utf-8", "replace")
    manifest = json.loads(body)
    missing = [field for field in REQUIRED if field not in manifest]
    if missing:
        raise ValueError(f"answered 200 but is not a Stremio manifest, missing {missing}")
    if "stream" not in manifest.get("resources", []):
        # a catalog-only addon cannot be in this field at all
        raise ValueError("manifest declares no `stream` resource")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated target names")
    args = parser.parse_args()

    names = registry.enabled(only=args.only.split(",") if args.only else None)
    minted = local_urls()
    failures = []
    for name in names:
        target = registry.TARGETS[name]
        url = minted.get(name, {}).get("manifest") or target["manifest"]
        if "{" in url:
            print(f"{name:<12} SKIP     needs a minted URL in config/endpoints.local.json "
                  f"(template is {url})")
            failures.append(name)
            continue
        try:
            manifest = check(name, url)
        except Exception as problem:
            print(f"{name:<12} FAIL     {type(problem).__name__}: {problem}")
            failures.append(name)
            continue
        state = target["verified"]
        note = "" if state != "shakedown" else "   <- clear `shakedown` in targets.py"
        print(f"{name:<12} ok       {manifest.get('id')} v{manifest.get('version', '?')}"
              f" types={manifest.get('types')}{note}")

    if failures:
        print(f"\n{len(failures)} target(s) unverified: {failures}")
        print("A round must not start on these.")
        return 1
    print("\nevery enabled target answered a usable manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
