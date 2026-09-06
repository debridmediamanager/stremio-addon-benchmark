#!/usr/bin/env python3
"""The client plane: drive Stremio 4.4 itself, and watch the player, not pixels.

    python3 harness/client.py --target stremthru --round round1
    python3 harness/client.py --target zurg --only-title tt0111161

The protocol plane cannot see a stream an addon serves correctly and the player
refuses -- a codec, a container, a range the player asks for and does not get.
This plane can, because it is the real client: Stremio 4.4 on the Windows box,
driven over CDP, opening a title and clicking a stream the way a viewer would.

**It never looks at pixels.** Stremio's video is a multi-plane overlay and is
invisible to every screen-capture path (docs/design.md, trap 8), so a "is there
a picture" check by screenshot is guaranteed to answer no even while a film is
playing. The signal is the app's own player service, reached through its
AngularJS injector: `player.time`, `player.paused`, `player.state`. Time moving
forward is the only proof of playback that this client can give.

Three things about the setup, all of which are properties of the plane rather
than of any target:

  * **there is a network hop.** The protocol plane measures over loopback on
    the bench host; Stremio runs on a different machine, so a client-plane
    number carries one LAN hop that the protocol number does not. The two are
    published side by side and never averaged, which is why that is tolerable.
  * **the addon URL must be reachable from the player**, so `127.0.0.1` in
    config/endpoints.local.json is rewritten to `--addon-host`.
  * **Stremio must be launched detached.** Started over ssh it dies with the
    session; the round launches it from a scheduled task and connects to the
    debugger it leaves behind.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import targets as registry  # noqa: E402
from cdp import Browser  # noqa: E402
from sanitize import Scrubber  # noqa: E402
import protocol  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINTS = os.path.join(ROOT, "config", "endpoints.local.json")
SCRUB = Scrubber()

# how long to wait for a stream list to render, and for the player to start.
# The play budget is generous for the same reason the protocol plane's is: a
# cold multi-volume archive legitimately takes tens of seconds.
LIST_BUDGET_S = 60
PLAY_BUDGET_S = 180
POLL_S = 0.25

INJECTOR = "angular.element(document).injector()"


def js(expression):
    """Wrap an expression as an IIFE so `return` works."""
    return "(function(){" + expression + "})()"


class Stremio:
    def __init__(self, page):
        self.page = page

    def evaluate(self, expression, timeout=40):
        return self.page.evaluate(expression, timeout=timeout)

    def player_state(self):
        return self.evaluate(js(f"""
            var p = {INJECTOR}.get('player');
            return {{time: p.time, paused: p.paused, state: p.state,
                     length: p.length, initialized: p.initialized,
                     width: p.width, height: p.height}};
        """))

    def install(self, manifest_url):
        return self.evaluate(js(f"""
            var s = {INJECTOR}.get('settings');
            try {{ s.addAddon({json.dumps(manifest_url)}); return 'ok'; }}
            catch (e) {{ return 'ERR ' + e.message; }}
        """))

    def go(self, hash_route):
        self.evaluate(f"location.hash = {json.dumps(hash_route)}; 1")

    def streams(self):
        """The rendered stream list, read off each node's Angular scope.

        The DOM shows a name and a description; the scope holds the stream the
        addon actually sent, which is what the size cap has to be applied to.
        """
        return self.evaluate(js("""
            var nodes = document.querySelectorAll('.stream');
            var out = [];
            for (var i = 0; i < nodes.length; i++) {
                var scope = angular.element(nodes[i]).scope();
                var item = scope && scope.streamItem ? scope.streamItem : {};
                out.push({
                    index: i,
                    name: item.name || '',
                    description: item.description || item.title || '',
                    url: item.url || '',
                    behaviorHints: item.behaviorHints || {},
                    text: (nodes[i].innerText || '').slice(0, 200)
                });
            }
            return out;
        """))

    def click(self, index):
        return self.evaluate(js(f"""
            var nodes = document.querySelectorAll('.stream');
            if (!nodes[{index}]) return 'missing';
            nodes[{index}].click();
            return 'clicked';
        """))

    def stop(self):
        """Leave the player, so the next title starts from the same place."""
        try:
            self.evaluate(js("""
                var p = angular.element(document).injector().get('player');
                if (p && p.states && typeof p.stop === 'function') { p.stop(); }
                location.hash = '#/board';
                return 1;
            """))
        except Exception:
            pass


def wait_for_streams(app, budget=LIST_BUDGET_S):
    """Wait for stream rows that carry a url.

    Two things make the naive version wrong, and both produced a `no-streams`
    row for a title that renders seven of them. The previous title's `.stream`
    nodes are still in the DOM the instant a new route is set, so a wait that
    returns on "any node" returns stale ones; and a row renders before its
    scope has a stream on it, so a node without a url is not yet an answer.
    """
    started = time.monotonic()
    deadline = started + budget
    while time.monotonic() < deadline:
        found = [s for s in app.streams() if s.get("url")]
        if found:
            return found, time.monotonic() - started
        time.sleep(POLL_S)
    return [], time.monotonic() - started


def clear_streams(app, budget=15):
    """Leave the detail page and wait for its rows to go, before the next one."""
    app.go("#/board")
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not app.streams():
            return True
        time.sleep(POLL_S)
    return False


def wait_for_play(app, budget=PLAY_BUDGET_S):
    """Playing means time is moving. A paused player at 0 is not playback."""
    started = time.monotonic()
    deadline = started + budget
    first_seen = None
    while time.monotonic() < deadline:
        try:
            state = app.player_state()
        except Exception:
            state = None
        if state and isinstance(state.get("time"), (int, float)) and state["time"] > 0:
            if not state.get("paused"):
                return time.monotonic() - started, state
            first_seen = first_seen or state
        time.sleep(POLL_S)
    return None, first_seen


def describe(stream):
    """Reuse the protocol plane's parsing so `chosen` means the same thing."""
    return protocol.describe({
        "name": stream.get("name"),
        "description": stream.get("description") or stream.get("text"),
        "behaviorHints": stream.get("behaviorHints") or {},
        "url": stream.get("url"),
    })


