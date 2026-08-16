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

import datetime
import glob
import importlib.util
import json
import os
import re
import sys
import unicodedata
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
# The footer is generated from a single template, so every page carries the
# same links including a link to itself. The one real asymmetry: the Santo
# Domingo landing page exists ONLY in Spanish, so the English footer cannot
# link it. Anything else differing between the two trees is a bug.
FOOTER_SELF_OMIT = {}
FOOTER_ES_ONLY = {"diseno-web-santo-domingo.html"}

BASE = "https://madebysebby.com"

failures = []
notes = []

# Schema prose that is correctly identical in both trees. Keep this short: each
# entry is a string a Spanish reader will see in English.
BRAND_SCHEMA_PROSE = set()


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
    "WhatsApp", "Cal.com", "Google", "Instagram", "LinkedIn", "SEO",
    "GitHub Pages", "Wave",
    "Ir al contenido",   # diseno-web-santo-domingo is Spanish-only by design
    "<1s", "$3K+",       # a load-time metric and a price figure -- same in both
}
NUMERIC_ONLY = re.compile(r"^[\W\d]*$")
# A price renders the same in both trees by design -- $2,500 USD is not
# untranslated copy, it is a number.
PRICE_ONLY = re.compile(r"^[\$\d,.\s\u2013\u2014-]*(USD)?[\$\d,.\s\u2013\u2014-]*$")
# The copyright line is a brand name and a year. Both are identical in Spanish;
# the sentence that follows it in the footer IS a lang pair and is checked.
COPYRIGHT_ONLY = re.compile(r"^\u00a9 \d{4} Made by Sebby\.$")


def check_bilingual_coverage():
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        name = os.path.relpath(src_path, "src")
        if name in build.PASSTHROUGH:
            continue        # precios.html is Spanish-only; 404.html stays inline
        html = read(src_path)
        # Expand the footer before checking, rather than ignoring the token:
        # the footer is real user-facing copy and has to be bilingual too. It is
        # rendered as English here because the Spanish tree is what adds links,
        # never what removes them.
        html = html.replace("{{FOOTER}}", build.footer_html("en"))
        html = build.substitute_prices(html)
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
            if len(text) < 2 or NUMERIC_ONLY.match(text) or text in NON_TRANSLATABLE \
               or PRICE_ONLY.match(text) or COPYRIGHT_ONLY.match(text):
                continue
            fail("bilingual coverage", src_path,
                 "visible text %r has no lang=\"en\"/lang=\"es\" siblings, so it "
                 "renders identically in both trees" % text[:60])


# ---------------------------------------------------------------------------
# 6b. No English descriptive alt/aria text survives onto a Spanish page
#
# These are attributes, so the bilingual sibling spans cannot carry them and
# check_bilingual_coverage cannot see them. build.py translates them from tables;
# this makes sure a NEW image added later doesn't quietly ship English alt text
# to Spanish screen readers and to Google Images.
#
# Brand names are correctly identical in both trees and are not translated.
# ---------------------------------------------------------------------------
BRAND_ALT = {
    "Made by Sebby", "Riera Law Firm", "Elite Care Recovery",
    "Riera Law Firm, Coral Gables, Florida", "Riera Law Firm: Coral Gables, Florida",
}


def check_translated_attributes():
    for page in pages():
        if not page.startswith("es/") or page in EXEMPT:
            continue
        # The language toggle is deliberately cross-language: its link to English
        # carries lang="en" and an English aria-label, so a screen reader switches
        # pronunciation and the label matches the element it labels.
        html = re.sub(r'<div class="lang-toggle">.*?</div>', "", read(page), flags=re.S)
        for attr, table in (("alt", build.ALT_ES), ("aria-label", build.ARIA_ES)):
            for m in re.finditer(r'%s="([^"]*)"' % attr, html):
                value = m.group(1)
                if not value or value in BRAND_ALT or value in table.values():
                    continue
                if value in table:
                    fail("translated attributes", page,
                         "%s=%r is still the English key from build.py %s -- the "
                         "table was not applied" % (attr, value, table))
                else:
                    fail("translated attributes", page,
                         "%s=%r has no Spanish form. Add it to %s in build.py, or "
                         "to BRAND_ALT here if it is a name that must not be translated"
                         % (attr, value, "ALT_ES" if attr == "alt" else "ARIA_ES"))


