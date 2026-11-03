#!/usr/bin/env python3
"""
Webinar creative quality tracker: which ads bring people who SHOW UP, BOOK and BUY,
not just people who register cheaply.

Every number comes from GoHighLevel except spend:

  registrant   tag  webinar-<mon-d-yyyy>-registrant
  attended     tag  webinar-<mon-d-yyyy>-attended
  booked call  tag  webinar-<mon-d-yyyy>-booked-call | -webinar-booked-call | -webinar-ppa-booked
               (GHL renamed the booked tag after the Oct 12 webinar; all three are read)
  booked call  also: a "Webinar PPA Calendar" opportunity in the Closer Pipeline created in the
               week after the webinar (catches the few bookings that never got a tag)
  call held    the booker's Closer Pipeline stage is past the call (anything but no show/cancel,
               needs reschedule, scheduled-call)
  purchase     a "closed won" stage in the Closer Pipeline, dated by its last stage change, valued
               at the opportunity's contract value; plus any "bb purchased" contact the pipeline
               does not show as won (close-date and amount fields)
  the ad       the contact's own form submission in that cycle:
                 website opt-in  -> url_params.h_ad_id on the Weekly Webinar [Facebook] funnel
                 Meta lead form  -> GHL's synced fb-<location> submission, which carries ad_id
  spend        Meta hourly ad-level spend inside the cycle (noon CT Monday to noon CT Monday)

Purchases are credited to the most recent webinar the buyer registered for on or before
the close date, and to the ad on that registration. A buyer with no webinar registration
before the sale is credited to their last paid-ad opt-in anywhere in GHL (challenge,
lead magnet, ...), so the purchase table answers "where are sales coming from" for the
whole account, not only the webinar.

Nothing personal leaves this script: contacts are reduced to opaque ids before anything
is written, and the published files hold counts only.

    python3 track.py            # pull everything, write data/ and the out/ row files
    python3 track.py --cached   # rebuild out/ from the last pull, no API calls
"""

import json
import re
import subprocess
import sys
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pullkit as pull  # noqa: E402  (Graph/GHL helpers and cycle boundaries, standalone)

DATA = HERE / "data"
GHL_API = pull.GHL_API
LOC = pull.GHL_LOCATION
WEBINAR_FUNNEL = pull.GHL_WEBINAR_FUNNEL
FB_FORM = f"fb-{LOC}"
SUBS_SINCE = "2026-07-01"          # far enough back to see a buyer's earlier opt-ins
FIRST_WEBINAR = date(2026, 8, 17)  # first weekly webinar with registrant tags
WEBINAR_HOUR_CT = 12               # cycles close at noon CT on webinar Monday
F_AMOUNT = "p543tZk44oLgbiu8FYgS"
F_CLOSE = "M3nXNgDc6mkCuPUwV1HR"
F_PLAN = "uLotw7jRnZDVYdp3oz91"
BOOKED_SUFFIXES = ("booked-call", "webinar-booked-call", "webinar-ppa-booked")
PURCHASE_TAG = "bb purchased"
CLOSER_PIPELINE = "thjmVh2DeUQlBhNUOCp0"   # "02: Closer Pipeline"
PPA_SOURCE = "Webinar PPA Calendar"
NOT_HELD = {"no show/cancel", "needs reschedule", "scheduled-call"}
# PBI/CLAUDE.md: retired creatives, never rank them as winners (matched on image hash)
RETIRED_HASHES = {"5aa5b3c86b8de4813d14c4cf7ef428c1": "Image 06",
                  "d8214f6e4cbe8373bb0f3b9f3ed79610": "Image ad 14"}


# ---------------------------------------------------------------- GHL

