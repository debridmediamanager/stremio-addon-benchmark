#!/usr/bin/env python3
"""Replace identities on the way *into* a result file, not on the way out.

`harness/scan_leaks.py` is the gate in front of a push and it stays there. This
is the other half: the round writes what a target told it, and a target tells
it the indexer's real name in the middle of a stream description
(`The.Release.Name\\n35.94 GB · real-indexer`) and the news host in the middle
of an error string. Sanitising those at publish time means editing a
measurement after the fact, which is exactly the kind of hand-editing this
repository is built to avoid.

So a result file is written already carrying `indexer-a`. The mapping from
that to a real name lives in `config/indexers.json`, which is gitignored, and
scan_leaks.py still runs afterwards to catch whatever this missed.

    from sanitize import Scrubber
    scrub = Scrubber()
    row["detail"] = scrub(row["detail"])

An indexer this has never heard of becomes `unmapped-<8 hex>` rather than being
passed through. The hash is stable, so the same unknown indexer is recognisable
across rows and targets without being named -- the same trade the census makes
for a newznab guid.
"""
import base64
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "indexers.json")

# the news account's host never appears in a published artifact under any
# name. The sibling repository substitutes the same placeholder
NEWS_PLACEHOLDER = "news-provider.example"
ALWAYS_NEWS = ["frugalusenet", "newswest", "onlyusenet"]

# host labels too common to substitute blindly: "news" out of a provider host
# would rewrite ordinary prose
GENERIC_LABELS = {"feed", "api", "www", "nzb", "usenet", "news", "index", "cdn",
                  "search", "public", "cloud", "mail", "http", "https"}


def base64_variants(secret):
    """Every way `secret` can appear inside base64 of a larger string.

    base64 packs three bytes into four characters, so a secret embedded in a
    longer blob is encoded differently depending on whether it starts at an
    offset of 0, 1 or 2 bytes past a group boundary -- three renderings, and a
    search for only one of them finds nothing. The first and last four
    characters of each are dropped because they mix with the neighbouring
    bytes; what is left is stable wherever the secret sits.
    """
    out = []
    for offset in (0, 1, 2):
        encoded = base64.b64encode(b"x" * offset + secret.encode()).decode()
        middle = encoded[4:-4]
        if len(middle) >= 12:
            out.append(middle)
    return out


def _host_of(url):
    return re.sub(r"^https?://", "", (url or "")).strip("/").split("/")[0]


class Scrubber:
    """Rewrites indexer and provider identities to their published labels."""

    def __init__(self, config=CONFIG):
        # longest first: api.indexer.example must be replaced before indexer
        self.rules = []
        self.labels = {}
        self.known_labels = set()
        if os.path.exists(config):
            with open(config) as handle:
                for indexer in json.load(handle)["indexers"]:
                    label = indexer["label"]
                    self.known_labels.add(label.lower())
                    for token in self._tokens(indexer):
                        self.rules.append((token, label))
                        self.labels[token.lower()] = label
        for token in ALWAYS_NEWS:
            self.rules.append((token, NEWS_PLACEHOLDER))
        for variable in ("NNTP_HOST", "NNTP_USER", "NNTP_PASS"):
            value = os.environ.get(variable)
            if value:
                self.rules.append((value, NEWS_PLACEHOLDER if variable == "NNTP_HOST" else "[redacted]"))
        # api keys last and unconditionally, in plain form and base64-wrapped.
        # StremThru's addon URL is base64 of a JSON blob with the keys inside
        # it, so a scrubber looking only for the literal string hands a live
        # key to a published file the moment a URL lands in an error message.
        if os.path.exists(config):
            with open(config) as handle:
                for indexer in json.load(handle)["indexers"]:
                    if indexer.get("api_key"):
                        self.rules.append((indexer["api_key"], "[redacted]"))
                        for token in base64_variants(indexer["api_key"]):
                            self.rules.append((token, "[redacted]"))
        self.rules.sort(key=lambda rule: len(rule[0]), reverse=True)
        self.patterns = [(re.compile(re.escape(token), re.IGNORECASE), replacement)
                         for token, replacement in self.rules if token]

    @staticmethod
    def _tokens(indexer):
        """Every string a target might print for this indexer."""
        out = []
        if indexer.get("name"):
            out.append(indexer["name"])
        host = _host_of(indexer.get("url"))
        if host:
            out.append(host)
            labels = host.split(".")
            if len(labels) >= 2 and len(labels[-2]) > 3 and labels[-2] not in GENERIC_LABELS:
                out.append(labels[-2])
        return out

    def __call__(self, value):
        """Scrub a string, or walk a structure scrubbing every string in it."""
        if value is None:
            return None
        if isinstance(value, str):
            for pattern, replacement in self.patterns:
                value = pattern.sub(replacement, value)
            return value
        if isinstance(value, dict):
            return {key: self(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self(item) for item in value]
        return value

    def indexer_label(self, name):
        """A label for one indexer credit, or a stable pseudonym for a stranger.

        Passing an unknown name through is how a private tracker reaches a
        published table. An unknown one is hashed instead, which keeps rows
        comparable across targets without naming anybody.
        """
        if not name:
            return None
        key = name.strip().lower()
        # already a label. Every target in a round is configured with the
        # parity labels as its indexer names, so this is the common case and
        # hashing it would turn a correctly-labelled row into an unmapped one
        if key in self.known_labels:
            return key
        if key in self.labels:
            return self.labels[key]
        for token, label in self.rules:
            if token and token.lower() in key and label.startswith("indexer-"):
                return label
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        return f"unmapped-{digest}"
