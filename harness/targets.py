#!/usr/bin/env python3
"""The round-1 field: five Usenet-backed Stremio addons, and how each is driven.

This repository asks a different question from `usenet-streaming-benchmark`.
That one asks which mount should back a Plex library and measures WebDAV
serving with the release already imported. This one asks what happens between a
user pressing play in Stremio and the first byte of video arriving, which means
discovery, indexer search, release ranking, ingest and link minting are all
inside the measurement rather than set up beforehand.

Two consequences that shape everything here:

  * **Nobody streams the same bytes.** Each addon searches, ranks and picks its
    own release for a title. That choice is part of the product, so the primary
    number lets each one pick; but it means throughput is never a like-for-like
    comparison unless the chosen release is recorded, which is why every row
    carries `chosen`. See docs/design.md, "Release-choice divergence".
  * **Coverage outranks speed.** An addon that answers four titles quickly is
    worse than one that answers eighteen slowly. No table in this repository
    ranks on time without the coverage column next to it.

`verified` records where each endpoint below came from:

  live       read back off a running instance by harness/standup/<target>.py,
             which is the only state a published round may measure
  upstream   read out of that project's own source tree
  documented from its README or docs, not yet confirmed against a live instance
  shakedown  written from a reasonable guess; MUST be confirmed by
             harness/verify_endpoints.py before a round, not discovered as a
             row of zeros afterwards

A round refuses to start while any enabled target is still `shakedown`.
"""

# how the addon hands over bytes once a stream is chosen
REDIRECT = "redirect"      # 302 to a URL it also serves
DIRECT = "direct"          # the stream url in the list is the byte URL

# how a release becomes streamable at play time
INGEST = "ingest"          # it fetches the NZB and opens the archive on demand
PRELOADED = "preloaded"    # it can only stream what is already in its library

