#!/usr/bin/env python3
"""What build of each target a round actually measured.

    python3 harness/versions.py --round round2        # every target, one at a time
    python3 harness/versions.py --only zurg

A round that says "the latest versions" and does not record which ones it had
is not a measurement anybody can repeat, and it cannot be read against an
earlier round: a target that moved and a target that did not look identical in
the tables. Round 1 recorded nothing of the sort, so the only evidence of what
it ran is an image mtime on the bench host.

Three identities, and none of them is enough on its own:

  * **The manifest's `version`.** The addon's own claim, over the same protocol
    the round measures, and the only field every target in the field publishes.
    It is also the one a project forgets to bump, so it is recorded and never
    trusted alone.
  * **The image digest.** What was actually pulled. `:latest` is a moving tag
    and two rounds a day apart can run different code under the same name.
  * **The binary.** zurg is not a container, so its identity is its sha256 and
    the commit its ldflags carry.

Capture happens while the target is up, inside the round's own loop, because
the manifest is only answerable then and the image is only pinned then. Run by
hand it starts nothing: it reports what a running target says and marks the
rest `not-running`, which is honest rather than convenient.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.expanduser("~/stremio-addon-bench/run")
ENDPOINTS = os.path.join(ROOT, "config", "endpoints.local.json")
TIMEOUT = 20
USER_AGENT = "usenet-addon-census/1 (benchmark; contact via github.com/debridmediamanager)"


def manifest_url(name):
    entry = {}
    if os.path.exists(ENDPOINTS):
        with open(ENDPOINTS) as handle:
            entry = json.load(handle).get(name) or {}
    url = entry.get("manifest") or registry.TARGETS[name]["manifest"]
    return None if "{" in url else url


def manifest_version(name):
    """The addon's own declared version, or why it could not be asked."""
    url = manifest_url(name)
    if not url:
        return {"manifest_version": None, "manifest_error": "no minted URL"}
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            manifest = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as problem:
        return {"manifest_version": None,
                "manifest_error": f"{type(problem).__name__}: {problem}"}
    return {"manifest_version": manifest.get("version"),
            "manifest_id": manifest.get("id"),
            "manifest_name": manifest.get("name")}


def image_identity(name):
    """The image behind a container target, read off the daemon rather than the tag.

    `docker compose config` is what resolves the image the round would actually
    start, including an override in the target's own compose file. The digest
    is the only part that pins anything: `:latest` moved under three of the
    four targets between round 1 and round 2.
    """
    directory = os.path.join(RUN, name)
    if not os.path.isdir(directory):
        return {"image_error": f"no run directory at {directory}"}
    reference = subprocess.run(
        ["docker", "compose", "config", "--images"],
        cwd=directory, capture_output=True, text=True)
    image = (reference.stdout or "").strip().splitlines()
    if not image:
        return {"image_error": (reference.stderr or "no image in compose file").strip()[:200]}
    image = image[0]
    inspect = subprocess.run(
        ["docker", "image", "inspect", image, "--format",
         "{{.Id}}\t{{.Created}}\t{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}"],
        capture_output=True, text=True)
    if inspect.returncode != 0:
        return {"image": image, "image_error": inspect.stderr.strip()[:200]}
    identifier, created, digest = (inspect.stdout.strip().split("\t") + ["", "", ""])[:3]
    return {"image": image, "image_id": identifier, "image_created": created,
            "image_digest": digest.split("@", 1)[-1] if "@" in digest else digest or None}


def binary_identity(name):
    """A bare-process target's identity: the file, and what it says it is.

    zurg has no `--version` flag -- it prints its build at startup and nothing
    else does -- so the commit comes out of the log it just wrote. A binary
    built without ldflags reports the string `docker`, which is what round 1's
    did, and that is recorded as-is rather than guessed at.
    """
    start = registry.TARGETS[name].get("start", "")
    path = os.path.join(RUN, name, start.split()[0].lstrip("./")) if start else None
    identity = {}
    if path and os.path.exists(path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        identity["binary_sha256"] = digest.hexdigest()
        identity["binary_mtime"] = int(os.path.getmtime(path))
    log = os.path.join(RUN, name, "target.log")
    if os.path.exists(log):
        with open(log, errors="replace") as handle:
            for line in handle:
                for field, key in (("Version:", "build_version"),
                                   ("GitCommit:", "build_commit"),
                                   ("BuiltAt:", "build_at")):
                    if field in line:
                        identity[key] = line.split(field, 1)[1].strip()
    return identity


def capture(name):
    """Everything knowable about one target's build, right now."""
    target = registry.TARGETS[name]
    record = {"language": target["language"], "repo": target["repo"]}
    if target.get("branch"):
        record["branch"] = target["branch"]
    record.update(manifest_version(name))
    if target.get("launch") == "command":
        record.update(binary_identity(name))
    else:
        record.update(image_identity(name))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round")
    parser.add_argument("--only", help="comma-separated target names")
    args = parser.parse_args()

    names = registry.enabled(only=args.only.split(",") if args.only else None)
    captured = {name: capture(name) for name in names}
    for name, record in captured.items():
        pin = record.get("image_digest") or record.get("build_commit") or "?"
        print(f"{name:<12} manifest v{record.get('manifest_version') or '-'}   {pin}")
    if args.round:
        out_dir = os.path.join(ROOT, "results", args.round)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "versions.json")
        with open(path, "w") as out:
            json.dump({"round": args.round, "targets": captured}, out, indent=1)
        print(f"\nwrote {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
