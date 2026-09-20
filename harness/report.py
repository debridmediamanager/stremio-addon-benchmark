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

# A scene release ships a sample inside the archive beside the feature, and an
# addon has to choose which to open. One that opens the sample served a whole,
# valid video out of the right release -- the wrong file, not a broken stream.
# Which file to open is a product decision, like a size cap or a ranking rule,
# so these rows are named, set aside and reported: out of the served count,
# out of every speed median, out of the correctness tiers, and out of the
# population the rest are scored against. See harness/protocol.py.
SAMPLE = ("sample",)
SAMPLE_MAX_RELEASE_FRACTION = 0.05

# A 2xx over something that is not the film. The status line is right, the
# content type is right, the Content-Range agrees with itself, and what
# arrives is a 19 KB error clip or a stretch of zeros.
#
# Ranked below an honest failure on purpose, because the two cost a viewer
# different amounts. A refusal is information: the client knows, Stremio
# offers the next stream, an *arr retries, and the title gets another chance
# from something else. A fake success ends the search -- the player accepts
# it, nothing falls back, nothing retries, and the viewer concludes the film
# is broken rather than the addon. A target that cannot serve a title is
# better for the viewer than one that pretends it can.
#
# Severity, worst last: correct, partial, missed, faked.
FAKED = ("placeholder", "zero-bytes")


def looks_like_a_sample(row):
    """Both numbers are on the row: what was served, and what was offered."""
    total = row.get("content_bytes_total")
    release = (row.get("chosen") or {}).get("size_bytes")
    if not total or not release:
        return False
    if total <= PLACEHOLDER_MAX_BYTES:
        return False
    return total <= release * SAMPLE_MAX_RELEASE_FRACTION


def outcome_of(row):
    """The row's outcome under the current rule, not the one it was written under.

    Rows measured before placeholder detection existed carry `truncated` for a
    complete 19KB error clip. Everything needed to tell those apart -- the
    bytes read and the declared total -- is on the row, so a round is re-read
    under the corrected rule rather than re-run under it, and every target in
    the round is judged by the same rule whenever it was measured.
    """
    outcome = row.get("outcome")
    if outcome not in ("truncated", "served", "sample"):
        return outcome
    total = row.get("content_bytes_total")
    if total and total <= PLACEHOLDER_MAX_BYTES:
        return "placeholder"
    if looks_like_a_sample(row):
        return "sample"
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
    if outcome in SAMPLE:
        # Set aside rather than judged: see SAMPLE above. Counting it correct
        # would say the viewer got the film, and counting it missed would call
        # a ranking choice a failure to serve bytes.
        return "sample"
    if expected == "playable-stream":
        if outcome in SERVED:
            return "correct"
        if outcome in PARTIAL:
            return "partial"
        if outcome in FAKED:
            # Not a miss: a miss tells the truth. See FAKED above.
            return "faked"
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
    samples = [r for r in rows if outcome_of(r) in SAMPLE]
    # Two denominators, because they answer different questions. `population`
    # is what the target was ASKED, and the same-set guard and the budget
    # warning are both about that -- a sample set aside after the fact is not
    # a target being asked a different set. `scored` is what it is MEASURED
    # against, which drops the rows set aside so a sample neither flatters a
    # target's coverage nor counts against it.
    population = document.get("population") or len(rows)
    scored = population - len(samples)
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
        "scored": scored,
        "measured": len(rows),
        "served": len(served),
        "partial": len(partial),
        "sample": len(samples),
        "coverage_pct": round(100.0 * len(served) / scored, 1) if scored else None,
        "median_served_c2b_s": median(c2b),
        "n_c2b": len(c2b),
        "median_population_c2b_s": censored_median(c2b, scored),
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


def builds_document(round_name):
    path = os.path.join(ROOT, "results", round_name, "versions.json")
    if not os.path.exists(path):
        return {}
    with open(path) as handle:
        return json.load(handle)


def builds(round_name):
    """What each target was, in the round being reported.

    Written by the round itself (harness/versions.py) while each target was
    up. Absent for a round that ran before versions were recorded, in which
    case nothing is printed rather than a guess.
    """
    return builds_document(round_name).get("targets") or {}


def build_label(record):
    """One line for a build: what it calls itself, and what pins it."""
    if not record:
        return "not recorded"
    version = record.get("manifest_version") or "?"
    pin = (record.get("image_digest") or record.get("build_commit")
           or record.get("binary_sha256") or "")
    if pin.startswith("sha256:"):
        pin = pin[7:]
    return f"v{version}" + (f" `{pin[:12]}`" if pin else "")