TARGETS = {
    "zurg": {
        "role": "target",
        "language": "Go",
        "repo": "debridmediamanager/zurg",
        # the only target in the field that is a binary rather than a container,
        # so the round starts it from a command instead of a compose file
        "launch": "command",
        "start": "./zurg --config config.yml",
        "port": 9998,
        "manifest": "http://127.0.0.1:9998/stremio/{token}/manifest.json",
        "stream": "http://127.0.0.1:9998/stremio/{token}/stream/{type}/{id}.json",
        "play_mode": REDIRECT,
        "serve_mode": INGEST,
        "verified": "upstream",
        "note": (
            "the author's own project, and the reason this repository exists: "
            "rounds 3-9 measured zurg as a mount and never once exercised its "
            "addon path. Routes read from internal/handlers/stremio.go: the "
            "stream list searches every configured indexer by imdbid under a "
            "15s budget, /play/{payload} ingests the winning NZB through its "
            "own SABnzbd path, waits for the archive to list, then 302s to "
            "/strm/e/ which is what actually serves ranges. Cap "
            "stremio.max_size_gb or every top result for a popular title is a "
            "17-36 GB remux and the size-cap parity rule is broken before the "
            "round starts"
        ),
    },
    "aiostreams": {
        "role": "target",
        "language": "TypeScript",
        "repo": "Viren070/AIOStreams",
        # 3000 is its default and is taken by an unrelated service on the
        # bench host, so the round runs it on 3010. The port is not part of
        # what is measured; colliding with production would have been
        "port": 3010,
        "manifest": "http://127.0.0.1:3010/stremio/{uuid}/{password}/manifest.json",
        "stream": "http://127.0.0.1:3010/stremio/{uuid}/{password}/stream/{type}/{id}.json",
        "play_mode": DIRECT,
        "serve_mode": INGEST,
        "verified": "live",
        "standup": "harness/standup/aiostreams.py",
        "note": (
            "the largest project in the field by a wide margin and the author "
            "of the competing benchmark, which is exactly why an independent "
            "number is worth having. Its manifest path embeds a per-user uuid "
            "and an encrypted password, both minted by POST /api/v1/user, so "
            "the URL cannot be written from a template. Configured across three "
            "surfaces: the news account is global and admin-owned behind a "
            "dashboard session, the indexers are one `newznab` preset each "
            "inside the user config, and the streaming service is per-user. "
            "That last choice decides what is being measured -- `stremio_nntp` "
            "hands NNTP details to the player, a Stremio V5 desktop feature "
            "that streams nothing through the addon, so the round uses the "
            "`aiostreams` built-in engine. Not a WebDAV server: no PROPFIND "
            "anywhere in the tree, which is why it belongs in this repository "
            "and not in the mount rounds"
        ),
    },
    "stremthru": {
        "role": "target",
        "language": "Go",
        "repo": "MunifTanjim/stremthru",
        "port": 8484,
        "manifest": "http://127.0.0.1:8484/stremio/newz/{userdata}/manifest.json",
        "stream": "http://127.0.0.1:8484/stremio/newz/{userdata}/stream/{type}/{id}.json",
        "play_mode": REDIRECT,
        "serve_mode": INGEST,
        "verified": "live",
        "standup": "harness/standup/stremthru.py",
        "note": (
            "its `newz` engine is its own usenet reader. It is the only target "
            "in this field that also appears in the mount field, so it is the "
            "one place a number here can be read against a number there -- "
            "same engine, one measured through WebDAV and one through the "
            "addon protocol. Three things this cost before it ran: the addon "
            "is /stremio/newz and NOT /stremio/store, which exposes usenet only "
            "behind a debrid-shaped store token and is not what a Newz user "
            "installs; the news account lives in the vault behind an admin "
            "dashboard session while the indexers live in the addon's own "
            "userdata, so standing it up needs both surfaces; and its userdata "
            "segment is base64 of that config with the api keys inside it, so "
            "the minted URL is a credential and never leaves "
            "config/endpoints.local.json"
        ),
    },
    "streamnzb": {
        "role": "target",
        "language": "Go",
        "repo": "Gaisberg/streamnzb",
        "port": 7000,
        "manifest": "http://127.0.0.1:7000/{token}/manifest.json",
        "stream": "http://127.0.0.1:7000/{token}/stream/{type}/{id}.json",
        "play_mode": DIRECT,
        "serve_mode": INGEST,
        "verified": "live",
        "standup": "harness/standup/streamnzb.py",
        "note": (
            "measured once in the 18 August six-way round and dropped before "
            "either repository existed, so it has never appeared in a "
            "published round. The only target in the field that takes its news "
            "account and its indexers from the environment, so it comes up "
            "already pointed at the parity set. Everything else about it has "
            "to be written through three separate surfaces: PUT /api/config "
            "silently discards a `streams` key it answers 200 for, "
            "POST /api/streams ignores every field but the username, and the "
            "playback timeout is capped at 60s by validation -- its 5s default "
            "fails a multi-volume RAR outright, so 60 is the round's value and "
            "is the most the app allows. Its NNTP proxy is off by default and "
            "stays off. Stored archives only by design, so the compressed "
            "negative entry is expected to fail here and that is a correct "
            "answer, not a defect"
        ),
    },
    "comet": {
        "role": "target",
        "language": "Python + Rust",
        "repo": "g0ldyy/comet",
        "branch": "feat/usenet",
        # 8085 is taken by an unrelated service on the bench host
        "port": 8091,
        "manifest": "http://127.0.0.1:8091/{config}/manifest.json",
        "stream": "http://127.0.0.1:8091/{config}/stream/{type}/{id}.json",
        "play_mode": REDIRECT,
        "serve_mode": INGEST,
        "verified": "live",
        "standup": "harness/standup/comet.py",
        # Registered, configured, and not in the round. The current native
        # engine starts, but it still cannot pass its own NNTP preflight, so
        # there is no honest Comet throughput row to publish.
        "excluded": True,
        "excluded_reason": (
            "commit ed1ede74 starts its native engine and searches both parity "
            "indexers, returning 188 candidates for the smoke title, but native "
            "playback then answers 503 `nntp_availability_unknown` with cause "
            "`nntp_capabilities_failed`. The addon turns that into a status "
            "placeholder rather than media bytes. Measured 2026-09-18 on an "
            "isolated Hetzner ccx23 with the same account and 10-connection "
            "configuration as the field. Comet can play "
            "through another project's reader instead, and a row measured that "
            "way would be that reader's number wearing Comet's name, so the "
            "round publishes no Comet row at all"
        ),
        "note": (
            "fits this benchmark better than it fitted the mount one. Round 10 "
            "had to register it as a discovery-only target with no import API, "
            "because a mount round needs to put a specific release in and Comet "
            "will not take one. An addon round never needed to: asking it for a "
            "stream list IS its interface. Its config segment is base64 JSON "
            "minted at configure time. It deadlocked its own SQLite under "
            "sustained load at shipped defaults in round 10 preparation, so it "
            "keeps a shorter budget here too. It has no configure API: the "
            "whole configuration is the URL, base64url of a JSON document, and "
            "a document it rejects does not error -- every route answers 200 "
            "with one stream named OBSOLETE CONFIGURATION, which reads as a "
            "served result to anything that does not check the name. Two "
            "environment traps cost a boot each: a 70 MB anime-mapping download "
            "at worker startup outlasts gunicorn's timeout on a small host and "
            "crashloops with no traceback, and USENET_ENGINE_ENABLED=true is "
            "what *stops* the native engine starting -- unset, the same build "
            "reported the engine ready while leaving the feature disabled in an "
            "older build. The current branch needs the flag enabled and boots the "
            "engine, but fails its provider capability preflight"
        ),
        "budget_s": 1800,
    },
}