def pick(streams, cap_bytes):
    """The same rule as the protocol plane, over what the player rendered."""
    sized = [(s, describe(s)["size_bytes"]) for s in streams]
    within = [(s, n) for s, n in sized if isinstance(n, int) and n <= cap_bytes]
    if within:
        return within[0][0], False
    unknown = [(s, n) for s, n in sized if not isinstance(n, int)]
    if unknown:
        return unknown[0][0], False
    return min(sized, key=lambda item: item[1])[0], True


def measure_title(app, title, cap_bytes):
    row = {
        "id": title["id"],
        "title": title["title"],
        "type": title["type"],
        "expected_outcome": title.get("expected_outcome"),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stream_list_s": None, "n_streams": None, "chosen": None,
        "picked_rank": None, "picked_over_cap": None,
        "click_to_play_s": None, "player": None,
        "outcome": None, "detail": None,
    }
    clear_streams(app)
    app.go(f"#/detail/{title['type']}/{title['id']}/{title['id']}")
    streams, listed = wait_for_streams(app)
    row["stream_list_s"] = round(listed, 3)
    playable = [s for s in streams if s.get("url") and not protocol.is_notice(
        {"name": s.get("name"), "description": s.get("description"), "url": s.get("url")})]
    row["n_streams"] = len(playable)
    if not playable:
        row["outcome"] = "no-streams"
        return row

    chosen, over_cap = pick(playable, cap_bytes)
    row["chosen"] = describe(chosen)
    row["picked_rank"] = chosen["index"]
    row["picked_over_cap"] = over_cap

    if app.click(chosen["index"]) != "clicked":
        row["outcome"] = "error"
        row["detail"] = "the stream row could not be clicked"
        return row

    elapsed, state = wait_for_play(app)
    row["player"] = state
    if elapsed is None:
        # the addon served it and the player would not play it. This is the
        # one outcome the protocol plane cannot produce, and the reason this
        # plane exists
        row["outcome"] = "player-refused"
        row["detail"] = "no forward playback within the budget"
    else:
        row["click_to_play_s"] = round(elapsed, 3)
        row["outcome"] = "played"
    app.stop()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True)
    parser.add_argument("--round", default="round1")
    parser.add_argument("--cdp-host", default="127.0.0.1")
    parser.add_argument("--cdp-port", type=int, default=9223)
    parser.add_argument("--addon-host", default="zen",
                        help="the address the player can reach the targets on; "
                             "127.0.0.1 in the endpoint file is rewritten to this")
    parser.add_argument("--only-title")
    parser.add_argument("--sample", type=int)
    args = parser.parse_args()

    if args.target not in registry.TARGETS:
        raise SystemExit(f"{args.target} is not a registered target")
    with open(ENDPOINTS) as handle:
        manifest = json.load(handle)[args.target]["manifest"]
    manifest = manifest.replace("127.0.0.1", args.addon_host).replace("localhost", args.addon_host)

    data, titles = protocol.load_titles(protocol.TITLES,
                                        args.only_title.split(",") if args.only_title else None,
                                        args.sample)
    cap_bytes = data.get("playable_cap_bytes") or 6 * 1024 ** 3

    out_dir = os.path.join(ROOT, "results", args.round)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"client-{args.target}.json")

    with Browser(args.cdp_host, args.cdp_port) as browser:
        app = Stremio(browser.first_page())
        # printed by shape only: two of these URLs are credentials
        shown = manifest.split("//", 1)[-1].split("/")[0]
        print(f"{args.target}: installing an addon on {shown}")
        print("  install:", app.install(manifest))
        time.sleep(6)
        rows = []
        for title in titles:
            row = measure_title(app, title, cap_bytes)
            rows.append(row)
            print(f"  {row['id']:<14} {row['outcome']:<15}"
                  f" list={row['stream_list_s']}s n={row['n_streams']}"
                  f" play={row['click_to_play_s']}s"
                  f"  {(row['chosen'] or {}).get('release') or ''}"[:180])

    document = {
        "target": args.target, "plane": "client", "round": args.round,
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client": "Stremio 4.4 over CDP", "addon_host": args.addon_host,
        "note": ("this plane crosses a LAN hop the protocol plane does not, "
                 "because the player is not on the bench host"),
        "population": len(titles), "cap_bytes": cap_bytes,
        "passes": [rows],
    }
    with open(out_path, "w") as handle:
        json.dump(SCRUB(document), handle, indent=1)
    print(f"wrote {os.path.relpath(out_path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