# ---------------------------------------------------------------------------
# 6c. NAP is identical everywhere it appears
#
# Local ranking depends on the site and the Google Business Profile agreeing on
# Name/Address/Phone; Google cross-references them. They did not agree -- the
# verified profile is a Miami address with a (786) number while the site's only
# structured address said Santo Domingo, DO with no phone.
#
# build.py injects build.NAP into every LocalBusiness node, so this verifies the
# injection actually reached every page and that no source snuck a second
# address in. It also checks the visible phone on the contact page, because
# Google reads the rendered page, not only the JSON-LD.
# ---------------------------------------------------------------------------
def check_nap():
    seen = 0
    for page in pages():
        for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                             read(page), re.S):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                fail("nap", page, "JSON-LD block does not parse")
                continue

            def walk(node):
                global_seen = None
                if isinstance(node, dict):
                    if node.get("@type") in build.LOCAL_TYPES:
                        for key, want in build.NAP.items():
                            got = node.get(key)
                            if got != want:
                                fail("nap", page,
                                     "%s is %r but build.NAP says %r -- the canonical "
                                     "NAP did not reach this page" % (key, got, want))
                        if node.get("@id") != build.BUSINESS_ID:
                            fail("nap", page,
                                 "business node has @id %r, expected %r so every page "
                                 "describes ONE business rather than a branch per page"
                                 % (node.get("@id"), build.BUSINESS_ID))
                        if "geo" in node:
                            fail("nap", page,
                                 "publishes geo coordinates. The address is a family "
                                 "home the Business Profile hides; coordinates would "
                                 "broadcast exactly what it hides")
                        return 1
                    return sum(walk(v) for v in node.values())
                if isinstance(node, list):
                    return sum(walk(i) for i in node)
                return 0

            seen += walk(data)

    if not seen:
        fail("nap", "(site)", "no LocalBusiness/ProfessionalService schema found anywhere")

    # The phone must be readable by a human, not only by a parser.
    phone_digits = re.sub(r"\D", "", build.NAP["telephone"])
    for page in ("contact.html", "es/contacto.html"):
        if not os.path.isfile(page):
            continue
        # Strip tags as well as script/style: a tel: href is an ATTRIBUTE, not
        # visible text, and matching against it would let the number disappear
        # from the rendered page while this check still passed.
        visible = re.sub(r"<script.*?</script>|<style.*?</style>", "", read(page), flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", visible)
        if phone_digits[-10:] not in re.sub(r"\D", "", visible):
            fail("nap", page,
                 "does not show the phone number in visible text. The page invites a "
                 "call, and Google corroborates the Business Profile from rendered copy")


# ---------------------------------------------------------------------------
# 6d. No bare price literal survives in src/
#
# The site published FOUR different answers to "what does a website cost" --
# $2,000-$8,000 on the home page, $1,500/$3,500/$7,000 on pricing, $3,000-$5,000
# on the local landing pages, $500-$2,500 on the DR one-pager. Read the Miami
# page then click Pricing and the number halved. Prices now come from
# build.PRICES, and this fails on any literal typed back into a source file.
#
# Allowed literals are figures about the MARKET, not about us: what agencies
# charge, what a client is worth, third-party survey numbers. Each is listed
# with the file it belongs to so a new one has to be justified, not just added.
# ---------------------------------------------------------------------------
MARKET_FIGURES = {
    "pricing.html": set(),
    "terms.html": {"$100"},
    "privacy.html": set(),
    # editorial: market rates, competitor figures, client lifetime values.
    #
    # Pruned 2026-08-16 down to what each page still quotes, and kept pruned by
    # check_market_figures_are_justified below. $4,500 and $8,000 lived here for
    # weeks after the copy stopped using them, which is exactly how the blog's
    # second price list slipped past a green run: the numbers were already
    # exempt before anyone typed them.
    "blog/how-much-does-a-website-cost.html": {"$300", "$500", "$1,000", "$4,000",
                                               "$5,000", "$50,000", "$3,000",
                                               "$10,000"},
    "blog/why-your-competitor-gets-calls-from-google.html": {"$3,000", "$10,000",
                                                             "$1,500", "$200", "$500"},
    "blog/what-a-website-does-for-a-law-firm.html": {"$500"},
    "blog/what-is-website-care.html": set(),
    "blog/5-signs-your-website-is-losing-clients.html": set(),
    "blog/does-your-restaurant-need-a-website.html": {"$10,000"},
    # $240 is an AI builder's annual subscription -- a competitor's price, cited
    # to compare against ours, which is the whole point of that post.
    "blog/should-i-use-ai-to-build-my-website.html": {"$240"},
    # RD$ agency comparison figures are pesos, explicitly marked, not our prices
    "diseno-web-santo-domingo.html": {"$2,500", "$10,000"},
    # Only the cost of a security breach survives here. Everything else that used
    # to sit in this set was OUR pricing, allow-listed as though it described the
    # market: edit packs, the hourly overage, annual totals, per-edit costs. That
    # made the guard blind to exactly the drift it exists to catch, so those are
    # now tokens in build.PRICES and the derived ones are computed, not typed.
    "website-care.html": {"$25,000", "$3,000"},
}
# Cents are part of the figure. Without \.\d\d the pattern matched "$503" out of
# a perfectly correct "$503.25" and then failed it for not being a canonical
# price, which is a false positive that reads exactly like a real one.
PRICE_LITERAL = re.compile(r"(?<!RD)\$[\d,]{3,}(?:\.\d\d)?")


def check_prices():
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        name = os.path.relpath(src_path, "src")
        allowed = MARKET_FIGURES.get(name, set())
        html = read(src_path)
        html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
        for m in PRICE_LITERAL.finditer(html):
            literal = m.group(0).rstrip(",")
            if literal in allowed:
                continue
            context = " ".join(unescape(re.sub(r"<[^>]+>", " ",
                               html[max(0, m.start() - 70):m.end() + 40])).split())
            fail("prices", src_path,
                 "bare literal %s -- prices come from build.PRICES so the site cannot "
                 "contradict itself. Use a token, or add it to MARKET_FIGURES in this "
                 "file if it describes the market rather than our pricing. Context: …%s…"
                 % (literal, context[:110]))

    # Every price the visitor actually sees must be one we sanctioned.
    ours = set(build.PRICES.values())
    for page in pages():
        if page in EXEMPT and page not in build.PASSTHROUGH:
            continue
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", read(page), flags=re.S)
        text = unescape(re.sub(r"<[^>]+>", " ", text))
        for literal in {m.rstrip(",") for m in PRICE_LITERAL.findall(text)}:
            src_name = URL_TO_SOURCE.get("/" + page) or URL_TO_SOURCE.get(
                "/" + page.replace("index.html", "")) or page
            if literal in ours or literal in MARKET_FIGURES.get(src_name, set()) \
               or literal in MARKET_FIGURES.get(page, set()):
                continue
            fail("prices", page, "shows %s, which is neither a canonical price nor a "
                                 "declared market figure" % literal)


# ---------------------------------------------------------------------------
# 6e. The contact form actually submits somewhere
#
# It used to be a mailto: handler that redirected to the thank-you page after
# 500ms whether or not anything sent -- so for every visitor on mobile or webmail
# it silently did nothing while telling them "thanks, we got it". This makes the
# half-configured state loud instead of silent.
# ---------------------------------------------------------------------------
def check_contact_form():
    if build.FORM_ACCESS_KEY.startswith("REPLACE_WITH"):
        fail("contact form", "build.py",
             "FORM_ACCESS_KEY is still the placeholder, so every submission fails. "
             "Get a free key at https://web3forms.com (enter the destination email, "
             "the key arrives by email) and paste it into build.FORM_ACCESS_KEY")

    for page in ("contact.html", "es/contacto.html"):
        if not os.path.isfile(page):
            continue
        html = read(page)
        if "mailto:hello@madebysebby.com?subject=" in html:
            fail("contact form", page,
                 "still builds a mailto: URL on submit -- that silently does nothing "
                 "for anyone without a configured desktop mail client")
        if "api.web3forms.com/submit" not in html:
            fail("contact form", page, "has no form endpoint")
        # The redirect must sit in the success branch, never on a timer.
        if re.search(r"setTimeout\([^)]*location\.href", html):
            fail("contact form", page,
                 "redirects to the thank-you page on a timer rather than on a "
                 "successful response, so a failed send still says it worked")
        if "generate_lead" not in html:
            fail("contact form", page,
                 "fires no GA4 conversion event, so submissions are invisible in analytics")


# ---------------------------------------------------------------------------
# 6f. No em dashes, anywhere
#
# Sebby's call, and it is a brand rule rather than a typographic one: the em dash
# has become a tell for machine-written copy, and this site sells human judgment.
# 341 of them had accumulated across 26 files before the sweep.
#
# Fail, do not auto-fix. The right replacement is a comma, a colon or a full stop
# depending on the sentence, and a blind swap produces copy that reads worse than
# what it replaced.
#
# The en dash (U+2013) is NOT covered: it is correct in ranges like 9am-6pm and
# $2,500-$3,500, and swapping it would be wrong.
# ---------------------------------------------------------------------------
EM_DASH = "\u2014"


EM_DASH_ESCAPES = ("&mdash;", "&#8212;", "&#x2014;", "\\u2014")


def check_em_dash_escapes():
    """Rule 1 covers the character, and an escape is still the character.

    The literal-only gate ran clean for months over src/contact.html, which built
    every inquiry email subject as "New inquiry \\u2014 Acme Dental". The em dash
    was in the JS as an escape, so it never appeared in the file as U+2014 and the
    check never saw it, while every lead notification carried one.
    """
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        body = read(src_path)
        for esc in EM_DASH_ESCAPES:
            if esc in body:
                fail("em dash", src_path,
                     "contains %s, an escaped em dash. Rule 1 is about the character "
                     "that reaches a reader, not how it is spelled in source." % esc)


def check_em_dashes():
    # build.py is in scope because user-facing copy now lives there too: the
    # footer template is real page text, and a gate that only watched src/
    # would have stopped seeing it the moment the footer moved.
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")
                           + ["build.py"]):
        html = read(src_path)
        hits = [m.start() for m in re.finditer(EM_DASH, html)]
        if not hits:
            continue
        context = " ".join(unescape(re.sub(r"<[^>]+>", " ",
                           html[max(0, hits[0] - 55):hits[0] + 55])).split())
        fail("em dash", src_path,
             "%d em dash%s. Replace with a comma, colon or full stop as the sentence "
             "needs -- never a blind swap. First: ...%s..."
             % (len(hits), "" if len(hits) == 1 else "es", context))


