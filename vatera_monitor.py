#!/usr/bin/env python3
"""
Vatera seller monitor -> ntfy push  (GitHub Actions version)
------------------------------------------------------------
Checks one Vatera seller's page on a ~60-second internal loop, detects
newly-added listings, and pushes one ntfy notification per new listing.
Runs inside a GitHub Actions job; the set of already-seen listing IDs is
kept in vatera_seen.json and committed back by the workflow between runs.

Config comes from environment variables (NTFY_TOPIC is a repo secret):
  NTFY_TOPIC    (required) your private ntfy topic
  NTFY_SERVER   (optional) default https://ntfy.sh
  SELLER_URL    (optional) defaults to LilyOleander's page
  KEYWORDS      (optional) comma-separated; empty = alert on everything
  LOOP_SECONDS  (optional) how long each run keeps polling, default 240
  CHECK_EVERY   (optional) seconds between checks, default 60
Standard library only.
"""

import json, os, re, sys, time
import urllib.request, urllib.error

NTFY_TOPIC   = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER  = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
SELLER_URL   = os.environ.get("SELLER_URL",
                 "https://www.vatera.hu/listings/index.php?us=LilyOleander")
KEYWORDS     = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "240"))
CHECK_EVERY  = int(os.environ.get("CHECK_EVERY", "60"))
NEW_PRIORITY = int(os.environ.get("NEW_LISTING_PRIORITY", "4"))

STATE_FILE = "vatera_seen.json"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
FETCH_TIMEOUT = 15


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def fetch_html(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def looks_like_challenge(html):
    return any(m in html for m in ("Just a moment", "cf-browser-verification",
                                   "Checking your browser", "Attention Required"))


def parse_listings(html):
    url_re = re.compile(r'https://www\.vatera\.hu/([a-z0-9\-]+)-(\d{6,})\.html')
    title_re = re.compile(
        r'https://www\.vatera\.hu/[a-z0-9\-]+-(\d{6,})\.html"\s+title="([^"]*)"')
    titles = {}
    for i, t in title_re.findall(html):
        c = re.sub(r"\s+", " ", t).strip()
        if c:
            titles[i] = c
    out = {}
    for slug, i in url_re.findall(html):
        if i in out:
            continue
        out[i] = {"id": i,
                  "url": "https://www.vatera.hu/%s-%s.html" % (slug, i),
                  "title": titles.get(i) or slug.replace("-", " ").strip()}
    return out


def matches(title):
    return True if not KEYWORDS else any(k in title.lower() for k in KEYWORDS)


def load_seen():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(ids):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(ids), f)
    os.replace(tmp, STATE_FILE)


def ntfy(title_ascii, body, click=None, tags=None, priority=3):
    h = {"Title": title_ascii, "Content-Type": "text/plain; charset=utf-8",
         "Priority": str(priority)}
    if click:
        h["Click"] = click
    if tags:
        h["Tags"] = tags
    req = urllib.request.Request("%s/%s" % (NTFY_SERVER, NTFY_TOPIC),
                                 data=body.encode("utf-8"), headers=h, method="POST")
    try:
        urllib.request.urlopen(req, timeout=FETCH_TIMEOUT)
    except urllib.error.URLError as e:
        log("ntfy failed: %s" % e)


def run_once(seen):
    try:
        html = fetch_html(SELLER_URL)
    except Exception as e:
        log("fetch error (skip): %s" % e)
        return seen
    if looks_like_challenge(html):
        log("bot-check page returned (skip)")
        return seen
    listings = parse_listings(html)
    if not listings:
        log("0 listings parsed (skip)")
        return seen

    current = set(listings)
    if seen is None:  # first run ever: baseline, one summary, no flood
        save_seen(current)
        ntfy("Vatera monitor active",
             "Watching this seller. Tracking %d listings; new ones ping you "
             "from now on." % len(current),
             click=SELLER_URL, tags="seedling")
        log("baselined %d listings" % len(current))
        return current

    new = current - seen
    if not new:
        log("no new (%d tracked)" % len(current))
        return seen

    updated = seen | current  # union, never shrink -> no false re-alerts
    save_seen(updated)
    sent = 0
    for i in sorted(new):
        it = listings[i]
        if not matches(it["title"]):
            continue
        ntfy("New listing", "%s\nTap to open the listing." % it["title"],
             click=it["url"], tags="seedling,rotating_light", priority=NEW_PRIORITY)
        sent += 1
        time.sleep(1)
    log("%d new listing(s), %d alert(s) sent" % (len(new), sent))
    return updated


def main():
    if not NTFY_TOPIC:
        log("ERROR: NTFY_TOPIC env var not set (add it as a repo secret)")
        sys.exit(1)
    seen = load_seen()
    deadline = time.time() + LOOP_SECONDS
    while True:
        seen = run_once(seen)
        if time.time() >= deadline:
            break
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()

