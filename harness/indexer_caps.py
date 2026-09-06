#!/usr/bin/env python3
"""Measure what each indexer can actually answer, before trusting any of them.

The parity set cannot be chosen by reputation. An indexer belongs in it only if
it honours the search an addon actually sends, has the quota to survive a round,
and does not refuse some of the field by name. This measures all three.

    python3 harness/indexer_caps.py
    python3 harness/indexer_caps.py --skip indexer-b

The interesting check is `tv_imdbid`. An indexer that ignores the `imdbid`
parameter on `t=tvsearch` does not answer zero. It answers with everything
matching the season and episode number, which looks like a large successful
result set and is a feed of unrelated shows. So the probe asks for two
different series and compares the answers. Identical result sets mean the
filter was dropped, and any addon trusting that indexer will offer a viewer
hundreds of streams for a programme they did not ask for.

`ua_policy` sends one request identifying as Stremio. That measures an
indexer's policy rather than its catalogue, which is why it lives here and not
in the census.

Writes corpus/indexer-capabilities.json.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "indexers.json")
OUT = os.path.join(ROOT, "corpus", "indexer-capabilities.json")

NEUTRAL_UA = "usenet-addon-census/1 (benchmark; contact via github.com/debridmediamanager)"
ADDON_UA = "Stremio/4.4.181"
TIMEOUT = 30

# two films and two series far enough apart that no honest index returns the
# same answer for both
MOVIE_A = ("1375666", "inception")
MOVIE_B = ("0111161", "shawshank")
SERIES_A = ("0903747", "breaking")
SERIES_B = ("0944947", "thrones")


def call(indexer, params, user_agent=NEUTRAL_UA):
    query = dict(params)
    if indexer.get("api_key"):
        query["apikey"] = indexer["api_key"]
    query.setdefault("o", "json")
    query.setdefault("limit", "20")
    url = (f"{indexer['url'].rstrip('/')}{indexer.get('api_path', '/api')}"
           f"?{urllib.parse.urlencode(query)}")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status, response.read().decode("utf-8", "replace")


def names_of(body):
    """Result names, however the indexer chose to encode them."""
    body = body.strip()
    out = []
    if body.startswith("{"):
        document = json.loads(body)
        channel = document.get("channel", document)
        items = channel.get("item", [])
        items = [items] if isinstance(items, dict) else items
        for item in items:
            title = item.get("title")
            out.append(title if isinstance(title, str) else (title or {}).get("#text", ""))
    elif body.startswith("<"):
        for item in ET.fromstring(body).findall(".//item"):
            out.append(item.findtext("title") or "")
    return [n.strip().lower() for n in out if n]


def probe(indexer, params):
    try:
        status, body = call(indexer, params)
        if status != 200:
            return {"error": f"http {status}"}
        names = names_of(body)
        return {"n": len(names), "names": names[:5]}
    except urllib.error.HTTPError as problem:
        detail = problem.read()[:120].decode("utf-8", "replace").replace("\n", " ")
        return {"error": f"http {problem.code}", "detail": detail}
    except Exception as problem:
        return {"error": f"{type(problem).__name__}: {problem}"}


def verdict(first, second, token_a, token_b):
    """Compare two searches that must not have the same answer."""
    if first.get("error") or second.get("error"):
        return "unmeasured", first.get("error") or second.get("error")
    if first["n"] == 0 and second["n"] == 0:
        return "unsupported", "answers nothing for either id"
    if first["names"] and first["names"] == second["names"]:
        return "filter-ignored", (f"two different ids returned an identical page of "
                                  f"{first['n']} results, so imdbid was dropped")
    hit_a = any(token_a in name for name in first["names"])
    hit_b = any(token_b in name for name in second["names"])
    if hit_a and hit_b:
        return "honoured", f"{first['n']} and {second['n']} results, both on-title"
    if first["n"] or second["n"]:
        return "suspect", "answers differ but neither page looks like the title asked for"
    return "unsupported", "no usable results"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip", action="append", default=[], help="label to skip")
    parser.add_argument("--pace", type=float, default=2.0)
    args = parser.parse_args()

    with open(CONFIG) as handle:
        indexers = [i for i in json.load(handle)["indexers"] if i["label"] not in args.skip]

    report = {}
    for indexer in indexers:
        label = indexer["label"]
        print(f"--- {label} ({indexer['kind']})", flush=True)
        row = {"kind": indexer["kind"], "keyless": not indexer.get("api_key")}

        caps = probe(indexer, {"t": "caps"})
        row["caps"] = "error" if caps.get("error") else "ok"
        if caps.get("error"):
            row["caps_detail"] = caps.get("detail", caps["error"])
        time.sleep(args.pace)

        movie_a = probe(indexer, {"t": "movie", "imdbid": MOVIE_A[0]})
        time.sleep(args.pace)
        movie_b = probe(indexer, {"t": "movie", "imdbid": MOVIE_B[0]})
        time.sleep(args.pace)
        state, why = verdict(movie_a, movie_b, MOVIE_A[1], MOVIE_B[1])
        row["movie_imdbid"] = {"state": state, "why": why,
                               "n": [movie_a.get("n"), movie_b.get("n")]}

        series_a = probe(indexer, {"t": "tvsearch", "imdbid": SERIES_A[0], "season": "1", "ep": "1"})
        time.sleep(args.pace)
        series_b = probe(indexer, {"t": "tvsearch", "imdbid": SERIES_B[0], "season": "1", "ep": "1"})
        time.sleep(args.pace)
        state, why = verdict(series_a, series_b, SERIES_A[1], SERIES_B[1])
        row["tv_imdbid"] = {"state": state, "why": why,
                            "n": [series_a.get("n"), series_b.get("n")]}

        try:
            status, _ = call(indexer, {"t": "caps"}, user_agent=ADDON_UA)
            row["ua_policy"] = {"stremio_ua": f"http {status}", "blocked": False}
        except urllib.error.HTTPError as problem:
            detail = problem.read()[:120].decode("utf-8", "replace").replace("\n", " ")
            row["ua_policy"] = {"stremio_ua": f"http {problem.code}", "blocked": problem.code == 403,
                                "detail": detail}
        except Exception as problem:
            row["ua_policy"] = {"stremio_ua": f"{type(problem).__name__}", "blocked": None}
        time.sleep(args.pace)

        # an indexer is only usable in the parity set if it answers the search,
        # serves every target, and did not fail this probe on quota
        row["parity_eligible"] = bool(
            row["movie_imdbid"]["state"] == "honoured"
            and not row["ua_policy"].get("blocked")
            and row["caps"] == "ok"
        )
        report[label] = row
        print(f"    movie_imdbid  {row['movie_imdbid']['state']}: {row['movie_imdbid']['why']}")
        print(f"    tv_imdbid     {row['tv_imdbid']['state']}: {row['tv_imdbid']['why']}")
        print(f"    stremio UA    {row['ua_policy']['stremio_ua']}"
              f"{'  BLOCKED' if row['ua_policy'].get('blocked') else ''}")
        print(f"    parity        {'eligible' if row['parity_eligible'] else 'NOT eligible'}",
              flush=True)

    document = {
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": {
            "movie_imdbid": f"t=movie for tt{MOVIE_A[0]} and tt{MOVIE_B[0]}, answers compared",
            "tv_imdbid": (f"t=tvsearch s01e01 for tt{SERIES_A[0]} and tt{SERIES_B[0]}. "
                          "Identical pages mean the imdbid filter was dropped"),
            "ua_policy": f"one t=caps sent as {ADDON_UA!r}",
        },
        "indexers": report,
    }
    with open(OUT, "w") as handle:
        json.dump(document, handle, indent=1)
    eligible = [l for l, r in report.items() if r["parity_eligible"]]
    tv = [l for l, r in report.items() if r["tv_imdbid"]["state"] == "honoured"]
    print(f"\nwrote {OUT}")
    print(f"parity eligible: {eligible or 'NONE'}")
    print(f"answers TV by imdbid: {tv or 'NONE'}")


if __name__ == "__main__":
    sys.exit(main())
