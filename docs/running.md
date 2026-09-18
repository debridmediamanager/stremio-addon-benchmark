# Running a round

## What runs today

Round 1 and round 2 are measured and published. Every step below is written and
has been exercised against a live target.

| Step | Command | State |
|---|---|---|
| 1. Credentials | edit `config/indexers.json` | works |
| 2. Indexer capabilities | `python3 harness/indexer_caps.py` | works |
| 3. Census | `python3 harness/census.py` | works |
| 4. Title set | `python3 harness/titles.py build` | works |
| 5. Stand up the targets | `python3 harness/standup/<target>.py` | works, one script per target |
| 6. Verify endpoints | `python3 harness/verify_endpoints.py` | works, needs targets running |
| 7. Protocol round | `./harness/round.sh --round <name>` | works |
| 8. Client round | `./harness/client-round.sh <name>` | works, needs the Windows player |
| 9. Connection parity | `python3 harness/parity.py --round <name> --expect 10` | works, **must pass before the report** |
| 10. Report | `python3 harness/report.py --round <name> --against <earlier>` | works |
| 11. Publish check | `python3 harness/scan_leaks.py` | works |

The round records what it measured as well as what it measured it at:
`harness/versions.py` captures each target's manifest version and the digest or
commit behind it, while that target is up, into `results/<round>/versions.json`.
Nothing else can tell two rounds of `:latest` apart afterwards.

Step 9 is not optional and it does not belong after the write-up. Round 2 threw
away two complete passes that the report rendered without complaint; the socket
samples were the only thing that showed a second target alive on the account.

**The measured field is four, not five.** Comet is registered, configured and
excluded. Its current native engine starts and discovery works, but its NNTP
capability preflight fails before media delivery. `harness/targets.py` carries
the measured reason, and `./harness/round.sh --dry-run` prints it. An excluded
target is dropped from the field unless it is named in `--only`, so the
exclusion cannot be forgotten and cannot be undone by accident.

Steps 1 to 4 need nothing but network and api keys. They do not touch the news
account, so they can be run from a laptop. Steps 5 onward need the bench host.

## Prerequisites

- **One Linux host that holds the news account alone.** Not a machine already
  running a production reader. The account's connection budget is shared across
  every host and process, so a round competing with production measures
  contention.
- A Usenet account with at least the round's connection budget (10 in round 3).
- Python 3.10 or newer. The harness imports only the standard library, so there
  is nothing to install and no virtualenv to create.
- Docker, plus the Go and Node toolchains, for building the targets.
- Newznab api keys. Three indexers is the minimum that makes a parity set
  meaningful.
- For the client plane only: a Windows box with Stremio 4.4 and remote debugging
  reachable over CDP.

If another Usenet suite is being prepared on a second VM, do not assume the VMs
are independent. Read the same 512 MiB raw range twice on each VM alone and once
on both VMs concurrently, with the final connection budget active. Only overlap
the measured phases when each concurrent median remains within the isolated
noise floor. This gate catches provider/account bandwidth caps that live socket
parity cannot: both processes can hold exactly 10 connections and still split
one fixed transfer ceiling.

## 1. Credentials

```bash
cp config/indexers.example.json config/indexers.json
```

Fill in real values. That file is gitignored and holds both the api keys and the
mapping from `indexer-a` to a real name, which published artifacts never carry.

`pace_s` on an indexer overrides the global `--pace`. Set it wide for anything
that rate limits. The census is not in a hurry and a 429 wall costs far more
than the wait does.

The news account goes in each target's own config at step 5, and in
`NNTP_HOST`, `NNTP_PORT`, `NNTP_USER` and `NNTP_PASS` for anything that reads it
from the environment.

## 2. Measure the indexers first

```bash
python3 harness/indexer_caps.py
python3 harness/indexer_caps.py --skip indexer-b     # leave a rate-limited one out
```

