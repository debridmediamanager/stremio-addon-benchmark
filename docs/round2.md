# Round: round2

Generated 2026-09-07T21:01:03+00:00 by `harness/report.py`.

## What was measured

`:latest` is a moving tag, so a round records the digest it actually ran as well as the version the addon claims. Read this before comparing any number here with another round's.

| Target | Version | Pinned as | Built |
|---|---|---|---|
| stremthru | v0.104.1 | `e7e99159d19c0950` | 2026-09-07T11:56:10 |
| streamnzb | v5.18.0 | `64b213891a6199d7` | 2026-09-07T16:49:21 |
| zurg | v0.1.0 | `c88ae981156b0b42` | 2026-09-07T16:55:34 |
| aiostreams | v2.34.0 | `d25546200337f633` | 2026-09-04T23:21:47 |

## Coverage and click to byte

Coverage first, and no speed column appears without it. `median (population)` counts every entry the target was asked for, with a failure treated as slower than any success; `median (served)` is over the rows that produced a number and is the one that pays a target for failing early.

| Target | Lang | Served | Coverage | median c2b (population) | median c2b (served) | n |
|---|---|---|---|---|---|---|
| aiostreams | TypeScript | 18/23 | 78.3% | 6.45s | 5.63s | 18 |
| streamnzb | Go | 20/23 | 87.0% | 21.18s | 18.65s | 20 |
| stremthru | Go | 0/23 | 0.0% | >budget | - | 0 |
| zurg | Go | 8/23 | 34.8% | >budget | 7.02s | 8 |

## Against round1

The same fixed set, the same parity rules, measured again on the build each target shipped since. A row whose build did not move is a repeatability check and its deltas are this round's noise, not a change in the product.

| Target | Build | Served | Coverage | median c2b (population) | median c2b (served) |
|---|---|---|---|---|---|
| aiostreams | v2.34.0 `d25546200337` → v2.34.0 `d25546200337` | 18/23 → 18/23 | 78.3% → 78.3% | 5.62s → 6.45s | 5.01s → 5.63s |
| streamnzb | v5.17.0 `e0890ea961f1` → v5.18.0 `64b213891a61` | 19/23 → 20/23 | 82.6% → 87.0% | 20.04s → 21.18s | 14.32s → 18.65s |
| stremthru | v0.104.0 `efad6a37c01a` → v0.104.1 `e7e99159d19c` | 0/23 → 0/23 | 0.0% → 0.0% | - → - | - → - |
| zurg | v0.1.0 `53b7a9009716` → v0.1.0 `c88ae981156b` | 14/23 → 8/23 | 60.9% → 34.8% | 41.43s → - | 21.94s → 7.02s |

## Where the time goes

`click_to_byte` decomposed. A target losing on `stream_list` has a different product problem from one losing on `resolve`, and the composite alone cannot tell them apart. `resolve` nests inside `ttfb`, which nests inside `click_to_byte`.

| Target | stream_list | resolve | ttfb | click_to_byte | Served |
|---|---|---|---|---|---|
| aiostreams | 0.88s | 4.74s | 4.74s | 5.63s | 18/23 |
| streamnzb | 0.63s | 18.03s | 18.03s | 18.65s | 20/23 |
| stremthru | 0.52s | - | - | - | 0/23 |
| zurg | 0.39s | 6.61s | 6.61s | 7.02s | 8/23 |

## Reading, once it is playing

`p05` is the fifth percentile of one-second windows. A mean that looks fine can contain a second at zero, and that second is where the player stops. `sustain` counts titles that held 25 Mbps in every window of the read, not on average.

| Target | median MB/s | median p05 MB/s | sustain 25 Mbps | Served |
|---|---|---|---|---|
| aiostreams | 2.76 | 0.00 | 0/18 | 18/23 |
| streamnzb | 3.25 | 0.00 | 0/20 | 20/23 |
| stremthru | - | - | 0/0 | 0/23 |
| zurg | 2.50 | 0.00 | 0/8 | 8/23 |

## Did it do the right thing

Part of the set is negative. Two entries are real titles with nothing posted, where an empty list is correct and a stream is a fabrication; one is an id whose indexer answers with an unrelated feed, where the question is whether the addon forwards it.

| Target | correct | partial | missed | fabricated | forwarded-garbage | unclassified |
|---|---|---|---|---|---|---|
| aiostreams | 19 | 0 | 3 | 0 | 1 | 0 |
| streamnzb | 21 | 1 | 0 | 0 | 1 | 0 |
| stremthru | 3 | 0 | 20 | 0 | 0 | 0 |
| zurg | 9 | 4 | 9 | 0 | 1 | 0 |

## The error clip

**23 rows across the field are a complete, valid, tiny MP4 rather than a film.** HTTP 206, `video/mp4`, a `Content-Range` whose total is the same few kilobytes, and the whole of it delivered. Nothing about the response is malformed; it is simply not the movie. A harness that stops at the status line, or that reads a fixed first chunk, records these as served, and they are the difference between a target that answers a title and one that appears to.

| Target | error clips | of population |
|---|---|---|
| aiostreams | 3 | 23 |
| stremthru | 20 | 23 |

## Outcomes, and the size cap

The cap is 6.00 GiB and it is applied at the pick, identically for every target, because one of them cannot be configured to honour it. What each one *offered* is reported rather than corrected: `over cap` counts titles where the target's own list went above the cap, and `pick rank` is how far down its own ranking the cap had to reach to find something playable.

| Target | outcomes | largest offered | titles with oversize offers | median pick rank | oversize-only picks |
|---|---|---|---|---|---|
| aiostreams | empty-list 2, placeholder 3, served 18 | 442.99 GiB | 21 | 2 | 0 |
| streamnzb | empty-list 2, served 20, truncated 1 | 442.99 GiB | 21 | 15 | 0 |
| stremthru | empty-list 3, placeholder 20 | 443.00 GiB | 20 | 40 | 0 |
| zurg | empty-list 2, resolve-failed 9, served 8, truncated 4 | 39.78 GiB | 21 | 11 | 1 |

## The client plane

Stremio 4.4, driven over CDP, clicking the row the addon produced. `played` means the app's own player reported time moving forward, not that a screenshot looked right. `player-refused` is a stream the addon served and the player would not open, which is the whole reason this plane exists. Rows other addons contributed are rendered by the client and never counted. `clips the player accepted` is the row that matters most here: a short placeholder the addon served instead of the film, which Stremio starts and plays without complaint. Counting those as playback is how a target that serves almost nothing scores well.

| Target | Played | Coverage | median click to play | refused | clips the player accepted | other addons' rows |
|---|---|---|---|---|---|---|
| aiostreams | 18/23 | 78.3% | 5.05s | 0 | 3 | 10 max |
| streamnzb | 21/23 | 91.3% | 13.28s | 0 | 0 | 10 max |
| stremthru | 7/23 | 30.4% | 25.04s | 0 | 13 | 10 max |
| zurg | 9/23 | 39.1% | 5.91s | 10 | 2 | 10 max |

Measured through the player's loopback (127.0.0.1), which is what makes a plain-http addon a secure context for the shell. These numbers include a network hop and a player start that the protocol plane's do not, so the two tables are read side by side and never averaged.

## Noise floor

Repeat passes on one target, same evening. Two targets closer together than this are tied. Measured on a sampled subset in its own run, because repeat passes over the whole set cost the account more than the number is worth.

| Target | passes | titles | median spread | max spread |
|---|---|---|---|---|
| aiostreams | 3 | 6 | 1.44s | 6.52s |

