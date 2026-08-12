#!/usr/bin/env python3
"""
Drift checker for madebysebby.com.

This site has no include system: nav, footer, the theme/lang init scripts and the
whole <style> block are copy-pasted into every HTML file. That makes it easy for
one page to quietly fall out of sync with the other 24. This script finds those.

Run it before pushing a site-wide change:

    ./check-consistency.py

Exit code 0 = consistent, 1 = drift found. Read-only; it never edits anything.
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# ---------------------------------------------------------------------------
# Known-good exceptions. Each entry is a page that legitimately differs, with
# the reason. If you add an exception, say WHY -- an unexplained exception is
# indistinguishable from a bug someone silenced.
# ---------------------------------------------------------------------------
MINIMAL_PAGES = {
    "404.html": "error page - intentionally has no nav/footer/canonical",
    "thank-you.html": "post-form confirmation - noindex, no nav/footer/canonical",
}
STANDALONE_PAGES = {
    "precios.html": "Spanish-only DR one-pager - own nav/footer, WhatsApp CTA, noindex",
}
# Pages that don't register the service worker (they're precached by other pages).
NO_SW = set(MINIMAL_PAGES) | set(STANDALONE_PAGES)
# Pages excluded from the sitemap, so hreflang/canonical don't apply.
NOINDEX = set(MINIMAL_PAGES) | set(STANDALONE_PAGES)
# index.html links its own logo to "#top" rather than "/". Deliberate.
NAV_SELF_LINK = {"index.html": "#top"}
# blog.html doesn't repeat its own link in the footer.
FOOTER_SELF_OMIT = {"blog.html": "blog.html"}

failures = []
notes = []


def fail(check, page, detail):
    failures.append((check, page, detail))


def pages():
    return sorted(glob.glob("*.html")) + sorted(glob.glob("blog/*.html"))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def normalize(href):
    """Collapse ../foo.html, /foo.html and foo.html to a single form."""
    href = href.strip()
    if href.startswith(("http://", "https://", "mailto:", "tel:")):
        return href
    href = re.sub(r"^(\.\./)+", "", href)
    href = href.lstrip("/")
    return href or "/"


def block(html, tag):
    """First <tag> ... </tag>, skipping <nav class=...> breadcrumbs."""
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), html, re.S)
    return m.group(1) if m else None


def hrefs(fragment):
    return {normalize(h) for h in re.findall(r'href="([^"]+)"', fragment)}


def modal(sets):
    """Most common set -- treated as the intended baseline."""
    counts = {}
    for s in sets:
        counts[frozenset(s)] = counts.get(frozenset(s), 0) + 1
    return set(max(counts, key=counts.get))


# ---------------------------------------------------------------------------
# 1. Service worker precache vs what's actually on disk
# ---------------------------------------------------------------------------
def check_service_worker():
    sw = read("sw.js")
    listed = re.findall(r"'(/[^']*)'", sw)
    cache = re.search(r"CACHE\s*=\s*'([^']+)'", sw)
    if cache:
        notes.append("service worker cache version: %s" % cache.group(1))

    for path in listed:
        disk = "index.html" if path == "/" else path.lstrip("/")
        if not os.path.isfile(disk):
            fail("sw.js precache", "sw.js",
                 "lists %s which does not exist -- cache.addAll() is atomic, "
                 "so this breaks offline support for the WHOLE site" % path)

    listed_pages = {p.lstrip("/") for p in listed if p.endswith(".html")}
    for p in pages():
        if p in STANDALONE_PAGES:
            continue
        if p not in listed_pages:
            fail("sw.js precache", p,
                 "exists but is missing from PAGES in sw.js "
                 "(add it, and bump CACHE)")


# ---------------------------------------------------------------------------
# 2. Head elements every page needs
# ---------------------------------------------------------------------------
def check_head():
    required = [
        ("GA4 tag", r"G-DV4L5CZRML", None),
        ("theme init", r'getItem\("theme"\)', None),
        ("lang init", r'getItem\("lang"\)', STANDALONE_PAGES),
        ("canonical", r'rel="canonical"', NOINDEX),
        ("skip-link", r'class="skip-link"', MINIMAL_PAGES),
        ("dark via media query", r':root:not\(\[data-theme="light"\]\)', None),
        ("dark via attribute", r'\[data-theme="dark"\]', None),
        ("service worker reg", r"serviceWorker", NO_SW),
    ]
    for page in pages():
        html = read(page)
        for name, pattern, skip in required:
            if skip and page in skip:
                continue
            if not re.search(pattern, html):
                fail(name, page, "missing")


# ---------------------------------------------------------------------------
# 3. hreflang -- self-reference, x-default, and reciprocity
#
# Most pages serve both languages at one URL, so en/es/x-default all point at
# themselves. A page may instead name a DIFFERENT page as its counterpart
# (diseno-web-santo-domingo -> web-design-miami). That is allowed, but hreflang
# must be reciprocal: if A names B, B must name A. Google silently discards
# one-directional annotations, so a broken pair is worse than none.
# ---------------------------------------------------------------------------
BASE = "https://madebysebby.com/"


def hreflang_map():
    out = {}
    for page in pages():
        tags = re.findall(
            r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', read(page))
        if tags:
            out[page] = [(lang, url.replace(BASE, "") or "index.html")
                         for lang, url in tags]
    return out


def check_hreflang():
    hmap = hreflang_map()
    for page in pages():
        if page in NOINDEX:
            continue
        tags = hmap.get(page)
        if not tags:
            fail("hreflang", page, "has no hreflang annotations at all")
            continue

        langs = {lang for lang, _ in tags}
        if "x-default" not in langs:
            fail("hreflang", page, "missing x-default")

        base_langs = {lang.split("-")[0] for lang in langs if lang != "x-default"}
        for want in ("en", "es"):
            if want not in base_langs:
                fail("hreflang", page,
                     "declares no '%s' alternate (site is bilingual)" % want)

        if not any(url == page for _, url in tags):
            fail("hreflang", page, "never points at itself -- every page needs "
                                   "a self-referencing hreflang")

        for lang, target in tags:
            if target == page or lang == "x-default":
                continue
            back = hmap.get(target, [])
            if not any(url == page for _, url in back):
                fail("hreflang", page,
                     "claims %s is its '%s' version, but %s never points back. "
                     "hreflang must be reciprocal -- Google discards the whole "
                     "annotation otherwise" % (target, lang, target))


# ---------------------------------------------------------------------------
# 4. Nav and footer link sets
# ---------------------------------------------------------------------------
def check_shared_blocks():
    for tag, self_link, self_omit in (
        ("nav", NAV_SELF_LINK, {}),
        ("footer", {}, FOOTER_SELF_OMIT),
    ):
        collected = {}
        for page in pages():
            if page in MINIMAL_PAGES or page in STANDALONE_PAGES:
                continue
            frag = block(read(page), tag)
            if frag is None:
                fail("%s block" % tag, page, "has no <%s> at all" % tag)
                continue
            collected[page] = hrefs(frag)

        if not collected:
            return
        baseline = modal(collected.values())

        for page, found in sorted(collected.items()):
            expected = set(baseline)
            if page in self_link:
                expected.discard("/")
                expected.add(normalize(self_link[page]))
            if page in self_omit:
                expected.discard(normalize(self_omit[page]))

            # Don't restate what "CTA routing" already reported for this page.
            noise = {"book.html", "contact.html"} if page in cta_flagged else set()

            for missing in sorted(expected - found - noise):
                fail("%s block" % tag, page, "missing link to %s" % missing)
            for extra in sorted(found - expected - noise):
                fail("%s block" % tag, page, "has unexpected link to %s" % extra)


# ---------------------------------------------------------------------------
# 5. CTA routing -- every booking CTA goes through book.html (CLAUDE.md rule 6)
# ---------------------------------------------------------------------------

# Cal.com legal pages are cited in the privacy policy as a subprocessor
# disclosure. Those are not booking CTAs and must not be rewritten.
CAL_NON_BOOKING = re.compile(r"cal\.com/(privacy|terms|security|legal)")

cta_flagged = set()


def check_cta_routing():
    for page in pages():
        if page in MINIMAL_PAGES or page in STANDALONE_PAGES:
            continue
        frag = block(read(page), "nav")
        if frag is None:
            continue
        if "book.html" not in hrefs(frag):
            cta_flagged.add(page)
            alt = sorted(l for l in hrefs(frag) if l.endswith("contact.html"))
            fail("CTA routing", page,
                 "nav CTA points at %s instead of book.html -- booking CTAs "
                 "must route through the Cal.com page"
                 % (alt[0] if alt else "no booking page at all"))

    for page in pages():
        for url in re.findall(r'href="(https?://[^"]*cal\.com[^"]*)"', read(page)):
            if page == "book.html" or CAL_NON_BOOKING.search(url):
                continue
            fail("CTA routing", page,
                 "links straight to cal.com (%s) instead of book.html" % url)


# ---------------------------------------------------------------------------

def main():
    check_service_worker()
    check_head()
    check_hreflang()
    check_cta_routing()   # must precede check_shared_blocks (populates cta_flagged)
    check_shared_blocks()

    total = len(pages())
    print()
    print("  madebysebby.com consistency check -- %d HTML files" % total)
    for n in notes:
        print("  %s" % n)
    print()

    if not failures:
        print("  OK - no drift found.")
        print()
        return 0

    by_check = {}
    for check, page, detail in failures:
        by_check.setdefault(check, []).append((page, detail))

    for check in sorted(by_check):
        rows = by_check[check]
        print("  %s -- %d issue%s" % (check, len(rows), "" if len(rows) == 1 else "s"))
        for page, detail in rows:
            print("    x %s" % page)
            print("        %s" % detail)
        print()

    print("  %d issue%s found." % (len(failures), "" if len(failures) == 1 else "s"))
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