def check_copyright_year():
    """The footer year is stamped at build time and nothing rebuilds on Jan 1.

    A footer showing last year is the cheapest possible signal that a site is
    abandoned, and it is on all 52 pages at once. Rebuilding fixes it, so this
    just has to notice.
    """
    year = str(datetime.date.today().year)
    # The template writes the entity, not the literal character. Matching only
    # U+00A9 made this check silently match nothing at all, which is worse than
    # not having it: a green run that proves nothing.
    pattern = re.compile(r"(?:&copy;|\u00a9)\s*(\d{4})\s+Made by Sebby")
    stale = []
    for p in pages():
        if p in EXEMPT:
            continue
        m = pattern.search(read(p))
        if m and m.group(1) != year:
            stale.append(p)
    if stale:
        fail("copyright year", stale[0].split("/")[0] if len(stale) == 1 else "site",
             "%d page%s a copyright year that is not %s. Re-run ./build.py."
             % (len(stale), " shows" if len(stale) == 1 else "s show", year))


def check_precios_stays_spanish():
    """precios.html is Spanish-only, so every link it makes must stay Spanish.

    It is a passthrough page: rewrite_paths never runs on it, so its links are
    hand-written absolute URLs and nothing was retargeting them. All four pointed
    into the English tree, which meant a Dominican prospect who tapped "ver
    planes de mantenimiento" from a WhatsApp share landed on an English page.
    """
    page = "precios.html"
    if not os.path.exists(page):
        return
    for href in sorted(set(re.findall(r'href="([^"]+)"', read(page)))):
        if not href.startswith(BASE):
            continue
        path = href[len(BASE):] or "/"
        if path.startswith("/es/"):
            continue
        fail("precios language", page,
             "links to %s, which is in the English tree. This page is Spanish "
             "only and shared over WhatsApp, so every link has to land in /es/."
             % (path or "/"))


def check_dr_price_parity():
    """The DR one-pager must quote exactly the tier prices the main page does.

    This is the invariant the whole "no regional discount" position rests on. If
    precios.html and pricing.html ever disagree on a number, a prospect who finds
    both has a real argument, and the answer to "price by scope, not by country"
    stops being true.

    Worth stating what actually went wrong, because it was not the prices. Those
    matched. The two pages disagreed on what the price BOUGHT: $2,500 was "up to
    5 pages" on the main page and "1-3 paginas" on the DR one, so the page built
    for the Dominican market was the stingier of the two. Page counts are tokens
    now, which is the structural fix; this check covers the prices.
    """
    dr, main = "precios.html", "es/precios.html"
    if not (os.path.exists(dr) and os.path.exists(main)):
        return
    tiers = {build.PRICES[k] for k in
             ("{{PRICE_STARTER}}", "{{PRICE_CUSTOM}}", "{{PRICE_PREMIUM}}")}
    dr_text = unescape(re.sub(r"<[^>]+>", " ", read(dr)))
    missing = sorted(t for t in tiers if t not in dr_text)
    if missing:
        fail("DR price parity", dr,
             "does not quote %s, which the main Spanish pricing page does. The "
             "two pages have to agree on every tier price or the no-regional-"
             "discount position is not true." % ", ".join(missing))

    # Anything the DR page quotes that is NOT a sanctioned price is the shape a
    # regional discount would actually take.
    ours = set(build.PRICES.values()) | MARKET_FIGURES.get(dr, set())
    for literal in sorted({m.rstrip(",") for m in PRICE_LITERAL.findall(dr_text)}):
        if literal not in ours:
            fail("DR price parity", dr,
                 "quotes %s, which is not a canonical price. A number that "
                 "exists only on the DR page is exactly what a regional discount "
                 "looks like." % literal)


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

            if tag == "footer":
                if page.startswith("es/"):
                    expected |= FOOTER_ES_ONLY
                else:
                    expected -= FOOTER_ES_ONLY

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


