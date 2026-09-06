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


def fetch_manifest(url):
    """Read the manifest here rather than in the page, which cannot reach it."""
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": protocol.USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


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

    def install(self, manifest_url, manifest=None):
        """Add the target to the running client, and clear the modal it opens.

        `settings.addAddon(url)` is what the addon catalogue's own control
        calls, and it does install -- an earlier reading of `API.addons` as an
        array said otherwise and was simply wrong, because it is a service. It
        also opens an "Install Addon" prompt over the app, and that prompt
        blocks every later navigation until it is dismissed.

        The prompt is closed with `closePrompt()`, never with its Install
        button: by the time it is on screen the addon is already installed, so
        that button is a toggle that would uninstall it again.

        The page cannot fetch the manifest itself -- the shell runs from
        https://app.strem.io and the targets are plain http, so it is blocked
        as mixed content -- which is why `manifest` is fetched by the harness
        and kept on the row rather than read here.
        """
        self.evaluate(js(f"""
            var s = {INJECTOR}.get('settings');
            try {{ s.addAddon({json.dumps(manifest_url)}); return 'called'; }}
            catch (e) {{ return 'ERR ' + e.message; }}
        """))
        time.sleep(4)
        self.dismiss_prompt()
        return "installed" if manifest_url in (self.installed() or []) else "NOT installed"

    def dismiss_prompt(self):
        """Close any modal the app has open, without touching its actions."""
        return self.evaluate(js("""
            var wrap = document.querySelector('.modalWrap, .modal');
            if (!wrap) return 'none';
            var scope = angular.element(wrap).scope();
            if (scope && typeof scope.closePrompt === 'function') {
                scope.closePrompt();
                if (scope.$root && scope.$root.$applyAsync) scope.$root.$applyAsync();
                return 'closed';
            }
            var x = document.querySelector('.modal .close, .modalWrap .close');
            if (x) { x.click(); return 'clicked close'; }
            return 'left open';
        """))

    def installed(self):
        return self.evaluate(js(f"""
            var api = {INJECTOR}.get('API');
            var list = api.addons.getAddons ? api.addons.getAddons() : [];
            return list.map(function(x) {{ return x.transportUrl || ''; }});
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
                var wrapper = (scope && scope.streamItem) ? scope.streamItem : {};
                // streamItem is a wrapper: {addon, stream, idx}. The stream is
                // what the addon sent; the addon is who sent it, which is the
                // only way to tell one target's rows from another addon's in a
                // client that has more than one installed
                var item = wrapper.stream || {};
                var addon = wrapper.addon || {};
                out.push({
                    index: i,
                    addon_url: addon.transportUrl || '',
                    addon_name: (addon.manifest || {}).name || '',
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
            # `p.states` was the guard here and it never let stop() run, so
            # every title was measured against the previous one still playing
            self.evaluate(js("""
                var p = angular.element(document).injector().get('player');
                if (p && typeof p.stop === 'function') { p.stop(); }
                location.hash = '#/board';
                return 1;
            """))
        except Exception:
            pass


def wait_for_streams(app, transport_url, budget=LIST_BUDGET_S):
    """Wait for stream rows that carry a url *and* came from this target.

    Two things make the naive version wrong, and both produced a `no-streams`
    row for a title that renders seven of them. The previous title's `.stream`
    nodes are still in the DOM the instant a new route is set, so a wait that
    returns on "any node" returns stale ones; and a row renders before its
    scope has a stream on it, so a node without a url is not yet an answer.
    """
    started = time.monotonic()
    deadline = started + budget
    while time.monotonic() < deadline:
        rendered = app.streams()
        found = [s for s in rendered
                 if s.get("url") and s.get("addon_url") == transport_url]
        if found:
            return found, time.monotonic() - started, rendered
        time.sleep(POLL_S)
    return [], time.monotonic() - started, app.streams()


def clear_streams(app, budget=15):
    """Leave the detail page and wait for its rows to go, before the next one."""
    app.go("#/board")
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not app.streams():
            return True
        time.sleep(POLL_S)
    return False


# Stremio's player service reports `state` 3 while a stream is playing and 0
# once it has been stopped. Nothing else in it resets: `time` keeps the last
# position forever and `initialized` stays true, so "has this title started"
# cannot be asked of those two. Both values were read off the running player,
# not guessed, and the length check below is what covers the reading being
# incomplete.
STATE_PLAYING = 3
STATE_STOPPED = 0


def wait_for_stopped(app, budget=20):
    """Wait for the previous title to actually stop before timing the next.

    Two client-plane runs were thrown away over this. The first read the old
    position and called every row 0.04s. The second required the position to
    advance, which a *still playing* previous title does, so it passed just as
    fast. `state` is the field that moves, and this waits for it.
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        try:
            state = app.player_state()
        except Exception:
            return False
        if not state or state.get("state") != STATE_PLAYING:
            return True
        time.sleep(POLL_S)
    return False


def wait_for_play(app, before, budget=PLAY_BUDGET_S):
    """Playback of *this* title: playing, advancing, and a different file.

    Four conditions, because the previous title satisfies the first three on
    its own if it is still running:

      * `state` is playing;
      * `time` is greater than zero and the player is not paused;
      * `time` is greater again a poll later, so a frozen position cannot pass;
      * the file is not the one that was loaded before the click, judged by its
        duration. Twenty-three different films do not share a runtime to the
        second, and this is what stops a previous stream being measured twice.
    """
    started = time.monotonic()
    deadline = started + budget
    was_length = (before or {}).get("length")
    first_seen = None
    while time.monotonic() < deadline:
        try:
            state = app.player_state()
        except Exception:
            state = None
        if (state and state.get("state") == STATE_PLAYING
                and isinstance(state.get("time"), (int, float)) and state["time"] > 0
                and not state.get("paused")
                and (was_length is None or state.get("length") != was_length)):
            when = time.monotonic() - started
            time.sleep(max(POLL_S, 0.5))
            try:
                again = app.player_state()
            except Exception:
                again = None
            if again and isinstance(again.get("time"), (int, float)) \
                    and again["time"] > state["time"]:
                return when, again
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


def measure_title(app, title, cap_bytes, transport_url):
    row = {
        "id": title["id"],
        "title": title["title"],
        "type": title["type"],
        "expected_outcome": title.get("expected_outcome"),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stream_list_s": None, "n_streams": None, "chosen": None,
        "rows_from_other_addons": None, "player_stopped_first": None,
        "picked_rank": None, "picked_over_cap": None,
        "click_to_play_s": None, "player": None,
        "outcome": None, "detail": None,
    }
    app.dismiss_prompt()
    clear_streams(app)
    # never measure against a stream that is still running
    row["player_stopped_first"] = wait_for_stopped(app)
    try:
        before = app.player_state()
    except Exception:
        before = None
    app.go(f"#/detail/{title['type']}/{title['id']}/{title['id']}")
    streams, listed, rendered = wait_for_streams(app, transport_url)
    row["stream_list_s"] = round(listed, 3)
    # what else the client rendered, so a row is readable when the player has
    # other addons installed. Only this target's rows are ever clicked
    row["rows_from_other_addons"] = len([s for s in rendered
                                         if s.get("addon_url") != transport_url])
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

    elapsed, state = wait_for_play(app, before)
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
    parser.add_argument("--addon-host", default="127.0.0.1",
                        help="the address the PLAYER reaches the target on. Loopback by "
                             "default and for a reason: Stremio's shell is served from "
                             "https://app.strem.io, so a plain-http addon on any other "
                             "host is blocked as mixed content and silently contributes "
                             "no streams. Chromium exempts localhost, so the round "
                             "forwards each target onto the player's loopback "
                             "(ssh -R <port>:<bench host>:<port>)")
    parser.add_argument("--fetch-host", default="zen",
                        help="the address the HARNESS reaches the target on, to read the "
                             "manifest the page is not allowed to fetch. Different from "
                             "--addon-host whenever a tunnel is in use")
    parser.add_argument("--only-title")
    parser.add_argument("--sample", type=int)
    args = parser.parse_args()

    if args.target not in registry.TARGETS:
        raise SystemExit(f"{args.target} is not a registered target")
    with open(ENDPOINTS) as handle:
        manifest = json.load(handle)[args.target]["manifest"]
    # two URLs for one addon: the one the player installs, and the one the
    # harness reads the manifest from. They differ whenever the target is
    # reached through a tunnel, which is the normal case for this plane
    player_url = manifest.replace("127.0.0.1", args.addon_host).replace("localhost", args.addon_host)
    fetch_url = manifest.replace("127.0.0.1", args.fetch_host).replace("localhost", args.fetch_host)

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
        print(f"{args.target}: player installs {player_url.split('//', 1)[-1].split('/')[0]}, "
              f"manifest read from {fetch_url.split('//', 1)[-1].split('/')[0]}")
        descriptor = fetch_manifest(fetch_url)
        state = app.install(player_url, descriptor)
        print(f"  install: {state} ({descriptor.get('name', '?')})")
        if state != "installed":
            raise SystemExit("the addon did not install; nothing below would be this target")
        rows = []
        for title in titles:
            row = measure_title(app, title, cap_bytes, player_url)
            rows.append(row)
            print(f"  {row['id']:<14} {row['outcome']:<15}"
                  f" list={row['stream_list_s']}s n={row['n_streams']}"
                  f" play={row['click_to_play_s']}s"
                  f"  {(row['chosen'] or {}).get('release') or ''}"[:180])

    document = {
        "target": args.target, "plane": "client", "round": args.round,
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client": "Stremio 4.4 over CDP", "addon_host": args.addon_host,
        "fetch_host": args.fetch_host,
        "note": ("this plane crosses a LAN hop the protocol plane does not, "
                 "because the player is not on the bench host. Rows are "
                 "attributed to the addon that produced them, so a client with "
                 "other addons installed renders their streams and this never "
                 "clicks or counts one"),
        "population": len(titles), "cap_bytes": cap_bytes,
        "passes": [rows],
    }
    with open(out_path, "w") as handle:
        json.dump(SCRUB(document), handle, indent=1)
    print(f"wrote {os.path.relpath(out_path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
