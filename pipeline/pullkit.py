"""
The few Meta Graph, GoHighLevel and cycle helpers track.py needs, standalone so the same
code runs here and in the GitHub Actions copy of this tracker.

Keys come from the environment first (META_TOKEN, GHL_API_KEY: Actions secrets in CI)
and fall back to the files in the external PBI 2 folder on Phil's machine. Both are
read-only. Errors never print a key: URLs are redacted before they reach a message.

Constants mirror the webinar dashboard's pull.py (PBI/2026-08-12 - Webinar Dashboard):
change a cycle boundary or the funnel id there, change it here too.
"""

import json
import os
import random
import re
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

PBI2 = Path("/Users/philglutting/Documents/Claude/Projects/PBI 2")
API = "https://graph.facebook.com/v21.0"
ACCOUNT = "act_37394393"
ACCOUNT_TZ = ZoneInfo("America/Los_Angeles")
CAMPAIGN_MATCH = "webinar"
GHL_API = "https://services.leadconnectorhq.com"
GHL_LOCATION = "GmBTEcbq9PN9YY99gncv"
GHL_WEBINAR_FUNNEL = "jlPVWxKKFvE67r7hNCb0"

WEEK_TZ = ZoneInfo("America/Chicago")
# {nominal Monday-noon boundary: when it actually fell}; same map as pull.py
WEEK_EARLY_OPENS = {
    datetime(2026, 9, 28, 12, tzinfo=WEEK_TZ): datetime(2026, 9, 28, 3, tzinfo=WEEK_TZ),
}

TOKEN = None


def _secret(env, filename):
    v = os.environ.get(env, "").strip()
    if not v and (PBI2 / filename).exists():
        v = (PBI2 / filename).read_text().strip()
    return v


def token():
    t = _secret("META_TOKEN", "fb_token.txt")
    if not t:
        raise RuntimeError("no Meta token: set META_TOKEN")
    return t


def ghl_key():
    k = _secret("GHL_API_KEY", "ghl_key.txt")
    if not k:
        raise RuntimeError("no GoHighLevel key: set GHL_API_KEY")
    return k


def boundary(noon):
    return WEEK_EARLY_OPENS.get(noon, noon)


def redact(text):
    return re.sub(r"access_token=[^&\s\"']+", "access_token=REDACTED", str(text))


def curl(url, tries=6):
    last = ""
    for attempt in range(tries):
        p = subprocess.run(["curl", "-gsS", "--max-time", "90", "-w", "\n%{http_code}", url],
                           capture_output=True, text=True)
        body, _, code = p.stdout.rpartition("\n")
        if code.strip() == "200":
            return body
        last = f"HTTP {code.strip()} {body[:200]}"
        try:
            err = json.loads(body).get("error", {})
        except ValueError:
            err = {}
        throttled = err.get("code") in (4, 17, 32, 341, 613)
        if code.strip() not in ("", "000", "429", "500", "502", "503", "504") and not throttled \
                and err.get("code") not in (1, 2):
            break
        wait = (60 * (attempt + 1)) if throttled else min(45, 3 * 2 ** attempt)
        sleep(wait * (0.75 + random.random() * 0.5))
    raise RuntimeError("Graph request failed: " + redact(last))


def get(path, params):
    params = dict(params, access_token=TOKEN or token())
    d = json.loads(curl(f"{API}/{path}?" + urllib.parse.urlencode(params)))
    if "error" in d:
        raise RuntimeError(redact(d["error"].get("message", d["error"])))
    return d


def get_all(path, params):
    rows, d = [], get(path, params)
    while True:
        rows.extend(d.get("data", []))
        nxt = (d.get("paging") or {}).get("next")
        if not nxt:
            return rows
        d = json.loads(curl(nxt))
        if "error" in d:
            raise RuntimeError(redact(d["error"].get("message", d["error"])))