def ghl(method, path, body=None, tries=5):
    key = pull.ghl_key()
    cmd = ["curl", "-sS", "--max-time", "60", "-X", method,
           "-H", f"Authorization: Bearer {key}", "-H", "Version: 2021-07-28",
           "-H", "Content-Type: application/json", "-w", "\n%{http_code}", GHL_API + path]
    if body is not None:
        cmd[1:1] = ["-d", json.dumps(body)]
    for attempt in range(tries):
        p = subprocess.run(cmd, capture_output=True, text=True)
        text, _, code = p.stdout.rpartition("\n")
        if code.strip() == "200":
            return json.loads(text)
        if code.strip() in ("429", "500", "502", "503", "504", "000", ""):
            import time as _t
            _t.sleep(3 * 2 ** attempt)
            continue
        break
    raise RuntimeError(f"GHL {method} {path.split('?')[0]} failed: HTTP {code.strip()}")


def tagged(tag):
    """Every contact carrying a tag, reduced to what this report needs (no PII)."""
    out, page = [], 1
    while True:
        d = ghl("POST", "/contacts/search", {
            "locationId": LOC, "page": page, "pageLimit": 100,
            "filters": [{"field": "tags", "operator": "contains", "value": tag}]})
        for c in d.get("contacts", []):
            cf = {f["id"]: f.get("value") for f in c.get("customFields", [])}
            out.append({"id": c["id"], "tags": [t.lower() for t in c.get("tags", [])],
                        "amount": cf.get(F_AMOUNT), "close": cf.get(F_CLOSE),
                        "plan": cf.get(F_PLAN)})
        if len(out) >= d.get("total", 0) or not d.get("contacts"):
            break
        page += 1
    # contains is a substring match: keep exact tag holders only
    return [c for c in out if tag.lower() in c["tags"]]


def submission_row(s):
    """One opt-in reduced to contact, time, path and ad. Names, emails, phones dropped."""
    o = s.get("others") or {}
    ev = o.get("eventData") or {}
    up = ev.get("url_params") or {}
    las = o.get("lastAttributionSource") or {}
    fe = o.get("funneEventData") or {}  # sic: GHL's spelling
    is_fb = s.get("formId") == FB_FORM
    ad = up.get("h_ad_id") or up.get("ad_id") or (las.get("adId") if is_fb else None)
    if ad and not str(ad).isdigit():  # an unfilled URL macro such as {{ad.id}} is not an ad
        ad = None
    campaign = ev.get("campaign") or las.get("campaign") or up.get("utm_campaign") or ""
    funnel = fe.get("funnel_id")
    if is_fb:
        path = "Lead form"
    elif funnel == WEBINAR_FUNNEL:
        path = "Website"
    else:
        path = "Other funnel"
    return {"c": s.get("contactId"), "t": s.get("createdAt"), "ad": ad and str(ad),
            "path": path, "funnel": funnel, "campaign": campaign,
            "webinar": path == "Website" or (is_fb and "webinar" in campaign.lower())}


def all_submissions():
    first = ghl("GET", "/forms/submissions?" + urllib.parse.urlencode({
        "locationId": LOC, "startAt": SUBS_SINCE,
        "endAt": (date.today() + timedelta(days=2)).isoformat(), "limit": 100, "page": 1}))
    total = first["meta"]["total"]
    pages = (total + 99) // 100

    def page(n):
        return ghl("GET", "/forms/submissions?" + urllib.parse.urlencode({
            "locationId": LOC, "startAt": SUBS_SINCE,
            "endAt": (date.today() + timedelta(days=2)).isoformat(),
            "limit": 100, "page": n}))["submissions"]

    subs = list(first["submissions"])
    with ThreadPoolExecutor(8) as ex:
        for chunk in ex.map(page, range(2, pages + 1)):
            subs.extend(chunk)
    seen, rows = set(), []
    for s in subs:
        if s.get("id") in seen or not s.get("contactId") or not s.get("createdAt"):
            continue
        seen.add(s["id"])
        rows.append(submission_row(s))
    print(f"  ghl: {len(rows)} submissions since {SUBS_SINCE} (meta total {total})")
    return rows


