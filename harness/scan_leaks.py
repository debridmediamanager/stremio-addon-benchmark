#!/usr/bin/env python3
"""Refuse to publish anything carrying a credential or a private hostname.

    python3 harness/scan_leaks.py            # every tracked file
    python3 harness/scan_leaks.py results/   # one directory, tracked or not

Exit status is 1 if anything was found, so this belongs in front of a push.

Scan `results/` separately as well as everything else. The sibling repository
learned that the hard way: a scan that covers the harness and skips the output
directory looks clean and is not, and round 7 there needed five substitutions
before it could be pushed.

Three classes of hazard, and the second is the one that gets missed:

  * **Credentials.** Newznab api keys and the news account, read out of
    config/indexers.json and the NNTP_* environment so this file never has to
    carry a copy of what it is looking for.
  * **Identity.** The private indexers and the news host. Result files quote
    provider and indexer hostnames back at you inside error strings and echoed
    request parameters, which is how a hostname reaches a published artifact
    without anybody writing it down.
  * **Shape.** A bare 32 hex character token or an `apikey=` in a URL, which
    catches a key this script was never told about.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "indexers.json")

# Files whose whole purpose is to name what must not be published: an example
# credential, this scanner's own hostname list, and the scrubber's copy of it.
# Without harness/sanitize.py here the gate flags the one file whose job is to
# remove those names, and blocks every push on its own definition.
ALLOWED = {"config/indexers.example.json", "harness/scan_leaks.py",
           "harness/sanitize.py"}

SHAPES = [
    (re.compile(r"apikey=[A-Za-z0-9]{8,}", re.I), "an api key inside a URL"),
    (re.compile(r"\b[0-9a-f]{32}\b"), "a bare 32-hex token"),
    (re.compile(r"\b[A-Za-z0-9]{28,34}\b(?![^\s]*[./])"), "a bare api-key-shaped token"),
]

# hostnames that must never reach a published file even when no key is attached
ALWAYS = ["frugalusenet", "newswest", "onlyusenet"]

# host labels too common to search for on their own
GENERIC_LABELS = {"feed", "api", "www", "nzb", "usenet", "news", "index", "cdn",
                  "search", "public", "cloud", "mail", "http", "https"}


def base64_variants(secret):
    """The three ways a secret can appear inside base64 of a longer string.

    base64 packs three bytes into four characters, so the rendering depends on
    the secret's offset within the blob. The outer four characters of each are
    dropped because they mix with neighbouring bytes.
    """
    out = []
    for offset in (0, 1, 2):
        encoded = base64.b64encode(b"x" * offset + secret.encode()).decode()
        middle = encoded[4:-4]
        if len(middle) >= 12:
            out.append(middle)
    return out


def known_secrets():
    """What to look for, gathered from the places that legitimately hold it."""
    values, hosts = [], []
    if os.path.exists(CONFIG):
        with open(CONFIG) as handle:
            for indexer in json.load(handle)["indexers"]:
                if indexer.get("api_key"):
                    values.append(indexer["api_key"])
                    # a key inside a base64 blob is still a key. StremThru's
                    # addon URL is base64 of a JSON config with the keys in
                    # it, so the literal string never appears and a scan that
                    # looks only for that reports a clean file
                    values.extend(base64_variants(indexer["api_key"]))
                if indexer.get("name"):
                    hosts.append(indexer["name"])
                host = re.sub(r"^https?://", "", indexer.get("url", "")).strip("/")
                if host:
                    hosts.append(host)
                    # api.nzbgeek.info also hides inside "nzbgeek", so keep the
                    # label in front of the suffix. Generic ones are dropped:
                    # "feed" out of feed.animetosho.org matches ordinary prose
                    # and a scanner that cries wolf gets skipped
                    labels = host.split(".")
                    if len(labels) >= 2:
                        candidate = labels[-2]
                        if len(candidate) > 3 and candidate not in GENERIC_LABELS:
                            hosts.append(candidate)
    for variable in ("NNTP_USER", "NNTP_PASS", "NNTP_HOST"):
        if os.environ.get(variable):
            values.append(os.environ[variable])
    return sorted(set(values)), sorted(set(hosts) | set(ALWAYS), key=len, reverse=True)


def tracked_files():
    listing = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split("\n")
    return [f for f in listing if f]


def walk(path):
    if os.path.isfile(path):
        return [os.path.relpath(path, ROOT)]
    found = []
    for base, _, names in os.walk(path):
        for name in names:
            full = os.path.join(base, name)
            found.append(os.path.relpath(full, ROOT))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="file or directory; default is every tracked file")
    parser.add_argument("--shapes", action="store_true",
                        help="also report key-shaped strings this script was never told about. "
                             "Noisy by design: it is meant to be read, not to gate a push")
    args = parser.parse_args()

    secrets, hosts = known_secrets()
    if not secrets:
        print("warning: no credentials found to scan for. Without config/indexers.json "
              "or the NNTP_* environment this only checks hostnames and shapes.\n")

    files = walk(os.path.abspath(args.path)) if args.path else tracked_files()
    hits = 0
    for name in files:
        if name in ALLOWED:
            continue
        full = os.path.join(ROOT, name)
        try:
            with open(full, encoding="utf-8", errors="replace") as handle:
                body = handle.read()
        except (IsADirectoryError, FileNotFoundError, PermissionError):
            continue
        lowered = body.lower()
        for secret in secrets:
            if secret in body:
                print(f"CREDENTIAL  {name}: {secret[:6]}...{secret[-4:]}")
                hits += 1
        for host in hosts:
            if host.lower() in lowered:
                print(f"IDENTITY    {name}: {host}")
                hits += 1
        if args.shapes:
            for pattern, why in SHAPES:
                for match in set(pattern.findall(body)):
                    print(f"shape       {name}: {why}, {match[:10]}...")

    print(f"\n{len(files)} file(s) scanned, {hits} problem(s)")
    if hits:
        print("Do not push. Sanitise or gitignore these first.")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