def render_builds(round_name, out):
    """The builds table. A round that says `latest` has to say which."""
    measured = builds(round_name)
    if not measured:
        return
    out.append("## What was measured")
    out.append("")
    out.append("`:latest` is a moving tag, so a round records the digest it actually ran "
               "as well as the version the addon claims. Read this before comparing any "
               "number here with another round's.")
    out.append("")
    document = builds_document(round_name)
    if document.get("reconstructed"):
        out.append(f"**Reconstructed after the round, not recorded by it.** "
                   f"{document.get('note', '')}")
        out.append("")
    out.append("| Target | Version | Pinned as | Built |")
    out.append("|---|---|---|---|")
    for name, record in measured.items():
        pin = (record.get("image_digest") or record.get("build_commit")
               or record.get("binary_sha256") or "-")
        if pin.startswith("sha256:"):
            pin = pin[7:]
        built = record.get("image_created") or record.get("build_at") or ""
        if not built and record.get("binary_mtime"):
            built = datetime.fromtimestamp(record["binary_mtime"],
                                           timezone.utc).isoformat(timespec="seconds")
        built = built or "-"
        out.append(f"| {name} | v{record.get('manifest_version') or '?'} "
                   f"| `{pin[:16]}` | {built[:19]} |")
    out.append("")


def render_against(summaries, round_name, other_round, cap_bytes, out):
    """This round beside an earlier one, per target, with the builds beside it.

    A version-to-version round answers one question -- did anything change --
    and the only way to answer it wrongly is to print two numbers without
    saying whether the build moved between them. A target whose image did not
    move is a repeatability check, and its delta is this round's own noise
    rather than a change in the product.
    """
    documents = load(other_round)
    if not documents:
        return
    caps = {d.get("playable_cap_bytes") for d in documents.values() if d.get("playable_cap_bytes")}
    other_cap = max(caps) if caps else cap_bytes
    before = {name: summarise(name, document, other_cap)
              for name, document in documents.items()}
    now_builds, then_builds = builds(round_name), builds(other_round)
    out.append(f"## Against {other_round}")
    out.append("")
    out.append(f"The same fixed set, the same parity rules, measured again on the build each "
               f"target shipped since. A row whose build did not move is a repeatability "
               f"check and its deltas are this round's noise, not a change in the product.")
    out.append("")
    out.append(f"| Target | Build | Served | Coverage | median c2b (population) | median c2b (served) |")
    out.append("|---|---|---|---|---|---|")
    for summary in summaries:
        name = summary["target"]
        was = before.get(name)
        then = then_builds.get(name)
        moved = build_label(then)
        out.append(
            f"| {name} | {moved} → {build_label(now_builds.get(name))} "
            f"| {was['served'] if was else '-'}/{was.get('scored', was['population']) if was else '-'}"
            f" → {summary['served']}/{summary['scored']} "
            f"| {was['coverage_pct'] if was else '-'}% → {summary['coverage_pct']}% "
            f"| {seconds(was['median_population_c2b_s']) if was else '-'}"
            f" → {seconds(summary['median_population_c2b_s'])} "
            f"| {seconds(was['median_served_c2b_s']) if was else '-'}"
            f" → {seconds(summary['median_served_c2b_s'])} |")
    out.append("")


def combine_noise_rounds(round_names):
    """Combine independent clean-start samples into repeat passes per target.

    Noise samples are intentionally separate rounds so every pass starts from
    the same empty state.  Treating either directory on its own as a repeat
    would produce no floor at all, while putting both passes in one invocation
    would let the second inherit the first pass's cache.
    """
    combined = {}
    for round_name in round_names:
        for target, document in load(round_name).items():
            record = combined.setdefault(target, {
                "target": target,
                "passes": [],
            })
            record["passes"].extend(document.get("passes") or [])
    return combined


def load_resources(round_name):
    path = os.path.join(ROOT, "results", round_name, "resources.json")
    if not os.path.exists(path):
        return {}
    with open(path) as handle:
        return json.load(handle).get("targets") or {}


