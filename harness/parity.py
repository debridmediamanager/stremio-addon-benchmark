#!/usr/bin/env python3
"""Read connection parity out of a round's socket samples.

    python3 harness/parity.py --round round1

The round samples established connections to the news port every five seconds
for its whole duration, into `results/<round>/sockets.log`, and records when
each target was running in `windows.json`. This turns those two into the only
statement about connection parity worth publishing: what each target actually
held, taken as the maximum inside its own window.

Why it is not read off a config file. In several of these projects the pool cap
and the per-read budget are separate settings and the obvious one is not the one
that binds, so a round that quotes its configuration is quoting an intention.
The sibling repository measured a target running at 50 connections while its
config said 20, for two rounds, before anyone sampled.

Two things this deliberately does not do:

  * **it does not call an unequal count a broken round on its own.** A target
    can hold a connection outside its own pool accounting -- zurg's startup
    status check is one -- so a count one above the budget is worth reading
    before it is worth acting on.
  * **it does not attribute a socket to a target by guessing.** Only samples
    inside that target's window count, because that is the only interval in
    which the round knows what was running.
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ss prints `users:(("zurg",pid=1234,fd=7))`, one or more per line
PROCESS = re.compile(r'\(\("([^"]+)",pid=(\d+)')
TIMESTAMP = re.compile(r"^\d{9,}$")


def samples(path):
    """(epoch, source) per sampled socket, in order.

    Two line shapes, because a container's sockets are only visible from inside
    its own namespace:

        host ESTAB ... users:(("zurg",pid=1234,fd=7))
        sab-stremthru ESTAB ...

    The first carries process detail and comes from the host table; the second
    is tagged with the container it was sampled in and carries none, because
    `nsenter -n` enters the network namespace and not the pid namespace. Both
    are one established connection to the news port, which is what is being
    counted.
    """
    if not os.path.exists(path):
        raise SystemExit(f"no {os.path.relpath(path, ROOT)}; was the round run by "
                         f"harness/round.sh?")
    now = None
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if TIMESTAMP.match(line):
                now = int(line)
                continue
            if now is None:
                continue
            tag, _, rest = line.partition(" ")
            if tag != "host":
                # a container's own namespace: one line, one connection, and
                # the container name is the only attribution available
                yield now, tag, None
                continue
            found = PROCESS.findall(rest)
            if not found:
                # a host socket with no process attribution: still a connection
                yield now, "host", None
                continue
            for process, pid in found:
                yield now, process, int(pid)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round", default="round1")
    args = parser.parse_args()

    directory = os.path.join(ROOT, "results", args.round)
    with open(os.path.join(directory, "windows.json")) as handle:
        windows = json.load(handle)["windows"]

    # per target, per sample instant, how many sockets each pid held
    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for when, process, pid in samples(os.path.join(directory, "sockets.log")):
        for target, window in windows.items():
            if window["start"] <= when <= window["end"]:
                counts[target][when][(process, pid)] += 1

    print(f"round {args.round}: connection parity, sampled\n")
    print("`sab-<target>` is that target's own network namespace. Anything else is a "
          "host process sharing the news account -- production, usually -- and is "
          "listed because the budget is shared, not because it is the target.\n")
    print(f"{'target':<12} {'samples':>8} {'peak':>6}  processes at peak")
    print("-" * 68)
    for target in windows:
        instants = counts.get(target) or {}
        if not instants:
            print(f"{target:<12} {0:>8} {'-':>6}  no samples inside its window")
            continue
        peak_at, peak_total = None, 0
        for when, per_pid in instants.items():
            total = sum(per_pid.values())
            if total > peak_total:
                peak_at, peak_total = when, total
        detail = ", ".join((f"{name}[{pid}]={n}" if pid else f"{name}={n}")
                           for (name, pid), n in sorted(instants[peak_at].items(),
                                                        key=lambda item: -item[1]))
        print(f"{target:<12} {len(instants):>8} {peak_total:>6}  {detail}")

    print("\nA count above the configured budget is worth reading before it is worth "
          "acting on: a target can hold a connection outside its own pool accounting. "
          "A count well below it means the target never filled its allowance, which is "
          "a fact about the target rather than a broken parity rule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