**Run this before the census, and re-run it on any host that will run a round.**
It decides which indexers may be counted and for which kind of search, and
`titles.py` reads its answer rather than trusting a result count. It also has to
be re-measured per host because indexers treat datacenter addresses differently
from residential ones, so a parity set established from a laptop is not
automatically the parity set on the bench box.

It asks each indexer for two things that cannot honestly have the same answer.
Read the three verdicts:

- `honoured` — the filter works. Eligible.
- `filter-ignored` — two different ids returned an identical page, so the
  indexer dropped the filter and answered with an unrelated feed. This arrives
  as a large successful result set, which is why a result count alone cannot
  detect it.
- `unsupported` — it answers nothing for either id. Honest, and useless for
  that kind of search.

Then `parity eligible` at the bottom is the set `harness/titles.py build`
selects automatically, and `answers TV by imdbid` says whether the round can
include series at all. Do not copy the old set into source: capability drift is
exactly what the live probe is meant to catch.

## 3. Census

```bash
python3 harness/census.py                  # every candidate, every indexer
python3 harness/census.py --limit 2        # smoke test
python3 harness/census.py --retry-failed   # resume whatever rate limited
```

The census writes two files. `corpus/availability.json` is the working copy and
is gitignored. `corpus/availability.7z` is the published one, re-cut on every
run so it cannot lag, encrypted headers, password `dmmbench`, which keeps
several hundred release names out of search indexes and is not access control.

`--retry-failed` merges into the existing `corpus/availability.json` and only
re-queries the pairs that errored, so a rate limited indexer is resumable rather
than a reason to spend the whole quota again. It also picks up an indexer added
to the config after the first run.

An indexer that exhausts its backoff twice in a row is dropped from the run and
left for a later retry. Grinding through the rest of the set at four minutes a
call buys nothing.

## 4. Build the title set

```bash
python3 harness/titles.py build
python3 harness/titles.py show
```

On a fresh clone there is no `availability.json`, only the archive. `build`
extracts it first and says so. Nothing has to be unpacked by hand.

Read the table before going further. Four tiers mean stop and fix something
rather than continue:

- `incomplete` — a parity indexer errored on that entry, so it has not been
  measured. Re-run `--retry-failed`. **A failed call is not a zero**, and the
  first title set built here marked eight series entries `absent` on the
  strength of an HTTP 429.
- `id-suspect` — the results are coherent and they are not this film, so the
  IMDb id is probably wrong. A wrong id fails identically on every target and
  reads as a hard title.
- `no-capable-indexer` — nothing in the parity set honours that kind of search.
  The entry would measure the fleet and not the addon.
- `poisoned-index` — the id is right and the indexer answered with an unrelated
  feed. This one stays in the round. Whether an addon forwards its indexer's
  garbage to the viewer is the question, not a defect in the entry.

## 5. Stand up the targets

One at a time. Every target keeps a pool of NNTP sockets warm, so two running
together oversubscribe the account and skew both.

For an all-current build, clone the four upstream repositories beneath
`$HOME/sab/src`, put the exact zurg binary used by the sibling mount round at a
known path, then run:

```bash
ROUND=round3 ./harness/build-current.sh
CONNS=10 ZURG_BINARY=/path/to/the/exact/zurg \
  BUILD_MANIFEST=$HOME/sab/versions-round3-build.json \
  ./harness/prepare-current.sh
```

Image tags include each upstream commit and the build manifest records them.
StreamNZB is pulled from its official release image only when the image's OCI
revision is exactly the checked-out latest commit. Its release embeds the
project's metadata fallback inputs; a local source build cannot reproduce those
private inputs and otherwise returns empty lists before indexer search.
Preparation retires prior target state, applies the measured parity indexers,
sets all five configurations to the same connection count, verifies each
manifest, and stops the target again before the round. Retired state goes
under the run root's `.retired/` directory, outside every target directory;
otherwise old databases and caches would be charged to the next run's state
disk measurement.

StremThru is started with only `newz`, `stremio_newz`, and `vault`. Its
default feature set also starts IMDb, torrent and DMM hash-list
workers; those are unrelated to the Newz addon and otherwise consume CPU,
memory, network and disk during StremThru's measurement window. The explicit
allowlist is part of the generated Compose file, not an operator convention.