# stable order for every report. Order of *execution* rotates per round: an
# evening's provider throughput drifts, so whoever goes first must not always
# be the same target
DEFAULT = ["zurg", "aiostreams", "stremthru", "streamnzb", "comet"]

# One target may spend this long on the whole title set before the round gives
# up on it. A target that wedges has to cost its own rows and not the round.
#
# The number has to clear the worst case the title set can produce, or the
# budget truncates a slow-but-working target and its unmeasured entries are
# counted as coverage it does not have. 23 in-round titles at the protocol
# plane's 180s per-title ceiling is 4140s, so this sits above that with room
# for the ingest waits that make a title slow in the first place. Comet's own
# shorter budget is deliberate and is set on its entry.
DEFAULT_BUDGET_S = 5400


def excluded(names=None):
    """Registered targets a round will not measure, and why.

    Kept in the field rather than deleted: a reader comparing rounds needs to
    see that a target was set up, was measured against, and produced no row --
    which is a finding about that target -- rather than quietly not appearing.
    """
    return {name: TARGETS[name]["excluded_reason"]
            for name in (names or TARGETS)
            if TARGETS[name].get("excluded")}


def enabled(only=None, disable=None, include_excluded=False):
    """Resolve the field for one run. An unknown name is a hard error.

    A round that quietly measured four targets because the fifth was misspelled
    would publish a field that never existed. A target carrying `excluded` is
    dropped unless it is named explicitly in `only`, so an exclusion cannot be
    forgotten and cannot be undone by accident.
    """
    names = list(DEFAULT)
    if only:
        unknown = [n for n in only if n not in TARGETS]
        if unknown:
            raise SystemExit(f"--only names targets that are not registered: {unknown}")
        names = [n for n in names if n in only]
    for name in disable or []:
        if name not in TARGETS:
            raise SystemExit(f"--disable names a target that is not registered: {name}")
        names = [n for n in names if n != name]
    if not include_excluded:
        asked = set(only or [])
        for name in list(names):
            if TARGETS[name].get("excluded") and name not in asked:
                names.remove(name)
    if not names:
        raise SystemExit("the field is empty")
    return names


def unverified(names):
    """Targets still on a guessed endpoint. A round must not start on these."""
    return [n for n in names if TARGETS[n]["verified"] == "shakedown"]
