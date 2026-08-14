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
    # care-plan annual totals and edit packs -- derived, not tier prices
    "pricing.html": {"$125", "$49", "$129", "$199"},
    "terms.html": {"$100"},
    "privacy.html": set(),
    # editorial: market rates, competitor figures, client lifetime values
    "blog/how-much-does-a-website-cost.html": {"$300", "$500", "$1,000", "$4,000",
                                               "$4,500", "$5,000", "$8,000", "$50,000",
                                               "$249", "$99", "$3,000", "$10,000", "$200"},
    "blog/why-your-competitor-gets-calls-from-google.html": {"$3,000", "$10,000",
                                                             "$1,500", "$200", "$500"},
    "blog/what-a-website-does-for-a-law-firm.html": {"$3,000", "$10,000", "$500"},
    "blog/what-is-website-care.html": {"$99", "$249", "$500", "$3,000"},
    "blog/5-signs-your-website-is-losing-clients.html": {"$500", "$3,000"},
    "blog/does-your-restaurant-need-a-website.html": {"$10,000", "$500", "$3,000"},
    # $240 is an AI builder's annual subscription -- a competitor's price, cited
    # to compare against ours, which is the whole point of that post.
    "blog/should-i-use-ai-to-build-my-website.html": {"$240"},
    # RD$ agency comparison figures are pesos, explicitly marked, not our prices
    "diseno-web-santo-domingo.html": {"$150,000", "$500,000", "$2,500", "$10,000"},
    "website-care.html": {"$1,089", "$2,739", "$6,039", "$1,188", "$228", "$503",
                          "$125", "$129", "$199", "$25,000", "$49", "$3,000"},
}
PRICE_LITERAL = re.compile(r"(?<!RD)\$[\d,]{3,}")


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
    check_copyright_year()
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
