# Stremio addon benchmark

What happens between pressing play in Stremio and the first frame arriving,
measured against a real news account rather than described.

Its sibling [`usenet-streaming-benchmark`](https://github.com/debridmediamanager/usenet-streaming-benchmark)
answers a different question. It asks which mount should back a Plex library,
and it measures WebDAV serving with the release already imported. This one puts
the part that repository sets up by hand inside the measurement: indexer search
by IMDb id, release ranking, the pick, the NZB fetch, opening the archive,
minting a URL, and only then bytes.

A target can win there and lose here.

## Status

**Nothing has been measured.** This repository currently holds the field, the
method and the test data. The round-1 harness is not written yet, and there are
no results to quote.

| Piece | State |
|---|---|
| Field of five addons | registered in [`harness/targets.py`](harness/targets.py), four of five still on unverified endpoints |
| Method, parity rules, traps | [`docs/design.md`](docs/design.md) |
| How to run it | [`docs/running.md`](docs/running.md) |
| Candidate pool | [`corpus/candidates.json`](corpus/candidates.json), 31 entries |
| Indexer capability matrix | [`corpus/indexer-capabilities.json`](corpus/indexer-capabilities.json), measured 6 September 2026 |
| Indexer census | [`corpus/availability.7z`](corpus/availability.7z), 31 candidates across 6 indexers |
| Pinned title set | [`corpus/titles.json`](corpus/titles.json), 23 entries, built from the census |
| Protocol-plane harness | not written |
| Client-plane harness | not written |

## The field

Five Usenet-backed Stremio addons.

| Target | Language | Why it is here |
|---|---|---|
| zurg | Go | the author's own project, and the reason this exists. Nine mount rounds next door measured zurg as a WebDAV server and never once exercised its addon path |
| AIOStreams | TypeScript | the largest project in the field by a wide margin, and the author of a competing benchmark, which is exactly why an independent number is worth having |
| StremThru | Go | its `newz` store also answers WebDAV, so it is the one target whose number here can be read against a number in the mount rounds |
| streamnzb | Go | measured once in a six-way round on 18 August and dropped before either repository existed |
| Comet | Python + Rust | fits an addon round better than it fitted the mount rounds, where it had to be registered as discovery-only because it will not accept a chosen release |

zurg is the author's own project. Every number here is reproducible from this
repository against your own account and your own indexers, and you should do
that rather than take it at face value.

## Two planes, from round one

**Protocol.** Drive the addon protocol directly. Manifest, then
`/stream/{type}/{id}.json`, then follow the chosen stream to first byte and
through a sustained read.

**Client.** Stremio 4.4 driven through CDP, clicking the real interface. The
only plane that catches a stream an addon serves correctly and the player
refuses.

Rows from the two are published side by side and never averaged.

## What ranks a target

`click_to_byte_s`, the composite from the stream request to the first body
byte, with its three components published beside it. A target losing on search
has a different problem from one losing on ingest.

**Coverage outranks speed, and no table prints one without the other.** An
addon serving four titles in 900 ms is worse than one serving eighteen in four
seconds. Medians are taken over the fixed title set with `n`, including the
entries nothing can serve, because a median over "whatever this target
survived" pays a target for failing early.

## Four findings, before a single target was started

Choosing the indexers turned out to be the hard part, and measuring them
produced more than the setup step it was meant to be. All four were measured on
6 September 2026 and are reproducible from
[`harness/indexer_caps.py`](harness/indexer_caps.py).

**An indexer bans Stremio by name.** One of the seven refuses any client whose
User-Agent contains `stremio` or `aiostreams`, case-insensitively, anywhere in
the string, with `403 Access denied: Streaming services are not allowed.`
`Stremio/4.4.181`, `x-stremio-x` and `AIOStreams/2.0` are refused.
`StremThru/1.0`, `comet`, `torrentio`, `mediafusion`, `curl/8.4.0`,
`Sonarr/4.0.0` and browser strings are served. The first census run was blocked
by its own name and read as a dead api key. The round does not spoof a target's
User-Agent to get around this. An addon an indexer refuses by name has a real
coverage problem, and that is a finding rather than something to engineer away.

**An indexer answers an unknown IMDb id with its whole feed.** `tt0000001` is a
real id for a real 1894 short with nothing posted anywhere. One indexer answers
it with 2971 results, none of them related to each other or to the film. Its
neighbours `tt0000009` and `tt9999999` correctly return nothing, so this is a
parsing fault on one value rather than a policy. The entry stays in the title
set and its expected outcome is now `no-unrelated-streams`, because the real
question is whether an addon passes its indexer's garbage through to the viewer.

**An indexer silently drops the `imdbid` filter on `t=tvsearch`.** Asked for
Breaking Bad S01E01 and Game of Thrones S01E01, it returns the same 370,616
results, top to bottom identical, none of them either show. It honours the
season and episode numbers and ignores the title, so the failure arrives as a
large successful-looking result set rather than an error. Movie search on the
same indexer is correct.

**TV cannot be benchmarked with this indexer fleet, and round 1 is movies
only.** Of seven indexers, exactly two answer `t=tvsearch` by IMDb id at all.
One of those is the indexer that bans Stremio clients. The other served nine
titles and then rate limited for the rest of the run, recovering only after
about four minutes per call, which cannot survive five targets asking it for the
same set. Of the rest, three return nothing for episode search, one returns the
unfiltered feed above, and one is rate limited outright. The eight series
entries in the pool are therefore excluded with tier `no-capable-indexer`, which
records a fact about the fleet and nothing about any addon.

## The title set the census produced

23 entries in the round, all measured.

| Tier | Entries | What it is |
|---|---|---|
| `abundant` | 20 | more than 40 results across the parity indexers |
| `absent` | 2 | real ids, nothing posted, an empty stream list is the correct answer |
| `poisoned-index` | 1 | the indexer answers with an unrelated feed and the addon is judged on whether it forwards it |
| `no-capable-indexer` | 8 excluded | the eight series entries, see above |

Four of the twenty are `oversize-only`, meaning every result is above the
6 GB cap the desktop player will direct-play. Those are where the size-cap
parity rule gets tested rather than assumed.

**The set has no thin tier.** Pooling three indexers, even Metropolis (1927)
returns 62 results and City Lights (1931) returns 59, so nothing in the pool
landed under the 40-result threshold. Age does not predict scarcity for famous
films. A genuinely scarce title has to be chosen from a scarce catalogue rather
than an old one, and that entry is still missing.

## Running it

Full runbook in [`docs/running.md`](docs/running.md), including target setup,
the parity rules, the publish check and what each failure mode means.

The setup half works today and is what produced the committed test data. It
needs nothing but api keys and does not touch the news account, so it runs from
a laptop.

```bash
cp config/indexers.example.json config/indexers.json   # fill in real keys, gitignored
python3 harness/indexer_caps.py                        # who honours which search, who bans whom
python3 harness/census.py                              # ask every indexer what it holds
python3 harness/census.py --retry-failed               # resume anything rate limited
python3 harness/titles.py build                        # assign tiers, write corpus/titles.json
python3 harness/scan_leaks.py                          # before any push
```

Run `indexer_caps.py` first, and re-run it on whichever host will run the round.
It decides which indexers may be counted and for which kind of search, and
`titles.py` reads its answer rather than trusting a result count. An indexer that
drops a filter returns a big number, and counting that would make an
unbenchmarkable title look like the easiest one in the set.

The measurement half does not exist yet. `docs/running.md` says what the round
will do and marks those steps as unwritten rather than leaving a command that
looks runnable.

The harness imports only the Python standard library. There is nothing to
install.

`corpus/candidates.json` proposes a tier per entry and says why. The census
measures. `harness/titles.py` assigns the real tier, the size band and the
expected outcome from that measurement. A candidate that lands somewhere other
than where it was proposed is a finding about the catalogue, not a mistake in
the pool.

Two entries earn their place specifically. **Breaking Bad S01E01** is heavily
indexed and its two best-ranked releases are genuine spool gaps on this
account, so an addon reporting nothing playable is correct and one handing the
player a zero fill is not. **Carmencita (1894)** is a real IMDb id for a real
film with certainly nothing posted, so a legitimate title has to resolve to an
empty list rather than an error.

## The census archive

`corpus/availability.7z` is a 7-Zip archive with encrypted headers. The password
is `dmmbench`.

The password is not access control. It is published right here. The archive
exists so that several hundred release names stay out of search indexes and
automated scrapers, the same reason the sibling repository ships its NZB corpus
that way. No message ids are stored at all, and a result's guid is kept only as
a truncated sha1, because a newznab guid is usually a download URL carrying the
api key.

Nothing needs to be unpacked by hand. `harness/titles.py build` extracts it when
the plain json is not there, and `harness/census.py` re-cuts it on every run so
the published copy can never lag the census.

## Before quoting anything from here

Read [`docs/design.md`](docs/design.md). It carries the parity rules and the
nine traps, each of which produces a plausible wrong number rather than an
error. The short list of things that are true of every round:

- **One target runs at a time.** Each keeps a pool of NNTP sockets warm and two
  running together oversubscribe the account and skew both.
- **Order rotates.** Provider throughput drifts over an evening.
- **Nobody streams the same bytes.** Each addon ranks and picks its own release
  for a title, which is part of the product and makes throughput a comparison
  between two different files unless the chosen release is quoted with it.
- **Indexer identity is not published.** Two of the three are private trackers.
  The census carries `indexer-a/b/c` and each one's kind. The mapping stays in
  a gitignored config, along with the api keys.