**zurg** is the only target whose configuration is established. Minimum config:

```yaml
zurg: v1
rclone_enabled: false
providers:
  - type: nzb
    nntp:
      host: your.news.host
      port: 563
      tls: true
      username: YOUR_USENET_USERNAME
      password: YOUR_USENET_PASSWORD
      connections: 15          # the parity value
stremio:
  enabled: true
  max_size_gb: 6               # the parity value, see below
  indexers:
    - name: indexer-a
      url: https://your-indexer.example
      api_key: YOUR_KEY
    - name: indexer-c
      url: https://other-indexer.example
      api_key: YOUR_KEY
      api_path: /api/v1/api    # only when it is not /api
```

Three things that cost a run each if missed. An indexer entry with an empty
`api_key` is dropped silently, and the startup line reads `with 2 indexer(s)`
rather than three. The addon token is generated on first boot and printed as
`Stremio: generated addon token X`, so the manifest URL is
`http://host:9998/stremio/X/manifest.json` unless `stremio.token` pins it.
And `/version` is not a route, so liveness is probed through the manifest.

**Every target now has a standup script**, and each one carries what it cost to
write. Run the container (or the binary) first, then its script:

```bash
python3 harness/standup/zurg.py        # writes config.yml; needs a built binary
python3 harness/standup/stremthru.py   # dashboard vault + addon userdata
python3 harness/standup/streamnzb.py   # filter profile + stream binding
python3 harness/standup/aiostreams.py  # dashboard provider + user config
python3 harness/standup/comet.py       # excluded, kept reproducible
```

Each writes its minted URL into `config/endpoints.local.json`, which is
gitignored — two of those URLs are credentials, because the whole configuration
is encoded into the path. Nothing prints one in full.

The traps each script had to be taught are in its docstring, and they are the
kind that answer 200: StremThru's addon is `/stremio/newz` and not
`/stremio/store`, and its size filter is `Size` and not `File.Size`; streamnzb
discards a `streams` key it accepts; Comet answers a rejected configuration
with a stream named `OBSOLETE CONFIGURATION`; AIOStreams refuses every write as
a wrong password when what is missing is a field.

### The four parity rules

Anything unequal across targets is what the round actually measured.

1. **Indexer parity.** Same indexers, same keys, everywhere. This dominates
   everything else in an addon benchmark.
2. **Size cap.** Same maximum release size everywhere. Uncapped, the top result
   for a popular title is a 17 to 36 GB remux that the desktop player refuses,
   and the addon with the tightest default filter appears to win. Verify it from
   the served stream list, not from the config file.
3. **Connection parity.** Same NNTP connection count. Verify after the servers
   are up, never from a config: in several of these projects the provider pool
   cap and the per-read budget are separate settings and the obvious one is not
   the one that binds.
4. **Isolation.** One target alive at a time, sockets drained between targets,
   order of execution rotated between rounds.

Connection parity is verified by sampling, not by reading:

AIOStreams opens a transient validation connection while its stored provider
pool is already live. Round preparation therefore stores `budget - 1` for its
boot, and `round.py` raises the live pool to the full budget after readiness but
before the phase window. At a budget of 10 this measured 9 during boot and 10,
not 11, throughout the workload. This is connection accounting, not a smaller
measured pool.

```bash
while :; do date +%s; ss -tnp state established | grep :563; sleep 5; done > sockets.log
```

Take per-pid maxima inside each phase window, and separate bench pids from
production pids by `/proc/PID/cwd`. Counting sockets is not the same as counting
readers: a target may hold an idle connection outside its own pool accounting,
so confirm which sockets actually move bytes before calling a budget unequal.

## 6. Verify the endpoints

```bash
python3 harness/verify_endpoints.py
python3 harness/verify_endpoints.py --only zurg
```

Minted URLs go in `config/endpoints.local.json`:

