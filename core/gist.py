"""Near-live state publishing to a GitHub Gist (dashboard feed).

Committed ledgers update once per run; during sessions the dashboard wants
fresher data. Bots PATCH a compact state JSON into one shared gist; the
dashboard polls it. No-op without GIST_TOKEN + GIST_ID; never raises — a
dashboard hiccup must not take down a trading run.
"""

import json
import os
import time
import urllib.request

MIN_INTERVAL_S = 45


class GistPublisher:
    def __init__(self, token: str | None = None, gist_id: str | None = None):
        self.token = token or os.getenv("GIST_TOKEN", "")
        self.gist_id = gist_id or os.getenv("GIST_ID", "")
        self._last_push: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.gist_id)

    def push(self, filename: str, state: dict, force: bool = False) -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        if not force and now - self._last_push.get(filename, -1e9) < MIN_INTERVAL_S:
            return False
        try:
            body = json.dumps({"files": {filename: {
                "content": json.dumps(state)}}}).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.github.com/gists/{self.gist_id}",
                data=body, method="PATCH",
                headers={"Authorization": f"Bearer {self.token}",
                         "Accept": "application/vnd.github+json",
                         "Content-Type": "application/json",
                         "User-Agent": "edgelab-bot"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= resp.status < 300
            if ok:
                self._last_push[filename] = now
            return ok
        except Exception as e:            # noqa: BLE001 — never break a live run
            print(f"[gist] push failed ({e})")
            self._last_push[filename] = now
            return False
