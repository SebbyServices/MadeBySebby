#!/usr/bin/env python3
"""
Drift checker for madebysebby.com.

The site is generated: build.py splits each bilingual file in src/ into an
English page at the root and a Spanish one under /es/. That removes the old
copy-paste drift between 25 hand-maintained files, but introduces a new failure
mode -- committed output that no longer matches src/ -- and leaves the checks
that were never about duplication in the first place.

Run it before pushing:

    ./check-consistency.py

Exit code 0 = consistent, 1 = drift found. Read-only; it never edits anything.

Every check here exists because that exact thing had already gone wrong.
"""

import glob
import importlib.util
import os
import re
import sys
from html import unescape

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

_spec = importlib.util.spec_from_file_location("build", os.path.join(ROOT, "build.py"))
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

# ---------------------------------------------------------------------------
# Known-good exceptions, keyed by GENERATED path. Each says WHY -- an
# unexplained exception is indistinguishable from a bug someone silenced.
# ---------------------------------------------------------------------------
MINIMAL_PAGES = {
    "404.html": "error page - no nav/footer/canonical; serves both languages "
                "inline because GitHub Pages has only one 404 for the whole site",
    "thank-you.html": "post-form confirmation - noindex, no nav/footer/canonical",
    "es/gracias.html": "Spanish post-form confirmation - noindex",
}
STANDALONE_PAGES = {
    "precios.html": "Spanish-only DR one-pager - own nav/footer, WhatsApp CTA, "
                    "noindex. NOT the same page as es/precios.html, which is the "
                    "Spanish twin of pricing.html",
}
REDIRECT_STUBS = {
    "diseno-web-santo-domingo.html": "meta-refresh stub - the page moved to "
                                     "/es/diseno-web-santo-domingo.html when the "
                                     "site split into two language trees",
}
EXEMPT = set(MINIMAL_PAGES) | set(STANDALONE_PAGES) | set(REDIRECT_STUBS)
NO_SW = EXEMPT
NOINDEX = EXEMPT
# Both tree roots link their own logo to "#top" rather than "/". Deliberate.
NAV_SELF_LINK = {"index.html": "#top", "es/index.html": "#top"}
# The blog index doesn't repeat its own link in the footer.
FOOTER_SELF_OMIT = {"blog.html": "blog.html", "es/blog.html": "blog.html"}

BASE = "https://madebysebby.com"

failures = []
notes = []


def fail(check, page, detail):
    failures.append((check, page, detail))


def pages():
    """Every generated page. src/ is source, not output, and is never checked."""
    found = []
    for pattern in ("*.html", "blog/*.html", "es/*.html", "es/blog/*.html"):
        found += glob.glob(pattern)
    return sorted(p for p in found if not p.startswith("src/"))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Logical page identity.
#
# /services.html and /es/servicios.html are the same page in two languages. Most
# checks care about the logical page, not the URL, so both collapse to the
# source name "services.html". That lets one baseline cover both trees.
# ---------------------------------------------------------------------------
URL_TO_SOURCE = {}
SOURCE_TO_URLS = {}
for _src in build.PAGES:
    for _lang in ("en", "es"):
        if _lang == "en" and build.PAGES[_src].get("en", _src) is None:
            continue
        _url = build.url_for(_src, _lang)
        URL_TO_SOURCE[_url] = _src
        SOURCE_TO_URLS.setdefault(_src, {})[_lang] = _url


def rel_path(url):
    """URL as it exists on disk: '/es/' -> 'es/index.html'."""
    if url.endswith("/"):
        return url.lstrip("/") + "index.html"
    return url.lstrip("/")


def logical(href):
    href = href.strip()
    if href.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return href
    href = href.split("#")[0]
    return URL_TO_SOURCE.get(href, href)


def block(html, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), html, re.S)
    return m.group(1) if m else None


def hrefs(fragment):
    return {logical(h) for h in re.findall(r'href="([^"]+)"', fragment)}


