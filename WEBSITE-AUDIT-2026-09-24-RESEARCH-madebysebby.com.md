# Site Audit — Made by Sebby
Date: 2026-09-24
Auditor: Claude (Sonnet 4.6) + Explore subagent
Status: FINDINGS VERIFIED against both src/ and generated output

---

## FALSE POSITIVES (already handled by build.py)

These were flagged by the subagent reading src/ pre-build, but are CORRECT in the generated output:

- **hreflang ES links** — src/ has self-references as a placeholder. build.py rewrites them to `/es/[slug].html` correctly in all generated pages. VERIFIED.
- **Index schema address** — Generated index.html correctly shows Miami, FL, US. VERIFIED.
- **JSON-LD token strings** — build.py resolves all `{{TOKEN}}` placeholders before writing output. VERIFIED in generated files.

These are NOT bugs. Do not "fix" them in src/ or build.py.

---

## REAL BUGS (fix these)

### 🔴 CRITICAL

#### 1. `src/pricing.html` — Calculator email placeholder contains raw HTML tags
**Line:** ~903 in src/pricing.html
**Problem:** `placeholder="<span lang="en"></span><span lang="es"></span>you@business.com"`
The bilingual span trick cannot work inside an HTML attribute. Every browser renders this as literal text: `<span lang="en"></span><span lang="es"></span>you@business.com` visible as placeholder text in the input field.
**Fix:** Strip the span tags. Use `placeholder="you@business.com"` — no bilingual handling needed for a placeholder. Or use `data-placeholder-en` / `data-placeholder-es` with JS if translation is required.
**Checker:** Does not currently catch this.

---

### 🟠 HIGH

#### 2. `src/precios.html` — Hardcoded `<footer>` instead of `{{FOOTER}}` token
**Line:** 427
**Problem:** The only page in src/ with a hand-written `<footer>…</footer>` block. If the footer template changes (new link, copyright year, contact info, Sebby IT referral line), this page silently diverges.
**Fix:** Replace the hardcoded footer with `{{FOOTER}}` token.
**Note:** This page is noindex/WhatsApp-shared, so SEO impact is zero — but a stale footer is a professionalism problem if a client is sent the link.

#### 3. `src/diseno-web-santo-domingo.html` — Hardcoded prices in body copy AND schema
**Lines:** 397, 421, 564, 576
**Problem:** `US$2,500` and `$99/mes` appear as bare literals in both the JSON-LD FAQ schema and the body copy. If Starter price or Essentials care price changes, this page will contradict the pricing page and `check_dr_price_parity` may or may not catch it since these are in the DR-market page.
**Instances:**
- Schema FAQ: `"desde US$2,500"` (line 397)
- Schema FAQ: `"$99/mes"` (line 421)
- Body copy: `"desde US$2,500"` (line 564)
- Body copy: `"$99/mes"` (line 576)
**Fix:** Replace with `{{PRICE_STARTER}}` and `{{CARE_ESSENTIALS}}` tokens. The RD$ competitor range (`RD$150,000–RD$500,000`) is a market-context figure, not a service price — leave those as-is but add a comment flagging them for periodic review.

#### 4. `src/diseno-web-abogados-santo-domingo.html` — Hardcoded `$99/mes` in schema
**Line:** 444
**Problem:** JSON-LD FAQ answer hardcodes `$99/mes` for care plan starting price. Body copy (line 613) correctly uses `{{PRICE_STARTER}}` — the schema was never updated to match.
**Fix:** Replace schema `$99/mes` with `{{CARE_ESSENTIALS}}` token.

#### 5. `src/web-design-for-law-firms.html` — Hardcoded `$99/month` in JSON-LD FAQ schema
**Problem:** FAQ answer inside `<script type="application/ld+json">` hardcodes the care plan starting price. Same issue as #4 but on the English law firms landing page.
**Fix:** Replace with `{{CARE_ESSENTIALS}}` token in the schema string.

---

### 🟡 MEDIUM

#### 6. `src/services.html` — SEO service card implies it's bookable as standalone
**Problem:** The "Get Found on Google" card on services.html has a CTA that routes to `book.html` with no caveat. `llms.txt` and every other page state SEO/GBP is "bundled only, not sold as a standalone service." A visitor reading this page could book a call specifically expecting standalone SEO — then feel misled.
**Fix options:**
- Add a one-line caveat: "Included with every build and available inside care plans."
- Change the card CTA to link to pricing.html instead of book.html, where the bundled nature is clearer.
- Leave the CTA but add a parenthetical in the card body: "Bundled with every build, not sold separately."

