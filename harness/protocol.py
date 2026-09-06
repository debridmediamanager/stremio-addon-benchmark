#!/usr/bin/env python3
"""The protocol plane: drive one addon's own HTTP interface, end to end.

This is the measurement `docs/design.md` calls the protocol plane. It asks a
target for a stream list the way Stremio would, picks the option a viewer
would land on, follows it to bytes, reads for a while, and seeks. Nothing here
knows anything about a player, which is the point: it isolates the addon's
latency from Stremio's own, and the client plane isolates the other half.

    python3 harness/protocol.py --target zurg
    python3 harness/protocol.py --target zurg --only-title tt0111161
    python3 harness/protocol.py --target zurg --repeat 3 --sample 4   # noise floor

The timing split, and how the three published numbers nest:

    t0 ---- GET /stream/{type}/{id}.json ------------------> stream array
       |<------------ stream_list_s ------------->|
                                                  pick the top playable option
       t1 -- GET that stream url, 302s followed, 503s waited out -->
          |<-------- resolve_s ------->|   headers of the response that serves
          |<-------------- ttfb_s ---------------->| first body byte
       |<--------------- click_to_byte_s --------------------------->|

`resolve_s` is a prefix of `ttfb_s`, and `stream_list_s + ttfb_s` is
`click_to_byte_s` bar the microseconds spent choosing. Publishing the three
together is the whole diagnosis: a target losing on search has a different
product problem from one losing on ingest, and a single cold-open number hides
which.

Everything this writes lands in results/<round>/protocol-<target>.json. Read
docs/design.md before quoting any of it, and run harness/scan_leaks.py over
results/ before pushing any of it: a target echoes its provider and its
indexers back inside error strings, which is how a hostname reaches a
published artifact without anyone writing it down.
"""
import argparse
import http.client
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402
from sanitize import Scrubber  # noqa: E402

# Identities are replaced on the way into a result file rather than edited out
# of one afterwards: a target prints its indexer's real name in the middle of
# every stream description, and the news host inside its error strings.
# harness/scan_leaks.py still runs in front of a push to catch what this missed.
SCRUB = Scrubber()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLES = os.path.join(ROOT, "corpus", "titles.json")
ENDPOINTS = os.path.join(ROOT, "config", "endpoints.local.json")

# The harness identifies itself as what it is. It never borrows a target's
# User-Agent: one of the seven indexers refuses any client whose UA contains
# `stremio` or `aiostreams`, and an addon an indexer refuses by name has a real
# coverage problem that this round reports rather than engineers away. See
# docs/design.md, trap 2.
USER_AGENT = "usenet-addon-benchmark/1 (benchmark; contact via github.com/debridmediamanager)"

# how long one title may take end to end before it is abandoned as a timeout.
# Generous on purpose: an ingest that fetches an NZB, opens a 99-volume archive
# and learns article sizes legitimately takes tens of seconds cold, and cutting
# that off would publish a timeout where the honest answer is "slow".
TITLE_BUDGET_S = 180
# the sustained read. Thirty seconds is what `sustain_25mbps` is defined over.
READ_S = 30
# a first chunk small enough that time-to-first-byte is not time-to-first-chunk
FIRST_CHUNK = 64 * 1024
# what a seek costs: request a small window at each mark and time its first byte
SEEK_WINDOW = 256 * 1024
SEEK_MARKS = [0.01, 0.25, 0.50, 0.75, 0.95]
# 25 Mbps is a 1080p stream with headroom. Held for the whole read window, in
# every one-second window, not on average -- an average that holds hides the
# second at zero during which the player stops.
SUSTAIN_BPS = 25 * 1000 * 1000

OUTCOMES = (
    "served",           # bytes arrived, and they are bytes rather than a fill
    "empty-list",       # no playable stream was offered
    "resolve-failed",   # the chosen stream never reached something serving bytes
    "zero-bytes",       # a success status over a body that is entirely zeros
    "truncated",        # the body started and stopped short of what was asked
    "timeout",          # the title exhausted its budget
    "error",            # the target failed in a way none of the above describes
)


# --------------------------------------------------------------------------
# HTTP, deliberately at a low enough level to time a first body byte
# --------------------------------------------------------------------------