def modal(sets):
    counts = {}
    for s in sets:
        counts[frozenset(s)] = counts.get(frozenset(s), 0) + 1
    return set(max(counts, key=counts.get))


# ---------------------------------------------------------------------------
# 1. Is the committed output actually a build of src/?
#
# This is the check the whole generated-site model rests on. Edit index.html at
# the root instead of src/index.html and your change is real, deployed, and
# destroyed by the next build with no warning. Catch it here instead.
# ---------------------------------------------------------------------------
def check_build_current():
    for source in build.PAGES:
        src_path = os.path.join("src", source)
        if not os.path.isfile(src_path):
            fail("build freshness", src_path, "listed in build.py PAGES but missing from src/")
            continue
        raw = read(src_path)
        for lang in ("en", "es"):
            if lang == "en" and build.PAGES[source].get("en", source) is None:
                continue
            out = rel_path(build.url_for(source, lang))
            if not os.path.isfile(out):
                fail("build freshness", out, "should be generated from src/%s but does not exist "
                                             "-- run ./build.py" % source)
                continue
            if read(out) != build.render(source, raw, lang):
                fail("build freshness", out,
                     "differs from a fresh build of src/%s. Either someone edited the "
                     "GENERATED file (their change dies on the next build) or the build "
                     "was never re-run. Fix src/, then ./build.py" % source)

    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        name = os.path.relpath(src_path, "src")
        if name not in build.PAGES and name not in build.PASSTHROUGH:
            fail("build freshness", src_path,
                 "sits in src/ but is in neither PAGES nor PASSTHROUGH in build.py, "
                 "so it is never published")


# ---------------------------------------------------------------------------
# 2. Service worker precache vs what's on disk
# ---------------------------------------------------------------------------
def check_service_worker():
    sw = read("sw.js")
    listed = re.findall(r"'(/[^']*)'", sw)
    cache = re.search(r"CACHE\s*=\s*'([^']+)'", sw)
    if cache:
        notes.append("service worker cache version: %s" % cache.group(1))

    for path in listed:
        disk = rel_path(path)
        if not os.path.isfile(disk):
            fail("sw.js precache", "sw.js",
                 "lists %s which does not exist -- cache.addAll() is atomic, so this "
                 "breaks offline support for the WHOLE site" % path)

    listed_pages = {rel_path(p) for p in listed if p.endswith(".html") or p.endswith("/")}
    for p in pages():
        if p in STANDALONE_PAGES or p in REDIRECT_STUBS:
            continue
        if p not in listed_pages:
            fail("sw.js precache", p,
                 "exists but is missing from PAGES in sw.js (add it, and bump CACHE)")


# ---------------------------------------------------------------------------
# 3. Head elements every page needs
# ---------------------------------------------------------------------------
def check_head():
    required = [
        ("GA4 tag", r"G-DV4L5CZRML", REDIRECT_STUBS),
        ("theme init", r'getItem\("theme"\)', EXEMPT),
        ("canonical", r'rel="canonical"', set(MINIMAL_PAGES) | set(STANDALONE_PAGES)),
        ("skip-link", r'class="skip-link"', EXEMPT),
        ("dark via media query", r':root:not\(\[data-theme="light"\]\)', EXEMPT),
        ("dark via attribute", r'\[data-theme="dark"\]', EXEMPT),
        ("service worker reg", r"serviceWorker", NO_SW),
    ]
    for page in pages():
        html = read(page)
        for name, pattern, skip in required:
            if skip and page in skip:
                continue
            if not re.search(pattern, html):
                fail(name, page, "missing")

    # The localStorage language system is gone. If it comes back, the page is
    # serving hidden content to crawlers again -- the exact defect the split fixed.
    for page in pages():
        if page in STANDALONE_PAGES or page in MINIMAL_PAGES:
            continue
        html = read(page)
        if "data-lang" in html or 'getItem("lang")' in html:
            fail("language split", page,
                 "still carries the old data-lang/localStorage machinery. Each tree is "
                 "single-language now; hidden siblings are invisible to Google")
        if re.search(r'<(span|div)\b[^>]*\blang="(en|es)"[^>]*>', html):
            fail("language split", page,
                 "still contains bilingual lang= elements -- build.py should have "
                 "stripped the other language out")


