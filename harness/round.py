#!/usr/bin/env python3
"""Run a round: one target at a time, in a rotated order, with parity checked.

    ./harness/round.sh                          # every target in the field
    ./harness/round.sh --only zurg,stremthru
    ./harness/round.sh --plane protocol         # skip the Windows half
    ./harness/round.sh --dry-run                # what it would do, and refuse to

The rules this enforces are the ones a round breaks by accident:

**One target alive at a time.** Each keeps a pool of NNTP sockets warm, and two
running together oversubscribe an account whose budget is shared with
production. This stops every target before starting the next one, and waits for
the previous one's sockets to drain rather than assuming they have.

**Order rotates.** Provider throughput drifts over an evening, so whoever goes
first must not always be the same target. The rotation is derived from the
round name, so it is stable for a given round and different between rounds.

**Nothing starts on an unverified endpoint.** A target still marked `shakedown`
in harness/targets.py had its URL written from documentation. A round that
starts anyway discovers it as a row of zeros afterwards.

**Connection parity is sampled, not read.** A config file is not evidence: in
several of these projects the pool cap and the per-read budget are separate
settings and the obvious one is not the one that binds. This samples
established sockets to the news port for the whole run and reports the maximum
each target actually held, which is the only number that means anything.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.expanduser("~/stremio-addon-bench/run")
ENDPOINTS = os.path.join(ROOT, "config", "endpoints.local.json")
NNTP_PORT = 563


def run_dir(target):
    directory = os.path.join(RUN, target)
    if not os.path.isdir(directory):
        raise SystemExit(f"no run directory for {target} at {directory}")
    return directory


def compose(target, *args):
    return subprocess.run(["docker", "compose", *args], cwd=run_dir(target),
                          capture_output=True, text=True)


def start_target(name):
    """Start one target. Four are containers; zurg is a binary.

    The binary is started detached with its own pid file rather than through a
    shell that dies with the ssh session that launched it -- a round takes
    hours and is driven from a laptop.
    """
    target = registry.TARGETS[name]
    if target.get("launch") != "command":
        return compose(name, "up", "-d")
    directory = run_dir(name)
    pidfile = os.path.join(directory, "target.pid")
    stop_target(name)
    log = open(os.path.join(directory, "target.log"), "a")
    process = subprocess.Popen(target["start"], cwd=directory, shell=True,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    with open(pidfile, "w") as handle:
        handle.write(str(process.pid))
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def stop_target(name):
    target = registry.TARGETS[name]
    if target.get("launch") != "command":
        return compose(name, "stop")
    pidfile = os.path.join(run_dir(name), "target.pid")
    if not os.path.exists(pidfile):
        return None
    with open(pidfile) as handle:
        pid = handle.read().strip()
    if pid.isdigit():
        # SIGTERM so the target drains rather than dropping reads mid-flight
        subprocess.run(["kill", pid], capture_output=True)
        for _ in range(20):
            if subprocess.run(["kill", "-0", pid], capture_output=True).returncode != 0:
                break
            time.sleep(1)
        else:
            subprocess.run(["kill", "-9", pid], capture_output=True)
    os.remove(pidfile)
    return None


def manifest_of(target):
    with open(ENDPOINTS) as handle:
        entry = json.load(handle).get(target) or {}
    url = entry.get("manifest") or registry.TARGETS[target]["manifest"]
    if "{" in url:
        raise SystemExit(f"{target} has no minted manifest URL in config/endpoints.local.json")
    return url


def wait_ready(target, seconds=240):
    """`docker compose up -d` proves the container exists, nothing more."""
    url = manifest_of(target)
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "usenet-addon-benchmark/1"})
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status == 200:
                    return time.time()
                last = f"http {response.status}"
        except Exception as problem:
            last = f"{type(problem).__name__}"
        time.sleep(3)
    raise SystemExit(f"{target} never served a manifest in {seconds}s (last: {last})")


def stop_everything():
    """Every registered target, whether or not this round includes it.

    The sibling repository learned the other half of this the hard way -- a
    stop_all that freed ports by killing whatever held them, for targets the
    run had never started. This only stops containers this repository's own run
    directories define.
    """
    for name in registry.TARGETS:
        if os.path.isdir(os.path.join(RUN, name)):
            stop_target(name)


def drain(name=None, seconds=45):
    """Wait for the target that just stopped to release its news sockets.

    Scoped to that target, because the host is not quiet: production zurg holds
    a handful of connections to the same port at all times, and another
    session's test build may hold more. A drain that waits for the *host* to
    reach zero never reaches it, so every gap between targets silently became
    the full timeout instead of the moment the sockets actually closed.

    With no target named -- before the first one -- there is nothing of the
    round's own to wait for, so this returns at once.
    """
    if not name:
        return
    deadline = time.time() + seconds
    while time.time() < deadline:
        if count_nntp_sockets(name) == 0:
            return
        time.sleep(3)


def count_nntp_sockets(name):
    """Established news connections held by one target, wherever it runs.

    A container's sockets are only visible from inside its own namespace, so
    the count is taken there; a target that is a bare process is counted from
    the host table by its own pid, never by name, because several unrelated
    zurg processes run on this box.
    """
    target = registry.TARGETS.get(name) or {}
    if target.get("launch") == "command":
        pidfile = os.path.join(RUN, name, "target.pid")
        if not os.path.exists(pidfile):
            return 0
        with open(pidfile) as handle:
            pid = handle.read().strip()
        if not pid.isdigit():
            return 0
        result = subprocess.run(["ss", "-tnp", "state", "established"],
                                capture_output=True, text=True)
        return sum(1 for line in result.stdout.splitlines()
                   if f":{NNTP_PORT}" in line and f"pid={pid}," in line)

    container = f"sab-{name}"
    inspect = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", container],
                             capture_output=True, text=True)
    pid = inspect.stdout.strip()
    if inspect.returncode != 0 or not pid.isdigit() or pid == "0":
        return 0
    result = subprocess.run(["sudo", "-n", "nsenter", "-t", pid, "-n",
                             "ss", "-tn", "state", "established"],
                            capture_output=True, text=True)
    return sum(1 for line in result.stdout.splitlines() if f":{NNTP_PORT}" in line)


def start_sampler(path):
    """Sample established news sockets for the whole run, into one file.

    **Host `ss` cannot see a container's sockets.** Four of the five targets in
    this field run in containers, so their connections live in another network
    namespace and never appear in the host's socket table. The first rehearsal
    of this round sampled the host only and reported that not one target held a
    single news connection, while production zurg -- a bare process on the same
    box -- showed eight. That is not parity evidence; it is an empty
    measurement that looks like one.

    So each sample walks the host table and then every running target's own
    namespace, with `nsenter` borrowing the host's `ss` rather than needing one
    inside the image. Lines are tagged with where they came from, because the
    account is shared: production's sockets show up too and must be
    distinguishable from the target's.
    """
    script = (
        "while :; do "
        "date +%s; "
        f"ss -tnp state established 2>/dev/null | grep ':{NNTP_PORT}' | sed 's/^/host /' || true; "
        "for c in $(docker ps --filter name=sab- --format '{{.Names}}' 2>/dev/null); do "
        "  pid=$(docker inspect -f '{{.State.Pid}}' \"$c\" 2>/dev/null); "
        "  [ -n \"$pid\" ] && sudo -n nsenter -t \"$pid\" -n ss -tn state established 2>/dev/null "
        f"    | grep ':{NNTP_PORT}' | sed \"s|^|$c |\" || true; "
        "done; "
        "sleep 5; done"
    )
    handle = open(path, "w")
    return subprocess.Popen(["bash", "-c", script], stdout=handle, stderr=subprocess.DEVNULL), handle


def rotation(names, round_name):
    """A stable per-round rotation, so first place moves between rounds."""
    if not names:
        return names
    offset = sum(ord(character) for character in round_name) % len(names)
    return names[offset:] + names[:offset]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round", default="round1")
    parser.add_argument("--only", help="comma-separated target names")
    parser.add_argument("--disable", help="comma-separated target names")
    parser.add_argument("--plane", default="protocol", choices=["protocol", "client", "both"])
    parser.add_argument("--read-s", type=float, default=30)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--noise-floor", type=int, default=0,
                        help="extra passes over N sampled titles on the first target, "
                             "to state the resolution below which two targets are tied")
    parser.add_argument("--only-title",
                        help="comma-separated imdb ids. For a dress rehearsal of the "
                             "orchestration only -- a published round is over the whole "
                             "fixed population, because that is what its medians mean")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    names = registry.enabled(only=args.only.split(",") if args.only else None,
                             disable=args.disable.split(",") if args.disable else None)
    unverified = registry.unverified(names)
    if unverified:
        raise SystemExit(
            f"these targets are still `shakedown`: {unverified}\n"
            f"Their endpoints were written from documentation, not read off a running "
            f"instance. Stand each one up (harness/standup/<target>.py), run "
            f"harness/verify_endpoints.py, and clear the flag. A round must not start here.")

    # a budget that cannot cover the set truncates a slow target and reads as
    # missing coverage, so say so before hours are spent rather than after
    import protocol
    worst_case = len(protocol.load_titles(protocol.TITLES)[1]) * protocol.TITLE_BUDGET_S
    tight = [n for n in names
             if registry.TARGETS[n].get("budget_s", registry.DEFAULT_BUDGET_S) < worst_case]

    order = rotation(names, args.round)
    skipped = registry.excluded()
    print(f"round {args.round}: {len(order)} target(s), order {order}")
    if skipped:
        for name, why in skipped.items():
            print(f"  excluded: {name} — {why[:120]}...")
    if tight:
        print(f"  ! {tight} have a budget below the set's worst case ({worst_case}s). "
              f"If one is slow throughout, its remaining titles go unmeasured and read "
              f"as coverage it does not have.")
    if args.plane != "protocol":
        print("  note: the client plane is driven separately by harness/client.py")
    if args.dry_run:
        print("dry run, nothing started")
        return 0

    out_dir = os.path.join(ROOT, "results", args.round)
    os.makedirs(out_dir, exist_ok=True)
    sampler, handle = start_sampler(os.path.join(out_dir, "sockets.log"))
    windows = {}

    try:
        stop_everything()
        for name in order:
            print(f"\n=== {name} ===")
            result = start_target(name)
            if result.returncode != 0:
                print(f"  ! could not start {name}: {result.stderr.strip()[:300]}")
                continue
            ready = wait_ready(name)
            started = time.time()
            passes = args.repeat + (args.noise_floor if name == order[0] else 0)
            command = [sys.executable, "-u", os.path.join(ROOT, "harness", "protocol.py"),
                       "--target", name, "--round", args.round,
                       "--read-s", str(args.read_s), "--repeat", str(max(passes, 1))]
            if args.only_title:
                command += ["--only-title", args.only_title]
            subprocess.run(command, cwd=ROOT)
            windows[name] = {"ready_utc": datetime.fromtimestamp(ready, timezone.utc).isoformat(timespec="seconds"),
                             "start": started, "end": time.time()}
            stop_target(name)
            drain(name)
    finally:
        sampler.terminate()
        handle.close()

    with open(os.path.join(out_dir, "windows.json"), "w") as out:
        json.dump({"round": args.round, "order": order, "windows": windows,
                   "excluded": skipped}, out, indent=1)
    print(f"\nwrote {os.path.relpath(out_dir, ROOT)}/windows.json and sockets.log")
    print("next: python3 harness/report.py --round " + args.round)
    print("      python3 harness/scan_leaks.py results/   <- before publishing anything")
    return 0


if __name__ == "__main__":
    sys.exit(main())
