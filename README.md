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

**Round 3 is measured and published.** Four targets over the current 23-title
set, at exactly 10 sampled NNTP connections each, on 18 September 2026. Every
target was rebuilt from that day's upstream head and reset to empty state. The
full report is [`docs/round3.md`](docs/round3.md), with raw rows in
[`results/round3/`](results/round3/) and independent clean-start noise passes
in [`results/round3-noise-a/`](results/round3-noise-a/) and
[`results/round3-noise-b/`](results/round3-noise-b/).

Comet's current `feat/usenet` build is an explicit DNF: it discovers candidates
but fails its native capability preflight before opening an NNTP connection and
serves a status clip. The isolated diagnostic, including CPU, RSS and disk
counters for the failed path, is in
[`results/round3-comet-dnf/`](results/round3-comet-dnf/).

Round 3's protocol result: StreamNZB leads coverage at 19/23, AIOStreams leads
fixed-population click-to-byte at 4.19 s, and zurg leads observed successful
throughput at 6.91 MB/s but serves only 12 of the 22 titles it is scored on.
zurg therefore loses the
end-to-end ranking despite winning stream-list latency and read rate. The
latest-build client plane was not run because the browser-control surface was
unavailable; round 2 remains the latest client measurement and must not be
mixed into round 3.

### What rounds 1 and 2 measured

A round that says "the latest version" has to say which one. Round 2 records
this itself, from each target's own manifest and the digest the daemon actually
ran; round 1's column is reconstructed from evidence that outlived it and is
marked as such in [`docs/round1.md`](docs/round1.md).

| Target | Round 1 | Round 2 | Moved |
|---|---|---|---|
| StremThru | 0.104.0 | 0.104.1 | yes, but nothing in `newz`: the release is magnet and language fixes |
| streamnzb | v5.17.0 | v5.18.0 | yes, but nothing on the streaming path: proxy auth, share codes, a health check |
| zurg | a `main` build whose commit it did not record | `main` at `c88ae981` | yes, 57 commits, most of them on the NNTP read path |
| AIOStreams | v2.34.0 | v2.34.0 | **no.** Same digest. Its newer tags are nightly prereleases, and mixing one project's prerelease with three projects' releases is not a field |

AIOStreams not moving is what makes the rest of the table readable: it is the
round's repeatability control, and it came back inside the noise floor.

### Protocol plane

The addon's own HTTP interface, driven over loopback on the bench host.
`click_to_byte` is the request for a stream list to the first body byte.

| Target | Served | Coverage | median click to byte | error clips | vs round 1 |
|---|---|---|---|---|---|
| streamnzb | 20/23 | 87.0% | 18.65s | 0 | +1 served, +4.33s |
| AIOStreams | 18/23 | 78.3% | 5.63s | 3 | unchanged, +0.62s |
| zurg | 8/23 | 34.8% | 7.02s | 0 | **-6 served, -14.92s** |
| StremThru | 0/23 | 0% | none served | 20 | unchanged |

### Client plane

Stremio 4.4, driven over CDP, clicking the row the addon produced. These
numbers carry a LAN hop and a player start that the protocol numbers do not,
so the two are read side by side and never averaged.

| Target | Played | Coverage | median click to play | player refused | clips the player accepted |
|---|---|---|---|---|---|
| streamnzb | 21/23 | 91.3% | 13.28s | 0 | 0 |
| AIOStreams | 18/23 | 78.3% | 5.05s | 0 | 3 |
| zurg | 9/23 | 39.1% | 5.91s | 10 | 2 |
| StremThru | 7/23 | 30.4% | 25.04s | 0 | 13 |

Noise floor 1.44s median spread over three passes, 6.52s at its worst, so two
targets closer together than that are tied and so are two rounds. Connection
parity verified by sampling each target's own network namespace: every one
peaked at exactly 15, with production holding four alongside.

### What round 2 found

