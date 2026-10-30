#!/usr/bin/env python3
"""
Turn data/latest.json (from track.py) into the four row files the scorecard page reads:

  out/scorecard.json       one row per creative x path x period (last 4 completed webinars, all)
  out/weeks.json           one row per webinar
  out/week_creatives.json  one row per webinar x creative x path
  out/sales.json           one row per purchase by a webinar-ad registrant, credited to that ad

A creative is every ad that ran the same uploaded video or image hash, so copies of one
creative across ad sets add up (the JOM4 dashboard's rule). Path is where the person
registered: the Meta lead form or the website funnel. Spend is split the same way by
the ad's campaign (a "Lead Ads" campaign is lead-form spend).

Webinar ads only (Phil 2026-10-30): organic, past-cohort and other-funnel people are left\nout of every row. Counts only: no names, emails, phones or contact ids are written.
"""

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
LOW_VOLUME = 15           # fewer registrants than this: rates are too noisy to rank on
BOOKING_DAYS = 7          # bookings keep arriving for about a week after the webinar


def ratio(a, b, nd=4):
    return round(a / b, nd) if b else None


def money(a, b):
    return round(a / b, 2) if b else None


def spend_path(campaign):
    return "Lead form" if "lead ad" in (campaign or "").lower() else "Website"