def _connect(url, timeout):
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.scheme == "https":
        context = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return conn, path


def open_stream(url, timeout, headers=None):
    """Start a GET and return (conn, response) with the body unread.

    urllib reads eagerly enough that a first-body-byte measurement taken
    through it is really a first-chunk measurement. http.client hands back the
    response with the body still on the socket, which is the only way this
    file can honestly claim to time a first byte.
    """
    conn, path = _connect(url, timeout)
    send = {"User-Agent": USER_AGENT, "Accept": "*/*", "Connection": "close"}
    send.update(headers or {})
    conn.request("GET", path, headers=send)
    response = conn.getresponse()
    return conn, response


def get_json(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body)


def content_range_total(value):
    """Total size out of `bytes 0-65535/394141285`, or None."""
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


# --------------------------------------------------------------------------
# reading a target's stream list
# --------------------------------------------------------------------------

def pick(options, cap_bytes):
    """Choose the stream a capped viewer would land on, and say how.

    Size-cap parity used to be a per-target configuration rule: set the same
    maximum in five different filters and verify it from the served list. Three
    of the five honour that. streamnzb 5.17.0 does not honour it through any
    surface -- a bound filter profile, a `limits` entry and a `reject all` rule
    all read back saved and none of them changes the served list, measured on
    titles the instance had never searched -- so a round resting on five
    filters agreeing rests on something unverifiable.

    So the cap is applied here instead, identically for everyone: the
    highest-ranked option at or below the cap. Each addon still does its own
    searching, ranking and picking; the round just declines to play a 36 GB
    remux the desktop player would refuse. What a target *offered* stays on the
    row, because offering 129 oversize streams is a finding about the product
    rather than a detail to smooth over.

    Preference order: within the cap, then unknown size, then the smallest of
    the oversize ones. The last case is not a failure -- four titles in the set
    are `oversize-only`, where every release is above the cap and the smallest
    one is the honest answer.
    """
    sized = [(index, option, describe(option)["size_bytes"]) for index, option in enumerate(options)]
    within = [(i, o, size) for i, o, size in sized if isinstance(size, int) and size <= cap_bytes]
    if within:
        index, option, _ = within[0]
        return option, index, False
    unknown = [(i, o, size) for i, o, size in sized if not isinstance(size, int)]
    if unknown:
        index, option, _ = unknown[0]
        return option, index, False
    index, option, _ = min(sized, key=lambda item: item[2])
    return option, index, True


# A target that cannot serve a title does not always answer with an empty list.
# Comet answers 200 with one stream whose name is `[⚠️] Comet setup` or
# `[❌] Comet` and whose description is the problem -- an obsolete
# configuration, a missing engine, unavailable metadata -- pointing at its own
# configure page or a placeholder host. Counted as an option, that is a target
# credited with coverage for every title it cannot serve, and, worse, a `served`
# row: the placeholder URL answers bytes.
NOTICE_PREFIXES = ("[⚠️]", "[❌]", "⚠️", "❌")
NOTICE_HOSTS = ("comet.feels.legal",)
NOTICE_TEXT = ("obsolete configuration", "please re-configure", "unable to get metadata",
               "is unavailable", "open the addon configuration")


def is_notice(stream):
    """Is this a status message wearing a stream's shape?"""
    name = (stream.get("name") or "")
    if name.strip().startswith(NOTICE_PREFIXES):
        return True
    url = stream.get("url") or ""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in NOTICE_HOSTS:
        return True
    if url.rstrip("/").endswith("/configure"):
        return True
    blob = (name + " " + (stream.get("description") or "")).lower()
    return any(marker in blob for marker in NOTICE_TEXT)


def playable(streams):
    """The options a player could actually open, in the order offered.

    Three things get filtered out, and each one would otherwise be counted as
    coverage a target does not have:

      * a stream carrying `externalUrl` and no `url` is a control rather than a
        release -- zurg appends one to a cached list so a viewer can clear the
        cache;
      * `infoHash` without a url is a torrent, which nothing in this field
        should be offering and which this harness cannot play;
      * a status notice, which is the one that matters, because it carries a
        real url and would be measured as a served stream. See `is_notice`.
    """
    out = []
    for stream in streams or []:
        if not isinstance(stream, dict):
            continue
        if stream.get("url") and not is_notice(stream):
            out.append(stream)
    return out