def render_resources(measured, summaries):
    if not measured:
        return ["## Resources", "", "**Not measured.** This round has no per-target "
                "CPU, memory or disk sample file.", ""]
    coverage = {summary["target"]: summary for summary in summaries}
    rows = []
    for target, values in measured.items():
        population = (coverage.get(target) or {}).get("population") or 1
        rows.append({"target": target, **values,
                     "cpu_s_per_title": values.get("cpu_s", 0) / population,
                     "disk_read_MB_per_title": values.get("disk_read_MB", 0) / population,
                     "disk_write_MB_per_title": values.get("disk_write_MB", 0) / population})

    def order(key):
        return sorted(rows, key=lambda row: (row.get(key) is None, row.get(key) or 0))

    out = ["## Resources", "",
           "Sampled once per second from the target's own process tree while its "
           "protocol phase ran. CPU and block-I/O rankings use the fixed title "
           "population as the denominator, so a target does not win by failing early. "
           "State disk is allocated bytes in that target's run directory; source trees "
           "and container images are outside it.", "",
           "| Target | CPU s/title | CPU p95 cores | peak RSS MB | disk read MB/title | disk write MB/title | state Δ MB |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for row in sorted(rows, key=lambda item: item["cpu_s_per_title"]):
        out.append(
            f"| {row['target']} | {row['cpu_s_per_title']:.2f} "
            f"| {row.get('cpu_cores_p95', 0):.3f} | {row.get('rss_peak_MB', 0):.2f} "
            f"| {row['disk_read_MB_per_title']:.2f} | {row['disk_write_MB_per_title']:.2f} "
            f"| {row.get('state_disk_delta_MB', 0):.2f} |")
    out.append("")
    labels = {
        "cpu_s_per_title": "CPU seconds/title",
        "cpu_cores_p95": "CPU p95 cores",
        "rss_peak_MB": "peak RSS",
        "disk_read_MB_per_title": "disk reads/title",
        "disk_write_MB_per_title": "disk writes/title",
        "state_disk_delta_MB": "state growth",
    }
    for key, label in labels.items():
        ranked = order(key)
        out.append(f"- {label} (lower is better): " + " < ".join(
            f"{index}. {row['target']}" for index, row in enumerate(ranked, 1)))
    out.append("")
    return out


def render_metric_rankings(summaries):
    """Rank every protocol outcome in its own direction.

    The main tables keep related measurements together for diagnosis, but one
    table's row order cannot simultaneously rank coverage, latency and read
    rate. This section makes every ordering explicit instead of inviting the
    reader to treat the click-to-byte order as the order for every column.
    """
    metrics = [
        ("Coverage", lambda row: row.get("coverage_pct"), True, lambda v: f"{v:.1f}%"),
        ("Population click-to-byte", lambda row: row.get("median_population_c2b_s"), False, seconds),
        ("Served click-to-byte", lambda row: row.get("median_served_c2b_s"), False, seconds),
        ("Stream list", lambda row: row.get("median_stream_list_s"), False, seconds),
        ("Resolve", lambda row: row.get("median_resolve_s"), False, seconds),
        ("TTFB", lambda row: row.get("median_ttfb_s"), False, seconds),
        ("Median throughput", lambda row: row.get("median_throughput_mb_s"), True,
         lambda v: f"{v:.2f} MB/s"),
        ("Median p05 window", lambda row: row.get("median_p05_window_mb_s"), True,
         lambda v: f"{v:.2f} MB/s"),
        ("Titles sustaining 25 Mbps", lambda row: row.get("sustain_25mbps_n"), True,
         lambda v: str(v)),
        ("Correct outcomes", lambda row: row.get("verdicts", {}).get("correct", 0), True,
         lambda v: str(v)),
        ("Faked successes", lambda row: row.get("verdicts", {}).get("faked", 0), False,
         lambda v: str(v)),
        ("Titles offering oversize streams", lambda row: row.get("over_cap_titles"), False,
         lambda v: str(v)),
        ("Oversize-only picks", lambda row: row.get("picked_over_cap"), False,
         lambda v: str(v)),
    ]
    out = ["## Metric rankings", "",
           "Each metric is ordered independently. Missing population medians are "
           "DNF because fewer than half the fixed population was served.", ""]
    for label, value_of, higher, format_value in metrics:
        ranked = sorted(
            summaries,
            key=lambda row: (
                value_of(row) is None,
                -(value_of(row) or 0) if higher else (value_of(row) or 0),
                row["target"],
            ),
        )
        direction = "higher is better" if higher else "lower is better"
        places = []
        index = 0
        while index < len(ranked):
            value = value_of(ranked[index])
            end = index + 1
            while end < len(ranked) and value_of(ranked[end]) == value:
                end += 1
            names = ", ".join(row["target"] for row in ranked[index:end])
            tie = " (tie)" if end - index > 1 else ""
            places.append(f"{index + 1}{tie}. {names} "
                          f"({format_value(value) if value is not None else 'DNF'})")
            index = end
        out.append(f"- {label} ({direction}): " + "; ".join(places))
    out.append("")
    return out


def render(documents, round_name, client=None, noise_documents=None, against=None,
           resource_documents=None):
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

    render_builds(round_name, out)

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
        out.append(f"| {s['target']} | {s['language']} | {s['served']}/{s['scored']} "
                   f"| {s['coverage_pct']}% "
                   f"| {seconds(s['median_population_c2b_s']) if s['median_population_c2b_s'] is not None else '>budget'} "
                   f"| {seconds(s['median_served_c2b_s'])} | {s['n_c2b']} |")
    out.append("")

    if against:
        render_against(summaries, round_name, against, cap_bytes, out)

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
                   f"| {seconds(s['median_served_c2b_s'])} | {s['served']}/{s['scored']} |")
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
                   f"| {s['served']}/{s['scored']} |")
    out.append("")

    out.append("## Did it do the right thing")
    out.append("")
    out.append("Part of the set is negative. Two entries are real titles with "
               "nothing posted, where an empty list is correct and a stream is a "
               "fabrication; one is an id whose indexer answers with an unrelated "
               "feed, where the question is whether the addon forwards it.")
    out.append("")
    out.append("The tiers are in severity order. `faked` is below `missed` "
               "deliberately: a refusal is information a client can act on, and a "
               "2xx over an error clip or a stretch of zeros ends the search with "
               "the viewer holding nothing and no way to know it. A row that "
               "served the release's sample instead of its feature is not judged "
               "here at all; it is set aside and counted in its own section below.")
    out.append("")
    keys = ["correct", "partial", "missed", "faked", "fabricated",
            "forwarded-garbage", "unclassified"]
    out.append("| Target | " + " | ".join(keys) + " |")
    out.append("|---|" + "---|" * len(keys))
    for s in summaries:
        out.append(f"| {s['target']} | " + " | ".join(str(s["verdicts"].get(k, 0)) for k in keys) + " |")
    out.append("")

    samples = sum(s["outcomes"].get("sample", 0) for s in summaries)
    if samples:
        out.append("## The sample, and why it is not scored")
        out.append("")
        out.append(f"**{samples} {'row' if samples == 1 else 'rows'} across the field "
                   f"served the sample rather than "
                   f"the film.** A scene release ships one inside the same archive as "
                   f"the feature, so an addon has to choose between two real videos, "
                   f"and the one it opens is a product decision -- the same class of "
                   f"choice as a size cap or a ranking rule. What arrives is whole, "
                   f"valid and from the release the viewer asked for. It is simply not "
                   f"the film, and it ends long before a read window closes, which is "
                   f"why this reads as a truncation until it is named.")
        out.append("")
        out.append("So these rows score neither way: out of the served count, out of "
                   "every speed median, out of the correctness tiers, and out of the "
                   "population the rest are measured against. A target is told what it "
                   "did, and no target gains or loses a point for it. Measured as a "
                   "body at or under "
                   f"{SAMPLE_MAX_RELEASE_FRACTION:.0%} of the release the target itself "
                   "advertised; real samples run near one per cent.")
        out.append("")
        out.append("| Target | samples served | scored population |")
        out.append("|---|---|---|")
        for s in summaries:
            n = s["outcomes"].get("sample", 0)
            if n:
                out.append(f"| {s['target']} | {n} | {s['scored']} |")
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

    out.extend(render_metric_rankings(summaries))
    out.extend(render_resources(resource_documents or {}, summaries))
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
    parser.add_argument("--against",
                        help="an earlier round to print this one beside, per target, with "
                             "the build each one measured. A round that changes versions "
                             "has to say which moved and which did not")
    parser.add_argument("--noise-round", action="append",
                        help="read the noise floor from another round directory. Repeat "
                             "passes are expensive over the whole set, so the floor is "
                             "usually measured on a sample in its own run and quoted here. "
                             "Repeat this option for independent clean-start passes")
    args = parser.parse_args()

    documents = load(args.round)
    client = load(args.round, prefix="client-")
    floors = combine_noise_rounds(args.noise_round) if args.noise_round else None
    measured_resources = load_resources(args.round)
    text = render(documents, args.round, client, floors, against=args.against,
                  resource_documents=measured_resources)
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
