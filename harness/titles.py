#!/usr/bin/env python3
"""Turn the census into the pinned title set a round measures.

    python3 harness/titles.py build     # writes corpus/titles.json
    python3 harness/titles.py show      # print the set as a table

Every field below is derived from corpus/availability.json. Nothing in
candidates.json survives into the title set except the identity of the title
and the reason it was proposed; the tier, the size band and the expected
outcome are measurements.

The set is fixed before a round starts and every target is asked for every
entry, including the ones nobody can serve. A median taken over "whatever this
target survived" rewards failing early, so the round reports medians over the
whole population with `n`, and coverage as its own column.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES = os.path.join(ROOT, "corpus", "candidates.json")
AVAILABILITY = os.path.join(ROOT, "corpus", "availability.json")
CAPABILITIES = os.path.join(ROOT, "corpus", "indexer-capabilities.json")
OUT = os.path.join(ROOT, "corpus", "titles.json")

# Indexers every target in the field can actually reach, taken from the
# measured matrix in corpus/indexer-capabilities.json rather than chosen. An
# indexer qualifies by honouring the search, serving every target, and having
# the quota to survive a round.
#
# Two are excluded by measurement, not preference. indexer-c refuses any client
# whose User-Agent contains "stremio" or "aiostreams", so including it would
# hand three targets a catalogue the other two are banned from. indexer-b
# answered nine titles and then rate limited for the rest of the run, which
# cannot survive five targets asking it for the same set.
PARITY_INDEXERS = ["indexer-a", "indexer-d", "indexer-e"]

# per-entry-type: which parity indexers may be counted. An indexer that drops
# the imdbid filter on tvsearch does not answer zero for a series, it answers
# with every unrelated show carrying the same episode number, and counting that
# would make an unbenchmarkable title look abundant
CAPABILITY_FOR = {"movie": "movie_imdbid", "series": "tv_imdbid"}
NO_CAPABLE_INDEXER = "no-capable-indexer"

# a release the desktop player will direct-play. Above this every top result
# for a popular title is a 2160p remux and the addons are being compared on
# whose size filter is configured loosest, not on speed
PLAYABLE_CAP_BYTES = 6 * 1024**3
MIN_FEATURE_BYTES = 200 * 1024**2

ABUNDANT, CATALOG, THIN, ABSENT = "abundant", "catalog", "thin", "absent"
# a parity indexer errored, so this entry has not actually been measured yet.
# Treating a failed call as a zero is how a rate limit turns into a finding
# about availability
INCOMPLETE = "incomplete"
# the id is right and the index is wrong: the indexer dropped the filter and
# answered with an unrelated feed. The most valuable negative entry there is,
# because the question becomes whether the ADDON filters what its indexer will
# not
POISONED = "poisoned-index"
# the results are coherent and they are not this film, which usually means the
# id is wrong. Excluded, because a wrong id fails identically everywhere and
# reads as a hard title
ID_SUSPECT = "id-suspect"

# distinct-looking release names among the kept results, above which a
# MISMATCH is an unfiltered feed rather than one wrong film
FEED_DISTINCT = 5


def normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def title_tokens(candidate):
    """Words a genuine result for this title should contain.

    Deliberately crude: short and generic words are dropped, so `The Matrix`
    matches on `matrix` alone. This is a poison check, not a ranker -- it is
    looking for an id that returns a hundred results for a different film.
    """
    words = normalise(candidate.get("title", "")).split()
    words = [w for w in words if len(w) > 3 and w not in {"part", "them", "with", "from"}]
    return words or normalise(candidate.get("title", "")).split()


def distinct_works(items):
    """Roughly how many different releases the kept names describe.

    A wrong id returns many copies of one other film. An indexer that dropped
    the filter returns a slice of its whole feed. Four leading words is enough
    to tell those apart without trying to parse release names properly.
    """
    return len({" ".join(normalise(item.get("name")).split()[:4]) for item in items})


def size_band(items):
    sizes = [i["size"] for i in items if i.get("size")]
    if not sizes:
        return "unknown"
    playable = [s for s in sizes if MIN_FEATURE_BYTES <= s <= PLAYABLE_CAP_BYTES]
    if playable:
        return "has-playable"
    if min(sizes) > PLAYABLE_CAP_BYTES:
        return "oversize-only"
    return "undersized-only"


def tier_for(reachable):
    if reachable == 0:
        return ABSENT
    if reachable <= 5:
        return THIN
    if reachable <= 40:
        return CATALOG
    return ABUNDANT


def counting_indexers(capabilities):
    """Parity indexers whose answer for each entry type can be believed."""
    table = {}
    for kind, field in CAPABILITY_FOR.items():
        table[kind] = [
            label for label in PARITY_INDEXERS
            if capabilities.get("indexers", {}).get(label, {}).get(field, {}).get("state")
            == "honoured"
        ]
    return table


def build():
    with open(CANDIDATES) as handle:
        candidates = json.load(handle)["candidates"]
    with open(AVAILABILITY) as handle:
        census = json.load(handle)
    results = census["results"]
    with open(CAPABILITIES) as handle:
        capabilities = json.load(handle)
    countable = counting_indexers(capabilities)

    entries = []
    for candidate in candidates:
        rows = results.get(candidate["id"], {})
        counts_for_type = countable.get(candidate["type"], [])
        per_indexer = {}
        kept_all = []
        reachable = 0
        for label, row in rows.items():
            total = row.get("total")
            per_indexer[label] = {
                "total": total,
                "error": row.get("error"),
                "kept": len(row.get("kept", [])),
            }
            if label in counts_for_type and isinstance(total, int):
                reachable += total
            if label in counts_for_type:
                kept_all.extend(row.get("kept", []))

        tokens = title_tokens(candidate)
        names = [normalise(item.get("name")) for item in kept_all]
        matched = sum(1 for name in names if any(token in name for token in tokens))
        if not names:
            match = "no-results"
        elif matched == 0:
            match = "MISMATCH"
        elif matched < len(names) / 2:
            match = "weak"
        else:
            match = "ok"

        parity_errors = [label for label in counts_for_type
                         if rows.get(label, {}).get("error")]
        tier = tier_for(reachable)
        complete = not parity_errors
        if not counts_for_type:
            # nothing in the parity set answers this KIND of search at all, so
            # the entry says something about the indexer fleet and nothing
            # about any addon
            tier = NO_CAPABLE_INDEXER
        if parity_errors and reachable == 0:
            # nothing came back and one of the two that could have answered
            # never got asked properly
            tier = INCOMPLETE
        if match == "MISMATCH" and reachable > 0:
            tier = POISONED if distinct_works(kept_all) >= FEED_DISTINCT else ID_SUSPECT
        entry = {
            "id": candidate["id"],
            "type": candidate["type"],
            "title": candidate["title"],
            "year": candidate.get("year"),
            "proposed_tier": candidate["proposed_tier"],
            "measured_tier": tier,
            "reachable_results": reachable,
            "per_indexer": per_indexer,
            "size_band": size_band(kept_all),
            "title_match": match,
            "census_complete": complete,
            "parity_errors": parity_errors,
            "counting_indexers": counts_for_type,
            "expected_outcome": {
                ABSENT: "empty-list",
                NO_CAPABLE_INDEXER: None,
                POISONED: "no-unrelated-streams",
                INCOMPLETE: None,
                ID_SUSPECT: None,
            }.get(tier, "playable-stream"),
            # an entry is in the round when it has been measured and its id is
            # not in doubt. A poisoned index stays in: whether the addon passes
            # its indexer's garbage through to the player is the question
            "in_round": tier not in (INCOMPLETE, ID_SUSPECT, NO_CAPABLE_INDEXER),
        }
        if candidate["type"] == "series":
            entry["imdb"] = candidate["imdb"]
            entry["season"] = candidate["season"]
            entry["episode"] = candidate["episode"]
            entry["tv_by_imdbid"] = {
                label: bool(isinstance(row.get("total"), int) and row["total"] > 0)
                for label, row in rows.items()
            }
        entries.append(entry)

    document = {
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "census_measured_utc": census["measured_utc"],
        "parity_indexers": PARITY_INDEXERS,
        "counting_indexers_by_type": countable,
        "capabilities_measured_utc": capabilities["measured_utc"],
        "playable_cap_bytes": PLAYABLE_CAP_BYTES,
        "tier_thresholds": {"thin": "1-5", "catalog": "6-40", "abundant": ">40"},
        "rules": [
            "Every target is asked for every entry with in_round true, including "
            "the ones nothing can serve. Medians are over that fixed population.",
            "measured_tier comes from the parity indexers only. A title the "
            "excluded indexer holds is still absent for the round if the parity "
            "pair does not have it.",
            "expected_outcome empty-list means an empty stream list is the "
            "correct answer. An error, a hang or a fabricated stream is not.",
            "expected_outcome no-unrelated-streams marks an entry whose indexer "
            "answers an unfiltered feed instead of nothing. The addon is judged "
            "on whether it passes that through to the player.",
            "tier incomplete means a parity indexer errored on this entry and it "
            "has not been measured. Re-run census.py --retry-failed. A failed "
            "call is not a zero.",
            "tier no-capable-indexer means no indexer in the parity set honours "
            "this kind of search. The entry is excluded because it would measure "
            "the indexer fleet and not the addon.",
        ],
        "titles": entries,
    }
    with open(OUT, "w") as handle:
        json.dump(document, handle, indent=1)
    return document


def show(document=None):
    if document is None:
        with open(OUT) as handle:
            document = json.load(handle)
    print(f"{'id':<17}{'type':<8}{'tier':<15}{'n':>6}  {'size band':<16}{'match':<11}title")
    print("-" * 110)
    for entry in document["titles"]:
        flag = "" if entry["in_round"] else "  [EXCLUDED]"
        print(f"{entry['id']:<17}{entry['type']:<8}{entry['measured_tier']:<15}"
              f"{entry['reachable_results']:>6}  {entry['size_band']:<16}"
              f"{entry['title_match']:<11}{entry['title']}{flag}")
    counts = {}
    for entry in document["titles"]:
        counts[entry["measured_tier"]] = counts.get(entry["measured_tier"], 0) + 1
    total = sum(1 for e in document["titles"] if e["in_round"])
    print(f"\n{total} entries in the round, tiers: {counts}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "build"
    if command == "build":
        show(build())
    elif command == "show":
        show()
    else:
        raise SystemExit("usage: titles.py [build|show]")
