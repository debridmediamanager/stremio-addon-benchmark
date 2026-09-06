#!/usr/bin/env python3
"""Measure what the indexers actually hold for every candidate title.

This is the step that turns a list of films somebody liked the look of into
test data. Nothing downstream is allowed to assert an availability tier: the
tier comes from what came back here, so a round's title set is reproducible by
anyone pointing this at their own indexers.

    python3 harness/census.py                 # every candidate, every indexer
    python3 harness/census.py --indexer a     # one indexer
    python3 harness/census.py --limit 5       # first five candidates, a smoke test

Reads config/indexers.json, which is gitignored because it carries live api
keys. config/indexers.example.json is the shape.

Two publishing rules are enforced here rather than left to a reviewer:

  * **Newznab download links embed the api key.** Every `link` and most `guid`
    values are a fully authenticated download URL. They are never stored: an
    item keeps its name, size, category and date, and the guid only as a
    truncated sha1 so the same posting can be recognised across indexers.
  * **Indexer identity is not published.** Two of the three here are private
    trackers whose operators did not ask to be named in a benchmark. The
    committed census carries `indexer-a/b/c` plus each one's kind; the mapping
    stays in the gitignored config. The raw response bodies land in
    corpus/raw/, which is gitignored for the same reason.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "indexers.json")
CANDIDATES = os.path.join(ROOT, "corpus", "candidates.json")
RAW_DIR = os.path.join(ROOT, "corpus", "raw")
OUT = os.path.join(ROOT, "corpus", "availability.json")
# what actually gets published. The plain json carries several hundred release
# names, and this repository keeps those out of search indexes and automated
# scrapers the same way its sibling keeps the NZB corpus out. The password is
# published in the README: it is not access control
ARCHIVE = os.path.join(ROOT, "corpus", "availability.7z")
ARCHIVE_PASSWORD = "dmmbench"

# how many results to keep per (title, indexer). Enough to see what a ranker
# would have to choose between; not so many that the file becomes a mirror of
# the indexer
KEEP = 8
TIMEOUT = 45
# The census identifies itself as what it is: a measuring tool reading an
# index, not a service streaming from one. That distinction is load-bearing --
# indexer-c denies any client whose User-Agent contains "stremio" or
# "aiostreams", case-insensitively, with
# `403 Access denied: Streaming services are not allowed.` The first run of this
# script was blocked by its own name. See docs/design.md, "The User-Agent
# denylist". A round NEVER spoofs a target's User-Agent to get around that
# policy: an addon that an indexer refuses by name has a real coverage problem
# and the round reports it.
USER_AGENT = "usenet-addon-census/1 (benchmark; contact via github.com/debridmediamanager)"


def load_indexers():
    if not os.path.exists(CONFIG):
        raise SystemExit(
            f"missing {CONFIG}\n"
            "copy config/indexers.example.json to it and fill in real keys; it is gitignored"
        )
    with open(CONFIG) as handle:
        return json.load(handle)["indexers"]


def query_url(indexer, candidate):
    """Build one newznab search. Movies search by imdbid; episodes add s/e."""
    params = {
        "apikey": indexer["api_key"],
        "o": "json",
        "extended": "1",
        "limit": "100",
    }
    if candidate["type"] == "series":
        imdb = candidate["imdb"]
        params.update({
            "t": "tvsearch",
            "imdbid": imdb.removeprefix("tt"),
            "season": str(candidate["season"]),
            "ep": str(candidate["episode"]),
        })
    else:
        params.update({"t": "movie", "imdbid": candidate["id"].removeprefix("tt")})
    path = indexer.get("api_path", "/api")
    return f"{indexer['url'].rstrip('/')}{path}?{urllib.parse.urlencode(params)}"


# indexer-b answered nine titles at a 2.5s pace and then refused the remaining
# twenty-two with 429 for the rest of the run. Its limit is not the sub-minute
# burst window it was believed to be, so a rate limit here is waited out rather
# than paced around, and a run that still cannot get through leaves the row as
# an error for --retry-failed to pick up later
BACKOFF_S = [60, 180, 420]


def fetch(url, on_wait=None):
    started = time.monotonic()
    for attempt, pause in enumerate([0] + BACKOFF_S):
        if pause:
            if on_wait:
                on_wait(pause, attempt)
            time.sleep(pause)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, body, time.monotonic() - started
        except urllib.error.HTTPError as problem:
            if problem.code != 429 or pause == BACKOFF_S[-1]:
                raise
    raise RuntimeError("unreachable")


def parse_items(body):
    """Newznab in either encoding. `o=json` is a request, not a guarantee.

    Returns (total, items). `total` is the indexer's own count when it reports
    one, which is not always len(items): a capped page is the usual reason, and
    the difference matters when the tier is drawn from a threshold.
    """
    body = body.strip()
    if body.startswith("{"):
        return parse_json(body)
    if body.startswith("<"):
        return parse_xml(body)
    raise ValueError(f"unrecognised response encoding: {body[:80]!r}")


def parse_json(body):
    doc = json.loads(body)
    channel = doc.get("channel", doc)
    raw = channel.get("item", [])
    if isinstance(raw, dict):
        raw = [raw]
    total = None
    response = channel.get("response", {})
    if isinstance(response, dict):
        attributes = response.get("@attributes", response)
        if isinstance(attributes, dict) and "total" in attributes:
            total = int(attributes["total"])
    items = []
    for entry in raw:
        attrs = {}
        for attribute in as_list(entry.get("attr", entry.get("newznab:attr", []))):
            pair = attribute.get("@attributes", attribute)
            if isinstance(pair, dict) and "name" in pair:
                attrs[pair["name"]] = pair.get("value")
        items.append({
            "name": text_of(entry.get("title")),
            "size": to_int(entry.get("size") or attrs.get("size")),
            "posted": text_of(entry.get("pubDate")),
            "category": attrs.get("category"),
            "grabs": to_int(attrs.get("grabs")),
            "guid_sha1": guid_hash(entry.get("guid")),
        })
    return total if total is not None else len(items), items


def parse_xml(body):
    root = ET.fromstring(body)
    error = root.find(".//error")
    if error is not None:
        raise ValueError(f"indexer error {error.get('code')}: {error.get('description')}")
    namespace = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}
    total = None
    response = root.find(".//newznab:response", namespace)
    if response is not None and response.get("total") is not None:
        total = int(response.get("total"))
    items = []
    for entry in root.findall(".//item"):
        attrs = {}
        for attribute in entry.findall("newznab:attr", namespace):
            attrs[attribute.get("name")] = attribute.get("value")
        items.append({
            "name": (entry.findtext("title") or "").strip(),
            "size": to_int(entry.findtext("size") or attrs.get("size")),
            "posted": (entry.findtext("pubDate") or "").strip(),
            "category": attrs.get("category"),
            "grabs": to_int(attrs.get("grabs")),
            "guid_sha1": guid_hash(entry.findtext("guid")),
        })
    return total if total is not None else len(items), items


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def text_of(value):
    if isinstance(value, dict):
        value = value.get("#text", "")
    return (value or "").strip() if isinstance(value, str) else ""


def to_int(value):
    if isinstance(value, dict):
        value = value.get("#text")
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def guid_hash(guid):
    """A guid is often a download URL carrying the api key. Keep only a digest.

    It still does the one job the census needs a guid for: recognising the same
    posting when two indexers return it.
    """
    if isinstance(guid, dict):
        guid = guid.get("#text") or guid.get("@attributes", {}).get("text")
    if not isinstance(guid, str) or not guid:
        return None
    tail = guid.rstrip("/").rsplit("/", 1)[-1]
    return hashlib.sha1(tail.encode()).hexdigest()[:12]


def scrub(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "REDACTED")
    return text


def archive():
    """Re-cut the published archive so it can never lag the census."""
    if os.path.exists(ARCHIVE):
        os.remove(ARCHIVE)
    # run from the corpus directory and pass basenames, so the archive stores
    # "availability.json" and not "corpus/availability.json". A stored path
    # extracts relative to the output directory and buries the file one level
    # deeper than anything looks for it
    directory = os.path.dirname(OUT)
    result = subprocess.run(
        ["7z", "a", f"-p{ARCHIVE_PASSWORD}", "-mhe=on",
         os.path.basename(ARCHIVE), os.path.basename(OUT)],
        cwd=directory, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"7z failed, the published archive is now stale:\n{result.stdout[-400:]}")


def main():
    global USER_AGENT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indexer", action="append", help="label to include, repeatable")
    parser.add_argument("--limit", type=int, help="only the first N candidates")
    parser.add_argument("--ua", default=USER_AGENT,
                        help="what the census calls itself. Changing this to a "
                             "target's own string measures an indexer's policy "
                             "rather than its catalogue, which is a different "
                             "experiment and belongs in its own run")
    parser.add_argument("--give-up-after", type=int, default=2,
                        help="consecutive exhausted backoffs before an indexer is "
                             "dropped from the run and left for a later retry")
    parser.add_argument("--retry-failed", action="store_true",
                        help="re-query only the (title, indexer) pairs that errored "
                             "in corpus/availability.json and merge the answers in. "
                             "A rate-limited indexer is a resumable problem, not a "
                             "reason to spend the whole quota again")
    parser.add_argument("--pace", type=float, default=2.5,
                        help="seconds between calls to the SAME indexer. The private "
                             "TV indexer trips a burst limit at roughly six rapid "
                             "calls and clears in under a minute, so this defaults "
                             "high enough to never find out")
    args = parser.parse_args()

    USER_AGENT = args.ua

    all_indexers = load_indexers()
    indexers = all_indexers
    if args.indexer:
        indexers = [i for i in indexers if i["label"] in args.indexer]
        if not indexers:
            raise SystemExit(f"no indexer matches {args.indexer}")
    # scrub against every key, not just the queried ones: a previous run's
    # rows are merged forward and must be checked too
    secrets = [i["api_key"] for i in all_indexers]

    with open(CANDIDATES) as handle:
        candidates = json.load(handle)["candidates"]
    if args.limit:
        candidates = candidates[: args.limit]

    exhausted = {}
    skip = None
    if args.retry_failed:
        if not os.path.exists(OUT):
            raise SystemExit(f"--retry-failed needs an existing {OUT}")
        with open(OUT) as handle:
            skip = json.load(handle)["results"]
        outstanding = sum(1 for rows in skip.values() for row in rows.values() if row.get("error"))
        print(f"retrying {outstanding} failed (title, indexer) pair(s)")

    os.makedirs(RAW_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = os.path.join(RAW_DIR, f"census-{stamp}.json")

    census = {}
    raw = {}
    last_call = {}
    for position, candidate in enumerate(candidates, 1):
        # seed from what is already known so a run narrowed to one indexer
        # updates that indexer instead of deleting the others
        rows = dict(skip.get(candidate["id"], {})) if skip else {}
        for indexer in indexers:
            label = indexer["label"]
            known = (skip or {}).get(candidate["id"], {}).get(label)
            # reuse only a row that exists AND succeeded. An indexer added to
            # the config after the first census has no row at all and must be
            # queried, not skipped as already-done
            if known is not None and not known.get("error"):
                rows[label] = known
                continue
            pace = indexer.get("pace_s", args.pace)
            wait = pace - (time.monotonic() - last_call.get(label, 0))
            if wait > 0:
                time.sleep(wait)
            url = query_url(indexer, candidate)
            row = {"total": None, "kept": [], "error": None, "elapsed_s": None}
            try:
                def announce(pause, attempt, label=label):
                    print(f"    {label} rate limited, waiting {pause}s "
                          f"(attempt {attempt} of {len(BACKOFF_S)})", flush=True)

                status, body, elapsed = fetch(url, on_wait=announce)
                last_call[label] = time.monotonic()
                row["elapsed_s"] = round(elapsed, 3)
                raw.setdefault(candidate["id"], {})[label] = scrub(body[:200000], secrets)
                if status != 200:
                    row["error"] = f"http {status}"
                else:
                    total, items = parse_items(body)
                    row["total"] = total
                    row["kept"] = items[:KEEP]
            except Exception as problem:  # an indexer failing is data, not a crash
                last_call[label] = time.monotonic()
                row["error"] = scrub(f"{type(problem).__name__}: {problem}", secrets)
            rows[label] = row
            # an indexer that exhausts its backoff twice in a row is not
            # pacing-limited, it is out of quota for a window this run cannot
            # wait out. Grinding through the remaining titles at eleven minutes
            # each buys nothing, so drop the indexer and leave the rows for a
            # later --retry-failed
            if row["error"] and "429" in row["error"]:
                exhausted[label] = exhausted.get(label, 0) + 1
                if exhausted[label] >= args.give_up_after:
                    print(f"    {label} is out of quota, dropping it from this run. "
                          f"Re-run with --retry-failed once the window resets")
                    indexers = [i for i in indexers if i["label"] != label]
            elif not row["error"]:
                exhausted[label] = 0
            got = row["error"] or f"{row['total']} result(s)"
            print(f"[{position}/{len(candidates)}] {candidate['id']:<16} {label:<10} {got}",
                  flush=True)
        census[candidate["id"]] = rows

    with open(raw_path, "w") as handle:
        json.dump(raw, handle, indent=1)

    document = {
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "indexers": [
            {"label": i["label"], "kind": i["kind"], "api_path": i.get("api_path", "/api")}
            for i in indexers
        ],
        "keep_per_indexer": KEEP,
        "user_agent": USER_AGENT,
        "note": ("Indexer identity is deliberately reduced to a label and a kind; the "
                 "mapping lives in the gitignored config. Download links and raw guids "
                 "are never stored because they carry the api key."),
        "results": census,
    }
    leaked = [s for s in secrets if s and s in json.dumps(document)]
    if leaked:
        raise SystemExit("refusing to write: an api key survived into the census")
    with open(OUT, "w") as handle:
        json.dump(document, handle, indent=1)
    archive()
    print(f"\nwrote {OUT}\n      {ARCHIVE} (this is the published one)\nraw (gitignored) {raw_path}")


if __name__ == "__main__":
    sys.exit(main())
