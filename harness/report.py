#!/usr/bin/env python3
"""Turn result files into the tables a round publishes, with the rules enforced.

    python3 harness/report.py --round round1
    python3 harness/report.py --round round1 --out docs/round1.md

Three rules from docs/design.md are implemented here rather than left to
whoever writes the prose, because each one is a way to publish a number that
flatters a target:

**Coverage outranks speed, and no table prints one without the other.** Every
speed column in this file is emitted next to the coverage that produced it. An
addon that serves four titles in 900 ms is worse than one that serves eighteen
in four seconds, and a table sorted on time alone says the opposite.

**Medians are over the fixed population.** A target is asked for every
in-round title including the ones nothing can serve, so two medians are
reported and they mean different things:

  * `median_served` is over the rows that produced a number, with `n`. It is
    the honest answer to "when it works, how fast is it", and on its own it
    pays a target for failing early.
  * `median_population` treats a failure as slower than any success -- a
    censored median over all `N` entries. It is the ranking number. When a
    target serves less than half the population it has no median at all and
    this prints `>budget` rather than inventing one.

**An expectation is not always success.** Part of the set is negative: an
empty stream list is the correct answer for two entries, and forwarding an
indexer's unrelated feed is a failure even though bytes arrive. Verdicts come
from `expected_outcome` in the title set, not from whether the read worked.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVED = ("served",)
# a truncated body is not a served stream: the player stops. It is kept apart
# from a clean failure because the diagnosis is different
PARTIAL = ("truncated",)

# A feature film is not a megabyte. Two targets answer a stream they cannot
# serve with a small, complete, valid MP4 -- 206, video/mp4, a Content-Range
# whose total is that size -- which passes every check that stops at the status
# line. See harness/protocol.py, PLACEHOLDER_MAX_BYTES.
PLACEHOLDER_MAX_BYTES = 1024 * 1024


def outcome_of(row):
    """The row's outcome under the current rule, not the one it was written under.

    Rows measured before placeholder detection existed carry `truncated` for a
    complete 19KB error clip. Everything needed to tell those apart -- the
    bytes read and the declared total -- is on the row, so a round is re-read
    under the corrected rule rather than re-run under it, and every target in
    the round is judged by the same rule whenever it was measured.
    """
    outcome = row.get("outcome")
    if outcome not in ("truncated", "served"):
        return outcome
    total = row.get("content_bytes_total")
    if total and total <= PLACEHOLDER_MAX_BYTES:
        return "placeholder"
    return outcome


def load(round_name, prefix="protocol-"):
    directory = os.path.join(ROOT, "results", round_name)
    if not os.path.isdir(directory):
        raise SystemExit(f"no results at {os.path.relpath(directory, ROOT)}")
    documents = {}
    for name in sorted(os.listdir(directory)):
        if name.startswith(prefix) and name.endswith(".json"):
            with open(os.path.join(directory, name)) as handle:
                document = json.load(handle)
            documents[document["target"]] = document
    if not documents and prefix == "protocol-":
        raise SystemExit(f"no protocol-*.json in {os.path.relpath(directory, ROOT)}")
    return documents


def first_pass(document):
    """The measured pass. Later passes exist to give the noise floor."""
    return document["passes"][0] if document.get("passes") else []


def median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def censored_median(values, population):
    """The median over the whole population, failures counted as unbounded.

    `values` are the successes. Anything the target did not serve is worse
    than every one of them, so it sorts to the end without needing a number.
    If the successes are not more than half the population the median falls
    inside the failures and there is no number to print.
    """
    if population == 0:
        return None
    ordered = sorted(values)
    if len(ordered) * 2 <= population:
        return None
    index = (population - 1) // 2
    if population % 2:
        return ordered[index]
    lower = ordered[index]
    upper = ordered[index + 1] if index + 1 < len(ordered) else None
    return None if upper is None else (lower + upper) / 2


def verdict(row):
    """Did the target do the right thing for this entry, per its expectation."""
    expected = row.get("expected_outcome")
    outcome = outcome_of(row)
    streams = row.get("n_streams") or 0
    if expected == "playable-stream":
        if outcome in SERVED:
            return "correct"
        if outcome in PARTIAL:
            return "partial"
        # a placeholder is a miss dressed as a success, and counting it as a
        # partial would credit the target for the dressing
        return "missed"
    if expected == "empty-list":
        # a real title with nothing posted. Promptly nothing is the right
        # answer; an error, a hang, or a stream that cannot exist is not
        if outcome == "empty-list":
            return "correct"
        if streams:
            return "fabricated"
        return "missed"
    if expected == "no-unrelated-streams":
        # the indexer answers this id with thousands of unrelated releases.
        # The addon is judged on whether it forwards that to the viewer
        return "correct" if streams == 0 else "forwarded-garbage"
    return "unclassified"


def summarise(name, document, cap_bytes):
    rows = first_pass(document)
    population = document.get("population") or len(rows)
    served = [r for r in rows if outcome_of(r) in SERVED]
    partial = [r for r in rows if outcome_of(r) in PARTIAL]

    c2b = [r["click_to_byte_s"] for r in served if r.get("click_to_byte_s") is not None]
    lists = [r["stream_list_s"] for r in rows if r.get("stream_list_s") is not None]
    ttfb = [r["ttfb_s"] for r in served if r.get("ttfb_s") is not None]
    resolve = [r["resolve_s"] for r in served if r.get("resolve_s") is not None]
    throughput = [r["throughput_mb_s"] for r in served if r.get("throughput_mb_s")]
    p05 = [r["p05_window_mb_s"] for r in served if r.get("p05_window_mb_s") is not None]
    sustained = [r for r in served if r.get("sustain_25mbps")]

    offered = [r["max_offered_bytes"] for r in rows if r.get("max_offered_bytes")]
    over_cap = [r for r in rows if (r.get("max_offered_bytes") or 0) > cap_bytes]
    ranks = [r["picked_rank"] for r in rows if r.get("picked_rank") is not None]
    forced = [r for r in rows if r.get("picked_over_cap")]

    verdicts = {}
    for row in rows:
        verdicts[verdict(row)] = verdicts.get(verdict(row), 0) + 1

    outcomes = {}
    for row in rows:
        # not `name`: that is this function's target-name parameter, and
        # shadowing it made every row in the report claim the last outcome as
        # its target
        label = outcome_of(row)
        outcomes[label] = outcomes.get(label, 0) + 1

    return {
        "target": name,
        # a results file can name a target this checkout does not register --
        # a renamed target, or a round pulled from elsewhere. Report it rather
        # than crashing on it
        "language": (registry.TARGETS.get(name) or {}).get("language", "?"),
        "verified": document.get("verified"),
        "population": population,
        "measured": len(rows),
        "served": len(served),
        "partial": len(partial),
        "coverage_pct": round(100.0 * len(served) / population, 1) if population else None,
        "median_served_c2b_s": median(c2b),
        "n_c2b": len(c2b),
        "median_population_c2b_s": censored_median(c2b, population),
        "median_stream_list_s": median(lists),
        "median_resolve_s": median(resolve),
        "median_ttfb_s": median(ttfb),
        "median_throughput_mb_s": median(throughput),
        "median_p05_window_mb_s": median(p05),
        "sustain_25mbps_n": len(sustained),
        "max_offered_bytes": max(offered) if offered else None,
        "over_cap_titles": len(over_cap),
        "median_pick_rank": median(ranks),
        "picked_over_cap": len(forced),
        "outcomes": outcomes,
        "verdicts": verdicts,
    }


def noise_floor(document):
    """Spread across repeat passes on the same target, per title.

    "Provider throughput drifts over an evening" is true and it is not a
    number. This is the number: the resolution below which two targets are
    tied, taken from the same target measured twice.
    """
    passes = document.get("passes") or []
    if len(passes) < 2:
        return None
    by_id = {}
    for rows in passes:
        for row in rows:
            if row.get("click_to_byte_s") is not None:
                by_id.setdefault(row["id"], []).append(row["click_to_byte_s"])
    spreads = []
    for values in by_id.values():
        if len(values) > 1:
            spreads.append(max(values) - min(values))
    if not spreads:
        return None
    return {"titles": len(spreads), "passes": len(passes),
            "median_spread_s": round(median(spreads), 3),
            "max_spread_s": round(max(spreads), 3)}


def human_bytes(value):
    """Binary units, labelled as such.

    The cap is 6 GiB and the title set stores it as 6442450944 bytes. Printing
    that as "6.00 GB" invites a reader to compare it against a target whose own
    setting is in decimal GB -- streamnzb's is -- and they are not the same
    number. Saying GiB costs one character and removes the ambiguity.
    """
    if not value:
        return "-"
    return f"{value / 1024**3:.2f} GiB"


def seconds(value):
    return "-" if value is None else f"{value:.2f}s"


# the same rule the protocol plane applies to a 19KB body, applied to a played
# duration: the player starting is not evidence the viewer got the title
MIN_FEATURE_MS = 5 * 60 * 1000


def client_outcome(row):
    """Re-read a client row under the placeholder rule, whenever it was measured."""
    if row.get("outcome") != "played":
        return row.get("outcome")
    length = (row.get("player") or {}).get("length")
    if isinstance(length, (int, float)) and 0 < length < MIN_FEATURE_MS:
        return "placeholder"
    return "played"


def render_client(documents):
    """The client plane, beside the protocol plane and never averaged with it.

    The two answer different questions. The protocol plane isolates the addon
    from the player and measures over loopback on the bench host; this one is
    the real player on a different machine, so it carries a LAN hop and the
    player's own startup. What only this plane can say is whether a stream the
    addon served correctly is one the player will actually open.
    """
    if not documents:
        return ["## The client plane", "",
                "**Not run.** No `client-*.json` in this round, so nothing here says "
                "whether a player would accept what each addon served. The protocol "
                "plane cannot answer that.", ""]
    out = ["## The client plane", "",
           "Stremio 4.4, driven over CDP, clicking the row the addon produced. "
           "`played` means the app's own player reported time moving forward, not "
           "that a screenshot looked right. `player-refused` is a stream the addon "
           "served and the player would not open, which is the whole reason this "
           "plane exists. Rows other addons contributed are rendered by the client "
           "and never counted. `clips the player accepted` is the row that matters "
           "most here: a short placeholder the addon served instead of the film, "
           "which Stremio starts and plays without complaint. Counting those as "
           "playback is how a target that serves almost nothing scores well.", ""]
    out.append("| Target | Played | Coverage | median click to play | refused | clips the player accepted | other addons' rows |")
    out.append("|---|---|---|---|---|---|---|")
    for name, document in sorted(documents.items()):
        rows = first_pass(document)
        population = document.get("population") or len(rows)
        played = [r for r in rows if client_outcome(r) == "played"]
        refused = [r for r in rows if client_outcome(r) == "player-refused"]
        clips = [r for r in rows if client_outcome(r) == "placeholder"]
        times = [r["click_to_play_s"] for r in played if r.get("click_to_play_s") is not None]
        noise = [r.get("rows_from_other_addons") or 0 for r in rows]
        coverage = round(100.0 * len(played) / population, 1) if population else None
        out.append(f"| {name} | {len(played)}/{population} | {coverage}% "
                   f"| {seconds(median(times))} | {len(refused)} | {len(clips)} "
                   f"| {max(noise) if noise else 0} max |")
    out.append("")
    hop = {d.get("addon_host") for d in documents.values()}
    out.append(f"Measured through the player's loopback ({', '.join(sorted(str(h) for h in hop))}), "
               f"which is what makes a plain-http addon a secure context for the shell. "
               f"These numbers include a network hop and a player start that the "
               f"protocol plane's do not, so the two tables are read side by side and "
               f"never averaged.")
    out.append("")
    return out


def render(documents, round_name, client=None, noise_documents=None):
    caps = {d.get("playable_cap_bytes") for d in documents.values() if d.get("playable_cap_bytes")}
    cap_bytes = max(caps) if caps else 6 * 1024**3
    summaries = [summarise(name, document, cap_bytes) for name, document in documents.items()]
    # ranked by the censored median, which is the only ordering that cannot be
    # won by failing early. A target with no median sorts last, not first
    summaries.sort(key=lambda s: (s["median_population_c2b_s"] is None,
                                  s["median_population_c2b_s"] or 0))

    populations = {s["population"] for s in summaries}
    out = []
    out.append(f"# Round: {round_name}")
    out.append("")
    out.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
               f"by `harness/report.py`.")
    out.append("")
    if len(populations) > 1:
        out.append(f"**The targets were not asked the same set** ({sorted(populations)}). "
                   f"Medians below are not comparable until they are.")
        out.append("")
    cut = [s for s in summaries if s["measured"] < s["population"]]
    if cut:
        out.append("**A target ran out of its budget before the set was finished**, so some "
                   "of its entries were never asked rather than answered badly:")
        out.append("")
        for s in cut:
            out.append(f"- `{s['target']}` measured {s['measured']} of {s['population']}. "
                       f"Its coverage and its population median count the "
                       f"{s['population'] - s['measured']} unmeasured entries as not served, "
                       f"which is a claim about the budget as much as about the target.")
        out.append("")

    out.append("## Coverage and click to byte")
    out.append("")
    out.append("Coverage first, and no speed column appears without it. "
               "`median (population)` counts every entry the target was asked for, "
               "with a failure treated as slower than any success; `median (served)` "
               "is over the rows that produced a number and is the one that pays a "
               "target for failing early.")
    out.append("")
    out.append("| Target | Lang | Served | Coverage | median c2b (population) | median c2b (served) | n |")
    out.append("|---|---|---|---|---|---|---|")
    for s in summaries:
        out.append(f"| {s['target']} | {s['language']} | {s['served']}/{s['population']} "
                   f"| {s['coverage_pct']}% "
                   f"| {seconds(s['median_population_c2b_s']) if s['median_population_c2b_s'] is not None else '>budget'} "
                   f"| {seconds(s['median_served_c2b_s'])} | {s['n_c2b']} |")
    out.append("")

    out.append("## Where the time goes")
    out.append("")
    out.append("`click_to_byte` decomposed. A target losing on `stream_list` has a "
               "different product problem from one losing on `resolve`, and the "
               "composite alone cannot tell them apart. `resolve` nests inside "
               "`ttfb`, which nests inside `click_to_byte`.")
    out.append("")
    out.append("| Target | stream_list | resolve | ttfb | click_to_byte | Served |")
    out.append("|---|---|---|---|---|---|")
    for s in summaries:
        out.append(f"| {s['target']} | {seconds(s['median_stream_list_s'])} "
                   f"| {seconds(s['median_resolve_s'])} | {seconds(s['median_ttfb_s'])} "
                   f"| {seconds(s['median_served_c2b_s'])} | {s['served']}/{s['population']} |")
    out.append("")

    out.append("## Reading, once it is playing")
    out.append("")
    out.append("`p05` is the fifth percentile of one-second windows. A mean that "
               "looks fine can contain a second at zero, and that second is where "
               "the player stops. `sustain` counts titles that held 25 Mbps in "
               "every window of the read, not on average.")
    out.append("")
    out.append("| Target | median MB/s | median p05 MB/s | sustain 25 Mbps | Served |")
    out.append("|---|---|---|---|---|")
    for s in summaries:
        mb = "-" if s["median_throughput_mb_s"] is None else f"{s['median_throughput_mb_s']:.2f}"
        p05 = "-" if s["median_p05_window_mb_s"] is None else f"{s['median_p05_window_mb_s']:.2f}"
        out.append(f"| {s['target']} | {mb} | {p05} | {s['sustain_25mbps_n']}/{s['served']} "
                   f"| {s['served']}/{s['population']} |")
    out.append("")

    out.append("## Did it do the right thing")
    out.append("")
    out.append("Part of the set is negative. Two entries are real titles with "
               "nothing posted, where an empty list is correct and a stream is a "
               "fabrication; one is an id whose indexer answers with an unrelated "
               "feed, where the question is whether the addon forwards it.")
    out.append("")
    keys = ["correct", "partial", "missed", "fabricated", "forwarded-garbage", "unclassified"]
    out.append("| Target | " + " | ".join(keys) + " |")
    out.append("|---|" + "---|" * len(keys))
    for s in summaries:
        out.append(f"| {s['target']} | " + " | ".join(str(s["verdicts"].get(k, 0)) for k in keys) + " |")
    out.append("")

    placeholders = sum(s["outcomes"].get("placeholder", 0) for s in summaries)
    if placeholders:
        out.append("## The error clip")
        out.append("")
        out.append(f"**{placeholders} rows across the field are a complete, valid, tiny "
                   f"MP4 rather than a film.** HTTP 206, `video/mp4`, a `Content-Range` "
                   f"whose total is the same few kilobytes, and the whole of it delivered. "
                   f"Nothing about the response is malformed; it is simply not the movie. "
                   f"A harness that stops at the status line, or that reads a fixed first "
                   f"chunk, records these as served, and they are the difference between "
                   f"a target that answers a title and one that appears to.")
        out.append("")
        out.append("| Target | error clips | of population |")
        out.append("|---|---|---|")
        for s in summaries:
            n = s["outcomes"].get("placeholder", 0)
            if n:
                out.append(f"| {s['target']} | {n} | {s['population']} |")
        out.append("")

    out.append("## Outcomes, and the size cap")
    out.append("")
    out.append(f"The cap is {human_bytes(cap_bytes)} and it is applied at the pick, "
               f"identically for every target, because one of them cannot be "
               f"configured to honour it. What each one *offered* is reported rather "
               f"than corrected: `over cap` counts titles where the target's own list "
               f"went above the cap, and `pick rank` is how far down its own ranking "
               f"the cap had to reach to find something playable.")
    out.append("")
    out.append("| Target | outcomes | largest offered | titles with oversize offers | median pick rank | oversize-only picks |")
    out.append("|---|---|---|---|---|---|")
    for s in summaries:
        breakdown = ", ".join(f"{k} {v}" for k, v in sorted(s["outcomes"].items()))
        rank = "-" if s["median_pick_rank"] is None else f"{s['median_pick_rank']:g}"
        out.append(f"| {s['target']} | {breakdown} | {human_bytes(s['max_offered_bytes'])} "
                   f"| {s['over_cap_titles']} | {rank} | {s['picked_over_cap']} |")
    out.append("")

    out.extend(render_client(client or {}))

    source = noise_documents if noise_documents else documents
    floors = {name: noise_floor(document) for name, document in source.items()}
    floors = {name: floor for name, floor in floors.items() if floor}
    out.append("## Noise floor")
    out.append("")
    if not floors:
        out.append("**Not measured.** No target was run with `--repeat`, so this round "
                   "states no resolution below which two targets are tied. Any "
                   "difference read off the tables above is unqualified.")
    else:
        out.append("Repeat passes on one target, same evening. Two targets closer "
                   "together than this are tied."
                   + (" Measured on a sampled subset in its own run, because repeat "
                      "passes over the whole set cost the account more than the number "
                      "is worth." if noise_documents else ""))
        out.append("")
        out.append("| Target | passes | titles | median spread | max spread |")
        out.append("|---|---|---|---|---|")
        for name, floor in floors.items():
            out.append(f"| {name} | {floor['passes']} | {floor['titles']} "
                       f"| {floor['median_spread_s']}s | {floor['max_spread_s']}s |")
    out.append("")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round", default="round1")
    parser.add_argument("--out", help="write markdown here instead of stdout")
    parser.add_argument("--noise-round",
                        help="read the noise floor from another round directory. Repeat "
                             "passes are expensive over the whole set, so the floor is "
                             "usually measured on a sample in its own run and quoted here")
    args = parser.parse_args()

    documents = load(args.round)
    client = load(args.round, prefix="client-")
    floors = load(args.noise_round) if args.noise_round else None
    text = render(documents, args.round, client, floors)
    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        with open(path, "w") as handle:
            handle.write(text + "\n")
        print(f"wrote {os.path.relpath(path, ROOT)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