# Pages a visitor is NOT expected to reach by clicking from the home page.
# Every one of these is deliberate, and the reason is what keeps this list from
# quietly growing into an excuse.
UNLINKED_BY_DESIGN = {
    "404.html":                      "served by GitHub Pages on a bad URL, never linked",
    "thank-you.html":                "reached only by submitting the contact form",
    "es/gracias.html":               "same, Spanish",
    "precios.html":                  "DR one-pager, shared over WhatsApp, deliberately not in nav",
    "diseno-web-santo-domingo.html": "meta-refresh stub for the old English URL",
}


def check_reachable_from_home():
    """Every page must be clickable from its own tree's home page.

    A page nobody can navigate to is a page that only exists in the sitemap.
    Before the footer was rebuilt, pricing, contact and book.html were not
    linked from the footer at all, so whole branches depended on a single nav
    entry. This walks the actual link graph instead of assuming.
    """
    def out_links(page):
        html = re.sub(r"<(script|style)\b.*?</\1>", " ", read(page), flags=re.S | re.I)
        found = set()
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith(("http", "mailto:", "tel:", "#", "data:")):
                continue
            path = href.split("#")[0].split("?")[0].lstrip("/")
            if not path or path.endswith("/"):
                path += "index.html"
            if path.endswith(".html") and os.path.exists(path):
                found.add(path)
        return found

    for root in ("index.html", "es/index.html"):
        if not os.path.exists(root):
            continue
        seen, queue = {root}, [root]
        while queue:
            for nxt in out_links(queue.pop()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        spanish = root.startswith("es/")
        for page in pages():
            if page.startswith("es/") != spanish or page in seen:
                continue
            if page in UNLINKED_BY_DESIGN:
                continue
            fail("reachability", page,
                 "cannot be reached by clicking from %s. A page nobody can "
                 "navigate to exists only in the sitemap. Link it, or add it to "
                 "UNLINKED_BY_DESIGN with the reason." % root)


def check_schema_prices():
    """Every Offer.price must equal a canonical price from build.PRICES.

    This exists because the 2026-08-13 price raise reached the visible copy and
    missed the schema entirely. For two days both pricing pages showed $2,500 to
    a human and told every crawler the floor was $1,500, in the machine-readable
    layer that Google and AI assistants trust over the visible page, on a site
    whose central claim is transparent pricing.
    """
    numeric = {v.lstrip("$").replace(",", "") for v in build.PRICES.values()}
    numeric |= {v for v in build.PRICES.values() if v.isdigit()}
    for page in pages():
        if page in EXEMPT:
            continue
        html = read(page)
        for m in re.finditer(r'"price"\s*:\s*"?([\d.]+)"?', html):
            price = m.group(1).rstrip(".0") if "." in m.group(1) else m.group(1)
            if price not in numeric and m.group(1) not in numeric:
                fail("schema price", page,
                     "Offer.price is %s, which is not a canonical price. Schema "
                     "prices must come from build.PRICES like the visible ones, "
                     "or the two drift apart silently." % m.group(1))


def check_llms_txt():
    """llms.txt is the file written specifically for AI assistants.

    It sits outside src/, so the em dash gate and every other content check
    ignored it completely. It spent an unknown period stating the business was
    based in the Dominican Republic, three days after that exact claim was
    removed from all 52 pages for contradicting the Miami NAP.
    """
    if not os.path.exists("llms.txt"):
        return
    text = read("llms.txt")
    hits = text.count(EM_DASH)
    if hits:
        fail("llms.txt", "llms.txt",
             "%d em dash%s. Rule 1 applies here too, and this is the file AI "
             "assistants read." % (hits, "" if hits == 1 else "es"))
    for claim in ("based in the Dominican Republic", "based in Santo Domingo",
                  "based in the DR"):
        if claim.lower() in text.lower():
            fail("llms.txt", "llms.txt",
                 "says %r, which contradicts the Miami NAP every page declares."
                 % claim)
    digits = re.sub(r"\D", "", build.NAP["telephone"])
    if digits and digits[-10:] not in re.sub(r"\D", "", text):
        notes.append("llms.txt does not carry the canonical phone number")


def check_spanish_schema_is_spanish():
    """JSON-LD prose that is byte-identical in both trees was never translated.

    SCHEMA_ES is a dict keyed on the exact English string, so every edit to an
    English schema sentence silently orphans its Spanish translation: the lookup
    misses, the walk falls through, and the Spanish page ships English prose to
    the one consumer that cannot see the visible copy. On 2026-08-15 all three
    Offer descriptions on /es/precios.html were English for exactly this reason,
    because "bilingual option" had become "bilingual as standard" upstream.

    Identity across twins is the precise test. Brand names and URLs are short or
    listed, so a long string that survives translation unchanged is a bug.
    """
    keys = set(build.PROSE_KEYS)

    def prose(page):
        out = {}
        for block in re.findall(
                r'<script type="application/ld\+json">(.*?)</script>', read(page), re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue

            def walk(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k in keys and isinstance(v, str):
                            out.setdefault(v, k)
                        else:
                            walk(v)
                elif isinstance(node, list):
                    for i in node:
                        walk(i)
            walk(data)
        return out

    for source in build.PAGES:
        if build.PAGES[source].get("en", source) is None:
            continue                      # Spanish-only page, no twin to compare
        en_page = rel_path(build.url_for(source, "en"))
        es_page = rel_path(build.url_for(source, "es"))
        if not (os.path.isfile(en_page) and os.path.isfile(es_page)):
            continue
        english = set(prose(en_page))
        for text, key in prose(es_page).items():
            if len(text.split()) < 5:
                continue                  # names, brands, short labels
            if text in BRAND_SCHEMA_PROSE:
                continue
            if text in english:
                fail("spanish schema", es_page,
                     "JSON-LD %s is identical to the English page, so it was never "
                     "translated and Google reads this Spanish page as English: %r. "
                     "Add it to SCHEMA_ES in build.py, keyed with the same {{TOKENS}} "
                     "the source uses." % (key, text[:90]))


def check_shortlinks():
    """Vanity short links must exist, stay unindexed, and land somewhere real.

    These are outside pages(), because they are not site pages, so nothing else
    in this file looks at them. That is precisely why they need their own check:
    a stub that silently stopped redirecting would keep passing every other test
    while quietly sending an Instagram bio to a blank page.
    """
    for path, target in build.SHORTLINKS.items():
        if not os.path.exists(path):
            fail("shortlink", path, "declared in build.SHORTLINKS but not generated")
            continue
        html = read(path)
        if "noindex" not in html:
            fail("shortlink", path,
                 "is missing noindex. A tracked short link indexed as a real page "
                 "competes with the destination it points at.")
        # The canonical must be the clean URL. A canonical carrying campaign tags
        # is how a tracked URL ends up indexed as the canonical one.
        canon = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html)
        if not canon:
            fail("shortlink", path, "has no canonical")
        elif "utm_" in canon.group(1):
            fail("shortlink", path,
                 "canonical %s carries campaign tags. It must point at the clean "
                 "destination." % canon.group(1))
        if "http-equiv=\"refresh\"" not in html.replace("'", '"'):
            fail("shortlink", path, "has no meta refresh, so it redirects nowhere")
        # The destination has to be a real page in this build.
        dest = target.split("?")[0].split("#")[0].lstrip("/") or "index.html"
        if dest.endswith("/"):
            dest += "index.html"
        if not dest.endswith(".html"):
            dest = dest.rstrip("/") + "/index.html" if dest else "index.html"
        if not os.path.exists(dest):
            fail("shortlink", path, "points at %s, which does not exist" % target)


def check_primary_buttons_go_somewhere():
    """A primary button must navigate, not scroll.

    The home page hero said "Work with Sebby" and scrolled to the closing
    section, where a SECOND "Work with Sebby" then went to the booking page.
    Two clicks to do one thing, with the intent already unambiguous at the
    first. The Website Care section did the same, and pitched Website Care
    without linking the Website Care page anywhere.

    The nav CTA check above never saw either, because it only inspects <nav>.
    A ghost button is exempt: those are secondary, and "See my work" scrolling
    to the work section on the same page is correct behaviour.
    """
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        for m in re.finditer(r"<a\b([^>]*)>", read(src_path)):
            attrs = m.group(1)
            cls = re.search(r'class="([^"]*)"', attrs)
            href = re.search(r'href="([^"]*)"', attrs)
            if not (cls and href):
                continue
            classes = cls.group(1).split()
            if "btn" not in classes or "btn-ghost" in classes:
                continue
            if href.group(1).startswith("#"):
                fail("CTA routing", src_path,
                     'primary button links to %s, so it scrolls instead of '
                     "navigating. Point it at the page it is asking for. Use "
                     "btn-ghost if it really is secondary in-page navigation."
                     % href.group(1))


# ---------------------------------------------------------------------------
# 9. Spanish that lost its diacritics
#
# 28 of these reached production across five Spanish pages while every check
# above ran green, because none of them reads the copy. The care plan page, the
# one that sells the recurring revenue, carried "Tu manejas tu negocio" as its
# H1 and spelled its own product tier "Estandar" five times in the pricing card
# and "Estandar" again in the comparison table, with "Estandar" appearing
# correctly in the FAQ four hundred lines below. pricing.html sells that page
# with "written by a native speaker, never machine-translated", and stripped
# diacritics are the single most recognisable signature of machine output, so
# the claim was falsifiable on the site in about thirty seconds.
#
# The whole difficulty is precision. A naive word list fails on "esta", "el",
# "tu", "mas", "se", "si", "aun" and "solo", every one of which is a real word
# whose accented form is a DIFFERENT real word, and a check that cries wolf on
# correct Spanish gets commented out within a week. So this runs three passes,
# each with its own reason to be trusted:
#
#   A. words with no valid unaccented spelling in Spanish at all
#   B. one word spelled two ways on a single page, which is wrong either way
#   C. a closing "?" with no opening "¿"
#
# It reads the GENERATED Spanish pages rather than src/, on purpose: that is
# where build.py's own Spanish lands too (the <head> strings in PAGES, ALT_ES,
# ARIA_ES, SCHEMA_ES), and a missing accent in a <title> is the one a searcher
# sees first. The failure names the source file to fix.
# ---------------------------------------------------------------------------

# Every entry here is a misspelling in any context. Words with a legitimate
# unaccented twin are deliberately absent and listed in DUAL_FORM below.
#
# Two rules keep this list honest, and both were learned by getting them wrong:
# a -cion noun loses its accent in the plural (informacion is an error,
# informaciones is not), and a derived form usually loses it too (rapido needs
# one, rapidez does not; posicion needs one, posicionamiento does not).
NEEDS_ACCENT = set("""
diseno disenos disenar disenador disenadora disenadores disenada disenado
senal senales senala senalan senalar senalado
estandar caracteristica confia confian confio
musica panico tecnica tecnico tecnicas tecnicos
continuacion configuracion capacitacion basica basico basicas basicos
dia dias republica bilingue bilingues
facil faciles facilmente dificil dificiles teoria teorias
segun despues ademas tambien aqui alli ahi alla aca asi estan
pagina paginas rapido rapida rapidos rapidas rapidamente
informacion atencion seccion accion razon version
mayoria garantia garantias tecnologia tecnologias
movil moviles unico unica unicos unicas unicamente ultimos
proximo proxima minimo minima maximo maxima
metodo metodos dolares ingles espanol espanola
ano anos pequeno pequena pequenos pequenas
dueno duena duenos duenas compania companias manana
exito exitos interes terminos numeros telefonos
sesion opcion descripcion optimizacion conversion navegacion reputacion
gestion decision direccion posicion produccion publicacion
recomendacion resolucion actualizacion aplicacion integracion migracion
validacion traduccion inversion comunicacion creacion educacion evaluacion
generacion instalacion presentacion programacion promocion proteccion
reduccion relacion reparacion revision solucion transaccion ubicacion
duracion medicion presion politica politicas
automatico automatica automaticamente electronico electronica
economico economica organico organica estrategico estrategica
historico historica clasico clasica dinamico dinamica estatico estatica
telefonico telefonica juridico juridica juridicos juridicas
academico academica grafico grafica graficos graficas
analisis sintoma sintomas credito creditos debito parrafo parrafos
util utiles inutil sabado miercoles quiza quizas
podria deberia haria tendria sera estara hara podra
""".split())

# Unaccented these are only valid as a conjugated verb: "yo trafico", "el
# publica". Flagged only directly after an article or possessive, which forces
# the noun reading and makes "el trafico" unambiguously wrong.
NEEDS_ACCENT_AS_NOUN = set("""
trafico numero telefono ultimo ultima ultimas publico publica practica
practicas practico medico medica medicos medicas critico critica calculo
limite termino titulo titulos articulo articulos capitulo diagnostico
""".split())
DETERMINERS = set("""
el la los las un una unos unas del al su sus tu tus mi mis nuestro nuestra
este esta estos estas ese esa esos esas cada otro otra otros otras mismo misma
""".split())

# Pass B compares a page against itself, so it needs to know which words really
# do appear both ways in correct Spanish. These are the function words the brief
# warns about: "el" and "él", "tu" and "tú", "si" and "sí", "mas" and "más".
DUAL_FORM = set("""
el tu mi si se de te mas aun solo esta este ese esos esas estas aquel aquella
que como cuando donde quien cual cuanto cuanta cuantos cuantas porque
esto eso aquello sino
""".split())

WORD = re.compile(r"[0-9A-Za-zÀ-ɏ]+")
# Slugs are deliberately unaccented (/es/diseno-web-santo-domingo.html), so any
# token that is part of a path or a domain is not copy and is not checked.
URLISH = re.compile(r"https?://\S+|\S*/\S*|\b[\w.-]+\.(?:html|com|net|org|dev|io)\b")
PROSE_META = re.compile(
    r'<meta[^>]+(?:name|property)="(?:description|og:title|og:description|'
    r'og:site_name|twitter:title|twitter:description)"[^>]*content="([^"]*)"')
JSONLD_PROSE = ("name", "description", "headline", "slogan", "reviewBody",
                "text", "articleBody", "alternateName", "caption")
# The accent falls on the final syllable, which is where Spanish puts the
# preterite and the second person: "construyo" and "construyo", "estas" and
# "estas" are both correct and both common on one page. Pass A already covers
# the words in this shape that ARE errors ("segun", "tambien"), so pass B can
# skip the shape entirely and stay silent on correct copy.
FINAL_STRESS = re.compile(r"[áéíóú][sn]?$")


def unaccented(word):
    return "".join(c for c in unicodedata.normalize("NFD", word)
                   if unicodedata.category(c) != "Mn")


def spanish_pages():
    """Everything a Spanish reader sees, including the pages exempt elsewhere.

    es/gracias.html is in MINIMAL_PAGES and skipped by most checks here. It is
    also where "¡Mensaje enviado!" is missing its opening exclamation mark, so
    the exemptions that exist for structure must not extend to the copy.
    """
    found = glob.glob("es/*.html") + glob.glob("es/blog/*.html")
    for extra in ("precios.html", "diseno-web-santo-domingo.html"):
        if os.path.isfile(extra):
            found.append(extra)
    return sorted(found)


def readable_text(html):
    """Copy a human reads: body text, prose meta, alt/aria, JSON-LD prose."""
    parts = list(PROSE_META.findall(html))
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    if title:
        parts.append(title.group(1))
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                         html, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str):
                        if key in JSONLD_PROSE:
                            parts.append(value)
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(data)
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ",
                  html, flags=re.S)
    parts += re.findall(r'\b(?:alt|aria-label|title)="([^"]*)"', body)
    parts.append(re.sub(r"<[^>]+>", " ", body))
    return URLISH.sub(" ", unescape(" ".join(parts)))


