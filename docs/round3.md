# Round: round3

Generated 2026-09-18T03:40:21+00:00 by `harness/report.py`.

## Short version

There is no single winner. StreamNZB has the best coverage and correctness
(19/23 served, 22/23 correct). AIOStreams has the best fixed-population and
successful click-to-byte medians (4.19 s and 2.68 s). zurg is the quickest to
return a stream list (0.37 s) and leads the observed successful-read rate and
p05 floor (6.91 and 0.04 MB/s), but it serves only 12/23, so its
fixed-population click-to-byte falls to third at 21.95 s. Coverage outranks a
fast subset: on the end-to-end question, zurg loses this round.

zurg also loses the resource comparison. It is much cheaper in CPU than
StreamNZB, but only third of four on CPU, last on peak RSS at 1167.97 MB, and
last on physical writes at 315.66 MB per title. It retains 660.21 MB, second
best behind StreamNZB. The likely explanation, consistent with its fast stream
list and high write/RSS footprint, is that zurg gets through discovery cheaply
and spends its work in local archive/cache materialisation. Its five resolve
failures, two truncations and one zero-byte response then erase that successful
path's speed from the fixed-population ranking.

## Fairness and isolation

This ran on a fresh Hetzner `ccx23` in `nbg1`: four dedicated x86 cores,
16 GiB RAM, 160 GiB local disk, Ubuntu 24.04. The sibling mount suite had an
identical separate VM. Every target began with new state, used the same two
live parity indexers, the same 6 GiB pick cap, and a configured 10-connection
pool. The live census, including each container's network namespace, found an
exact peak of 10 for all four targets in the main pass and both noise passes;
[`parity.txt`](../results/round3/parity.txt) is the proof.

The suites were provisioned and prepared in parallel, but their publishable
measurement windows were serialized. A 512 MiB raw preflight at 10 connections
measured 52.42/42.48 MB/s on the mount VM and 63.77/59.54 MB/s on this VM in
isolation. When run concurrently they fell to 30.99 and 30.31 MB/s, a combined
61.30 MB/s versus 109.10 MB/s from the two isolated medians. Separate VMs did
not isolate the shared provider account; overlapping the main phases would have
ranked account throttling. The machine-readable record is
[`environment.json`](../results/round3/environment.json).

Two independent three-title clean-start passes establish click-to-byte noise:
maximum spread is 2.125 s for zurg, 1.287 s for StreamNZB, 0.293 s for
AIOStreams and 0.203 s for StremThru. The target order rotated between passes.

The real Stremio client plane was not run. The required browser-control surface
was unavailable, so this report makes no latest-build claim about player
acceptance and does not reuse round 2's client numbers.

## What was measured

`:latest` is a moving tag, so a round records the digest it actually ran as well as the version the addon claims. Read this before comparing any number here with another round's.

| Target | Version | Source revision | Artifact pin | Built |
|---|---|---|---|---|
| streamnzb | v6.2.0 | `c5aa001b` | `3f5864a7f28877b8` | 2026-09-16T20:19:45 |
| zurg | v0.1.0-main.1b0c2dbf | `1b0c2dbf` | `d02889bda8e87914` | 2026-09-18T00:16:11 |
| aiostreams | v0.0.0 | `5afa43cd` | `4f6ee7609e2228cf` | 2026-09-18T00:26:34 |
| stremthru | v0.105.0 | `b77350e6` | `bec7703a6825350a` | 2026-09-18T00:23:33 |

These were the upstream branch heads when the VMs were provisioned. zurg
`main` advanced afterwards to `63aa505a`; that delta is watchlist/acquisition,
dashboard, torrent and `.strm` work and does not touch the Usenet reader,
WebDAV streaming or Stremio addon paths measured here. The result remains
pinned to `1b0c2dbf` rather than quietly changing binaries mid-round.

Comet `feat/usenet` at `ed1ede74` was also rebuilt and configured against the
same account and parity indexers. It found 188 candidates, but every playback
attempt stopped at its own NNTP capability check with
`nntp_availability_unknown` / `nntp_capabilities_failed` and returned a status
placeholder rather than media. It is therefore an explicit DNF, not a missing
competitor and not a borrowed-reader throughput result. Exact build provenance
is recorded in [`source-build.json`](../results/round3/source-build.json).