def closer_opps():
    """Every Closer Pipeline opportunity as {contact, stage, value, created, changed, source}."""
    pipes = ghl("GET", f"/opportunities/pipelines?locationId={LOC}")["pipelines"]
    stage = {st["id"]: st["name"] for p in pipes if p["id"] == CLOSER_PIPELINE for st in p["stages"]}
    out, page = [], 1
    while True:
        d = ghl("GET", "/opportunities/search?" + urllib.parse.urlencode({
            "location_id": LOC, "pipeline_id": CLOSER_PIPELINE, "limit": 100, "page": page}))
        rows = d.get("opportunities", [])
        for o in rows:
            out.append({"c": o.get("contactId"), "stage": stage.get(o.get("pipelineStageId"), ""),
                        "value": float(o.get("monetaryValue") or 0), "created": o.get("createdAt") or "",
                        "changed": o.get("lastStageChangeAt") or o.get("lastStatusChangeAt")
                        or o.get("updatedAt") or "", "source": o.get("source") or ""})
        if len(rows) < 100:
            break
        page += 1
    print(f"  ghl: {len(out)} Closer Pipeline opportunities")
    return out


# ---------------------------------------------------------------- cycles

def overrides():
    """Phil's hand-supplied attendee / booked / buyer lists as opaque contact ids
    (make_overrides.py). Unioned with the GHL tags, which lag the webinar by days."""
    f = HERE / "overrides.json"
    return json.loads(f.read_text()) if f.exists() else {}


def slug(d):
    return f"webinar-{d.strftime('%b').lower()}-{d.day}-{d.year}"


def cycle_window(d):
    """Registration window for the webinar on Monday d, as UTC instants."""
    close = pull.boundary(datetime.combine(d, time(WEBINAR_HOUR_CT), pull.WEEK_TZ))
    opened = pull.boundary(datetime.combine(d - timedelta(days=7), time(WEBINAR_HOUR_CT),
                                            pull.WEEK_TZ))
    return opened, close


def cycles():
    out, d = [], FIRST_WEBINAR
    last = date.today() + timedelta(days=7)
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- Meta

def spend_by_ad(opened, closed, now):
    """Webinar-campaign spend per ad inside [opened, closed), from hourly buckets."""
    cache = DATA / f"spend_{opened.date()}.json"
    if closed <= now - timedelta(days=2) and cache.exists():
        return json.loads(cache.read_text())
    o = opened.astimezone(pull.ACCOUNT_TZ)
    c = min(closed, now).astimezone(pull.ACCOUNT_TZ)
    rows = pull.get_all(f"{pull.ACCOUNT}/insights", {
        "level": "ad", "fields": "ad_id,spend",
        "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
        "filtering": json.dumps([{"field": "campaign.name", "operator": "CONTAIN",
                                  "value": pull.CAMPAIGN_MATCH}]),
        "time_range": json.dumps({"since": o.date().isoformat(), "until": c.date().isoformat()}),
        "time_increment": 1, "limit": 500})
    spend = defaultdict(float)
    for r in rows:
        try:
            hour = int(r.get("hourly_stats_aggregated_by_advertiser_time_zone", "")[:2])
        except ValueError:
            continue
        stamp = datetime.combine(date.fromisoformat(r["date_start"]), time(hour), pull.ACCOUNT_TZ)
        if o <= stamp < c:
            spend[r["ad_id"]] += float(r.get("spend") or 0)
    spend = {k: round(v, 2) for k, v in spend.items()}
    if closed <= now - timedelta(days=2):
        cache.write_text(json.dumps(spend))
    return spend


def ad_meta(ad_ids):
    """Name, campaign and the creative asset each ad runs, so copies roll up together."""
    cache_f = DATA / "ad_meta.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    todo = [a for a in ad_ids if a not in cache]
    for i in range(0, len(todo), 40):
        chunk = todo[i:i + 40]
        try:
            d = pull.get("", {"ids": ",".join(chunk),
                              "fields": "id,name,campaign{name},creative{image_hash,video_id,"
                                        "object_type,object_story_spec,thumbnail_url,image_url}"})
        except RuntimeError as e:  # one deleted ad fails the whole batch: go one by one
            d = {}
            for a in chunk:
                try:
                    d.update(pull.get("", {"ids": a, "fields": "id,name,campaign{name},creative{"
                                           "image_hash,video_id,object_type,object_story_spec,"
                                           "thumbnail_url,image_url}"}))
                except RuntimeError:
                    cache[a] = {"name": f"(ad {a} not readable)", "campaign": "", "asset": a,
                                "format": "?", "thumb": None, "retired": None}
        for a, ad in d.items():
            cr = ad.get("creative") or {}
            oss = cr.get("object_story_spec") or {}
            vid = (oss.get("video_data") or {}).get("video_id") or cr.get("video_id")
            h = cr.get("image_hash") or (oss.get("link_data") or {}).get("image_hash")
            cache[a] = {"name": ad.get("name", ""), "campaign": (ad.get("campaign") or {}).get("name", ""),
                        "asset": f"v:{vid}" if vid else (f"i:{h}" if h else f"ad:{a}"),
                        "format": "Video" if vid or cr.get("object_type") == "VIDEO" else "Image",
                        "thumb": cr.get("thumbnail_url") or cr.get("image_url"),
                        "retired": RETIRED_HASHES.get(h)}
    cache_f.write_text(json.dumps(cache, indent=1))
    return cache