# ---------------------------------------------------------------------------
# 4. hreflang -- self-reference, x-default, reciprocity
#
# This check finally means something. Under the old one-URL-two-languages model
# every page pointed en/es/x-default at ITSELF, which is a well-formed
# annotation that conveys nothing. Now the pairs are real, so a broken pair is a
# real defect: Google discards non-reciprocal annotations wholesale.
# ---------------------------------------------------------------------------
def hreflang_map():
    out = {}
    for page in pages():
        tags = re.findall(
            r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', read(page))
        if tags:
            out[page] = [(lang, rel_path(url.replace(BASE, "") or "/"))
                         for lang, url in tags]
    return out


def check_hreflang():
    hmap = hreflang_map()
    for page in pages():
        if page in NOINDEX:
            continue
        source = URL_TO_SOURCE.get("/" + page) or URL_TO_SOURCE.get(
            "/" + page.replace("index.html", ""))
        tags = hmap.get(page)
        if not tags:
            fail("hreflang", page, "has no hreflang annotations at all")
            continue

        langs = {lang for lang, _ in tags}
        if "x-default" not in langs:
            fail("hreflang", page, "missing x-default")

        # A Spanish-only page is nobody's translation and correctly declares no
        # English alternate. Everything else must declare both.
        english_exists = source is None or build.PAGES[source].get("en", source) is not None
        expected_langs = {"en", "es"} if english_exists else {"es"}
        base_langs = {lang.split("-")[0] for lang in langs if lang != "x-default"}
        for want in expected_langs:
            if want not in base_langs:
                fail("hreflang", page, "declares no '%s' alternate" % want)
        if not english_exists and "en" in base_langs:
            fail("hreflang", page,
                 "declares an English alternate but has no English version")

        if not any(target == page for _, target in tags):
            fail("hreflang", page,
                 "never points at itself -- every page needs a self-referencing hreflang")

        for lang, target in tags:
            if target == page or lang == "x-default":
                continue
            if not os.path.isfile(target):
                fail("hreflang", page,
                     "claims %s is its '%s' version, but that file does not exist"
                     % (target, lang))
                continue
            back = hmap.get(target, [])
            if not any(t == page for _, t in back):
                fail("hreflang", page,
                     "claims %s is its '%s' version, but %s never points back. "
                     "hreflang must be reciprocal -- Google discards the whole "
                     "annotation otherwise" % (target, lang, target))


# ---------------------------------------------------------------------------
# 5. Internal links resolve to files that exist
#
# Added after nine blog CTAs shipped pointing at /blog/book.html, a 404. The
# source said href="book.html" inside blog/, which resolves one directory too
# deep. Every one of them rendered as a normal button.
# ---------------------------------------------------------------------------
def check_links():
    for page in pages():
        # 404.html and precios.html are copied to the root verbatim rather than
        # generated, and both sit at depth 0, so their relative paths resolve.
        passthrough = page in set(build.PASSTHROUGH)
        # srcset is included because leaving it out is how 11 broken hero images
        # shipped past this check: <img src> was correct on every Spanish page
        # while the <source srcset> beside it pointed one directory too deep,
        # and the browser prefers the source.
        candidates = []
        for m in re.finditer(r'\b(href|src|action|srcset)="([^"]+)"', read(page)):
            if m.group(1) == "srcset":
                candidates += [c.strip().split(None, 1)[0]
                               for c in m.group(2).split(",") if c.strip()]
            else:
                candidates.append(m.group(2))
        for url in candidates:
            if url.startswith(("http://", "https://", "//", "#", "mailto:", "tel:", "data:")):
                continue
            target = url.split("#")[0].split("?")[0]
            if not target:
                continue
            if not target.startswith("/"):
                if passthrough:
                    resolved = os.path.normpath(os.path.join(os.path.dirname(page), target))
                    if not os.path.isfile(resolved):
                        fail("internal links", page,
                             "links to %s which does not exist" % url)
                    continue
                fail("internal links", page,
                     "relative link %r -- generated pages must use root-absolute "
                     "paths, or they resolve differently in /es/ than at the root" % url)
                continue
            if not os.path.isfile(rel_path(target)):
                fail("internal links", page, "links to %s which does not exist" % target)


# ---------------------------------------------------------------------------
# 6. Every visible string is bilingual
#
# Rule 4 says all user-facing copy needs lang="en"/lang="es" siblings. Under the
# old one-URL model a missed string was invisible -- it just showed in English
# either way and nobody noticed. Now it lands on a page that is Spanish top to
# bottom, so it stands out: the breadcrumb read "Home / Reservar Llamada" and
# the care plans were priced "$249/mo".
#
# Text that survives BOTH language filters is identical in the two trees. Some
# of that is correct -- names, domains, numerals -- hence the allowlist. Add to
# it only for strings that genuinely should not be translated.
# ---------------------------------------------------------------------------
NON_TRANSLATABLE = {
    "Blog", "Made by Sebby", "hello@madebysebby.com", "EN", "ES", "Sebby",
    "Riera Law Firm", "Elite Care Recovery", "rieralaw.com", "elitecarerecovery.net",
    "rieralaw.com →", "elitecarerecovery.net →", "madebysebby.com",
    "Jorge L. Riera", "John Pierce", "JP", "JR", "EC",
    "WhatsApp", "Cal.com", "Google", "Instagram", "SEO", "GitHub Pages", "Wave",
    "Ir al contenido",   # diseno-web-santo-domingo is Spanish-only by design
    "<1s", "$3K+",       # a load-time metric and a price figure -- same in both
}
NUMERIC_ONLY = re.compile(r"^[\W\d]*$")


def check_bilingual_coverage():
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        name = os.path.relpath(src_path, "src")
        if name in build.PASSTHROUGH:
            continue        # precios.html is Spanish-only; 404.html stays inline
        html = read(src_path)
        html = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", html, flags=re.S)
        m = re.search(r"<body.*?</body>", html, re.S)
        if not m:
            continue
        remaining = m.group(0)
        for tag in ("span", "div"):
            pattern = re.compile(r'<%s\b[^>]*\blang="(en|es)"[^>]*>' % tag)
            while True:
                hit = pattern.search(remaining)
                if not hit:
                    break
                _, close_end = build.matching_close(remaining, hit.start(), tag)
                remaining = remaining[:hit.start()] + " " + remaining[close_end:]
        for chunk in re.split(r"<[^>]+>", remaining):
            text = " ".join(unescape(chunk).split())
            if len(text) < 2 or NUMERIC_ONLY.match(text) or text in NON_TRANSLATABLE:
                continue
            fail("bilingual coverage", src_path,
                 "visible text %r has no lang=\"en\"/lang=\"es\" siblings, so it "
                 "renders identically in both trees" % text[:60])


# ---------------------------------------------------------------------------
# 7. Nav and footer link sets, compared by logical page across BOTH trees
# ---------------------------------------------------------------------------
def check_shared_blocks():
    for tag, self_link, self_omit in (
        ("nav", NAV_SELF_LINK, {}),
        ("footer", {}, FOOTER_SELF_OMIT),
    ):
        collected = {}
        for page in pages():
            if page in EXEMPT:
                continue
            frag = block(read(page), tag)
            if frag is None:
                fail("%s block" % tag, page, "has no <%s> at all" % tag)
                continue
            # The language toggle is the one nav link that points at the other
            # tree, so it collapses to the SAME logical page as its host and
            # every nav would read as "unexpected link to itself". It has its
            # own check.
            frag = re.sub(r'<div class="lang-toggle">.*?</div>', "", frag, flags=re.S)
            collected[page] = hrefs(frag)

        if not collected:
            return
        baseline = modal(collected.values())

        for page, found in sorted(collected.items()):
            expected = set(baseline)
            if page in self_link:
                expected.discard("index.html")
                expected.add(self_link[page])
            if page in self_omit:
                expected.discard(self_omit[page])

            noise = {"book.html", "contact.html"} if page in cta_flagged else set()

            for missing in sorted(expected - found - noise):
                fail("%s block" % tag, page, "missing link to %s" % missing)
            for extra in sorted(found - expected - noise):
                fail("%s block" % tag, page, "has unexpected link to %s" % extra)


# ---------------------------------------------------------------------------
# 7. The language toggle must cross trees
#
# The toggle is the only link that deliberately points at the other tree, and it
# is how a crawler discovers the Spanish site at all. build.py had a bug where
# the path rewriter dragged it back into the current tree, so every Spanish
# page's "EN" link pointed at itself.
# ---------------------------------------------------------------------------
def check_lang_toggle():
    for page in pages():
        if page in EXEMPT:
            continue
        html = read(page)
        m = re.search(r'<div class="lang-toggle">(.*?)</div>', html, re.S)
        if not m:
            fail("lang toggle", page, "has no language toggle")
            continue
        frag = m.group(1)
        links = re.findall(r'href="([^"]+)"', frag)
        if len(links) != 1:
            fail("lang toggle", page,
                 "should offer exactly one link to the other language, found %d" % len(links))
            continue
        target, in_es = links[0], page.startswith("es/")
        if in_es and target.startswith("/es/"):
            fail("lang toggle", page,
                 "'EN' link points at %s, which is still inside the Spanish tree" % target)
        if not in_es and not target.startswith("/es/"):
            fail("lang toggle", page,
                 "'ES' link points at %s, which is not in the Spanish tree" % target)


# ---------------------------------------------------------------------------
# 8. CTA routing -- every booking CTA goes through book.html (CLAUDE.md rule 6)
# ---------------------------------------------------------------------------
CAL_NON_BOOKING = re.compile(r"cal\.com/(privacy|terms|security|legal)")
BOOKING_PAGES = {rel_path(u) for u in SOURCE_TO_URLS.get("book.html", {}).values()}

cta_flagged = set()


def check_cta_routing():
    for page in pages():
        if page in EXEMPT:
            continue
        frag = block(read(page), "nav")
        if frag is None:
            continue
        if "book.html" not in hrefs(frag):
            cta_flagged.add(page)
            alt = sorted(l for l in hrefs(frag) if str(l).endswith("contact.html"))
            fail("CTA routing", page,
                 "nav CTA points at %s instead of the booking page -- booking CTAs "
                 "must route through Cal.com" % (alt[0] if alt else "no booking page at all"))

    for page in pages():
        for url in re.findall(r'href="(https?://[^"]*cal\.com[^"]*)"', read(page)):
            if page in BOOKING_PAGES or CAL_NON_BOOKING.search(url):
                continue
            fail("CTA routing", page,
                 "links straight to cal.com (%s) instead of the booking page" % url)


# ---------------------------------------------------------------------------

def main():
    check_build_current()
    check_service_worker()
    check_head()
    check_hreflang()
    check_links()
    check_bilingual_coverage()
    check_lang_toggle()
    check_cta_routing()   # must precede check_shared_blocks (populates cta_flagged)
    check_shared_blocks()

    built = pages()
    src_count = len(glob.glob("src/*.html")) + len(glob.glob("src/blog/*.html"))
    print()
    print("  madebysebby.com consistency check")
    print("  %d sources in src/  ->  %d generated pages (%d English, %d Spanish)"
          % (src_count, len(built),
             len([p for p in built if not p.startswith("es/")]),
             len([p for p in built if p.startswith("es/")])))
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
