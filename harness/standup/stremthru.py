#!/usr/bin/env python3
"""Stand up StremThru's Newz addon and mint its URL. Clears its `shakedown`.

    python3 harness/standup/stremthru.py --base http://127.0.0.1:8484

StremThru splits the configuration this round needs across two surfaces, and
neither is an environment variable, which is why `harness/targets.py` carried
`/stremio/store/{userdata}` written from documentation and why that turned out
to be the wrong addon:

  * **the news account** lives in the vault, behind the dashboard API at
    `/dash/api/vault/usenet/servers`, which needs an admin session. So the
    container needs `STREMTHRU_AUTH` and `STREMTHRU_AUTH_ADMIN`, and this signs
    in before it can write anything.
  * **the indexers** live in the addon's own userdata, posted to
    `/stremio/newz/configure`, which mints the per-install URL segment. There
    is no template for that segment and no way to write it in advance.

The addon is `/stremio/newz`, not `/stremio/store`. The store addon can expose
usenet, but only as a store behind a debrid-shaped token, and it is not the
surface a Newz user installs. Measuring it would have measured the wrong
product.

`mode=stream` on purpose: this round measures NNTP streaming, and
`debrid+stream` would let a debrid cache answer for it.

**The size cap is `Size`, not `File.Size`.** Both parse and both mint a working
URL. `File.Size <= "6 GB"` returns the full 120 streams with a 71 GB release at
the top -- the field is empty at this point in the pipeline, so the comparison
is vacuously true and the filter silently does nothing. `Size <= "6 GB"` returns
18 with a 5.7 GB maximum. This is exactly why parity rule 2 says to verify the
cap from the served stream list rather than from the config: the wrong one of
these is indistinguishable from the right one everywhere except the output.

**The minted URL carries live api keys.** StremThru's userdata segment is not
an opaque id: it is base64 of the JSON this posts, indexer api keys included.
So the URL goes only into config/endpoints.local.json, which is gitignored,
and harness/sanitize.py knows how to recognise a base64-wrapped key as well as
a bare one -- a scrubber that only looks for the plain string would pass this
straight through into a published result file.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def signin(base, user, password):
    status, body, headers = common.request(
        f"{base}/dash/api/auth/signin", data={"user": user, "password": password})
    if status != 200:
        raise SystemExit(f"dash sign-in failed: http {status}: {body[:200]}\n"
                         f"the container needs STREMTHRU_AUTH={user}:<pass> and "
                         f"STREMTHRU_AUTH_ADMIN={user}")
    cookies = []
    for line in headers.get("Set-Cookie", "").split("\n"):
        if line.strip():
            cookies.append(line.split(";", 1)[0].strip())
    # urllib collapses repeated headers, so read them off the raw message too
    return "; ".join(cookies)


def add_server(base, cookie, account, connections):
    payload = {
        "host": account["host"],
        "port": account["port"],
        "tls": account["tls"],
        "tls_skip_verify": False,
        "username": account["user"],
        "password": account["password"],
        "name": "bench",
        "max_connections": connections,
        "priority": 1,
        "is_backup": False,
    }
    status, body, _ = common.request(f"{base}/dash/api/vault/usenet/servers",
                                     data=payload, headers={"Cookie": cookie})
    if status not in (200, 201):
        raise SystemExit(f"could not add the usenet server: http {status}: {body[:300]}")
    print(f"usenet server added, max_connections={connections}")


def existing_servers(base, cookie):
    status, body, _ = common.request(f"{base}/dash/api/vault/usenet/servers",
                                     headers={"Cookie": cookie})
    if status != 200:
        return []
    try:
        return json.loads(body).get("data") or []
    except json.JSONDecodeError:
        return []


def filter_expression(max_size_gb):
    """The stream filter, empty by default.

    The round gives no target a size filter of its own -- it caps at the pick,
    identically for everyone, so that `n_streams` and `picked_rank` compare
    unfiltered rankings rather than configurations. `--max-size-gb` is kept
    because the expression below is the one that works and the one that does
    not is indistinguishable from it: `File.Size <= "6 GB"` mints a working URL
    and filters nothing (120 streams, a 71 GB release on top), while
    `Size <= "6 GB"` returns 18 with a 5.7 GB maximum. Anyone reproducing this
    with a cap should use the second.
    """
    return "" if not max_size_gb else f'Size <= "{max_size_gb} GB"'


def configure(base, user, password, indexers, cookie, max_size_gb):
    """Post the addon's own configure form and read the minted URL back."""
    # stores_length 0 is rejected at stream time, not at configure time: the
    # URL mints fine and every stream request then answers
    # `stores[0].code: missing store`. In mode=stream the store IS StremThru
    # itself -- code "" -- and its token is the plain `user:pass` of the
    # account whose vault holds the news server, not a base64 of it.
    fields = [("user", user), ("pass", password), ("mode", "stream"),
              ("indexers_length", str(len(indexers))), ("stores_length", "1"),
              ("stores[0].code", ""), ("stores[0].token", f"{user}:{password}"),
              ("sort", ""), ("filter", filter_expression(max_size_gb))]
    for index, indexer in enumerate(indexers):
        fields.append((f"indexers[{index}].type", "generic"))
        # the label, never the real name: a target that prints its indexer
        # into a stream description would otherwise publish the identity
        fields.append((f"indexers[{index}].name", indexer["label"]))
        fields.append((f"indexers[{index}].url", indexer["url"].rstrip("/")))
        fields.append((f"indexers[{index}].apikey", indexer["api_key"]))
    body = urllib.parse.urlencode(fields)
    status, text, headers = common.request(
        f"{base}/stremio/newz/configure", data=body,
        headers={"Cookie": cookie, "HX-Request": "true"})
    if status not in (200, 302):
        raise SystemExit(f"configure failed: http {status}: {text[:400]}")
    # HTMX answers a successful configure with an empty body and the address in
    # Hx-Location, so there is nothing to scrape out of the HTML. The plain
    # form post returns the page instead and does not carry the URL at all.
    location = ""
    for header in ("Hx-Location", "HX-Location", "Location", "Hx-Redirect", "HX-Redirect"):
        if headers.get(header):
            location = headers[header]
            break
    found = re.findall(r"/stremio/newz/([A-Za-z0-9_\-%.=+]+)/(?:manifest\.json|configure)", location)
    found += re.findall(r"/stremio/newz/([A-Za-z0-9_\-%.=+]+)/manifest\.json", text)
    if not found:
        raise SystemExit("configure answered without a manifest URL. The response is below; "
                         "the form fields it rejected are usually named in it.\n"
                         + (location or text[:800]))
    return found[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8484")
    parser.add_argument("--user", default="bench")
    parser.add_argument("--password", default=os.environ.get("STREMTHRU_BENCH_PASS", "sabround1pass"))
    parser.add_argument("--connections", type=int, default=15,
                        help="the parity value. Verified from sockets afterwards, not from here")
    parser.add_argument("--max-size-gb", type=int, default=0,
                        help="0 (default) sets no filter, which is what a round uses: "
                             "the cap is applied at the pick for every target alike")
    args = parser.parse_args()

    account = common.account()
    indexers = common.parity_indexers()
    print(f"parity indexers: {[i['label'] for i in indexers]}")

    cookie = signin(args.base, args.user, args.password)
    if existing_servers(args.base, cookie):
        print("a usenet server is already in the vault, leaving it alone")
    else:
        add_server(args.base, cookie, account, args.connections)

    userdata = configure(args.base, args.user, args.password, indexers, cookie,
                         args.max_size_gb)
    manifest = f"{args.base}/stremio/newz/{userdata}/manifest.json"
    stream = f"{args.base}/stremio/newz/{userdata}/stream/{{type}}/{{id}}.json"
    common.record_endpoint("stremthru", manifest, stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