**zurg fixed the thing round 1 said was wrong with it, and got worse.** Round 1
found that its fifteen results led with the largest releases, so for 17 of 23
titles not one of them was under the 6 GiB a desktop player will direct-play.
One commit in this window addresses exactly that -- `a71bafb0`, *cap Stremio
stream lists per resolution rather than across the list* -- and it worked: on
the new build 1 of 23 titles has nothing under the cap, and median options
within the cap went from 0 to 4 out of the same fifteen results. Click to byte
fell from 21.94s to 7.02s, five times the noise floor. And coverage halved,
from 14 titles to 8.

Because it now picks different releases, it fails on different ones. Its
failures moved from "nothing here is playable at this size" to three titles
answering `404 Release holds nothing playable` and three more `404 File is not
available`, on 720p rips it never reached in round 1. In the player it is worse
still: 10 of its streams were refused outright against 5 in round 1. A ranking
change is not a fix on its own, and this is what the repository means by
"nobody streams the same bytes" -- the two rounds did not read the same files,
so the comparison is between two products and not between two builds of one.

**Two targets shipped a release and neither release touched what this measures.**
StremThru 0.104.1 is magnet handling and language aliases; streamnzb 5.18.0 is
reverse-proxy auth, profile share codes and a health check. Both are real
releases and neither is a usenet-streaming change, which is the honest reading
of streamnzb's +1 title and +4.33s: one is inside the run-to-run spread and the
other is above the median spread but inside its worst case.

**StremThru still serves the error clip, and one more of them.** 20 of 23
titles now, against 14 in round 1, and the three it answers correctly are the
three where an empty list is the right answer. Nothing about 0.104.1 was
expected to change this and nothing did.

### What round 2 got wrong

**Two full passes were voided before this one, both for the same reason and
neither caught by anything but the socket sampler.** A round measures one
target at a time because they share one news account, and both times something
was still holding fifteen connections when the next target started.

- **An operator's own instance, left running from a version check.** It owned
  the port, so the round's own zurg could not bind, exited immediately, and
  `wait_ready` got its manifest from the stranger. That phase measured a
  process the round never started and could not stop, and it ran on through the
  three phases after it. The whole pass is kept in
  [`results/round2-voided/`](results/round2-voided) with the parity table that
  condemned it, because that table is the evidence.
- **Then the round's own zurg, which it never stopped.** `round.py` started it
  through a shell, so the pid it recorded was the shell's, and `/bin/sh` on the
  bench host forks rather than execs. Stopping the target killed a shell that
  had already exited; zurg itself received no signal and was still writing to
  its log 23 minutes after its phase ended. Round 1 never saw this because its
  rotation put zurg last.

Reading that code turned up two more of the same shape: the drain between
targets read the pid file that stopping the target had just deleted, so it
always saw zero sockets and returned instantly, and nothing ever checked that
the process had actually died. All of it is fixed -- no shell, the stop proves
the process is gone and hands the pid to the drain, a target surviving SIGKILL
stops the round, and a bare-process target refuses to start onto a port
something already answers.

Only AIOStreams was contaminated by the second pass, so it alone was measured
again, on its own, after the round. **Its window is therefore not interleaved
with the other three**, which is the one asymmetry in this round's conditions.

**Round 1's client-plane numbers carried an addon it did not know about.** The
player keeps whatever a round installs, and three of the four targets mint a
fresh URL every time they are stood up, so round 2 arrived at a player already
carrying round 1's copy of each target on the same port -- still valid, still
answering. StremThru was measured in round 1 with two copies of StremThru
installed, which is where its 71 "other addons' rows" came from. The client
plane now removes an earlier install of the target it is about to measure, and
touches nothing else in the profile; every target in round 2 reports 10.

### What round 1 found

**A complete, valid, tiny MP4 is not a film, and every check short of its size
passes it.** HTTP 206, `video/mp4`, a `Content-Range` whose total is the same
few kilobytes, and the whole of it delivered. StremThru answers 14 of 23 titles
this way and its log says why: `nzb is not streamable`. Stremio then plays the
clip for 30 seconds and reports success, so a benchmark asking "did it 206" or
"did the player start" scores StremThru 20 of 23. It serves about 6.

**Coverage and speed point in opposite directions.** AIOStreams is roughly
three times faster than streamnzb on the protocol plane and serves one title
fewer. Ranking on time alone puts it first, which is why no table here prints a
speed without the coverage beside it.