def main():
    d = json.loads((HERE / "data" / "latest.json").read_text())
    now = datetime.fromisoformat(d["generated"])
    ads = d["ads"]

    def asset(ad):
        return (ads.get(ad) or {}).get("asset") or f"ad:{ad}"

    names = defaultdict(Counter)
    fmt, retired = {}, {}
    for a, m in ads.items():
        names[m["asset"]][m["name"]] += 1
        fmt[m["asset"]] = m.get("format") or "?"
        if m.get("retired"):
            retired[m["asset"]] = m["retired"]

    weeks = sorted(d["weeks"], key=lambda w: w["webinar"])
    for w in weeks:
        closed = datetime.fromisoformat(w["closed"])
        w["complete"] = closed <= now and w["attended_tag_total"] > 0
        w["booking_open"] = w["complete"] and now < closed + timedelta(days=BOOKING_DAYS)
    done = [w["webinar"] for w in weeks if w["complete"]]
    periods = {"last4": set(done[-4:]), "all": set(done)}
    prev4 = set(done[-8:-4])

    regs = d["regs"]
    sales = d["sales"]
    web_sales = [s for s in sales if s["route"] == "Webinar" and s["ad"]]

    # spend per (webinar, asset, path)
    spend = defaultdict(float)
    ads_used = defaultdict(set)
    for w in weeks:
        for ad, v in w["spend_by_ad"].items():
            k = (w["webinar"], asset(ad), spend_path((ads.get(ad) or {}).get("campaign")))
            spend[k] += v
            ads_used[k[1]].add(ad)

    def tally(webinars):
        """{(asset, path): metrics} over a set of webinars, plus path 'All' rollups."""
        t = defaultdict(lambda: Counter())
        for (wk, a, p), v in spend.items():
            if wk in webinars:
                for path in (p, "All"):
                    t[(a, path)]["spend"] += v
        for r in regs:
            if r["webinar"] in webinars and r["ad"]:
                for path in (r["path"], "All"):
                    c = t[(asset(r["ad"]), path)]
                    c["registrants"] += 1
                    c["attended"] += r["attended"]
                    c["booked"] += r["booked"]
                    c["held"] += r.get("held", False)
        for s in web_sales:
            if s["webinar"] in webinars and s["ad"]:
                for path in (s["path"], "All"):
                    c = t[(asset(s["ad"]), path)]
                    c["purchases"] += 1
                    c["revenue"] += s["amount"]
        return t

    def row(a, path, c):
        sp = round(c["spend"], 2)
        return {
            "creative": names[a].most_common(1)[0][0] if names[a] else a,
            "format": fmt.get(a, "?"), "path": path, "ads": len(ads_used.get(a, ())),
            "spend": sp, "registrants": c["registrants"], "attended": c["attended"],
            "show_up": ratio(c["attended"], c["registrants"]), "booked": c["booked"],
            "book_rate": ratio(c["booked"], c["registrants"]),
            "held": c["held"], "held_rate": ratio(c["held"], c["booked"]),
            "cost_per_held": money(sp, c["held"]),
            "purchases": c["purchases"], "revenue": round(c["revenue"], 2),
            "cost_per_reg": money(sp, c["registrants"]),
            "cost_per_attendee": money(sp, c["attended"]),
            "cost_per_booked": money(sp, c["booked"]),
            "cost_per_purchase": money(sp, c["purchases"]),
            "low_volume": c["registrants"] < LOW_VOLUME,
            "retired": retired.get(a, ""),
        }

    scorecard = []
    for pname, wks in periods.items():
        for (a, path), c in tally(wks).items():
            if not c["registrants"] and c["spend"] < 1:
                continue
            r = row(a, path, c)
            r.update({"key": f"{pname}|{path}|{a}", "period": pname})
            scorecard.append(r)

    week_creatives = []
    for w in weeks:
        for (a, path), c in tally({w["webinar"]}).items():
            if path == "All" or (not c["registrants"] and c["spend"] < 1):
                continue
            r = row(a, path, c)
            r.update({"key": f"{w['webinar']}|{path}|{a}", "webinar": w["webinar"]})
            week_creatives.append(r)

    week_rows = []
    for w in weeks:
        mine = [r for r in regs if r["webinar"] == w["webinar"] and r["ad"]]
        ws = [s for s in web_sales if s["webinar"] == w["webinar"]]
        status = ("Registration open" if not w["complete"] else
                  "Bookings still coming in" if w["booking_open"] else "Complete")
        n = len(mine)
        att = sum(r["attended"] for r in mine) if w["complete"] else None
        booked = sum(r["booked"] for r in mine) if w["complete"] else None
        held = sum(r.get("held", False) for r in mine) if w["complete"] else None
        week_rows.append({
            "webinar": w["webinar"], "status": status, "spend": w["spend"],
            "registrants": n,
            "lead_form": sum(1 for r in mine if r["path"] == "Lead form"),
            "website": sum(1 for r in mine if r["path"] == "Website"),
            "attended": att, "show_up": ratio(att, n) if att is not None else None,
            "booked": booked, "book_rate": ratio(booked, n) if booked is not None else None,
            "held": held, "held_rate": ratio(held, booked) if booked else None,
            "cost_per_held": money(w["spend"], held) if held else None,
            "cost_per_reg": money(w["spend"], n),
            "cost_per_attendee": money(w["spend"], att) if att else None,
            "cost_per_booked": money(w["spend"], booked) if booked else None,
            "purchases": len(ws), "revenue": round(sum(s["amount"] for s in ws), 2),
            "cost_per_purchase": money(w["spend"], len(ws)),
            "in_last4": w["webinar"] in periods["last4"],
            "in_prev4": w["webinar"] in prev4,
        })

    sale_rows = []
    for i, s in enumerate(sorted(web_sales, key=lambda s: s["close"], reverse=True), 1):
        a = asset(s["ad"])
        sale_rows.append({
            "sale": f"S{i:03d}", "close": s["close"], "amount": s["amount"],
            "plan": s.get("plan") or "", "path": s["path"],
            "creative": names[a].most_common(1)[0][0] if names[a] else s["ad"],
            "webinar": s["webinar"],
            "attended": "Yes" if s["attended"] else "No",
            "booked": "Yes" if s["booked"] else "No",
            "held": "Yes" if s.get("held") else "No",
            "via": s.get("via", ""),
        })

    OUT.mkdir(exist_ok=True)
    for name, rows in (("scorecard", scorecard), ("weeks", week_rows),
                       ("week_creatives", week_creatives), ("sales", sale_rows)):
        (OUT / f"{name}.json").write_text(json.dumps(rows))
        print(f"  out/{name}.json: {len(rows)} rows")
    return {"generated": d["generated"], "last4": sorted(periods["last4"])}


if __name__ == "__main__":
    print(main())