def source_of(page):
    return "src/" + URL_TO_SOURCE.get(
        "/" + page, URL_TO_SOURCE.get("/" + page.replace("index.html", ""), page))


def check_spanish_diacritics():
    for page in spanish_pages():
        text = readable_text(read(page))
        tokens = list(WORD.finditer(text))
        for i, m in enumerate(tokens):
            word = m.group(0).lower()
            previous = tokens[i - 1].group(0).lower() if i else ""
            if word in NEEDS_ACCENT or (word in NEEDS_ACCENT_AS_NOUN
                                        and previous in DETERMINERS):
                context = " ".join(text[max(0, m.start() - 45):m.end() + 45].split())
                fail("spanish diacritics", source_of(page),
                     "%s renders %r, which is not a Spanish word without its "
                     "accent. Fix the Spanish copy in the source. Context: ...%s..."
                     % (page, m.group(0), context[:110]))

        forms = {}
        for m in WORD.finditer(text):
            word = m.group(0).lower()
            forms.setdefault(unaccented(word), set()).add(word)
        for base, spellings in sorted(forms.items()):
            if len(spellings) < 2 or len(base) < 4 or base in DUAL_FORM:
                continue
            accented = [s for s in spellings if s != unaccented(s)]
            if not accented or all(FINAL_STRESS.search(s) for s in accented):
                continue
            fail("spanish diacritics", source_of(page),
                 "%s spells one word two ways: %s. One of them is wrong on a "
                 "page that sells copy written by a native speaker."
                 % (page, " and ".join(sorted(spellings))))