# ---------------------------------------------------------------- build

def build():
    DATA.mkdir(exist_ok=True)
    pull.TOKEN = pull.token()
    now = datetime.now(timezone.utc)

    subs = all_submissions()
    by_contact = defaultdict(list)
    for s in subs:
        by_contact[s["c"]].append(s)
    for v in by_contact.values():
        v.sort(key=lambda s: s["t"])

    ov = overrides()
    refunded = set(ov.get("refunded", [])) | {c["id"] for c in tagged("bb-purchase-refund")}
    opps = closer_opps()
    opp_by_c = defaultdict(list)
    for o in opps:
        opp_by_c[o["c"]].append(o)

    def held(cid):
        st = [o["stage"] for o in opp_by_c.get(cid, [])]
        return bool(st) and any(x not in NOT_HELD for x in st)

    regs = []      # one row per (contact, webinar)
    weeks = []
    for d in cycles():
        reg = tagged(f"{slug(d)}-registrant")
        if not reg:
            continue
        att = {c["id"] for c in tagged(f"{slug(d)}-attended")}
        booked = set()
        for suf in BOOKED_SUFFIXES:
            booked |= {c["id"] for c in tagged(f"{slug(d)}-{suf}")}
        att |= set(ov.get("attended", {}).get(d.isoformat(), []))
        booked |= set(ov.get("booked", {}).get(d.isoformat(), []))
        wk_lo, wk_hi = d.isoformat(), (d + timedelta(days=7)).isoformat()
        booked |= {o["c"] for o in opps if o["source"] == PPA_SOURCE and wk_lo <= o["created"][:10] < wk_hi}
        opened, closed = cycle_window(d)
        lo, hi = opened - timedelta(days=1), closed + timedelta(hours=2)
        reg_ids = {c["id"] for c in reg}
        for c in reg:
            mine = [s for s in by_contact.get(c["id"], [])
                    if s["webinar"] and lo <= parse_t(s["t"]) < hi]
            with_ad = [s for s in mine if s["ad"]]
            if with_ad:
                s = with_ad[0]
                src, ad, path = "ad", s["ad"], s["path"]
            elif f"{slug(d)}-organic-social" in c["tags"]:
                src, ad, path = "Organic social", None, "Organic"
            elif f"{slug(d)}-past-cohort" in c["tags"]:
                src, ad, path = "Past cohort", None, "Past cohort"
            elif mine:
                src, ad, path = f"{mine[0]['path']}, no ad id", None, mine[0]["path"]
            else:
                src, ad, path = "No opt-in on record", None, "Unknown"
            regs.append({"c": c["id"], "webinar": d.isoformat(), "src": src, "ad": ad,
                         "path": path, "attended": c["id"] in att, "booked": c["id"] in booked,
                         "held": c["id"] in booked and held(c["id"])})
        spend = spend_by_ad(opened, closed, now)
        weeks.append({"webinar": d.isoformat(), "opened": opened.isoformat(),
                      "closed": closed.isoformat(), "open": closed > now,
                      "registrants": len(reg_ids), "attended": len(att & reg_ids),
                      "attended_tag_total": len(att), "booked": len(booked),
                      "booked_unmatched": len(booked - reg_ids),
                      "spend": round(sum(spend.values()), 2), "spend_by_ad": spend})
        print(f"  {d}: {len(reg_ids)} registrants, {len(att & reg_ids)} attended, "
              f"{len(booked)} booked ({len(booked - reg_ids)} not on the registrant list), "
              f"${sum(spend.values()):,.0f} spend")

    # purchases: pipeline wins first (contract value, stage-change date), then tag-only buyers
    buyers = []
    won_c = set()
    for o in opps:
        if o["stage"].startswith("closed won") and o["c"] not in won_c:
            won_c.add(o["c"])
            plan = {"closed won - PIF": "PIF", "closed won - payment plan": "Payment plan",
                    "closed won - bb retake": "BB retake"}.get(o["stage"], o["stage"])
            buyers.append({"id": o["c"], "close": o["changed"][:10], "amount": o["value"],
                           "plan": plan, "tags": None, "via": "Closer Pipeline"})
    buyers = [b for b in buyers if b["id"] not in refunded]
    tag_buyers = tagged(PURCHASE_TAG)
    tag_by_c = {b["id"]: b for b in tag_buyers}
    for b in tag_buyers:
        if b["id"] not in won_c and b["id"] not in refunded:
            buyers.append(dict(b, via="bb purchased tag"))
    for b in buyers:
        if b["tags"] is None:
            b["tags"] = (tag_by_c.get(b["id"]) or {}).get("tags") or []
    reg_by_c = defaultdict(list)
    for r in regs:
        reg_by_c[r["c"]].append(r)
    sales = []
    for b in buyers:
        close = b.get("close")
        try:
            cd = date.fromisoformat(close[:10]) if close else None
        except ValueError:
            cd = None
        if not cd or cd < FIRST_WEBINAR:
            continue
        try:
            amount = float(b.get("amount") or 0)
        except ValueError:
            amount = 0.0
        prior = [r for r in reg_by_c.get(b["id"], []) if date.fromisoformat(r["webinar"]) <= cd]
        booked_tags = sorted(t for t in b["tags"] if re.match(r"webinar-[a-z]{3}-\d+-\d{4}-(" +
                             "|".join(BOOKED_SUFFIXES) + r")$", t))
        if prior:
            r = max(prior, key=lambda r: r["webinar"])
            sale = {"route": "Webinar", "webinar": r["webinar"], "ad": r["ad"], "src": r["src"],
                    "path": r["path"], "attended": r["attended"], "booked": r["booked"] or b["id"] in opp_by_c}
        else:
            paid = [s for s in by_contact.get(b["id"], [])
                    if s["ad"] and parse_t(s["t"]).date() <= cd]
            if booked_tags:
                route = "Webinar call, registered under another email"
            elif paid:
                route = "Other funnel (no webinar)"
            else:
                route = "No ad opt-in on record"
            s = paid[-1] if paid else None
            sale = {"route": route, "webinar": None, "ad": s["ad"] if s else None,
                    "src": "ad" if s else route, "path": s["path"] if s else "Unknown",
                    "campaign": s["campaign"] if s else "", "attended": None, "booked": None}
        sale.update({"close": cd.isoformat(), "amount": amount, "plan": b.get("plan"), "via": b["via"],
                     "held": held(b["id"])})
        sales.append(sale)

    ads = sorted({r["ad"] for r in regs if r["ad"]} | {s["ad"] for s in sales if s["ad"]}
                 | {a for w in weeks for a in w["spend_by_ad"]})
    meta = ad_meta(ads)

    out = {"generated": now.isoformat(timespec="minutes"), "weeks": weeks,
           "regs": [{k: v for k, v in r.items() if k != "c"} for r in regs],
           "sales": sales, "ads": {a: meta[a] for a in ads if a in meta}}
    (DATA / "latest.json").write_text(json.dumps(out, indent=1))
    print(f"  wrote data/latest.json: {len(regs)} registrations, {len(sales)} purchases, "
          f"{len(ads)} ads")
    return out


if __name__ == "__main__":
    if "--cached" not in sys.argv:
        build()
    import export
    print(export.main())