A final empty-state, one-title diagnostic preserved the whole failure path in
[`round3-comet-dnf/`](../results/round3-comet-dnf/). Comet listed 188 candidates
in 1.629 s and resolved its pick in 0.114 s, then returned a complete 421,667
byte error clip. It was configured for 10 connections but opened zero provider
sockets because the capability preflight stopped it first. That is failed exact
parity, not a fast stream. The monitor recorded 1.12 CPU seconds, 1.088 p95
cores, 486.42 MB peak RSS, 0.00 MB physical reads, 0.89 MB physical writes and
no retained-state growth. Those one-title failure-path counters are published
for completeness but are not comparable to the 23-title resource table.

## Coverage and click to byte

Coverage first, and no speed column appears without it. `median (population)` counts every entry the target was asked for, with a failure treated as slower than any success; `median (served)` is over the rows that produced a number and is the one that pays a target for failing early.

| Target | Lang | Served | Coverage | median c2b (population) | median c2b (served) | n |
|---|---|---|---|---|---|---|
| aiostreams | TypeScript | 14/23 | 60.9% | 4.19s | 2.68s | 14 |
| streamnzb | Go | 19/23 | 82.6% | 17.30s | 12.13s | 19 |
| zurg | Go | 12/23 | 52.2% | 21.95s | 3.84s | 12 |
| stremthru | Go | 2/23 | 8.7% | >budget | 10.73s | 2 |

## Where the time goes

`click_to_byte` decomposed. A target losing on `stream_list` has a different product problem from one losing on `resolve`, and the composite alone cannot tell them apart. `resolve` nests inside `ttfb`, which nests inside `click_to_byte`.

| Target | stream_list | resolve | ttfb | click_to_byte | Served |
|---|---|---|---|---|---|
| aiostreams | 0.72s | 1.97s | 1.97s | 2.68s | 14/23 |
| streamnzb | 0.47s | 11.85s | 11.85s | 12.13s | 19/23 |
| zurg | 0.37s | 3.46s | 3.46s | 3.84s | 12/23 |
| stremthru | 0.63s | 10.04s | 10.04s | 10.73s | 2/23 |

## Reading, once it is playing

`p05` is the fifth percentile of one-second windows. A mean that looks fine can contain a second at zero, and that second is where the player stops. `sustain` counts titles that held 25 Mbps in every window of the read, not on average.

| Target | median MB/s | median p05 MB/s | sustain 25 Mbps | Served |
|---|---|---|---|---|
| aiostreams | 6.29 | 0.01 | 1/14 | 14/23 |
| streamnzb | 6.10 | 0.00 | 0/19 | 19/23 |
| zurg | 6.91 | 0.04 | 0/12 | 12/23 |
| stremthru | 4.45 | 0.00 | 0/2 | 2/23 |

## Did it do the right thing

Part of the set is negative. Two entries are real titles with nothing posted, where an empty list is correct and a stream is a fabrication; one is an id whose indexer answers with an unrelated feed, where the question is whether the addon forwards it.

| Target | correct | partial | missed | fabricated | forwarded-garbage | unclassified |
|---|---|---|---|---|---|---|
| aiostreams | 17 | 3 | 3 | 0 | 0 | 0 |
| streamnzb | 22 | 1 | 0 | 0 | 0 | 0 |
| zurg | 15 | 2 | 6 | 0 | 0 | 0 |
| stremthru | 5 | 1 | 17 | 0 | 0 | 0 |

## The error clip

**20 rows across the field are a complete, valid, tiny MP4 rather than a film.** HTTP 206, `video/mp4`, a `Content-Range` whose total is the same few kilobytes, and the whole of it delivered. Nothing about the response is malformed; it is simply not the movie. A harness that stops at the status line, or that reads a fixed first chunk, records these as served, and they are the difference between a target that answers a title and one that appears to.

| Target | error clips | of population |
|---|---|---|
| aiostreams | 3 | 23 |
| stremthru | 17 | 23 |

## Outcomes, and the size cap

The cap is 6.00 GiB and it is applied at the pick, identically for every target, because one of them cannot be configured to honour it. What each one *offered* is reported rather than corrected: `over cap` counts titles where the target's own list went above the cap, and `pick rank` is how far down its own ranking the cap had to reach to find something playable.

| Target | outcomes | largest offered | titles with oversize offers | median pick rank | oversize-only picks |
|---|---|---|---|---|---|
| aiostreams | empty-list 3, placeholder 3, served 14, truncated 3 | 442.99 GiB | 20 | 3.5 | 0 |
| streamnzb | empty-list 3, served 19, truncated 1 | 442.99 GiB | 20 | 10 | 0 |
| zurg | empty-list 3, resolve-failed 5, served 12, truncated 2, zero-bytes 1 | 39.76 GiB | 20 | 5.5 | 0 |
| stremthru | empty-list 3, placeholder 17, served 2, truncated 1 | 443.00 GiB | 20 | 40 | 0 |

