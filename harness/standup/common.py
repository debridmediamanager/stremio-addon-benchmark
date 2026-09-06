#!/usr/bin/env python3
"""Shared pieces for standing a target up: the account, the parity indexers, waiting.

Every target in this field is configured differently -- one from environment
variables, one through a dashboard API, two through a web configure form that
mints a per-install URL -- but all four need the same three things, and none of
them may be written into a tracked file:

  * the news account, read from the bench account file rather than typed
  * the parity indexers, read from the gitignored config/indexers.json
  * a wait for the thing to actually answer, because `docker compose up -d`
    returns long before an addon serves a manifest

The parity set is not "the indexers you have". It is the measured set in
corpus/titles.json, and a target given a different one measures the indexer
rather than the addon. That is the parity rule this file exists to make
difficult to break by accident.
"""
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEXERS = os.path.join(ROOT, "config", "indexers.json")
TITLES = os.path.join(ROOT, "corpus", "titles.json")
ACCOUNT = os.path.expanduser("~/.config/zurg/bench-account.env")

USER_AGENT = "usenet-addon-benchmark/1 (benchmark; contact via github.com/debridmediamanager)"


def account(path=ACCOUNT):
    """The news account, from the bench account file. Never from an argument."""
    if not os.path.exists(path):
        raise SystemExit(f"missing {path}: the round reads the news account from there, "
                         f"so it is never typed into a config that could be committed")
    values = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in ("BENCH_NNTP_HOST", "BENCH_NNTP_PORT", "BENCH_NNTP_USER",
                           "BENCH_NNTP_PASS") if not values.get(k)]
    if missing:
        raise SystemExit(f"{path} is missing {missing}")
    return {
        "host": values["BENCH_NNTP_HOST"],
        "port": int(values["BENCH_NNTP_PORT"]),
        "tls": values.get("BENCH_NNTP_TLS", "true").lower() in ("1", "true", "yes"),
        "user": values["BENCH_NNTP_USER"],
        "password": values["BENCH_NNTP_PASS"],
        "budget": int(values.get("BENCH_NNTP_CONNECTIONS", "15")),
    }


def parity_indexers():
    """The measured parity set, in the order the title set records it.

    Reads which labels are in the set from corpus/titles.json, so adding a key
    to config/indexers.json cannot silently widen a round's catalogue.
    """
    with open(TITLES) as handle:
        labels = json.load(handle)["parity_indexers"]
    with open(INDEXERS) as handle:
        by_label = {i["label"]: i for i in json.load(handle)["indexers"]}
    missing = [label for label in labels if label not in by_label]
    if missing:
        raise SystemExit(f"config/indexers.json has no entry for {missing}, which the "
                         f"title set counts as parity indexers")
    return [by_label[label] for label in labels]


def api_url(indexer):
    """The newznab api endpoint, honouring a non-default api_path."""
    return indexer["url"].rstrip("/") + (indexer.get("api_path") or "/api")


def request(url, data=None, headers=None, method=None, timeout=30):
    body = None
    send = {"User-Agent": USER_AGENT}
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            send["Content-Type"] = "application/json"
        elif isinstance(data, str):
            body = data.encode()
            send["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = data
    send.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=send, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as problem:
        return problem.code, problem.read().decode("utf-8", "replace"), dict(problem.headers)


def wait_for(url, seconds=120, expect=200, label=""):
    """Poll until a URL answers. `docker compose up -d` proves nothing."""
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            status, body, _ = request(url, timeout=10)
            if status == expect:
                return body
            last = f"http {status}"
        except Exception as problem:
            last = f"{type(problem).__name__}: {problem}"
        time.sleep(2)
    raise SystemExit(f"{label or url} never answered {expect} in {seconds}s (last: {last})")


def record_endpoint(target, manifest, stream=None):
    """Write the minted URL where verify_endpoints.py and the round will read it."""
    path = os.path.join(ROOT, "config", "endpoints.local.json")
    current = {}
    if os.path.exists(path):
        with open(path) as handle:
            current = json.load(handle)
    entry = {"manifest": manifest}
    if stream:
        entry["stream"] = stream
    current[target] = entry
    with open(path, "w") as handle:
        json.dump(current, handle, indent=1, sort_keys=True)
    # never echoed in full: two of these targets encode their whole
    # configuration, api keys included, into the path segment
    print(f"recorded {target} -> {redact_url(manifest)}")


def redact_url(url):
    """A URL safe to print, with any per-install segment elided.

    StremThru's segment is base64 of its config and AIOStreams' carries an
    encrypted password. Printing either into a terminal, a log or a session
    transcript publishes a live key, so the shape is shown and the payload is
    not.
    """
    import re
    return re.sub(r"/([A-Za-z0-9_\-%.=+]{24,})(?=/)", "/<userdata>", url)