def notices(streams):
    """The status messages a target answered with, kept for the row."""
    return [(s.get("name") or "").strip() + ": " + (s.get("description") or "").strip()
            for s in (streams or []) if isinstance(s, dict) and is_notice(s)]


def describe(stream):
    """Name, size and indexer for the `chosen` column.

    Every target writes these somewhere different and none of them is
    obliged to write them at all. behaviorHints.videoSize is the only
    structured field the protocol defines; the rest is prose, so the size is
    read out of the description text when the hint is absent and left null
    when neither carries it. A null here is a fact about the target's stream
    list, not a gap in the harness -- a viewer choosing between releases sees
    exactly what this sees.
    """
    hints = stream.get("behaviorHints") or {}
    text = "\n".join(filter(None, [stream.get("name", ""), stream.get("description", ""),
                                   stream.get("title", "")]))
    size = hints.get("videoSize")
    if not isinstance(size, int):
        size = parse_size(text)
    release = hints.get("filename") or first_release_line(text)
    return {
        "release": SCRUB(release),
        "size_bytes": size,
        "indexer": SCRUB.indexer_label(parse_indexer(text)),
        "name": SCRUB(stream.get("name")),
        "description": SCRUB(stream.get("description")),
    }


SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
              "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


def parse_size(text):
    """`36.24 GB` anywhere in a stream's prose, as bytes."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(TiB|GiB|MiB|KiB|TB|GB|MB|KB|B)\b", text, re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    return int(amount * SIZE_UNITS[match.group(2).upper()])


def parse_indexer(text):
    """The indexer credit, when a target prints one.

    Every target writes this differently -- zurg uses ` · name`, StremThru a
    `🔍 name` field, and others not at all -- so the first thing looked for is
    the label itself. Each target in this round is configured with the parity
    labels as its indexer names, so `indexer-a` appears verbatim in whatever
    prose that target happens to use, and no per-target format needs guessing.
    """
    labelled = re.search(r"\bindexer-[a-g]\b", text)
    if labelled:
        return labelled.group(0)
    for marker in ("🔍", "·"):
        for line in text.splitlines():
            if marker in line:
                tail = line.rsplit(marker, 1)[1].strip()
                # a marker can be followed by another field on the same line
                tail = tail.split("  ")[0].strip()
                if tail and len(tail) < 40:
                    return tail
    return None


def first_release_line(text):
    """The release name out of a stream's prose.

    Scene names have no spaces and plenty of separators, which is the only
    property they share across every target's formatting: zurg puts the name
    on its own first line, StremThru puts it last behind a folder emoji and
    three other emoji-labelled fields above it. Picking the first line that
    "looks like a name" gave `📺 DV 🎧 DTS Lossless | 5.1` for StremThru, so
    the longest space-free candidate is taken instead.
    """
    best = None
    for token in re.split(r"[\s]+", text):
        token = token.strip("()[]|,")
        if len(token) < 20 or "." not in token and "-" not in token:
            continue
        if token.startswith(("http://", "https://")):
            continue
        if best is None or len(token) > len(best):
            best = token
    if best:
        return best
    return next((line.strip() for line in text.splitlines() if line.strip()), None)


# --------------------------------------------------------------------------
# following a chosen stream to bytes
# --------------------------------------------------------------------------

class Resolution:
    """What happened between choosing a stream and something serving bytes."""

    def __init__(self):
        self.hops = []
        self.conn = None
        self.response = None
        self.final_url = None
        self.resolve_s = None
        self.waited_s = 0.0
        self.error = None


def resolve(url, deadline, first_byte_range=True, max_hops=8):
    """Follow a chosen stream URL until something answers with a body.

    Three behaviours the targets in this field actually have, none of which a
    plain urlopen survives:

      * a 302 to the endpoint that really serves ranges, which is how both Go
        targets hand playback over;
      * a 503 with Retry-After while an ingest is still opening the archive,
        which is a wait rather than a failure and is timed as `waited_s`
        inside resolve rather than counted as latency the reader caused;
      * a 200 with the whole file, for the targets that serve directly.

    The Range header is sent from the first hop. A target that ignores it and
    answers 200 is recorded as such rather than corrected: whether an addon
    honours a range is the difference between a player that can seek and one
    that cannot.
    """
    result = Resolution()
    started = time.monotonic()
    current = url
    headers = {}
    if first_byte_range:
        # open-ended on purpose. A bounded first range ends the body after the
        # chunk, and the sustained read then reports every target as truncated
        # -- which is what the first smoke run of this harness did. This still
        # gets a 206 and a Content-Range to learn the total size from, and the
        # connection is closed when the read window closes.
        headers["Range"] = "bytes=0-"

    for _ in range(max_hops):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.error = "budget exhausted while resolving"
            return result
        try:
            conn, response = open_stream(current, min(remaining, 60), headers)
        except Exception as problem:
            result.error = f"{type(problem).__name__}: {problem}"
            return result

        status = response.status
        location = response.getheader("Location")
        result.hops.append({
            "url_host": urllib.parse.urlsplit(current).netloc,
            "status": status,
            "content_type": response.getheader("Content-Type"),
        })

        if status in (301, 302, 303, 307, 308) and location:
            response.read()
            conn.close()
            current = urllib.parse.urljoin(current, location)
            continue

        if status == 503:
            retry_after = response.getheader("Retry-After")
            response.read()
            conn.close()
            wait = 2.0
            if retry_after and retry_after.strip().isdigit():
                wait = min(float(retry_after.strip()), 15.0)
            if time.monotonic() + wait > deadline:
                result.error = "still 503 when the title budget ran out"
                return result
            time.sleep(wait)
            result.waited_s += wait
            continue

        if status >= 400:
            body = response.read(2048).decode("utf-8", "replace")
            conn.close()
            result.error = f"http {status}: {body.strip()[:200]}"
            return result

        result.conn = conn
        result.response = response
        result.final_url = current
        result.resolve_s = time.monotonic() - started
        return result

    result.error = f"more than {max_hops} redirects"
    return result


def read_window(conn, response, seconds, deadline):
    """Read the body for `seconds`, timing the first byte and each window.

    Returns (ttfb_from_now_s, bytes_read, per_second_bytes, ended_early, sample).
    `ended_early` distinguishes a body that stopped short of the window from
    one the harness stopped on time -- design trap 7: a stream that breaks
    mid-body after a good header is a different failure from one that never
    started. `sample` is the opening bytes, kept so the same trap's other half
    -- a success status over a zero fill -- is checked here too and not only
    on the seeks.
    """
    started = time.monotonic()
    first_byte_at = None
    total = 0
    windows = []
    window_bytes = 0
    window_ends = started + 1.0
    ended_early = False
    sample = b""

    while True:
        now = time.monotonic()
        if now >= deadline or (first_byte_at is not None and now - first_byte_at >= seconds):
            break
        try:
            # read1, not read: read(n) blocks until n bytes arrive, which
            # would make the first measurement time-to-64KB rather than
            # time-to-first-byte
            chunk = response.read1(1 if first_byte_at is None else 256 * 1024)
        except Exception:
            ended_early = True
            break
        if not chunk:
            ended_early = True
            break
        if first_byte_at is None:
            first_byte_at = time.monotonic()
            window_ends = first_byte_at + 1.0
        if len(sample) < 64 * 1024:
            sample += chunk[: 64 * 1024 - len(sample)]
        total += len(chunk)
        window_bytes += len(chunk)
        now = time.monotonic()
        while now >= window_ends:
            windows.append(window_bytes)
            window_bytes = 0
            window_ends += 1.0

    ttfb = None if first_byte_at is None else first_byte_at - started
    read_seconds = 0.0 if first_byte_at is None else time.monotonic() - first_byte_at
    return ttfb, total, windows, ended_early, sample, read_seconds


def looks_like_a_fill(sample):
    """A 206 is not proof of bytes -- design trap 7.

    The mount rounds found engines answering a range with a zero fill and the
    status line already sent. This does not attempt to prove a stream is the
    right film; it separates "served something" from "served nothing dressed
    as something", which is the failure that reads as a pass everywhere else.
    """
    if not sample:
        return True
    return sample.count(0) == len(sample)


# --------------------------------------------------------------------------
# one title against one target
# --------------------------------------------------------------------------

def measure_title(target, base, title, read_s, cap_bytes, do_seeks=True):
    row = {
        "id": title["id"],
        "title": title["title"],
        "type": title["type"],
        "measured_tier": title.get("measured_tier"),
        "expected_outcome": title.get("expected_outcome"),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stream_list_s": None,
        "n_streams": None,
        "n_within_cap": None,
        "notices": None,
        "max_offered_bytes": None,
        "chosen": None,
        "picked_rank": None,
        "picked_over_cap": None,
        "resolve_s": None,
        "resolve_waited_s": None,
        "ttfb_s": None,
        "click_to_byte_s": None,
        "http_status": None,
        "content_bytes_total": None,
        "read_bytes": None,
        "read_s": None,
        "windows_n": None,
        "throughput_mb_s": None,
        "p05_window_mb_s": None,
        "sustain_25mbps": None,
        "seek_profile": None,
        "outcome": None,
        "detail": None,
    }
    t0 = time.monotonic()
    deadline = t0 + TITLE_BUDGET_S

    stream_url = base["stream"].format(type=title["type"], id=urllib.parse.quote(title["id"], safe=""))
    try:
        payload = get_json(stream_url, min(60, TITLE_BUDGET_S))
    except Exception as problem:
        row["stream_list_s"] = time.monotonic() - t0
        row["outcome"] = "error"
        row["detail"] = f"stream list: {type(problem).__name__}: {problem}"
        return row
    row["stream_list_s"] = round(time.monotonic() - t0, 3)

    options = playable(payload.get("streams"))
    row["n_streams"] = len(options)
    told = notices(payload.get("streams"))
    if told:
        row["notices"] = told[:3]
    sizes = [describe(option)["size_bytes"] for option in options]
    sizes = [size for size in sizes if isinstance(size, int)]
    row["max_offered_bytes"] = max(sizes) if sizes else None

    if not options:
        # a notice is not an empty list: the target had something to say about
        # why, and that is a different finding from "nothing is posted"
        row["outcome"] = "empty-list"
        if told:
            row["detail"] = told[0][:200]
        return row

    chosen, rank, over_cap = pick(options, cap_bytes)
    row["chosen"] = describe(chosen)
    row["picked_rank"] = rank
    row["picked_over_cap"] = over_cap
    row["n_within_cap"] = sum(1 for option in options
                              if isinstance(describe(option)["size_bytes"], int)
                              and describe(option)["size_bytes"] <= cap_bytes)

    resolution = resolve(chosen["url"], deadline)
    row["resolve_s"] = None if resolution.resolve_s is None else round(resolution.resolve_s, 3)
    row["resolve_waited_s"] = round(resolution.waited_s, 3)
    row["detail"] = resolution.error
    row["hops"] = resolution.hops
    if resolution.response is None:
        row["outcome"] = "timeout" if time.monotonic() >= deadline else "resolve-failed"
        return row

    conn, response = resolution.conn, resolution.response
    row["http_status"] = response.status
    row["content_bytes_total"] = (content_range_total(response.getheader("Content-Range"))
                                  or int(response.getheader("Content-Length") or 0) or None)

    try:
        (ttfb_tail, read_bytes, windows, ended_early,
         sample, read_seconds) = read_window(conn, response, read_s, deadline)
        if ttfb_tail is None:
            row["outcome"] = "zero-bytes"
            row["detail"] = f"http {response.status} with no body byte before the budget"
            return row
        # ttfb_s is measured from the request to the chosen stream URL, which
        # is why resolve_s nests inside it rather than adding to it.
        row["ttfb_s"] = round(resolution.resolve_s + ttfb_tail, 3)
        row["click_to_byte_s"] = round(row["stream_list_s"] + row["ttfb_s"], 3)
        row["read_bytes"] = read_bytes
        row["read_s"] = round(read_seconds, 2)
        row["windows_n"] = len(windows)
        if windows:
            per_second = [w / 1e6 for w in windows]
            row["throughput_mb_s"] = round(sum(per_second) / len(per_second), 2)
            row["p05_window_mb_s"] = round(percentile(per_second, 5), 2)
            row["sustain_25mbps"] = all(w * 8 >= SUSTAIN_BPS for w in windows)
        else:
            row["throughput_mb_s"] = round(read_bytes / 1e6 / max(read_s, 0.001), 2)
            row["p05_window_mb_s"] = None
            row["sustain_25mbps"] = False
        if looks_like_a_fill(sample):
            # design trap 7: the status line is already sent and the length is
            # right. Only reading the body separates this from a served stream
            row["outcome"] = "zero-bytes"
            row["detail"] = f"http {response.status} over {read_bytes} bytes that are entirely zero"
        elif ended_early:
            row["outcome"] = "truncated"
            row["detail"] = "the body stopped before the read window closed"
        else:
            row["outcome"] = "served"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if do_seeks and row["content_bytes_total"] and row["outcome"] in ("served", "truncated"):
        row["seek_profile"] = seek_profile(resolution.final_url, row["content_bytes_total"], deadline)
    return row


def percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * pct / 100.0
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def seek_profile(url, total, deadline):
    """Time to first byte at five marks, then one backward seek.

    The backward seek is the one a cache serves worst and the one a viewer
    does most: scrubbing back after overshooting. It is taken last, from the
    95% mark back to 25%, so it is genuinely backward rather than a second
    forward read that happens to be at a lower offset.
    """
    marks = []
    for fraction in SEEK_MARKS:
        marks.append(("%.0f%%" % (fraction * 100), int(total * fraction)))
    marks.append(("back to 25%", int(total * 0.25)))

    out = []
    for label, offset in marks:
        if time.monotonic() >= deadline:
            out.append({"mark": label, "ttfb_s": None, "note": "budget exhausted"})
            continue
        end = min(offset + SEEK_WINDOW - 1, max(total - 1, offset))
        started = time.monotonic()
        record = {"mark": label, "offset": offset}
        try:
            conn, response = open_stream(url, 60, {"Range": f"bytes={offset}-{end}"})
            chunk = response.read(1)
            record["ttfb_s"] = round(time.monotonic() - started, 3)
            record["status"] = response.status
            body = chunk + response.read(64 * 1024)
            record["zero_fill"] = looks_like_a_fill(body)
            conn.close()
        except Exception as problem:
            record["ttfb_s"] = None
            record["note"] = f"{type(problem).__name__}: {problem}"
        out.append(record)
    return out


# --------------------------------------------------------------------------
# a pass over the whole set
# --------------------------------------------------------------------------

def endpoints_for(name):
    """The minted URLs for one target, or the template if it needs none."""
    minted = {}
    if os.path.exists(ENDPOINTS):
        with open(ENDPOINTS) as handle:
            minted = json.load(handle)
    target = registry.TARGETS[name]
    entry = minted.get(name, {})
    stream = entry.get("stream")
    if not stream:
        manifest = entry.get("manifest")
        if manifest and manifest.endswith("/manifest.json"):
            # every addon serves stream/ next to its manifest, which is the
            # only way to build a URL for the three targets that mint a
            # per-install segment nobody can write from a template
            stream = manifest[: -len("manifest.json")] + "stream/{type}/{id}.json"
    if not stream:
        stream = target["stream"]
    if "{token}" in stream or "{uuid}" in stream or "{userdata}" in stream or "{config}" in stream:
        raise SystemExit(
            f"{name}: no minted stream URL. Put its real manifest URL in "
            f"config/endpoints.local.json and run harness/verify_endpoints.py first.\n"
            f"  template is {stream}"
        )
    return {"stream": stream, "manifest": entry.get("manifest") or target["manifest"]}


def load_titles(path, only=None, sample=None, seed=7):
    with open(path) as handle:
        data = json.load(handle)
    titles = [t for t in data["titles"] if t.get("in_round")]
    if only:
        titles = [t for t in titles if t["id"] in only]
        missing = set(only) - {t["id"] for t in titles}
        if missing:
            raise SystemExit(f"--only-title names ids that are not in the round: {sorted(missing)}")
    if sample:
        # a deterministic subset, for the noise-floor pass only. Never for a
        # published row: medians are over the fixed population, with n.
        random.Random(seed).shuffle(titles)
        titles = titles[:sample]
    return data, titles


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="one target name from harness/targets.py")
    parser.add_argument("--round", default="round1", help="results/<round>/ to write into")
    parser.add_argument("--titles", default=TITLES)
    parser.add_argument("--only-title", help="comma-separated imdb ids, for a smoke test")
    parser.add_argument("--sample", type=int, help="measure N titles, deterministic; noise floor only")
    parser.add_argument("--repeat", type=int, default=1, help="passes over the set, for the noise floor")
    parser.add_argument("--read-s", type=float, default=READ_S)
    parser.add_argument("--no-seeks", action="store_true")
    parser.add_argument("--allow-shakedown", action="store_true",
                        help="measure a target whose endpoint is still a guess. For a "
                             "shakedown run only; a published round must not use it")
    args = parser.parse_args()

    if args.target not in registry.TARGETS:
        raise SystemExit(f"{args.target} is not a registered target")
    target = registry.TARGETS[args.target]
    if target["verified"] == "shakedown" and not args.allow_shakedown:
        raise SystemExit(
            f"{args.target} is still `shakedown` in harness/targets.py: its endpoint was "
            f"written from documentation, not read off a running instance.\n"
            f"Stand it up, run harness/verify_endpoints.py, and clear the flag. "
            f"A round must not discover a bad endpoint as a row of zeros."
        )

    base = endpoints_for(args.target)
    data, titles = load_titles(args.titles, 
                               args.only_title.split(",") if args.only_title else None,
                               args.sample)

    out_dir = os.path.join(ROOT, "results", args.round)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"protocol-{args.target}.json")

    cap_bytes = data.get("playable_cap_bytes") or 6 * 1024 ** 3
    budget = target.get("budget_s", registry.DEFAULT_BUDGET_S)
    started = time.monotonic()
    passes = []
    print(f"{args.target}: {len(titles)} title(s) x {args.repeat} pass(es), "
          f"budget {budget}s, read {args.read_s}s, "
          f"pick capped at {cap_bytes / 1024 ** 3:.0f} GiB")

    for index in range(args.repeat):
        rows = []
        for title in titles:
            if time.monotonic() - started > budget:
                print(f"  ! {args.target} exhausted its {budget}s budget; remaining titles unmeasured")
                break
            row = measure_title(target, base, title, args.read_s, cap_bytes,
                                not args.no_seeks)
            rows.append(row)
            print(f"  {row['id']:<14} {row['outcome']:<15}"
                  f" list={fmt(row['stream_list_s'])} n={row['n_streams']}"
                  f" ttfb={fmt(row['ttfb_s'])} c2b={fmt(row['click_to_byte_s'])}"
                  f" {fmt(row['throughput_mb_s'])}MB/s"
                  f" #{row['picked_rank']}{'!' if row['picked_over_cap'] else ''}"
                  f"  {(row['chosen'] or {}).get('release') or ''}"[:200])
        passes.append(rows)

    document = {
        "target": args.target,
        "plane": "protocol",
        "round": args.round,
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness": os.path.basename(__file__),
        "title_set_built_utc": data.get("built_utc"),
        "parity_indexers": data.get("parity_indexers"),
        "playable_cap_bytes": data.get("playable_cap_bytes"),
        "population": len(titles),
        "read_s": args.read_s,
        "cap_bytes": cap_bytes,
        "title_budget_s": TITLE_BUDGET_S,
        "target_budget_s": budget,
        "verified": target["verified"],
        "passes": passes,
    }
    # every string in the document, not only the ones known to carry a name:
    # a target's error text is unbounded prose and the round cannot enumerate
    # in advance which field it will arrive in
    with open(out_path, "w") as handle:
        json.dump(SCRUB(document), handle, indent=1)
    print(f"wrote {os.path.relpath(out_path, ROOT)}")
    print("run harness/scan_leaks.py results/ before publishing any of this")
    return 0


def fmt(value):
    return "-" if value is None else f"{value:g}"


if __name__ == "__main__":
    sys.exit(main())