**zurg comes third on its own benchmark, and a default is why.** It answers
with exactly 15 streams per title, its `max_results` default, against a field
median of 120 to 203. Its ranking leads with the largest releases, so for 17 of
23 titles not one of those 15 was under the 6 GiB the player will direct-play.
Median options within the cap: AIOStreams 49, streamnzb 37, StremThru 25, zurg
0. That produces all five of its resolve failures and all five of its player
refusals, every one on a release between 8 and 27 GiB.

**One target loses an indexer to its own User-Agent.** StremThru sends none on
indexer queries, and one of the three parity indexers refuses an empty
User-Agent with newznab code 109. Probing all three with five different strings
shows only the empty one refused, and only by that indexer. So StremThru ran on
two indexers where the others ran on three. The round reports that rather than
spoofing around it.

**The two planes disagree usefully.** They agree on 16 of StremThru's 23
entries. On five the protocol plane recorded `truncated` and the player played
the film to a real runtime, which is the difference between a 30 second read
window and a viewer. Neither plane alone describes that target correctly.

### What round 1 got wrong

Four measurements in this round looked excellent and were artefacts. Each was
caught by being implausibly good rather than by a test, which is worth stating
because the same shape of error is easy to publish.

- **Every target held zero news connections.** The socket sampler read the host
  table, and four of five targets run in containers whose sockets live in
  another network namespace. Fixed by sampling each target's own namespace.
- **A 19 KB body counted as a served film.** Fixed by the placeholder rule
  above. The rule was applied to rows already written, from the bytes read and
  the declared total that every row records, rather than by re-running the
  field under it.
- **Click to play of 0.04s across a whole target.** Leaving Stremio's player
  does not reset its position, so the next title read as already playing.
- **And after the first fix, 0.05s.** Requiring the position to advance passes
  instantly when the previous title is still playing. `player.stop()` had never
  been called, because of a guard in this harness. Playback is now identified by
  the player's own state, forward progress, and a changed file duration.

Two client-plane runs were discarded over the last two. **StremThru was also
re-measured**: its first pass carried a 6 GB filter the other three did not,
which broke size-cap parity. Unfiltered it served fewer titles, not more, so the
filter was not what held it back.

The protocol-plane rows were not affected by the playback bugs, which are
confined to the client plane.

## The field

Five Usenet-backed Stremio addons.

| Target | Language | Why it is here |
|---|---|---|
| zurg | Go | the author's own project, and the reason this exists. Nine mount rounds next door measured zurg as a WebDAV server and never once exercised its addon path |
| AIOStreams | TypeScript | the largest project in the field by a wide margin, and the author of a competing benchmark, which is exactly why an independent number is worth having |
| StremThru | Go | its `newz` store also answers WebDAV, so it is the one target whose number here can be read against a number in the mount rounds |
| streamnzb | Go | measured once in a six-way round on 18 August and dropped before either repository existed |
| Comet | Python + Rust | **excluded from rounds 1–3.** The latest `feat/usenet` revision was rebuilt and re-tested on 18 September. Its native engine now starts and its two Newznab searches return candidates, but playback fails its own NNTP capability preflight with `nntp_availability_unknown` / `nntp_capabilities_failed` and returns a status placeholder instead of media. It can play through another project's reader, but that would be the other reader's number wearing Comet's name, so there is no throughput row |

zurg is the author's own project. Every number here is reproducible from this
repository against your own account and your own indexers, and you should do
that rather than take it at face value.

## Two planes

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

```bash
./harness/round.sh --dry-run                    # the field, the order, what is excluded
./harness/round.sh --round round2               # the protocol plane, every target
./harness/client-round.sh round2                # the client plane, from the machine that reaches both
python3 harness/parity.py --round round2        # what each target actually held, sampled
python3 harness/report.py --round round2 --against round1 --noise-round round2-noise
```

**Read `parity.py` before the report, every time.** Both of the passes this
round threw away looked perfectly good in the report and were condemned by that
one table.

The setup half needs nothing but api keys and does not touch the news account,
so it runs from a laptop.

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
