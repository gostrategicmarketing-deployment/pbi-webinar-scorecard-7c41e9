#!/usr/bin/env python3
"""
Build the shareable, PBI-branded webinar ad scorecard as a static site.

    python3 build.py --site <dir>     # reads out/*.json + data/latest.json, writes <dir>/

Writes index.html (data inlined, no external data calls), version.json (the page checks it
to get past GitHub Pages' 10-minute cache), robots.txt + a noindex meta, and thumbs/ (the
creative previews, downloaded from Meta at build time because Meta's CDN links expire).

Client-facing: no em dashes anywhere in the copy, and only facts the data holds.
"""

import argparse
import base64
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pullkit  # noqa: E402

CT = ZoneInfo("America/Chicago")
LOGO = HERE / "brand" / "pbi-logo-reversed.png"
THUMB_TOP = 60          # thumbnails for the creatives most likely to be looked at
MIN_REG = 15


def load(name):
    return json.loads((HERE / "out" / f"{name}.json").read_text())


def thumb_targets(score, ads):
    """Asset keys worth a picture: the last-4 and all-time leaders plus every creative with a sale."""
    pick = []
    for period in ("last4", "all"):
        rows = [r for r in score if r["period"] == period and r["path"] == "All"]
        rows.sort(key=lambda r: (-r["booked"], -r["purchases"], -r["spend"]))
        pick += [r["key"].split("|", 2)[2] for r in rows[:THUMB_TOP // 2]]
    pick += [r["key"].split("|", 2)[2] for r in score if r["purchases"]]
    seen, out = set(), []
    for a in pick:
        if a not in seen:
            seen.add(a)
            out.append(a)
    by_asset = {}
    for ad, m in ads.items():
        by_asset.setdefault(m["asset"], ad)
    return [(a, by_asset[a]) for a in out if a in by_asset]


def fetch_thumbs(score, ads, site):
    tdir = site / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    have = {}
    try:
        pullkit.TOKEN = pullkit.token()
    except RuntimeError as e:
        print(f"  thumbs: skipped ({e})")
        pullkit.TOKEN = None
    for asset, ad in thumb_targets(score, ads):
        if not str(ad).isdigit():
            continue
        name = re.sub(r"[^A-Za-z0-9]+", "_", asset) + ".jpg"
        f = tdir / name
        if not f.exists() and pullkit.TOKEN:
            try:
                cid = pullkit.get(ad, {"fields": "creative{id}"})["creative"]["id"]
                c = pullkit.get(cid, {"fields": "thumbnail_url,image_url",
                                      "thumbnail_width": 600, "thumbnail_height": 600})
                url = c.get("image_url") or c.get("thumbnail_url")
                raw = f.with_suffix(".src")
                subprocess.run(["curl", "-fsSL", "--max-time", "60", "-A", "Mozilla/5.0", url,
                                "-o", str(raw)], check=True, capture_output=True)
                from PIL import Image
                im = Image.open(raw).convert("RGB")
                im.thumbnail((480, 600))
                im.save(f, "JPEG", quality=82, optimize=True, progressive=True)
                raw.unlink()
            except Exception as e:  # a missing preview never fails the build
                print(f"  thumbs: {asset} unavailable ({str(e)[:80]})")
        if f.exists():
            have[asset] = "thumbs/" + name
    print(f"  thumbs: {len(have)} creatives have a preview")
    return have


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>Webinar Ad Scorecard | Photography Business Institute</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,500;6..96,700&family=Inter:wght@400;500;600;700&display=swap">
<style>
/* Layout: an onyx brand band, then a single 1180px column of cards on a warm grey ground. */
:root {
  --onyx: #212121; --orange: #FF522B; --burgundy: #720C3A; --slate: #38C5BA; --orchid: #B065C0;
  --bg: #F4F3F1; --panel: #FFFFFF; --ink: #212121; --ink-2: #5B5754; --line: #E3E0DC;
  --soft: #EAECEF; --accent: #E8431D; --good: #1E8A6E; --bad: #B4232C; --warn: #A4610B;
  --bar: #212121; --bar-2: #C9C5C0; --chip: #F3EFEC;
  --display: 'Bodoni Moda', 'Bodoni 72', Didot, Georgia, serif;
  --sans: 'Inter', 'Helvetica Neue', Arial, sans-serif;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141312; --panel: #1E1C1B; --ink: #F1EEEB; --ink-2: #A9A39E; --line: #34302D;
    --soft: #2A2725; --accent: #FF6A47; --good: #4CC3A0; --bad: #F06A6F; --warn: #E3A548;
    --bar: #F1EEEB; --bar-2: #4A4542; --chip: #2A2725; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #141312; --panel: #1E1C1B; --ink: #F1EEEB; --ink-2: #A9A39E; --line: #34302D;
  --soft: #2A2725; --accent: #FF6A47; --good: #4CC3A0; --bad: #F06A6F; --warn: #E3A548;
  --bar: #F1EEEB; --bar-2: #4A4542; --chip: #2A2725; color-scheme: dark;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 var(--sans); -webkit-font-smoothing: antialiased; }
.num { font-variant-numeric: tabular-nums; }
.wrap { max-width: 1180px; margin: 0 auto; padding-left: max(20px, env(safe-area-inset-left)); padding-right: max(20px, env(safe-area-inset-right)); }
.band { background: var(--onyx); color: #fff; position: relative; overflow: hidden; }
.band::after { content: ""; position: absolute; right: -120px; top: -160px; width: 420px; height: 420px; border-radius: 50%;
  border: 56px solid var(--orange); opacity: .14; pointer-events: none; }
.band .wrap { padding-block: 28px 40px; position: relative; z-index: 1; }
.band-top { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.band-top img { height: 46px; width: auto; display: block; }
.stamp { font-size: 12px; color: #C9C5C0; text-align: right; }
.stamp b { color: #fff; font-weight: 600; }
.eyebrow { margin: 34px 0 10px; font-size: 11px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; color: var(--orange); }
h1 { font-family: var(--display); font-weight: 500; font-size: clamp(38px, 6.4vw, 68px); line-height: 1; margin: 0; letter-spacing: -.01em; text-wrap: balance; }
.lede { margin: 14px 0 0; max-width: 60ch; color: #D8D4D0; font-size: 16px; }
main { display: grid; gap: 28px; padding-block: 28px 56px; }
.thesis { font-family: var(--display); font-size: clamp(22px, 3.2vw, 32px); line-height: 1.25; margin: 4px 0 0; text-wrap: pretty; max-width: 34ch; }
.thesis b { font-weight: 700; color: var(--accent); }
.thesis-row { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 28px; align-items: end; }
.thesis-row p.small { margin: 0; color: var(--ink-2); font-size: 13px; }
section { display: grid; gap: 14px; }
h2 { font-family: var(--display); font-weight: 700; font-size: 26px; line-height: 1.15; margin: 0; text-wrap: balance; }
.sub { margin: -6px 0 0; color: var(--ink-2); font-size: 14px; max-width: 75ch; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 20px; min-width: 0; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.kpi { padding: 18px 20px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); margin: 0 -1px -1px 0; display: grid; gap: 4px; align-content: start; }
.kpi .lab { font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-2); }
.kpi .val { font-family: var(--display); font-size: 34px; line-height: 1.05; font-variant-numeric: lining-nums tabular-nums; }
.kpi .chg { font-size: 12px; color: var(--ink-2); }
.kpi .chg.up { color: var(--good); } .kpi .chg.down { color: var(--bad); }
.kpi.hero { background: var(--onyx); color: #fff; }
.kpi.hero .lab, .kpi.hero .chg { color: #C9C5C0; }
.kpi.hero .chg.up { color: #6FE0BF; } .kpi.hero .chg.down { color: #FF9A8F; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 440px), 1fr)); gap: 20px; }
.card h3 { margin: 0 0 4px; font-size: 15px; font-weight: 600; }
.card .note { margin: 0 0 14px; font-size: 12.5px; color: var(--ink-2); }
.funnel { display: grid; gap: 12px; }
.fstep { display: grid; grid-template-columns: 128px 1fr; gap: 12px; align-items: center; }
.fstep .fl { font-size: 13px; color: var(--ink-2); }
.fstep .fl b { display: block; color: var(--ink); font-size: 20px; font-family: var(--display); font-weight: 700; }
.fbar { height: 22px; border-radius: 4px; background: var(--soft); position: relative; overflow: hidden; }
.fbar i { position: absolute; inset: 0 auto 0 0; background: var(--bar); border-radius: 4px; }
.fstep:last-child .fbar i { background: var(--orange); }
.fbar + small { display: block; margin-top: 4px; font-size: 12px; color: var(--ink-2); }
.chart { position: relative; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart text { font-family: var(--sans); font-size: 11px; fill: var(--ink-2); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: var(--ink-2); margin-top: 8px; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -2px; margin-right: 6px; }
.tip { position: absolute; pointer-events: none; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.14); padding: 8px 10px; font-size: 12px; line-height: 1.5; white-space: nowrap; z-index: 5; }
.tip b { display: block; font-size: 12px; margin-bottom: 2px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr)); gap: 18px; }
.cr { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; display: grid; grid-template-rows: auto 1fr; min-width: 0; }
.cr .ph { aspect-ratio: 4 / 5; background: var(--soft); position: relative; overflow: hidden; }
.cr .ph img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cr .ph .noimg { position: absolute; inset: 0; display: grid; place-items: center; color: var(--ink-2); font-size: 12px; }
.cr .rank { position: absolute; left: 12px; top: 12px; background: var(--onyx); color: #fff; font-family: var(--display); font-weight: 700; font-size: 18px; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; box-shadow: 0 0 0 3px var(--orange); }
.cr .fmt { position: absolute; right: 10px; top: 12px; background: rgba(33,33,33,.82); color: #fff; font-size: 10.5px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; padding: 4px 8px; border-radius: 999px; }
.cr .body { padding: 14px 16px 16px; display: grid; gap: 12px; align-content: start; }
.cr .nm { font-weight: 600; font-size: 13.5px; line-height: 1.35; overflow-wrap: anywhere; }
.cr dl { margin: 0; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px 8px; }
.cr dt { font-size: 10.5px; color: var(--ink-2); letter-spacing: .04em; text-transform: uppercase; }
.cr dd { margin: 0; font-family: var(--display); font-weight: 700; font-size: 19px; font-variant-numeric: lining-nums tabular-nums; }
.cr dd.hot { color: var(--accent); }
.meter { height: 6px; border-radius: 3px; background: var(--soft); overflow: hidden; }
.meter i { display: block; height: 100%; background: var(--orange); }
.mlab { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-2); margin-bottom: 4px; }
.leak { display: grid; gap: 0; }
.leak .row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto; gap: 16px; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--line); font-size: 13.5px; }
.leak .row:last-child { border-bottom: 0; }
.leak .row .n { overflow-wrap: anywhere; }
.leak .row span.v { text-align: right; min-width: 74px; }
.leak .row span.v small { display: block; color: var(--ink-2); font-size: 11px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; margin-bottom: 14px; font-size: 13px; }
.seg { display: inline-flex; background: var(--chip); border-radius: 999px; padding: 3px; gap: 2px; }
.seg button { all: unset; cursor: pointer; padding: 6px 12px; border-radius: 999px; font-size: 12.5px; color: var(--ink-2); font-weight: 500; }
.seg button[aria-pressed="true"] { background: var(--onyx); color: #fff; }
:root[data-theme="dark"] .seg button[aria-pressed="true"] { background: var(--orange); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .seg button[aria-pressed="true"] { background: var(--orange); } }
.seg button:focus-visible, th button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--orange); outline-offset: 2px; }
.controls input[type=search], .controls select { font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--ink); }
.controls input[type=search] { min-width: 0; flex: 1 1 200px; max-width: 320px; }
.controls label.chk { display: inline-flex; gap: 6px; align-items: center; color: var(--ink-2); }
.tbl { overflow: auto; max-height: 620px; border: 1px solid var(--line); border-radius: 8px; }
.tbl.short { max-height: none; }
table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
th { position: sticky; top: 0; z-index: 1; background: var(--panel); text-align: right; font-weight: 600; font-size: 11.5px; color: var(--ink-2); padding: 10px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; }
td { text-align: right; padding: 9px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
th.l, td.l { text-align: left; }
td.nm { white-space: normal; min-width: 220px; max-width: 380px; overflow-wrap: anywhere; }
th button { all: unset; cursor: pointer; }
th[aria-sort="descending"] button::after { content: " \2193"; color: var(--accent); }
th[aria-sort="ascending"] button::after { content: " \2191"; color: var(--accent); }
tr.low td { color: var(--ink-2); }
tbody tr:hover td { background: var(--soft); }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; background: var(--chip); color: var(--ink-2); }
.pill.ret { background: rgba(180,35,44,.12); color: var(--bad); }
.pill.open { background: rgba(164,97,11,.14); color: var(--warn); }
.pill.done { background: rgba(30,138,110,.12); color: var(--good); }
.method { columns: 2 340px; column-gap: 32px; font-size: 13px; color: var(--ink-2); }
.method p { margin: 0 0 10px; break-inside: avoid; }
.method b { color: var(--ink); }
footer { border-top: 1px solid var(--line); padding-block: 22px 40px; font-size: 12px; color: var(--ink-2); }
@media (max-width: 760px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .thesis-row { grid-template-columns: 1fr; }
  .fstep { grid-template-columns: 96px 1fr; }
  .leak .row { grid-template-columns: minmax(0, 1fr) auto auto; }
  .leak .row span.v:nth-child(4) { display: none; }
}
@media (prefers-reduced-motion: no-preference) {
  .fbar i, .meter i { transition: width .6s cubic-bezier(.2,.7,.2,1); }
}
</style>
</head>
<body>
<header class="band">
  <div class="wrap">
    <div class="band-top">
      <img src="__LOGO__" alt="Photography Business Institute" width="84" height="46">
      <div class="stamp">Updated <b id="stamp"></b><br>Refreshes every morning from GoHighLevel and Meta</div>
    </div>
    <p class="eyebrow">Joy of Marketing &middot; Weekly webinar</p>
    <h1>Webinar Ad Scorecard</h1>
    <p class="lede">Which webinar ads bring photographers who show up live, book a Profit Path call and buy. Ranked on what happens after the registration, not on what the registration cost.</p>
  </div>
</header>

<main class="wrap">
  <div class="thesis-row">
    <p class="thesis" id="thesis"></p>
    <p class="small" id="window"></p>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid2">
    <div class="card">
      <h3>Where registrants drop off</h3>
      <p class="note" id="funnel-note"></p>
      <div class="funnel" id="funnel"></div>
    </div>
    <div class="card">
      <h3>Booked calls and show-up rate, webinar by webinar</h3>
      <p class="note">Bars are booked calls from webinar-ad registrants. The line is the share of those registrants who showed up live. A lighter bar is still collecting bookings.</p>
      <div class="chart" id="trend"></div>
      <div class="legend"><span><i style="background:var(--bar)"></i>Booked calls</span><span><i style="background:var(--orange);height:3px;vertical-align:3px"></i>Show-up rate</span></div>
    </div>
  </div>

  <section id="top">
    <h2>Creatives that book calls</h2>
    <p class="sub" id="top-sub"></p>
    <div class="cards" id="cards"></div>
  </section>

  <section id="leaks">
    <h2>Spend that is not turning into calls</h2>
    <p class="sub">Creatives that spent $300 or more over the last 4 webinars and booked no calls. Cheap registrations here are not paying off further down.</p>
    <div class="card"><div class="leak" id="leak"></div></div>
  </section>

  <section id="all">
    <h2>Every creative</h2>
    <p class="sub">A creative is every ad that ran the same video or image, added up across ad sets and campaigns. Rows under 15 registrants are grey: one or two people swing their rates.</p>
    <div class="card">
      <div class="controls">
        <div class="seg" id="seg-period" role="group" aria-label="Period">
          <button type="button" data-v="last4" aria-pressed="true">Last 4 webinars</button>
          <button type="button" data-v="all" aria-pressed="false">Since Aug 17</button>
        </div>
        <div class="seg" id="seg-path" role="group" aria-label="Registered on">
          <button type="button" data-v="All" aria-pressed="true">Both paths</button>
          <button type="button" data-v="Lead form" aria-pressed="false">Lead form</button>
          <button type="button" data-v="Website" aria-pressed="false">Website</button>
        </div>
        <input type="search" id="q" placeholder="Search creatives" aria-label="Search creatives">
        <label class="chk"><input type="checkbox" id="small"> Include under 15 registrants</label>
      </div>
      <div class="tbl">
        <table id="tbl">
          <thead><tr>
            <th class="l"><button data-k="creative">Creative</button></th>
            <th class="l"><button data-k="path">Path</button></th>
            <th><button data-k="spend">Spend</button></th>
            <th><button data-k="registrants">Registrants</button></th>
            <th><button data-k="show_up">Show-up</button></th>
            <th aria-sort="descending"><button data-k="booked">Booked calls</button></th>
            <th><button data-k="book_rate">Booked per registrant</button></th>
            <th><button data-k="cost_per_booked">Cost per booked call</button></th>
            <th><button data-k="purchases">Purchases</button></th>
            <th><button data-k="cost_per_purchase">Cost per purchase</button></th>
            <th><button data-k="cost_per_attendee">Cost per show-up</button></th>
            <th><button data-k="cost_per_reg">Cost per registrant</button></th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </section>

  <section id="weeks">
    <h2>Webinar by webinar</h2>
    <div class="card">
      <div class="tbl short">
        <table id="wtbl">
          <thead><tr>
            <th class="l">Webinar</th><th class="l">Status</th><th>Spend</th><th>Registrants</th><th>Lead form</th><th>Website</th>
            <th>Show-up</th><th>Booked calls</th><th>Cost per booked call</th><th>Purchases</th><th>Cost per purchase</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="controls"><label for="wk">Creatives for the webinar on</label> <select id="wk"></select></div>
      <div class="tbl">
        <table id="ctbl">
          <thead><tr>
            <th class="l">Creative</th><th class="l">Path</th><th>Spend</th><th>Registrants</th><th>Showed up</th><th>Show-up</th><th>Booked calls</th><th>Purchases</th><th>Cost per purchase</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </section>

  <section id="sales">
    <h2>Purchases from webinar-ad registrants</h2>
    <p class="sub">Each sale is credited to the ad on the buyer's most recent webinar registration on or before the day it closed. Amount is the figure recorded in GoHighLevel: paid in full shows the full price, a payment plan shows the plan amount.</p>
    <div class="card">
      <div class="tbl short">
        <table id="stbl">
          <thead><tr>
            <th class="l">Closed</th><th>Amount</th><th class="l">Plan</th><th class="l">Creative</th><th class="l">Path</th><th class="l">Webinar</th><th class="l">Showed up</th><th class="l">Booked a call</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </section>

  <section id="method">
    <h2>How the numbers are built</h2>
    <div class="method">
      <p><b>Webinar ads only.</b> Every count is a person who registered for the weekly webinar through a Meta webinar ad. People who came from organic posts, the past-cohort list or another funnel are left out.</p>
      <p><b>The ad.</b> Website registrations carry the ad in the page link. Meta lead-form registrations carry it in GoHighLevel's copy of the lead form. Each person is credited to the ad on their own registration that week.</p>
      <p><b>Show-up, booked call, purchase.</b> GoHighLevel's tags for that webinar: attended, booked call (both tag names used since October), and the Boutique Breakthrough purchase tag with its close date.</p>
      <p><b>Spend.</b> Meta's spend per ad, hour by hour, from noon Central on the Monday before each webinar to noon Central on webinar day. Lead-form spend is spend in the lead-ad campaigns.</p>
      <p><b>What it cannot see.</b> A booking or purchase made under a different email than the registration cannot be tied to an ad, so these counts are a floor. The newest finished webinar keeps collecting bookings for about a week.</p>
      <p><b>Retired creatives</b> stay in the table for the record with a Retired label. They are switched off on purpose and are never ranked as winners.</p>
    </div>
  </section>
</main>
<footer><div class="wrap">Prepared for Photography Business Institute by Go Strategic Marketing. Data as of <span id="stamp2"></span>.</div></footer>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  const D = JSON.parse(document.getElementById('data').textContent);
  const MIN = __MIN__;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = (v) => v == null ? 'n/a' : '$' + Math.round(v).toLocaleString('en-US');
  const pct = (v) => v == null ? 'n/a' : Math.round(v * 100) + '%';
  const n = (v) => v == null ? 'n/a' : Number(v).toLocaleString('en-US');
  const day = (s) => s ? new Date(s + 'T12:00:00Z').toLocaleDateString('en-US', {month: 'short', day: 'numeric', timeZone: 'UTC'}) : 'n/a';
  const assetOf = (key) => key.split('|').slice(2).join('|');

  // Freshness: GitHub Pages caches for 10 minutes, so ask for the version file directly.
  const check = () => fetch('version.json?cb=' + Date.now(), {cache: 'no-store'}).then((r) => r.json()).then((v) => {
    if (v.generated && v.generated > D.generated && !sessionStorage.getItem('pbi-reload-' + v.generated)) {
      try { sessionStorage.setItem('pbi-reload-' + v.generated, '1'); } catch (e) {}
      location.replace(location.pathname + '?v=' + encodeURIComponent(v.generated));
    }
  }).catch(() => {});
  check();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });

  const stamp = new Date(D.generated).toLocaleString('en-US', {timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'});
  $('#stamp').textContent = stamp;
  $('#stamp2').textContent = stamp;

  const weeks = D.weeks.slice().sort((a, b) => a.webinar.localeCompare(b.webinar));
  const sumOf = (rows) => {
    const s = (f) => rows.reduce((t, r) => t + (r[f] || 0), 0);
    const reg = s('registrants'), att = s('attended'), bk = s('booked'), sp = s('spend'), pu = s('purchases');
    return {spend: sp, registrants: reg, attended: att, show_up: reg ? att / reg : null, booked: bk,
            cost_per_booked: bk ? sp / bk : null, purchases: pu, cost_per_purchase: pu ? sp / pu : null,
            cost_per_reg: reg ? sp / reg : null};
  };
  const last4 = weeks.filter((w) => w.in_last4), prev4 = weeks.filter((w) => w.in_prev4);
  const L = sumOf(last4), P = sumOf(prev4);
  $('#window').textContent = 'Last 4 finished webinars: ' + last4.map((w) => day(w.webinar)).join(', ') + '. Compared with the 4 before: ' + prev4.map((w) => day(w.webinar)).join(', ') + '.';

  const kpis = [
    ['Cost per booked call', 'cost_per_booked', money, true, true],
    ['Booked calls', 'booked', n, false],
    ['Show-up rate', 'show_up', pct, false],
    ['Purchases', 'purchases', n, false],
    ['Cost per purchase', 'cost_per_purchase', money, true],
    ['Registrants from ads', 'registrants', n, null],
    ['Cost per registrant', 'cost_per_reg', money, null],
    ['Ad spend', 'spend', money, null],
  ];
  $('#kpis').innerHTML = kpis.map(([lab, f, fmt, lowerBetter, hero]) => {
    const a = L[f], b = P[f];
    let chg = '<span class="chg">&nbsp;</span>';
    if (a != null && b != null && b !== 0) {
      const d = (a - b) / Math.abs(b);
      const good = lowerBetter == null ? null : (lowerBetter ? d < 0 : d > 0);
      const cls = good == null || Math.abs(d) < 0.005 ? '' : (good ? 'up' : 'down');
      chg = `<span class="chg ${cls}">${d > 0 ? '+' : ''}${Math.round(d * 100)}% vs the 4 before</span>`;
    }
    return `<div class="kpi${hero ? ' hero' : ''}"><span class="lab">${lab}</span><span class="val">${fmt(a)}</span>${chg}</div>`;
  }).join('');

  // Funnel, last 4 webinars
  const steps = [['Registered', L.registrants], ['Showed up live', L.attended], ['Booked a call', L.booked], ['Purchased', L.purchases]];
  $('#funnel-note').textContent = 'Last 4 finished webinars, webinar-ad registrants only. ' + pct(L.registrants ? L.booked / L.registrants : null) + ' of registrants booked a call.';
  $('#funnel').innerHTML = steps.map(([lab, v], i) => {
    const w = L.registrants ? Math.max(1.5, v / L.registrants * 100) : 0;
    const conv = i ? pct(steps[i - 1][1] ? v / steps[i - 1][1] : null) + ' of the step before' : '100%';
    return `<div class="fstep"><div class="fl"><b class="num">${n(v)}</b>${lab}</div><div><div class="fbar"><i style="width:${w}%"></i></div><small>${conv}</small></div></div>`;
  }).join('');

  // Trend chart
  const trend = $('#trend');
  const drawTrend = () => {
    const rows = weeks.filter((w) => w.booked != null);
    const W = Math.max(320, trend.clientWidth), H = 250, m = {t: 26, r: 40, b: 30, l: 32};
    const iw = W - m.l - m.r, ih = H - m.t - m.b, bw = iw / rows.length;
    const top = Math.max(...rows.map((r) => r.booked), 1), step = top > 40 ? 20 : top > 20 ? 10 : 5;
    const maxB = Math.max(Math.ceil(top / step) * step, top * 1.15);
    const maxS = Math.max(...rows.map((r) => r.show_up || 0), 0.3) * 1.15;
    const yb = (v) => m.t + ih - v / maxB * ih, ys = (v) => m.t + ih - v / maxS * ih;
    const ticks = []; for (let t = 0; t <= maxB; t += step) ticks.push(t);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Booked calls and show-up rate by webinar">`;
    ticks.forEach((t) => { s += `<line x1="${m.l}" x2="${W - m.r}" y1="${yb(t)}" y2="${yb(t)}" stroke="var(--line)"/><text x="${m.l - 8}" y="${yb(t) + 4}" text-anchor="end">${t}</text>`; });
    [0, 0.2, 0.3].filter((t) => t < maxS).forEach((t, i) => { if (i) s += `<text x="${W - m.r + 8}" y="${ys(t) + 4}">${pct(t)}</text>`; });
    const every = Math.ceil(rows.length / Math.max(1, Math.floor(iw / 54)));
    rows.forEach((r, i) => {
      const x = m.l + i * bw, w = Math.min(34, bw * 0.62), cx = x + bw / 2;
      const fill = r.status === 'Complete' ? 'var(--bar)' : 'var(--bar-2)';
      s += `<rect class="hit" data-i="${i}" x="${x}" y="${m.t}" width="${bw}" height="${ih}" fill="transparent"/>`;
      s += `<rect x="${cx - w / 2}" y="${yb(r.booked)}" width="${w}" height="${m.t + ih - yb(r.booked)}" rx="3" fill="${fill}" pointer-events="none"/>`;
      const tall = m.t + ih - yb(r.booked) > 22;
      s += `<text x="${cx}" y="${tall ? yb(r.booked) + 15 : yb(r.booked) - 6}" text-anchor="middle" style="fill:${tall ? (r.status === 'Complete' ? 'var(--bg)' : 'var(--ink)') : 'var(--ink)'};font-weight:600" pointer-events="none">${r.booked}</text>`;
      if (i % every === 0) s += `<text x="${cx}" y="${H - 8}" text-anchor="middle">${day(r.webinar)}</text>`;
    });
    const pts = rows.map((r, i) => [m.l + i * bw + bw / 2, ys(r.show_up || 0)]);
    s += `<polyline points="${pts.map((p) => p.join(',')).join(' ')}" fill="none" stroke="var(--orange)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" pointer-events="none"/>`;
    pts.forEach((p) => { s += `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="var(--panel)" stroke="var(--orange)" stroke-width="2" pointer-events="none"/>`; });
    s += '</svg><div class="tip" hidden></div>';
    trend.innerHTML = s;
    const tip = trend.querySelector('.tip');
    trend.querySelectorAll('rect.hit').forEach((el) => {
      el.addEventListener('pointermove', (ev) => {
        const r = rows[+el.dataset.i];
        tip.innerHTML = `<b>Webinar ${day(r.webinar)}${r.status === 'Complete' ? '' : ' (still booking)'}</b>${n(r.booked)} booked calls<br>${pct(r.show_up)} showed up (${n(r.attended)} of ${n(r.registrants)})<br>${money(r.cost_per_booked)} per booked call<br>${n(r.purchases)} purchases`;
        tip.hidden = false;
        const box = trend.getBoundingClientRect();
        const x = ev.clientX - box.left, y = ev.clientY - box.top;
        tip.style.left = Math.min(Math.max(0, x + 14), box.width - tip.offsetWidth) + 'px';
        tip.style.top = Math.max(0, y - 90) + 'px';
      });
      el.addEventListener('pointerleave', () => { tip.hidden = true; });
    });
  };
  drawTrend();
  let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(drawTrend, 120); });

  // Top creatives
  const base = D.scorecard.filter((r) => r.period === 'last4' && r.path === 'All');
  const ranked = base.filter((r) => !r.retired && r.registrants >= MIN && r.booked > 0)
    .sort((a, b) => b.booked - a.booked || (a.cost_per_booked ?? 1e9) - (b.cost_per_booked ?? 1e9));
  const best = ranked[0];
  $('#thesis').innerHTML = best
    ? `Over the last 4 webinars, <b>${esc(best.creative)}</b> booked the most calls: ${n(best.booked)} at ${money(best.cost_per_booked)} each, with ${pct(best.show_up)} of its registrants showing up live.`
    : 'Not enough finished webinars yet to rank creatives.';
  const maxBk = Math.max(...ranked.map((r) => r.book_rate || 0), 0.01);
  $('#top-sub').textContent = 'Last 4 finished webinars, both registration paths, creatives with ' + MIN + ' or more registrants, ranked by booked calls. The bar shows booked calls per registrant.';
  $('#cards').innerHTML = ranked.slice(0, 8).map((r, i) => {
    const th = D.thumbs[assetOf(r.key)];
    return `<article class="cr"><div class="ph">${th ? `<img src="${th}" alt="" loading="lazy">` : '<span class="noimg">No preview</span>'}<span class="rank">${i + 1}</span><span class="fmt">${esc(r.format)}</span></div>
      <div class="body"><div class="nm">${esc(r.creative)}</div>
      <div><div class="mlab"><span>Booked per registrant</span><span class="num">${pct(r.book_rate)}</span></div><div class="meter"><i style="width:${(r.book_rate || 0) / maxBk * 100}%"></i></div></div>
      <dl><div><dt>Booked</dt><dd class="hot">${n(r.booked)}</dd></div><div><dt>Per call</dt><dd>${money(r.cost_per_booked)}</dd></div><div><dt>Show-up</dt><dd>${pct(r.show_up)}</dd></div>
      <div><dt>Registrants</dt><dd>${n(r.registrants)}</dd></div><div><dt>Purchases</dt><dd>${n(r.purchases)}</dd></div><div><dt>Per sale</dt><dd>${money(r.cost_per_purchase)}</dd></div></dl></div></article>`;
  }).join('');

  const leaks = base.filter((r) => !r.retired && r.spend >= 300 && r.booked === 0).sort((a, b) => b.spend - a.spend);
  $('#leak').innerHTML = leaks.length ? leaks.map((r) => `<div class="row"><span class="n">${esc(r.creative)}</span>
    <span class="v num">${money(r.spend)}<small>spend</small></span><span class="v num">${n(r.registrants)}<small>registrants</small></span><span class="v num">${pct(r.show_up)}<small>show-up</small></span></div>`).join('')
    : '<div class="row"><span class="n">Every creative that spent $300 or more booked at least one call.</span></div>';

  // Full table
  const st = {period: 'last4', path: 'All', q: '', small: false, k: 'booked', dir: -1};
  const seg = (id, key) => document.querySelectorAll(id + ' button').forEach((b) => b.addEventListener('click', () => {
    st[key] = b.dataset.v;
    document.querySelectorAll(id + ' button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    drawTable();
  }));
  seg('#seg-period', 'period'); seg('#seg-path', 'path');
  $('#q').addEventListener('input', (e) => { st.q = e.target.value.toLowerCase(); drawTable(); });
  $('#small').addEventListener('change', (e) => { st.small = e.target.checked; drawTable(); });
  document.querySelectorAll('#tbl th button').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.k;
    if (st.k === k) st.dir = -st.dir; else { st.k = k; st.dir = (k === 'creative' || k === 'path' || k.startsWith('cost_')) ? 1 : -1; }
    document.querySelectorAll('#tbl th').forEach((th) => th.removeAttribute('aria-sort'));
    b.parentElement.setAttribute('aria-sort', st.dir < 0 ? 'descending' : 'ascending');
    drawTable();
  }));
  function drawTable() {
    const rows = D.scorecard.filter((r) => r.period === st.period && r.path === st.path && (st.small || r.registrants >= MIN) && (!st.q || r.creative.toLowerCase().includes(st.q)));
    rows.sort((a, b) => {
      const x = a[st.k], y = b[st.k];
      if (x == null || x === '') return (y == null || y === '') ? 0 : 1;
      if (y == null || y === '') return -1;
      const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
      return c * st.dir || b.booked - a.booked || b.registrants - a.registrants;
    });
    $('#tbl tbody').innerHTML = rows.map((r) => `<tr class="${r.registrants < MIN ? 'low' : ''}">
      <td class="l nm">${esc(r.creative)}${r.retired ? ' <span class="pill ret">Retired</span>' : ''}</td><td class="l">${esc(r.path)}</td>
      <td>${money(r.spend)}</td><td>${n(r.registrants)}</td><td>${pct(r.show_up)}</td><td>${n(r.booked)}</td><td>${pct(r.book_rate)}</td>
      <td>${money(r.cost_per_booked)}</td><td>${n(r.purchases)}</td><td>${money(r.cost_per_purchase)}</td><td>${money(r.cost_per_attendee)}</td><td>${money(r.cost_per_reg)}</td></tr>`).join('')
      || '<tr><td class="l" colspan="12">No creatives match.</td></tr>';
  }
  drawTable();

  // Weekly
  const statusPill = (s) => `<span class="pill ${s === 'Complete' ? 'done' : 'open'}">${esc(s)}</span>`;
  $('#wtbl tbody').innerHTML = weeks.slice().reverse().map((r) => `<tr><td class="l">${day(r.webinar)}</td><td class="l">${statusPill(r.status)}</td>
    <td>${money(r.spend)}</td><td>${n(r.registrants)}</td><td>${n(r.lead_form)}</td><td>${n(r.website)}</td><td>${pct(r.show_up)}</td>
    <td>${n(r.booked)}</td><td>${money(r.cost_per_booked)}</td><td>${n(r.purchases)}</td><td>${money(r.cost_per_purchase)}</td></tr>`).join('');
  const done = weeks.filter((w) => w.status !== 'Registration open').map((w) => w.webinar).reverse();
  $('#wk').innerHTML = done.map((w) => `<option value="${w}">${day(w)}</option>`).join('');
  const drawWeek = () => {
    const wk = $('#wk').value;
    const rows = D.week_creatives.filter((r) => r.webinar === wk && r.registrants > 0)
      .sort((a, b) => b.booked - a.booked || b.purchases - a.purchases || b.attended - a.attended || b.registrants - a.registrants);
    $('#ctbl tbody').innerHTML = rows.map((r) => `<tr><td class="l nm">${esc(r.creative)}${r.retired ? ' <span class="pill ret">Retired</span>' : ''}</td><td class="l">${esc(r.path)}</td>
      <td>${money(r.spend)}</td><td>${n(r.registrants)}</td><td>${n(r.attended)}</td><td>${pct(r.show_up)}</td><td>${n(r.booked)}</td><td>${n(r.purchases)}</td><td>${money(r.cost_per_purchase)}</td></tr>`).join('');
  };
  $('#wk').addEventListener('change', drawWeek);
  drawWeek();

  // Sales
  $('#stbl tbody').innerHTML = D.sales.map((r) => `<tr><td class="l">${day(r.close)}</td><td>${money(r.amount)}</td><td class="l">${esc(r.plan)}</td>
    <td class="l nm">${esc(r.creative)}</td><td class="l">${esc(r.path)}</td><td class="l">${day(r.webinar)}</td><td class="l">${esc(r.attended)}</td><td class="l">${esc(r.booked)}</td></tr>`).join('')
    || '<tr><td class="l" colspan="8">No purchases yet.</td></tr>';
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    a = ap.parse_args()
    site = Path(a.site).resolve()
    site.mkdir(parents=True, exist_ok=True)

    latest = json.loads((HERE / "data" / "latest.json").read_text())
    score = load("scorecard")
    keep = ("key", "period", "path", "creative", "format", "spend", "registrants", "attended", "show_up",
            "booked", "book_rate", "purchases", "cost_per_reg", "cost_per_attendee", "cost_per_booked",
            "cost_per_purchase", "retired")
    data = {
        "generated": latest["generated"],
        "scorecard": [{k: r[k] for k in keep} for r in score],
        "weeks": load("weeks"),
        "week_creatives": [{k: r[k] for k in keep if k in r} | {"webinar": r["webinar"]}
                           for r in load("week_creatives")],
        "sales": load("sales"),
        "thumbs": fetch_thumbs(score, latest["ads"], site),
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()
    page = PAGE.replace("__DATA__", blob).replace("__LOGO__", logo).replace("__MIN__", str(MIN_REG))
    if "—" in page:
        raise SystemExit("em dash in the page: client-facing copy may not carry one")
    (site / "index.html").write_text(page)
    (site / "version.json").write_text(json.dumps({"generated": latest["generated"]}))
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (site / ".nojekyll").write_text("")
    print(f"  site: {site / 'index.html'} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
