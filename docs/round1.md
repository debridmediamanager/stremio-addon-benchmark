# Round: round1

Generated 2026-09-07T02:32:35+00:00 by `harness/report.py`.

## Coverage and click to byte

Coverage first, and no speed column appears without it. `median (population)` counts every entry the target was asked for, with a failure treated as slower than any success; `median (served)` is over the rows that produced a number and is the one that pays a target for failing early.

| Target | Lang | Served | Coverage | median c2b (population) | median c2b (served) | n |
|---|---|---|---|---|---|---|
| aiostreams | TypeScript | 18/23 | 78.3% | 5.62s | 5.01s | 18 |
| streamnzb | Go | 19/23 | 82.6% | 20.04s | 14.32s | 19 |
| zurg | Go | 14/23 | 60.9% | 41.43s | 21.94s | 14 |
| stremthru | Go | 0/23 | 0.0% | >budget | - | 0 |

## Where the time goes

`click_to_byte` decomposed. A target losing on `stream_list` has a different product problem from one losing on `resolve`, and the composite alone cannot tell them apart. `resolve` nests inside `ttfb`, which nests inside `click_to_byte`.

| Target | stream_list | resolve | ttfb | click_to_byte | Served |
|---|---|---|---|---|---|
| aiostreams | 1.70s | 3.16s | 3.16s | 5.01s | 18/23 |
| streamnzb | 1.12s | 13.42s | 13.42s | 14.32s | 19/23 |
| zurg | 0.27s | 21.61s | 21.61s | 21.94s | 14/23 |
| stremthru | 1.81s | - | - | - | 0/23 |

## Reading, once it is playing

`p05` is the fifth percentile of one-second windows. A mean that looks fine can contain a second at zero, and that second is where the player stops. `sustain` counts titles that held 25 Mbps in every window of the read, not on average.

| Target | median MB/s | median p05 MB/s | sustain 25 Mbps | Served |
|---|---|---|---|---|
| aiostreams | 4.87 | 0.00 | 0/18 | 18/23 |
| streamnzb | 4.28 | 0.00 | 1/19 | 19/23 |
| zurg | 6.02 | 0.00 | 0/14 | 14/23 |
| stremthru | - | - | 0/0 | 0/23 |

## Did it do the right thing

Part of the set is negative. Two entries are real titles with nothing posted, where an empty list is correct and a stream is a fabrication; one is an id whose indexer answers with an unrelated feed, where the question is whether the addon forwards it.

| Target | correct | partial | missed | fabricated | forwarded-garbage | unclassified |
|---|---|---|---|---|---|---|
| aiostreams | 19 | 0 | 3 | 0 | 1 | 0 |
| streamnzb | 20 | 1 | 1 | 0 | 1 | 0 |
| zurg | 15 | 2 | 5 | 0 | 1 | 0 |
| stremthru | 3 | 6 | 14 | 0 | 0 | 0 |

## The error clip

**17 rows across the field are a complete, valid, tiny MP4 rather than a film.** HTTP 206, `video/mp4`, a `Content-Range` whose total is the same few kilobytes, and the whole of it delivered. Nothing about the response is malformed; it is simply not the movie. A harness that stops at the status line, or that reads a fixed first chunk, records these as served, and they are the difference between a target that answers a title and one that appears to.

| Target | error clips | of population |
|---|---|---|
| aiostreams | 3 | 23 |
| stremthru | 14 | 23 |

## Outcomes, and the size cap

The cap is 6.00 GiB and it is applied at the pick, identically for every target, because one of them cannot be configured to honour it. What each one *offered* is reported rather than corrected: `over cap` counts titles where the target's own list went above the cap, and `pick rank` is how far down its own ranking the cap had to reach to find something playable.

| Target | outcomes | largest offered | titles with oversize offers | median pick rank | oversize-only picks |
|---|---|---|---|---|---|
| aiostreams | empty-list 2, placeholder 3, served 18 | 442.99 GiB | 21 | 3 | 0 |
| streamnzb | empty-list 2, resolve-failed 1, served 19, truncated 1 | 442.99 GiB | 21 | 15 | 0 |
| zurg | empty-list 2, resolve-failed 5, served 14, truncated 2 | 39.76 GiB | 21 | 14 | 17 |
| stremthru | empty-list 3, placeholder 14, truncated 6 | 443.00 GiB | 20 | 0 | 0 |

## The client plane

Stremio 4.4, driven over CDP, clicking the row the addon produced. `played` means the app's own player reported time moving forward, not that a screenshot looked right. `player-refused` is a stream the addon served and the player would not open, which is the whole reason this plane exists. Rows other addons contributed are rendered by the client and never counted. `clips the player accepted` is the row that matters most here: a short placeholder the addon served instead of the film, which Stremio starts and plays without complaint. Counting those as playback is how a target that serves almost nothing scores well.

| Target | Played | Coverage | median click to play | refused | clips the player accepted | other addons' rows |
|---|---|---|---|---|---|---|
| aiostreams | 19/23 | 82.6% | 1.68s | 0 | 2 | 34 max |
| streamnzb | 21/23 | 91.3% | 8.99s | 0 | 0 | 10 max |
| stremthru | 6/23 | 26.1% | 28.60s | 0 | 14 | 71 max |
| zurg | 14/23 | 60.9% | 7.65s | 5 | 2 | 10 max |

Measured through the player's loopback (127.0.0.1), which is what makes a plain-http addon a secure context for the shell. These numbers include a network hop and a player start that the protocol plane's do not, so the two tables are read side by side and never averaged.

## Noise floor

Repeat passes on one target, same evening. Two targets closer together than this are tied. Measured on a sampled subset in its own run, because repeat passes over the whole set cost the account more than the number is worth.

| Target | passes | titles | median spread | max spread |
|---|---|---|---|---|
| aiostreams | 3 | 6 | 2.867s | 8.48s |