## Metric rankings

Each metric is ordered independently. Missing population medians are DNF because fewer than half the fixed population was served.

- Coverage (higher is better): 1. streamnzb (82.6%); 2. aiostreams (60.9%); 3. zurg (52.2%); 4. stremthru (8.7%)
- Population click-to-byte (lower is better): 1. aiostreams (4.19s); 2. streamnzb (17.30s); 3. zurg (21.95s); 4. stremthru (DNF)
- Served click-to-byte (lower is better): 1. aiostreams (2.68s); 2. zurg (3.84s); 3. stremthru (10.73s); 4. streamnzb (12.13s)
- Stream list (lower is better): 1. zurg (0.37s); 2. streamnzb (0.47s); 3. stremthru (0.63s); 4. aiostreams (0.72s)
- Resolve (lower is better): 1. aiostreams (1.97s); 2. zurg (3.46s); 3. stremthru (10.04s); 4. streamnzb (11.85s)
- TTFB (lower is better): 1. aiostreams (1.97s); 2. zurg (3.46s); 3. stremthru (10.04s); 4. streamnzb (11.85s)
- Median throughput (higher is better): 1. zurg (6.91 MB/s); 2. aiostreams (6.29 MB/s); 3. streamnzb (6.10 MB/s); 4. stremthru (4.45 MB/s)
- Median p05 window (higher is better): 1. zurg (0.04 MB/s); 2. aiostreams (0.01 MB/s); 3 (tie). streamnzb, stremthru (0.00 MB/s)
- Titles sustaining 25 Mbps (higher is better): 1. aiostreams (1); 2 (tie). streamnzb, stremthru, zurg (0)
- Correct outcomes (higher is better): 1. streamnzb (22); 2. aiostreams (17); 3. zurg (15); 4. stremthru (5)
- Titles offering oversize streams (lower is better): 1 (tie). aiostreams, streamnzb, stremthru, zurg (20)
- Oversize-only picks (lower is better): 1 (tie). aiostreams, streamnzb, stremthru, zurg (0)

Comet is DNF on every playback metric. It is not assigned last-place numbers:
doing so would turn a 412 KB status clip and zero NNTP connections into a valid
latency or throughput observation.

## Resources

Sampled once per second from the target's own process tree while its protocol phase ran. CPU and block-I/O rankings use the fixed title population as the denominator, so a target does not win by failing early. State disk is allocated bytes in that target's run directory; source trees and container images are outside it.

| Target | CPU s/title | CPU p95 cores | peak RSS MB | disk read MB/title | disk write MB/title | state Δ MB |
|---|---:|---:|---:|---:|---:|---:|
| stremthru | 2.63 | 0.311 | 391.00 | 0.01 | 43.58 | 1001.43 |
| aiostreams | 6.11 | 0.405 | 400.51 | 0.09 | 189.69 | 1929.31 |
| zurg | 12.26 | 0.669 | 1167.97 | 0.00 | 315.66 | 660.21 |
| streamnzb | 85.32 | 3.277 | 703.95 | 0.01 | 2.53 | 13.51 |

- CPU seconds/title (lower is better): 1. stremthru < 2. aiostreams < 3. zurg < 4. streamnzb
- CPU p95 cores (lower is better): 1. stremthru < 2. aiostreams < 3. zurg < 4. streamnzb
- peak RSS (lower is better): 1. stremthru < 2. aiostreams < 3. streamnzb < 4. zurg
- disk reads/title (lower is better): 1. zurg < 2. streamnzb < 3. stremthru < 4. aiostreams
- disk writes/title (lower is better): 1. streamnzb < 2. stremthru < 3. aiostreams < 4. zurg
- state growth (lower is better): 1. streamnzb < 2. zurg < 3. stremthru < 4. aiostreams

## The client plane

**Not run.** No `client-*.json` in this round, so nothing here says whether a player would accept what each addon served. The protocol plane cannot answer that.

## Noise floor

Repeat passes on one target, same evening. Two targets closer together than this are tied. Measured on a sampled subset in its own run, because repeat passes over the whole set cost the account more than the number is worth.

| Target | passes | titles | median spread | max spread |
|---|---|---|---|---|
| aiostreams | 2 | 2 | 0.29s | 0.293s |
| streamnzb | 2 | 2 | 0.686s | 1.287s |
| stremthru | 2 | 2 | 0.108s | 0.203s |
| zurg | 2 | 2 | 1.09s | 2.125s |
