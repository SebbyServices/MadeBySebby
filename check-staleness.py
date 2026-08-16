#!/usr/bin/env python3
"""Which pages to spend today's Search Console re-indexing quota on.

THE PROBLEM THIS SOLVES. Search Console's URL Inspection answers "is this URL
in the index". It does not answer "is the copy in the index the copy that is
live". On 2026-08-16 nine pages were inspected, eight reported "already
indexed", and eight of those nine were being served with pre-13-August titles.
One told every searcher that a website starts at $1500 against a live page
saying $2,500. Reading "indexed" as "fine" spent that evening's quota on pages
needing nothing and skipped the page needing it most.

Manual re-indexing is rate limited to roughly ten a day on purpose, so the
scarce resource is not crawling, it is knowing which ten to spend it on.

TWO MODES, AND THE DIFFERENCE MATTERS.

  Default, no credentials. Ranks pages by when they last changed in git and
  how commercially important they are. This is a WORK LIST, not proof. It
  cannot see Google, so it reports what is likely stale, never what is.

  --gsc, with Search Console credentials. Calls the URL Inspection API for the
  real lastCrawlTime and compares it against each page's last commit. A page
  committed after Google last crawled it is stale as a fact, not a guess.
  Setup is in the README section at the bottom of this file.

WHAT THIS DELIBERATELY DOES NOT DO. It does not scrape Google. A scripted
request to google.com/search returns a JavaScript shell and a consent wall,
92 KB containing zero results, measured 2026-08-16. Any tool that claims to
read your rankings by fetching that URL is reporting on an empty page.

It also does not submit anything. Google's Indexing API is documented as
accepting only JobPosting and BroadcastEvent pages; calling it for ordinary
pages is a script that looks like it works and is ignored. The sanctioned
signal is the sitemap lastmod, which the GitHub Action already maintains.

    ./check-staleness.py                  work list, ranked
    ./check-staleness.py --since 2026-08-13
    ./check-staleness.py --top 10         just today's ten
    ./check-staleness.py --gsc            real crawl dates, needs credentials
    ./check-staleness.py --json
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build  # noqa: E402

DOMAIN = "https://madebysebby.com"

# What a page is worth having correct in the index. A wrong price on the
# pricing page costs money on every impression; a stale terms page costs
# nothing. Quota goes to the top of this list first.
VALUE = [
    (r"^/(es/)?(pricing|precios)\.html$", 100, "publishes the prices"),
    (r"^/(es/)?$",                         95, "homepage, carries the entity signals"),
    (r"^/(es/)?(diseno-web-para-abogados|web-design-for-law-firms)\.html$", 85, "the vertical with the softest SERP"),
    (r"^/(es/)?(web-design-miami|diseno-web-miami)\.html$", 80, "primary local landing page"),
    (r"^/(es/)?(website-care|cuidado-web)\.html$", 75, "the recurring revenue offer"),
    (r"^/(es/)?(web-design|diseno-web)-fort-lauderdale\.html$", 65, "secondary local landing page"),
    (r"^/(es/)?diseno-web-santo-domingo\.html$", 60, "the DR market"),
    (r"(services|servicios)\.html$",       55, "the service list"),
    (r"^/(es/)?(case-study|caso)-", 50, "proof, cited by AI answers"),
    (r"(work|portafolio)\.html$",          45, "portfolio"),
    (r"(about|sobre-mi)\.html$",           40, "entity and location signals"),
    (r"(contact|contacto|book|agendar)",   35, "conversion pages"),
    (r"/blog/",                            25, "supporting content"),
    (r"(blog)\.html$",                     20, "blog index"),
    (r"(privacy|privacidad|terms|terminos)", 5, "legal, rarely a search entry point"),
]


def value_of(path):
    for pattern, score, why in VALUE:
        if re.search(pattern, path):
            return score, why
    return 30, "standard page"


def indexable_paths():
    """Every URL in the sitemap, derived the same way the sitemap derives it."""
    paths = []
    for source in build.PAGES:
        for lang in ("en", "es"):
            if lang == "en" and build.PAGES[source].get("en", source) is None:
                continue
            url = build.url_for(source, lang)
            disk = url.lstrip("/")
            # Directory-style URLs are the index.html inside them. Without this
            # the Spanish homepage drops out, and it is one of the two pages
            # most worth checking.
            if disk.endswith("/") or not disk:
                disk = disk + "index.html"
            full = os.path.join(HERE, disk)
            if not os.path.isfile(full):
                continue
            if re.search(r'<meta name="robots"[^>]*noindex',
                         open(full, encoding="utf-8").read(), re.I):
                continue
            paths.append((url, disk))
    return sorted(set(paths))


def last_commit(disk):
    """When this generated page last actually changed."""
    out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", disk],
                         capture_output=True, text=True, cwd=HERE).stdout.strip()
    return out or None


def gsc_crawl_times(urls):
    """Real lastCrawlTime per URL from the Search Console API.

    Returns {} and explains itself if credentials are absent, rather than
    pretending the pages are current. A silent empty result here would turn
    this tool into the thing it was written to replace.
    """
    token = os.environ.get("GSC_ACCESS_TOKEN", "")
    prop = os.environ.get("GSC_PROPERTY", DOMAIN + "/")
    if not token:
        print("  --gsc needs GSC_ACCESS_TOKEN. See the setup notes at the",
              file=sys.stderr)
        print("  bottom of this file. Falling back to the git work list.\n",
              file=sys.stderr)
        return {}
    import urllib.request
    out = {}
    for u in urls:
        body = json.dumps({"inspectionUrl": u, "siteUrl": prop}).encode()
        req = urllib.request.Request(
            "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
            data=body, headers={"Authorization": "Bearer " + token,
                                "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
            idx = d.get("inspectionResult", {}).get("indexStatusResult", {})
            out[u] = {"crawled": idx.get("lastCrawlTime"),
                      "state": idx.get("coverageState")}
        except Exception as exc:
            out[u] = {"crawled": None, "state": "lookup failed: %s" % exc}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="only pages changed on or after this date, YYYY-MM-DD")
    ap.add_argument("--top", type=int, default=0, help="show only the top N")
    ap.add_argument("--gsc", action="store_true", help="use real Search Console crawl dates")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = []
    for url, disk in indexable_paths():
        changed = last_commit(disk)
        score, why = value_of(url)
        rows.append({"url": DOMAIN + url, "path": url, "changed": changed,
                     "score": score, "why": why})

    crawl = gsc_crawl_times([r["url"] for r in rows]) if args.gsc else {}
    for r in rows:
        c = crawl.get(r["url"])
        if c:
            r["crawled"] = c["crawled"]
            r["coverage"] = c["state"]
            # A page committed after Google last crawled it is stale as fact.
            r["stale"] = bool(c["crawled"] and r["changed"] and r["changed"] > c["crawled"])
        else:
            r["crawled"] = None
            r["stale"] = None

    if args.since:
        rows = [r for r in rows if r["changed"] and r["changed"][:10] >= args.since]

    # Provably stale first, then by commercial value, then most recently changed.
    rows.sort(key=lambda r: (r["stale"] is not True, -r["score"], r["changed"] or ""),
              reverse=False)
    if args.top:
        rows = rows[:args.top]

    if args.json:
        print(json.dumps(rows, indent=1))
        return

    mode = "REAL crawl dates from Search Console" if crawl else \
           "git dates only, this is a ranked guess and not proof"
    print("\n  %d pages. Mode: %s.\n" % (len(rows), mode))
    for i, r in enumerate(rows, 1):
        flag = "STALE" if r["stale"] else ("     " if r["stale"] is False else "  ?  ")
        print("  %2d. %s %s" % (i, flag, r["url"]))
        print("          changed %s, %s" % ((r["changed"] or "unknown")[:10], r["why"]))
        if r["crawled"]:
            print("          google last crawled %s" % r["crawled"][:10])
    if not crawl:
        print("\n  Without --gsc this cannot see Google. It ranks what is most")
        print("  likely worth submitting; it does not know what is stale.")


# ---------------------------------------------------------------------------
# SETTING UP --gsc, once
#
# 1. console.cloud.google.com, create a project, enable "Google Search Console API"
# 2. Create an OAuth 2.0 Client ID of type "Desktop app", download the JSON
# 3. Get an access token with the scope
#       https://www.googleapis.com/auth/webmasters.readonly
#    The quickest route is the OAuth 2.0 Playground, developers.google.com/oauthplayground,
#    using your own client ID and secret under the gear icon.
# 4. Export it before running:
#       export GSC_ACCESS_TOKEN="ya29...."
#       export GSC_PROPERTY="https://madebysebby.com/"
#
# Tokens expire in an hour, which is fine for a weekly check by hand. Wiring a
# refresh token in is only worth it if this ever runs unattended.
#
# Quota is 2000 inspections a day, far above the 47 pages here.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