def check_spanish_punctuation():
    """Spanish opens a question. English does not, and the copy forgets.

    "El resultado?" and "En el plan Esencial o solo necesitas un cambio
    puntual?" both shipped, and the second is inside the FAQ on the page that
    sells the care plans. Counting is enough to catch it and cannot misfire on
    correct copy, because correct copy pairs every mark.
    """
    for page in spanish_pages():
        text = readable_text(read(page))
        for closing, opening, name in (("?", "¿", "question"),
                                       ("!", "¡", "exclamation")):
            if text.count(closing) != text.count(opening):
                fail("spanish punctuation", source_of(page),
                     "%s has %d closing %s mark%s and %d opening. Spanish opens "
                     "them too." % (page, text.count(closing), name,
                                    "" if text.count(closing) == 1 else "s",
                                    text.count(opening)))


# ---------------------------------------------------------------------------
# 10. MARKET_FIGURES is an allowlist, and an allowlist is a place bugs hide
#
# check_prices above already reads every price literal in src/. It still exited
# 0 the day blog/how-much-does-a-website-cost.html published a three tier table
# at $3,000 / $4,500 / $8,000 against real prices of $2,500 / $5,000 / $9,000,
# because all three numbers were sitting in
# MARKET_FIGURES["blog/how-much-does-a-website-cost.html"] under a comment
# reading "market rates, competitor figures, client lifetime values". They were
# none of those. They were a second price list for our own packages, and once a
# number is in that set it is invisible to BOTH halves of check_prices, the src
# pass and the rendered pass, permanently and without expiry.
#
# So the allowlist needs two things it did not have.
#
# A. An entry has to still be in use. Nothing noticed when the copy stopped
#    quoting a number, so the exemption outlived the sentence that earned it and
#    the same wrong table could be pasted back in tomorrow to another green run.
#    Delete the figure from the copy, delete it here, in the same commit.
#
# B. An entry has to read like a figure about someone else. A market rate says
#    who charges it: an agency, a freelancer, a template subscription, the
#    typical range. A number sitting in a table of tiers with a plan name next
#    to it is our price no matter which file it is in.
# ---------------------------------------------------------------------------
THIRD_PARTY = re.compile(
    r"\b(agenc(?:y|ies)|agencias?|freelancers?|competitors?|competencia|market"
    r"|mercado|typical(?:ly)?|t[ií]pic[oa]s?|average|promedio|industry|industria"
    r"|DIY|Wix|Squarespace|Shopify|WordPress|template|plantilla|others?"
    r"|elsewhere|quotes?|cotiza|charge[sd]?|cobran?|cuestan?|worth|vale"
    r"|lifetime value|breach|hack(?:ed)?|recover|subscription|suscripci[oó]n"
    r"|retainer|per year|al a[nñ]o|anual|range|rango|somewhere between|entre"
    # A liability cap in the terms is not a market rate and not a price. It is
    # a contract number, and it is the third legitimate kind.
    r"|liabilit(?:y|ies)|liable|claim|reclamo|responsabilidad|indemn\w*)\b",
    re.I)


