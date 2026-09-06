# What this measures, and what it refuses to claim

## The question

Between pressing play in Stremio and the first frame appearing, a Usenet-backed
addon does work no mount benchmark ever touches: it searches indexers by IMDb
id, ranks what comes back, picks a release, fetches the NZB, opens the archive,
mints a URL and only then serves bytes. `usenet-streaming-benchmark` measures
the last step of that with everything before it done by hand in advance. This
repository measures the whole thing.

That is a different question with a different answer, and the two must not be
read across. A target can win here and lose there.

## The field

Five Usenet-backed addons: **zurg**, **AIOStreams**, **StremThru** (`newz`
store), **streamnzb** and **Comet** (`feat/usenet`). Registered with their
endpoints and provenance in [`harness/targets.py`](../harness/targets.py).

zurg is the author's own project. It is also the reason this repository exists:
rounds 3 through 9 next door measured zurg as a mount five times and never once
exercised its addon path, so its own weakest surface went unmeasured while its
strongest was published. Every number here is reproducible against your own
account and indexers, and you should do that rather than take it at face value.

**Comet fits here better than it fitted the mount rounds.** Round 10 had to
register it as discovery-only with no import API, because a mount round needs to
put a chosen release in and Comet will not accept one. An addon round never
needed to: asking for a stream list *is* its interface.

## Two planes, measured from round one

**Protocol plane.** Drive the addon protocol directly: `manifest.json`, then
`/stream/{type}/{id}.json`, then follow whatever the first stream points at to
first byte and through a sustained read. Deterministic, repeatable, and it
isolates the addon from the player.

**Client plane.** Stremio 4.4 on the Windows box over CDP, clicking through the
real UI. This is the only plane that can catch a stream the addon serves
correctly and the player refuses.

Neither is sufficient alone. The protocol plane cannot see a codec the player
will not decode; the client plane cannot separate an addon's latency from
Stremio's own. Rows from the two planes are reported side by side and never
averaged.

## What gets timed

An addon's cold open is not one number, and publishing it as one is how a slow
searcher hides behind a fast reader.

| Field | From | To |
|---|---|---|
| `stream_list_s` | request to `/stream/…` | the stream array | 
| `n_streams` | — | how many playable options came back |
| `chosen` | — | name, size and indexer of the top stream, recorded every time |
| `resolve_s` | request to the chosen stream URL | a URL that will serve bytes, redirects followed |
| `ttfb_s` | that request | first body byte |
| **`click_to_byte_s`** | **request to `/stream/…`** | **first body byte** | 
| `throughput_mb_s` | — | mean over the read |
| `p05_window_mb_s` | — | 5th percentile of one-second windows. A 54 MB/s mean can contain a second at zero and the player stops during it |
| `sustain_25mbps` | — | whether thirty seconds hold a 25 Mbps buffer |
| `seek_profile` | — | 1/25/50/75/95% and one backward seek |
| `outcome` | — | `served`, `empty-list`, `resolve-failed`, `zero-bytes`, `player-refused`, `timeout` |

`click_to_byte_s` is the ranking metric. The three components are published
next to it, because they are the whole diagnosis: a target losing on search is
a different product problem from one losing on ingest.

## Ranking rules

**Coverage outranks speed, and no table prints one without the other.** An addon
that serves four titles in 900 ms is worse than one that serves eighteen in
four seconds. Any ranking on time alone rewards a target for failing.

**Medians are over the fixed population, with `n`.** Every target is asked for
every in-round title including the ones nothing can serve. Import and search
time both scale with how much a title has posted, so a target credited only
with what it survived is credited with the easy half of the set.

**A round states its noise floor.** "Provider throughput drifts over an
evening" is true and it is not a number. Repeat passes on one target give the
resolution below which two targets are tied.

## Parity, and the four ways it silently breaks

Anything unequal across targets is what the round actually measured.

1. **Indexer parity.** Every target queries the same indexers with the same
   keys. Unequal indexers measure the indexer, not the addon, and the effect
   dwarfs everything else in this benchmark. The parity set is
   `indexer-a`, `indexer-d` and `indexer-e`, chosen by measurement in
   [`corpus/indexer-capabilities.json`](../corpus/indexer-capabilities.json)
   rather than by reputation. An indexer qualifies by honouring the search an
   addon actually sends, serving every target in the field, and holding up under
   a round's call volume. Two of the seven fail that on their own behaviour and
   one on quota, which is what makes round 1 movies only.
2. **Size-cap parity.** Every target is capped at the same maximum release size.
   Uncapped, the top result for a popular title is a 17-36 GB remux; the player
   refuses it and the addon with the tightest default filter "wins". zurg needs
   `stremio.max_size_gb`; the others have their own filter and each one must be
   set to the same number and verified from the served list, not from the config
   file.
3. **Connection parity.** Same NNTP connection count everywhere, verified after
   the servers are up by sampling established sockets, never read off a config.
   In several of these projects the provider pool cap and the per-read budget
   are separate settings and the obvious one is not the one that binds.
4. **Isolation.** One target alive at a time. Each keeps a pool of sockets warm
   and two running together oversubscribe the account and skew both. Order of
   execution rotates between rounds.

## Traps

Each of these produces a plausible wrong number rather than an error.

**1. Newznab download links carry the api key.** Every `link`, and most `guid`
values, is a fully authenticated URL. `harness/census.py` stores a truncated
sha1 of the guid and nothing else, and refuses to write a file in which a key
survived. Raw response bodies land in the gitignored `corpus/raw/`. Scan
`results/` separately from the harness before any push: a scan that skips it
looks clean and is not.

