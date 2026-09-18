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
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402
import versions  # noqa: E402
import resources  # noqa: E402

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

    **It refuses to start onto a port something else already answers.** A bare
    process cannot bind a port twice: the one this round starts exits at once
    with `address already in use`, `wait_ready` then gets its manifest from the
    stranger, and every row in that phase is measured against a process the
    round did not start, cannot stop and does not count. Round 2 was voided by
    exactly this -- an operator's own instance, left running from a version
    check, answered zurg's whole phase and held fifteen news connections
    through the three phases after it. A container cannot fail this way: the
    name collides and `compose up` says so.
    """
    target = registry.TARGETS[name]
    if target.get("launch") != "command":
        return compose(name, "up", "-d")
    directory = run_dir(name)
    pidfile = os.path.join(directory, "target.pid")
    stop_target(name)
    if answers(name):
        raise SystemExit(
            f"{name}: something is already answering its manifest, and it is not this "
            f"round's -- the round stops what it starts and it has just stopped that. "
            f"Find it (pgrep -x {name}) and stop it before starting a round: left "
            f"running it would answer this phase and hold news connections through "
            f"every phase after it.")
    log = open(os.path.join(directory, "target.log"), "a")
    # **Not `shell=True`.** Through a shell, the pid this records is the
    # shell's, and `/bin/sh` on the bench host forks rather than execs: the
    # round then stops a shell that has already gone and leaves the target
    # running. It ran to the end of round 2's first clean pass that way,
    # holding fifteen news connections through the phase after it, and round 1
    # never noticed because the rotation put zurg last.
    process = subprocess.Popen(shlex.split(target["start"]), cwd=directory,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    with open(pidfile, "w") as handle:
        handle.write(str(process.pid))
    # a target that exits immediately -- a bad config, a bound port -- must not
    # be waited on for four minutes and then measured as a row of failures
    time.sleep(3)
    if process.poll() is not None:
        raise SystemExit(f"{name} exited {process.returncode} within three seconds of "
                         f"starting; see {os.path.join(directory, 'target.log')}")
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def answers(name):
    """Is anything already serving this target's manifest?"""
    try:
        url = manifest_of(name)
    except SystemExit:
        return False
    request = urllib.request.Request(url, headers={"User-Agent": "sab-round/1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def stop_target(name):
    """Stop one target, and prove it stopped. Returns the pid, for the drain.

    Proving it matters more than stopping it. A stop that silently fails leaves
    the previous target running beside the next one, sharing an account whose
    connection budget is the parity rule this whole round rests on, and the
    only place that shows up afterwards is the socket samples.
    """
    target = registry.TARGETS[name]
    if target.get("launch") != "command":
        return compose(name, "stop")
    pidfile = os.path.join(run_dir(name), "target.pid")
    if not os.path.exists(pidfile):
        return None
    with open(pidfile) as handle:
        pid = handle.read().strip()
    if not pid.isdigit():
        os.remove(pidfile)
        return None
    alive = lambda: subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0
    # SIGTERM so the target drains rather than dropping reads mid-flight
    subprocess.run(["kill", pid], capture_output=True)
    for _ in range(20):
        if not alive():
            break
        time.sleep(1)
    else:
        subprocess.run(["kill", "-9", pid], capture_output=True)
        time.sleep(2)
    if alive():
        raise SystemExit(f"{name} (pid {pid}) survived SIGKILL. It would run through the "
                         f"next target's phase on the same news account, so the round "
                         f"stops here rather than publishing that as parity.")
    os.remove(pidfile)
    return pid


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


def drain(name=None, pid=None, seconds=45):
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
        if count_nntp_sockets(name, pid) == 0:
            return
        time.sleep(3)


def count_nntp_sockets(name, pid=None):
    """Established news connections held by one target, wherever it runs.

    A container's sockets are only visible from inside its own namespace, so
    the count is taken there; a target that is a bare process is counted from
    the host table by its own pid, never by name, because several unrelated
    zurg processes run on this box.
    """
    target = registry.TARGETS.get(name) or {}
    if target.get("launch") == "command":
        # the pid is passed in by the drain, which runs after stop_target has
        # removed the pidfile: reading the file there found nothing and
        # returned zero, so the drain returned instantly every time
        if pid is None:
            pidfile = os.path.join(RUN, name, "target.pid")
            if not os.path.exists(pidfile):
                return 0
            with open(pidfile) as handle:
                pid = handle.read().strip()
        if not str(pid).isdigit():
            return 0
        result = subprocess.run(["ss", "-tnp", "state", "established"],
                                capture_output=True, text=True)
        return sum(1 for line in result.stdout.splitlines()
                   if nntp_peer(line) and f"pid={pid}," in line)

    container = f"sab-{name}"
    inspect = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", container],
                             capture_output=True, text=True)
    pid = inspect.stdout.strip()
    if inspect.returncode != 0 or not pid.isdigit() or pid == "0":
        return 0
    result = subprocess.run(["sudo", "-n", "nsenter", "-t", pid, "-n",
                             "ss", "-tn", "state", "established"],
                            capture_output=True, text=True)
    return sum(1 for line in result.stdout.splitlines() if nntp_peer(line))


def nntp_peer(line):
    """True only when the remote endpoint is the NNTP port.

    Matching `:563` anywhere also matches an unrelated connection whose local
    ephemeral port happens to end in 563. That produced one phantom sshd socket
    beside every target in a clean parity run.
    """
    fields = line.split()
    return len(fields) >= 4 and fields[3].endswith(f":{NNTP_PORT}")


def activate_connection_budget(name, budget):
    """Apply the measured pool budget after startup, then enforce its ceiling.

    AIOStreams validates its provider with a transient NNTP connection while
    its configured pool is already live. Booting at budget-1 and raising the
    pool here keeps that control connection inside the same total allowance;
    the measured phase still runs with the requested pool size.
    """
    if name == "aiostreams":
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "harness", "standup", "aiostreams.py"),
             "--connections", str(budget)],
            cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"could not raise AIOStreams to {budget} connections: "
                             f"{result.stderr.strip()[:300]}")

    deadline = time.time() + 60
    while time.time() < deadline:
        sockets = count_nntp_sockets(name)
        if sockets <= budget:
            return
        time.sleep(1)
    raise SystemExit(f"{name} still holds {count_nntp_sockets(name)} NNTP sockets; "
                     f"the round budget is {budget}")


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
        f"ss -tnp state established 2>/dev/null | awk '$4 ~ /:{NNTP_PORT}$/ {{print}}' | sed 's/^/host /' || true; "
        "for c in $(docker ps --filter name=sab- --format '{{.Names}}' 2>/dev/null); do "
        "  pid=$(docker inspect -f '{{.State.Pid}}' \"$c\" 2>/dev/null); "
        "  [ -n \"$pid\" ] && sudo -n nsenter -t \"$pid\" -n ss -tn state established 2>/dev/null "
        f"    | awk '$4 ~ /:{NNTP_PORT}$/ {{print}}' | sed \"s|^|$c |\" || true; "
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
    parser.add_argument("--connections", type=int,
                        default=int(os.environ.get("CONNS", "10")))
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
    # captured inside the loop, because a target's manifest can only be asked
    # while it is the one running and `:latest` can move between rounds
    builds = {}
    resource_rows = {}

    try:
        stop_everything()
        for name in order:
            print(f"\n=== {name} ===")
            result = start_target(name)
            if result.returncode != 0:
                print(f"  ! could not start {name}: {result.stderr.strip()[:300]}")
                continue
            ready = wait_ready(name)
            activate_connection_budget(name, args.connections)
            builds[name] = versions.capture(name)
            started = time.time()
            passes = args.repeat + (args.noise_floor if name == order[0] else 0)
            command = [sys.executable, "-u", os.path.join(ROOT, "harness", "protocol.py"),
                       "--target", name, "--round", args.round,
                       "--read-s", str(args.read_s), "--repeat", str(max(passes, 1))]
            if args.only_title:
                command += ["--only-title", args.only_title]
            state_dir = run_dir(name)
            disk_before = resources.disk_usage_bytes(state_dir)
            resource_sampler = resources.Sampler(
                lambda target=name: resources.target_pids(target, RUN)).start()
            try:
                subprocess.run(command, cwd=ROOT)
            finally:
                disk_after = resources.disk_usage_bytes(state_dir)
                resource_rows[name] = resource_sampler.stop(disk_before, disk_after)
            windows[name] = {"ready_utc": datetime.fromtimestamp(ready, timezone.utc).isoformat(timespec="seconds"),
                             "start": started, "end": time.time()}
            drain(name, stop_target(name))
    finally:
        sampler.terminate()
        handle.close()

    with open(os.path.join(out_dir, "versions.json"), "w") as out:
        json.dump({"round": args.round, "targets": builds}, out, indent=1)

    with open(os.path.join(out_dir, "windows.json"), "w") as out:
        json.dump({"round": args.round, "order": order, "windows": windows,
                   "excluded": skipped}, out, indent=1)
    with open(os.path.join(out_dir, "resources.json"), "w") as out:
        json.dump({"round": args.round, "targets": resource_rows}, out, indent=1)
    print(f"\nwrote {os.path.relpath(out_dir, ROOT)}/windows.json, versions.json, "
          "resources.json and sockets.log")
    print("next: python3 harness/report.py --round " + args.round)
    print("      python3 harness/scan_leaks.py results/   <- before publishing anything")
    return 0


if __name__ == "__main__":
    sys.exit(main())