#### 7. `src/website-care.html` — Hardcoded market comparison figures
**Lines:**
- `$3,000-$25,000` (line 1035) — "one security breach" cost comparison
- `$90 and $250 a month` (line 1137) — competitor price range
**Problem:** These are editorial market figures, not service prices, so they can't be made into standard tokens. They will age silently.
**Fix:** Add inline HTML comments (`<!-- market figure: review annually -->`) flagging them for the content review cycle. Optionally move to a `MARKET_FIGURES` allowlist in the checker.

#### 8. `src/precios.html` — Hardcoded `$15–$25/mes` hosting footnote
**Line:** ~389
**Problem:** A footnote about hosting costs is hardcoded. Small risk but inconsistent with the no-bare-price-literal rule.
**Fix:** Either add to `PRICES` as `{{HOSTING_RANGE}}` or annotate as an intentional market figure (add to `MARKET_FIGURES` in check-consistency.py).

---

### 🔵 LOW / ENHANCEMENTS

#### 9. `src/about.html` — No testimonials
**Problem:** The about page is purely bio. Jorge Riera and John Pierce (or OrthoFlow) have strong quotes elsewhere. A trust-building page with zero social proof is a missed opportunity.
**Fix:** Add one or two quotes with attribution near the bottom, before the CTA.

#### 10. Blog citations from 2024 — flag for annual review
Pages with 2024-dated citations:
- `blog/why-your-competitor-gets-calls-from-google.html` — SOCi 2024, BrightLocal 2024
- `blog/does-your-restaurant-need-a-website.html` — NRA 2025
**Fix:** No action now. Add to Q1 2027 content review: check if 2026 editions of these studies have been published.

#### 11. `blog/how-much-does-a-website-cost.html` — Market comparison figures
**Problem:** `$300 DIY site`, `$50–$500/month` maintenance, `$1,000–$5,000+` e-commerce additions are hardcoded editorial figures. Not service prices, so cannot be tokens — but they will age.
**Fix:** Annotate with `<!-- market figure: review annually -->` comments.

#### 12. `src/website-care.html` — Verizon 2025 DBIR citation
**Line:** ~810
**Problem:** Correctly cited and accurate. The 2026 DBIR is likely available now.
**Fix:** Check if the 2026 DBIR has been published; update citation and stat if a more current figure exists.

---

## STALE PHOTOS / IMAGES CHECK

| Image | Status | Notes |
|---|---|---|
| `assets/hero-orthoflow.webp` | ✅ Fresh | Updated 2026-09-23 |
| `assets/hero-orthoflow.jpg` | ✅ Fresh | Updated 2026-09-23 |
| `assets/hero-rieralaw.webp` | ⚠️ Unknown | Verify still matches live rieralaw.com |
| `assets/hero-elitecare.webp` | ⚠️ Unknown | Verify still matches live elitecare site |
| `assets/og-image.jpg` | ⚠️ Shared | All pages use one OG image — no per-page OG images |
| `assets/sebby.webp` | ⚠️ Unknown | Profile photo — is it current? |
| `assets/client-logos/` | ⚠️ Unknown | Verify all client logos still match current branding |

**Action needed:** Manually check rieralaw.com and Elite Care Recovery's live site against their hero screenshots. If the sites have been updated since the screenshots were taken, the case studies show stale UIs.

---

## PRIORITY ORDER FOR FIXES

| # | Issue | File | Effort |
|---|---|---|---|
| 1 | Placeholder HTML tags | `src/pricing.html` | 5 min |
| 2 | Replace `{{FOOTER}}` in precios | `src/precios.html` | 10 min |
| 3 | Tokenize `$2,500`/`$99` in diseno-santo-domingo | `src/diseno-web-santo-domingo.html` | 15 min |
| 4 | Tokenize `$99/mes` in schema — abogados | `src/diseno-web-abogados-santo-domingo.html` | 5 min |
| 5 | Tokenize `$99/month` in schema — law-firms | `src/web-design-for-law-firms.html` | 5 min |
| 6 | SEO standalone vs bundled — services page | `src/services.html` | 15 min |
| 7 | Annotate market figures — care page | `src/website-care.html` | 10 min |
| 8 | Add testimonials to about page | `src/about.html` | 30 min |
| 9 | Verify case study hero screenshots | manual | 20 min |
| 10 | Check 2026 DBIR availability | research | 10 min |

**Total for #1–7 (the real bugs):** ~65 minutes of src/ edits + build + check.