**2. The User-Agent denylist.** Measured 6 September 2026: one of the three
indexers answers `403 Access denied: Streaming services are not allowed.` to
any client whose User-Agent contains `stremio` or `aiostreams`,
case-insensitively, anywhere in the string. `Stremio/4.4.181`, `x-stremio-x` and
`AIOStreams/2.0` are refused; `StremThru/1.0`, `comet`, `torrentio`,
`mediafusion`, `curl`, `Sonarr/4.0.0` and browser strings are served. The first
run of the census was blocked by its own name and read as a dead api key.

   Two consequences. The census identifies itself honestly as a benchmark and
   is therefore served. **The round does not spoof a target's User-Agent to get
   around an indexer's policy** — an addon an indexer refuses by name has a real
   coverage problem and that is a finding, not something to engineer away. And
   because the block hits two of five targets, that indexer is outside the
   parity set: including it would hand three targets a catalogue the other two
   are banned from. Its census rows are still collected, to price what the block
   costs in titles.

**3. Nobody streams the same bytes.** Identical indexers still leave each addon
ranking and picking for itself, and that choice is part of the product. So the
primary number lets each one choose, `chosen` is recorded on every row, and any
throughput comparison that does not quote the chosen release is unreadable.
Byte-identity checks across targets are meaningless in this repository, unlike
the mount rounds where all five imported one NZB.

**4. A wrong IMDb id is indistinguishable from unavailability.** It fails
identically on all five targets and looks like a hard title. `harness/titles.py`
keeps the top result names per entry and flags any whose results never contain
the expected title, so a bad id is caught before a round instead of during one.

**5. `t=tvsearch` by imdbid returns zero on some indexers.** The public indexer
answers movies by IMDb id and gives nothing for episodes, which an addon that
searches by id exclusively reports as "not available". Recorded per entry as
`tv_by_imdbid` so a series row is never read as an addon failure when it is an
indexer's search surface.

**6. An empty stream list is the correct answer for part of the set.** The
negative tier holds real titles with nothing posted anywhere. The correct
behaviour is an empty list, promptly; an error, a hang, or a fabricated stream
is a defect. A tier where success is the only possible outcome cannot catch any
of that.

**7. A 206 is not proof of bytes.** The mount rounds found engines answering a
range with a zero fill and the status line already sent. A read that returns the
requested length is checked for content, and a stream that stops mid-body after
a good header is a distinct outcome from one that never started.

**8. The client plane cannot be measured by capture.** Stremio's video is a
multi-plane overlay, invisible to every screen-capture path tried, so a
"is there a picture" check by pixel is guaranteed to say no. The client plane
polls player state through CDP instead. Stremio 4.4 accepts remote debugging
but only raw CDP works against its Qt build.

**9. A big result count is not a working filter.** One indexer answers
`t=tvsearch` with `imdbid` for two different series with the same 370,616
results, identical top to bottom, neither of them the show asked for. It honours
the season and episode numbers and drops the title. Another answers the IMDb id
`tt0000001` with 2971 unrelated releases while its immediate neighbours return
nothing. Both failures arrive as large successful result sets. So capability is
established by asking for two things that must not have the same answer, in
[`harness/indexer_caps.py`](../harness/indexer_caps.py), and
[`harness/titles.py`](../harness/titles.py) counts an indexer's results only for
the kinds of search it was measured to honour. A filter-dropping indexer would
otherwise make the least benchmarkable title in the set look like the easiest.

**10. A failed call is not a zero.** One indexer served nine titles at a 2.5
second pace and then rate limited for the rest of the run, recovering only after
roughly four minutes of backoff per call. The first title set built from that
census marked eight series entries `absent`, which is a claim about the
catalogue drawn from an HTTP 429. Entries whose parity indexers errored are now
tier `incomplete` and excluded until `census.py --retry-failed` has actually
measured them.

**11. The account is shared and finite.** Same rule as next door: the news
account's connection budget is spent by production too, so a round that
oversubscribes it measures contention. One target at a time, sockets drained
between targets, and a target that will not release the account fails the round
loudly rather than quietly skewing the next one.

## Test data

The title set is measured, not chosen. `corpus/candidates.json` is a pool with
a *proposed* tier and a stated reason per entry; `harness/census.py` asks every
configured indexer what it actually holds; `harness/titles.py` assigns the tier,
the size band and the expected outcome from that census and writes
`corpus/titles.json`. A candidate that lands in a different tier from the one it
was proposed for is a finding about the catalogue, not a mistake in the pool.

The pool spans blockbusters, catalogue, pre-1980 long tail, anime, non-English,
flagship and long-tail episodic TV, one very high bitrate documentary, and a
negative tier of real titles with nothing posted. What survived measurement is
23 entries: 20 abundant movies, two genuine negatives, one poisoned index, and
eight series entries excluded because no parity indexer answers episode search.

**The set has no thin tier, and that is a gap rather than a result.** Pooling
three indexers, Metropolis (1927) returns 62 results and City Lights (1931)
returns 59, so no candidate landed under the 40-result threshold. Age does not
predict scarcity for famous films. A genuinely scarce entry has to come from a
scarce catalogue rather than an old one and is still missing from the pool.

Two entries earn their place specifically:

- **Breaking Bad S01E01** is heavily indexed *and* its two best-ranked releases
  are genuine spool gaps on this account. An addon that reports nothing playable
  is behaving correctly; one that hands the player a zero fill is not. No other
  entry separates those two behaviours.
- **Carmencita (1894)** is a real IMDb id for a real film that certainly has no
  Usenet posting. It was put in the pool to test that a legitimate title
  resolves to an empty list rather than an error, and it caught something
  better: one indexer answers it with 2971 unrelated releases. Its expected
  outcome is now `no-unrelated-streams`, and it is the only entry that asks
  whether an addon filters what its indexer will not.