def check_market_figures_are_justified():
    for name, allowed in sorted(MARKET_FIGURES.items()):
        src_path = os.path.join("src", name)
        if not os.path.isfile(src_path):
            fail("prices", src_path,
                 "has entries in MARKET_FIGURES but no longer exists in src/. "
                 "Remove the entry.")
            continue
        # Presence is measured exactly the way check_prices measures it, or the
        # two disagree about what "still quoted" means and this reports a
        # phantom stale entry for a literal that only lives in a script.
        raw = re.sub(r"<style.*?</style>", "", read(src_path), flags=re.S)
        present = {m.group(0).rstrip(",") for m in PRICE_LITERAL.finditer(raw)}
        text = unescape(re.sub(r"<[^>]+>", " ",
                               re.sub(r"<script.*?</script>", " ", raw, flags=re.S)))
        for stale in sorted(allowed - present):
            fail("prices", src_path,
                 "MARKET_FIGURES still exempts %s, which this page no longer "
                 "quotes. A dead exemption is how the same wrong table gets "
                 "pasted back in to another green run. Remove it here in the "
                 "same commit that removed it from the copy." % stale)
        for m in PRICE_LITERAL.finditer(text):
            literal = m.group(0).rstrip(",")
            if literal not in allowed:
                continue        # check_prices already fails on it
            window = text[max(0, m.start() - 200):m.end() + 160]
            if THIRD_PARTY.search(window):
                continue
            context = " ".join(text[max(0, m.start() - 70):m.end() + 70].split())
            fail("prices", src_path,
                 "%s is exempted as a market figure, but nothing around it says "
                 "whose figure it is. A market rate names an agency, a template "
                 "subscription or a typical range; anything else reads as our "
                 "price, which is how a second price list shipped. Context: "
                 "...%s..." % (literal, context[:110]))


# ---------------------------------------------------------------------------
# 11. A statistic presented as fact needs a source
#
# Four numbers sit in display type across the top of website-care.html: 30,000+
# websites hacked every day, $3K+ to recover from a breach, 53% of visitors
# leave after 3 seconds, 75% judge credibility by design. None of them says who
# measured it, in either language, on the page that asks for a recurring
# payment. Roughly fifteen more sit in the blog.
#
# The site already knows how to do this properly. why-your-competitor,
# does-your-restaurant and what-a-website-does-for-a-law-firm each put a
# <p class="source">Source: BrightLocal ...</p> under the number, naming Moz,
# Clio, TouchBistro, Clutch and the National Restaurant Association. Sourcing
# here is inconsistent by page, not absent as a habit, so this check is mostly
# asking the weak pages to match the strong ones.
#
# Precision is the whole game, so this deliberately does not fire on:
#   100%             "no method of storage is 100% secure", "Yes, 100%"
#   50%              "50% upfront to start, 50% on launch"
#   8%               "Save ~8%" on the annual plan
#   1,000            "for a business that gets 1,000 visitors a month"
#   ~30 mi           the drive to Fort Lauderdale
# A figure has to be attached to a population ("of people", "de las visitas")
# to count as a statistic at all, and a round count has to be at least ten
# thousand to count as large. Everything else is arithmetic or a spec.
# ---------------------------------------------------------------------------
STAT_FIGURE = re.compile(r"(?<![\d.,$])(?!100\s?%)\d{1,3}\s?%"
                         r"|(?<![\d.,$])\d{2,3}(?:,\d{3})+\+?"
                         r"|\$\s?\d{1,3}(?:\.\d+)?\s?[KM]\+")
POPULATION = re.compile(
    r"\b(people|person|visitors?|users?|clients?|customers?|consumers?|buyers?"
    r"|shoppers?|diners?|patients?|searchers?|searches|traffic|businesses|owners?"
    r"|firms?|websites?|sites?|adults?|respondents?|prospects?|leads|companies"
    r"|personas|gente|visitantes|visitas|usuarios?|clientes?|compradores"
    r"|comensales|pacientes|b[uú]squedas|tr[aá]fico|negocios|empresas|bufetes"
    r"|sitios|adultos|encuestados|due[nñ]os|propietarios)\b", re.I)
CITED_SOURCES = ("Clio", "BrightLocal", "Moz", "TouchBistro", "iLawyerMarketing",
                 "BizIQ", "MyCase", "National Restaurant Association", "Andava",
                 "On The Map", "Clutch", "Martindale", "Avvo", "Statista",
                 "Nielsen", "HubSpot", "Pew", "Census", "Censo", "Sucuri",
                 "Wordfence", "Verizon")
ATTRIBUTION = re.compile(
    r"\b(?:according to|research (?:from|by|shows)|a study|studies (?:show|find)"
    r"|survey|reports? (?:from|by)|data from|source|seg[uú]n|de acuerdo con"
    r"|un estudio|una encuesta|datos de|fuente|investigaci[oó]n de"
    r"|" + "|".join(re.escape(s) for s in CITED_SOURCES) + r")\b", re.I)