```json
{"zurg": {"manifest": "http://127.0.0.1:9998/stremio/abc123/manifest.json"}}
```

A pass means the URL answered 200 with something that is genuinely a Stremio
manifest declaring a `stream` resource. It does not mean the addon works and it
is not a measurement.

## 7. The protocol round

```bash
./harness/round.sh --dry-run             # the field, the order, the exclusions
./harness/round.sh                       # every target, one at a time
./harness/round.sh --only zurg
./harness/round.sh --noise-floor 2       # extra passes on the first target
```

It starts one target, waits for a real manifest, runs `harness/protocol.py`
over the whole title set, stops it, waits for its news sockets to drain, and
moves on. Order rotates per round name. It samples established sockets to the
news port for the whole run into `results/<round>/sockets.log`, which is where
connection parity is read from afterwards — never from a config file.

## 8. The client round

Needs the Windows player, and three pieces of setup that are properties of the
plane rather than of any target.

**Stremio must be launched detached.** Started over ssh it dies with the
session. Launch it from a scheduled task with `QTWEBENGINE_REMOTE_DEBUGGING`
set, and connect to the debugger it leaves behind.

**Each target must be on the player's loopback.** Stremio's shell is served
from `https://app.strem.io`, so a plain-http addon on any other host is blocked
as mixed content: the addon installs, renders nothing, and reports no error.
Chromium exempts localhost, so forward each target onto the player's own
loopback and install it there.

```bash
ssh -f -N -R 8484:<bench host>:8484 <user>@<player host>   # per target port
ssh -f -N -L 9223:127.0.0.1:9223 <user>@<player host>      # the debugger
python3 harness/client.py --target stremthru --addon-host 127.0.0.1 --fetch-host <bench host>
```

`--fetch-host` exists because the harness has to read the manifest itself: the
page is not allowed to fetch it, for the same mixed-content reason.

**Rows are attributed to the addon that produced them.** A real player has
other addons installed, and their streams render in the same list. Each row
carries its addon's `transportUrl`, so the round only ever clicks and counts
the target's own, and records the rest as `rows_from_other_addons`. Nothing in
the player's profile is added, removed or restored.

Playback is confirmed from the app's own player service through its AngularJS
injector — `player.time` moving forward while `player.paused` is false — and
never from pixels: Stremio's video is a multi-plane overlay and is invisible to
every screen-capture path. Stremio 4.4 accepts remote debugging but only raw
CDP works against its Qt build, which is why `harness/cdp.py` exists.

## 9. Before publishing

```bash
python3 harness/scan_leaks.py            # every tracked file
python3 harness/scan_leaks.py results/   # and the output directory on its own
```

Run both. A scan that covers the harness and skips the output directory looks
clean and is not. Result files quote provider and indexer hostnames back inside
error strings and echoed request parameters, which is how a hostname reaches a
published artifact without anybody writing it down.

The scanner reads what to look for out of `config/indexers.json` and the
`NNTP_*` environment, so it never carries a copy of a secret itself. Exit status
1 means do not push.

## When something looks wrong

| Symptom | Cause |
|---|---|
| `403 Access denied: Streaming services are not allowed.` | an indexer's User-Agent denylist. Measured on one of the seven: any UA containing `stremio` or `aiostreams`, case-insensitively. The round does not spoof a UA to get around this |
| 429 after a handful of calls, for the rest of the run | a quota wall rather than a burst limit. Raise that indexer's `pace_s`, then `--retry-failed` |
| every entry suddenly `absent` | a parity indexer errored and the failure was counted as a zero. Check `census_complete` in `corpus/titles.json` |
| a TV search returning tens of thousands of results | the indexer dropped the `imdbid` filter. `indexer_caps.py` reports this as `filter-ignored` |
| every target fails one title identically | the IMDb id, not the targets. `title_match` in the title set flags it as `MISMATCH` |
| a target serves a 206 with no content | a zero fill dressed as a success. Read the body, and re-read minutes later to tell a repair from a permanent fill |