SENTENCE_END = re.compile(r"[.!?;]")
STAT_BLOCK = re.compile(r'<(\w+)[^>]*class="[^"]*\bstat[\w-]*\b[^"]*"', re.I)


def check_statistics_are_sourced():
    for src_path in sorted(glob.glob("src/*.html") + glob.glob("src/blog/*.html")):
        html = read(src_path)

        # Pass A: a number set in display type. The class name is the site's own
        # signal that this is a claim, not a detail, and every correctly built
        # one already carries class="source" inside it or right after it.
        blocks = []
        for m in STAT_BLOCK.finditer(html):
            _, close = build.matching_close(html, m.start(), m.group(1))
            blocks.append((m.start(), close))
        for start, close in blocks:
            fragment = html[start:close]
            if any(s > start and e <= close for s, e in blocks if (s, e) != (start, close)):
                continue        # a container; the inner cards are checked
            visible = unescape(re.sub(r"<[^>]+>", " ", fragment))
            if not STAT_FIGURE.search(visible):
                continue        # "~30 mi" is a distance, not a statistic
            trailing = unescape(re.sub(r"<[^>]+>", " ", html[close:close + 420]))
            if 'class="source"' in fragment or 'class="source"' in html[close:close + 420] \
               or ATTRIBUTION.search(visible + " " + trailing):
                continue
            fail("unsourced statistic", src_path,
                 "a display statistic carries no source: %r. Put a "
                 '<p class="source"> under it naming who measured it, in both '
                 "languages, the way why-your-competitor-gets-calls-from-google "
                 "does, or take the number out."
                 % " ".join(visible.split())[:80])

        # Pass B: the same claim in running copy. Display blocks are removed
        # first so a sourced one is not reported twice from two directions.
        stripped = html
        for start, close in reversed(blocks):
            stripped = stripped[:start] + " " + stripped[close:]
        body = re.search(r"<body.*?</body>", stripped, re.S)
        body = body.group(0) if body else stripped
        body = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ",
                      body, flags=re.S)
        text = unescape(re.sub(r"<[^>]+>", " ", body))
        for m in STAT_FIGURE.finditer(text):
            ahead = text[m.end():m.end() + 45]
            stop = SENTENCE_END.search(ahead)
            if stop:
                ahead = ahead[:stop.start()]
            if not POPULATION.search(ahead):
                continue
            if ATTRIBUTION.search(text[max(0, m.start() - 260):m.end() + 180]):
                continue
            context = " ".join(text[max(0, m.start() - 55):m.end() + 85].split())
            fail("unsourced statistic", src_path,
                 "%s is stated as fact with nothing nearby saying who measured "
                 "it. Name the source in the same paragraph, in both languages, "
                 "or drop the number. Context: ...%s..."
                 % (m.group(0), context[:120]))


# ---------------------------------------------------------------------------
# 12. Nothing in <head> may block the render
#
# CLAUDE.md rule 10 says all CSS and JS is inline, no external stylesheets or
# script files, and the reason is performance. 51 of 52 pages then shipped a
# blocking <link rel="stylesheet"> to fonts.googleapis.com, which Lighthouse
# scores at roughly 1,600 ms of both FCP and LCP, about five times what the GA4
# tag costs. It survived because the rule lived in prose and nothing enforced
# it, to the point where a prior audit recorded "zero render-blocking
# resources" while the request was in every <head> on the site.
#
# GA4 is exempt by being async, which is the point: the test is whether the
# browser has to wait, not who owns the domain.
# ---------------------------------------------------------------------------
def check_render_blocking():
    offenders = {}
    for page in pages():
        head = re.search(r"<head.*?</head>", read(page), re.S)
        if not head:
            continue
        head = head.group(0)
        # A stylesheet inside <noscript> only applies when scripting is off, so
        # it never blocks the render for a normal visitor. It is the required
        # other half of the media="print" onload pattern, and counting it made
        # this check fire on all 51 pages that had just been correctly fixed.
        head = re.sub(r"<noscript\b.*?</noscript>", " ", head, flags=re.S | re.I)
        for m in re.finditer(r"<link\b[^>]*>", head):
            tag = m.group(0)
            if not re.search(r'rel="?stylesheet', tag):
                continue
            href = re.search(r'href="([^"]+)"', tag)
            if not href or not href.group(1).startswith(("http://", "https://", "//")):
                continue
            # media="print" plus an onload swap is the standard non-blocking
            # pattern and is fine.
            if 'media="print"' in tag and "onload" in tag:
                continue
            offenders.setdefault(href.group(1).split("?")[0], []).append(page)
        for m in re.finditer(r"<script\b[^>]*\bsrc=\"(https?:)?//[^\"]+\"[^>]*>", head):
            tag = m.group(0)
            if "async" in tag or "defer" in tag:
                continue
            src = re.search(r'src="([^"]+)"', tag)
            offenders.setdefault(src.group(1).split("?")[0], []).append(page)

    for url, hit in sorted(offenders.items()):
        fail("render blocking", "%d page%s" % (len(hit), "" if len(hit) == 1 else "s"),
             "block on %s before anything paints. CLAUDE.md rule 10 says all CSS "
             "and JS is inline; this is the one request that is not. Inline an "
             "@font-face block with self-hosted files, or load it with "
             'media="print" onload="this.media=\'all\'". First: %s'
             % (url, hit[0]))


# ---------------------------------------------------------------------------

def main():
    check_build_current()
    check_service_worker()
    check_head()
    check_hreflang()
    check_links()
    check_bilingual_coverage()
    check_translated_attributes()
    check_nap()
    check_prices()
    check_contact_form()
    check_em_dashes()
    check_em_dash_escapes()
    check_copyright_year()
    check_precios_stays_spanish()
    check_dr_price_parity()
    check_lang_toggle()
    check_cta_routing()
    check_primary_buttons_go_somewhere()
    check_reachable_from_home()
    check_shortlinks()
    check_schema_prices()
    check_spanish_schema_is_spanish()
    check_spanish_diacritics()
    check_spanish_punctuation()
    check_market_figures_are_justified()
    check_statistics_are_sourced()
    check_render_blocking()
    check_llms_txt()   # must precede check_shared_blocks (populates cta_flagged)
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
