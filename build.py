#!/usr/bin/env python3
"""
Generates the public site from the bilingual sources in src/.

WHY THIS EXISTS
---------------
Until August 2026 this site shipped one URL per page carrying both languages as
sibling <span lang="en">/<span lang="es"> pairs, with CSS hiding the inactive
one. That works for humans and fails completely for search engines: the language
is chosen by a localStorage read that Googlebot never performs, so every crawl
saw `data-lang` absent and every Spanish string as display:none. 22,033 words of
Spanish were invisible to Google, while every page simultaneously declared
hreflang="es" pointing at itself. The Santo Domingo landing page was the sharpest
case -- a Spanish <title> wrapped around an English body.

You cannot fix that inside one URL. Google indexes one language per URL. So the
bilingual file is now a SOURCE, not a deliverable, and this script splits it into
two real trees:

    src/index.html   ->   index.html      (English only)
                     ->   es/index.html   (Spanish only)

Each output is single-language, single-purpose, and carries reciprocal hreflang
pointing at its twin. No hidden text, no localStorage guessing, half the bytes.

USAGE
-----
    ./build.py              # regenerate the site into the repo root
    ./build.py --check      # exit 1 if the output is stale (used by CI/checker)
    ./build.py --out DIR    # build somewhere else, for inspection

EDIT src/, NEVER the generated files. Anything you type into index.html at the
root is destroyed the next time this runs. check-consistency.py enforces this by
failing when the committed output does not match a fresh build.
"""

import argparse
import json
import os
import posixpath
import re
import sys
from html import unescape as html_unescape

DOMAIN = "https://madebysebby.com"

# FAQ entries dropped from Spanish pages because the page's own copy could not
# translate them. Reported at the end of every build -- a silent drop would read
# as "the Spanish FAQ is complete" when it is not.
DROPPED = {}

# Pages whose hand-written FAQ schema counted differently from the FAQ the page
# renders. Reported so a real authoring mistake cannot hide behind the rebuild.
MISMATCHED = {}

# ---------------------------------------------------------------------------
# The page table. One entry per source file in src/.
#
#   es      Spanish slug under /es/. None means the page has no Spanish twin.
#   en      English slug at the root. Defaults to the source name. None means
#           the page is Spanish-only and gets no English version at all.
#   meta    Spanish <head> strings. The body copy is already bilingual in the
#           source, but titles and descriptions never were -- they are English
#           on 23 of 25 pages. These had to be WRITTEN, not derived, and they
#           target Spanish search intent rather than translating the English.
#   noindex Excluded from the sitemap; gets no canonical or hreflang.
#
# Titles stay under 60 characters so they survive SERP truncation.
# ---------------------------------------------------------------------------
PAGES = {
    "index.html": dict(
        es="index.html",
        meta=dict(
            title="Diseño Web y Páginas Web para Negocios | Made by Sebby",
            desc="Diseño web y cuidado mensual para pequeños negocios en Miami, el sur de Florida y República Dominicana. Bilingüe, móvil primero. Consulta gratis.",
            og_title="Diseño Web y Páginas Web para Negocios | Made by Sebby",
            og_desc="Diseño, desarrollo y cuidado de páginas web para pequeños negocios en Miami, el sur de Florida y República Dominicana. Sitios bilingües que generan confianza y convierten visitantes.",
            tw_title="Diseño Web y Páginas Web para Negocios | Made by Sebby",
            tw_desc="Diseño, desarrollo y cuidado mensual de páginas web para pequeños negocios. Sitios que generan confianza, convierten visitantes y crecen con tu negocio.",
        ),
    ),
    "services.html": dict(
        es="servicios.html",
        meta=dict(
            title="Diseño Web, Cuidado Web y SEO Local | Made by Sebby",
            desc="Diseño web personalizado, mantenimiento mensual y SEO local para pequeños negocios en Miami y el sur de Florida. Sitios bilingües, móvil primero.",
            og_title="Diseño Web, Cuidado Web y SEO Local | Made by Sebby",
            og_desc="Diseño web personalizado, mantenimiento mensual y SEO local para pequeños negocios en Miami, el sur de Florida y República Dominicana. Sitios bilingües hechos para convertir.",
            tw_title="Diseño Web, Cuidado Web y SEO Local | Made by Sebby",
            tw_desc="Diseño web personalizado, mantenimiento mensual y SEO local para pequeños negocios. Sitios bilingües, móvil primero, hechos para convertir.",
        ),
    ),
    "work.html": dict(
        es="portafolio.html",
        meta=dict(
            title="Portafolio — Proyectos y Resultados | Made by Sebby",
            desc="Sitios web diseñados por Sebby para pequeños negocios en Miami y el sur de Florida. Casos reales de clientes, testimonios y resultados.",
            og_title="Portafolio — Proyectos y Resultados | Made by Sebby",
            og_desc="Mira sitios web diseñados y construidos por Sebby para negocios reales — bufetes de abogados, empresas de equipo médico y más. Con testimonios reales de clientes.",
            tw_title="Portafolio — Proyectos y Resultados | Made by Sebby",
            tw_desc="Mira sitios web diseñados y construidos por Sebby para negocios reales — bufetes de abogados, empresas de equipo médico y más.",
        ),
    ),
    "about.html": dict(
        es="sobre-mi.html",
        meta=dict(
            title="Sobre Sebby — Diseñador Web Independiente",
            desc="Diseñador web independiente para pequeños negocios en Miami y República Dominicana. Bilingüe, atención personal, y sitios cuidados como propios.",
            og_title="Sobre Sebby — Diseñador Web Independiente",
            og_desc="Diseñador web independiente para pequeños negocios en Miami y República Dominicana. Bilingüe, atención personal, y sitios cuidados como propios.",
            tw_title="Sobre Sebby — Diseñador Web Independiente",
            tw_desc="Diseñador web independiente para pequeños negocios en Miami y República Dominicana. Bilingüe y con atención personal.",
        ),
    ),
    "contact.html": dict(
        es="contacto.html",
        meta=dict(
            title="Contacto — Consulta Gratis de Diseño Web",
            desc="Agenda una consulta gratuita de 15 minutos en Miami o República Dominicana. Respuestas honestas sobre qué necesita tu sitio web y cuánto cuesta.",
            og_title="Contacto — Consulta Gratis de Diseño Web",
            og_desc="Agenda una consulta gratuita de 15 minutos en Miami o República Dominicana. Respuestas honestas sobre qué necesita tu sitio web y cuánto cuesta.",
            tw_title="Contacto — Consulta Gratis de Diseño Web",
            tw_desc="Agenda una consulta gratuita de 15 minutos. Respuestas honestas sobre qué necesita tu sitio web y cuánto cuesta.",
        ),
    ),
    "book.html": dict(
        es="agendar.html",
        meta=dict(
            title="Agendar Llamada — Consulta Gratis | Made by Sebby",
            desc="Agenda una consulta gratuita de 15 minutos con Sebby. Sin presión, sin jerga. Elige la hora que te convenga y hablemos de tu negocio.",
            og_title="Agendar Llamada — Consulta Gratis de Diseño Web",
            og_desc="Agenda una consulta gratuita de 15 minutos con Sebby. Sin presión, sin jerga. Elige la hora que te convenga.",
            tw_title="Agendar Llamada — Consulta Gratis de Diseño Web",
            tw_desc="Agenda una consulta gratuita de 15 minutos con Sebby. Sin presión, sin jerga. Elige la hora que te convenga.",
        ),
    ),
    "pricing.html": dict(
        es="precios.html",
        meta=dict(
            title="Precios de Diseño Web para Negocios | Made by Sebby",
            desc="Precios claros de diseño web para pequeños negocios. Sitios básicos desde $1,500, personalizados desde $3,500. Sin costos ocultos. Consulta gratis.",
            og_title="Precios y Paquetes de Diseño Web | Made by Sebby",
            og_desc="Precios claros de diseño web para pequeños negocios. Sitios básicos desde $1,500, personalizados desde $3,500, proyectos premium desde $7,000. Sin costos ocultos.",
            tw_title="Precios y Paquetes de Diseño Web | Made by Sebby",
            tw_desc="Precios claros de diseño web para pequeños negocios. Sitios básicos desde $1,500, personalizados desde $3,500. Sin costos ocultos.",
        ),
    ),
    "website-care.html": dict(
        es="cuidado-web.html",
        meta=dict(
            title="Planes de Cuidado Web — Mantenimiento Mensual",
            desc="Mantenimiento mensual de sitios web para negocios en Miami y el sur de Florida. Actualizaciones, respaldos, seguridad, velocidad y ediciones. Desde $99/mes.",
            og_title="Planes de Cuidado Web — Mantenimiento Mensual",
            og_desc="Planes accesibles de mantenimiento mensual. Actualizaciones de software, respaldos diarios, monitoreo de seguridad, optimización de velocidad y ediciones incluidas.",
            tw_title="Planes de Cuidado Web — Mantenimiento Mensual",
            tw_desc="Planes accesibles de mantenimiento mensual. Actualizaciones, respaldos diarios, seguridad, velocidad y ediciones incluidas.",
        ),
    ),
    "blog.html": dict(
        es="blog.html",
        meta=dict(
            title="Blog — Consejos de Diseño Web para Negocios",
            desc="Consejos prácticos de diseño web para dueños de pequeños negocios. Sin jerga, sin ventas forzadas, solo información directa de un diseñador independiente.",
            og_title="Blog — Consejos de Diseño Web para Negocios",
            og_desc="Consejos prácticos de diseño web para dueños de pequeños negocios. Sin jerga, sin ventas forzadas, solo información directa.",
            tw_title="Blog — Consejos de Diseño Web para Negocios",
            tw_desc="Consejos prácticos de diseño web para dueños de pequeños negocios. Sin jerga, solo información directa.",
        ),
    ),
    "case-study-rieralaw.html": dict(
        es="caso-riera-law.html",
        meta=dict(
            title="Caso de Éxito: Riera Law Firm | Made by Sebby",
            desc="Cómo Sebby reconstruyó la presencia web de un bufete de valores — más de 70 páginas bilingües, limpieza de DNS, correo restaurado y cuidado continuo.",
            og_title="Caso de Éxito: Riera Law Firm | Made by Sebby",
            og_desc="Cómo Sebby reconstruyó la presencia web completa de un bufete de valores — más de 70 páginas bilingües, limpieza de DNS, correo restaurado y cuidado web continuo.",
            tw_title="Caso de Éxito: Riera Law Firm | Made by Sebby",
            tw_desc="Cómo Sebby reconstruyó la presencia web de un bufete de valores — más de 70 páginas bilingües y cuidado web continuo.",
        ),
    ),
    "case-study-elitecare.html": dict(
        es="caso-elite-care.html",
        meta=dict(
            title="Caso de Éxito: Elite Care Recovery | Made by Sebby",
            desc="Cómo Sebby construyó un sitio web de salud para una empresa de recuperación ortopédica en el sur de Florida, con catálogo y diseño móvil primero.",
            og_title="Caso de Éxito: Elite Care Recovery | Made by Sebby",
            og_desc="Cómo Sebby construyó un sitio web profesional de salud para una empresa de recuperación ortopédica en el sur de Florida, con catálogo de productos, reservas en línea y diseño móvil primero.",
            tw_title="Caso de Éxito: Elite Care Recovery | Made by Sebby",
            tw_desc="Cómo Sebby construyó un sitio web de salud para una empresa de recuperación ortopédica en el sur de Florida.",
        ),
    ),
    "web-design-miami.html": dict(
        es="diseno-web-miami.html",
        meta=dict(
            title="Diseño Web en Miami para Pequeños Negocios",
            desc="Diseño web en Miami para pequeños negocios. Sitios personalizados que generan confianza y convierten visitantes. Bilingüe, móvil primero. Consulta gratis.",
            og_title="Diseño Web en Miami para Pequeños Negocios",
            og_desc="Diseño web en Miami para pequeños negocios — sitios personalizados que generan confianza y convierten visitantes. Bilingüe (español e inglés), móvil primero, con cuidado web continuo.",
            tw_title="Diseño Web en Miami para Pequeños Negocios",
            tw_desc="Diseño web en Miami para pequeños negocios — sitios personalizados que generan confianza y convierten. Bilingüe y móvil primero.",
        ),
    ),
    "web-design-fort-lauderdale.html": dict(
        es="diseno-web-fort-lauderdale.html",
        meta=dict(
            title="Diseño Web en Fort Lauderdale | Made by Sebby",
            desc="Diseño web en Fort Lauderdale para pequeños negocios. Sitios personalizados que generan confianza y convierten. Bilingüe, móvil primero. Consulta gratis.",
            og_title="Diseño Web en Fort Lauderdale | Made by Sebby",
            og_desc="Diseño web en Fort Lauderdale para pequeños negocios. Sitios personalizados que generan confianza y convierten visitantes. Bilingüe (español e inglés), móvil primero, con cuidado web continuo.",
            tw_title="Diseño Web en Fort Lauderdale | Made by Sebby",
            tw_desc="Diseño web en Fort Lauderdale para pequeños negocios. Sitios que generan confianza y convierten. Bilingüe y móvil primero.",
        ),
    ),
    # Spanish-only. This page exists to rank for "diseño web santo domingo" in a
    # Spanish-speaking market -- an English twin would serve nobody. It used to
    # live at the ROOT with a Spanish title and an English indexed body, which is
    # precisely the defect this whole build exists to remove. Its old root path
    # now emits a redirect stub (see REDIRECTS).
    "diseno-web-santo-domingo.html": dict(
        es="diseno-web-santo-domingo.html",
        en=None,
        meta=dict(
            title="Diseño Web Santo Domingo — Páginas Web | Made by Sebby",
            desc="Diseño web profesional en Santo Domingo y República Dominicana. Sitios personalizados, rápidos, modernos y optimizados para móvil.",
            og_title="Diseño Web Santo Domingo — Páginas Web para Negocios",
            og_desc="Diseño web profesional en Santo Domingo y República Dominicana. Sitios personalizados, rápidos, modernos y optimizados para móvil.",
            tw_title="Diseño Web Santo Domingo — Páginas Web para Negocios",
            tw_desc="Diseño web profesional en Santo Domingo y República Dominicana. Sitios web personalizados para negocios — rápidos, modernos y optimizados para móvil.",
        ),
    ),
    "privacy.html": dict(
        es="privacidad.html",
        meta=dict(
            title="Política de Privacidad | Made by Sebby",
            desc="Política de privacidad de Made by Sebby. Cómo recopilamos, usamos y protegemos tu información cuando visitas el sitio o usas nuestros servicios.",
            og_title="Política de Privacidad | Made by Sebby",
            og_desc="Política de privacidad de Made by Sebby. Cómo recopilamos, usamos y protegemos tu información.",
            tw_title="Política de Privacidad | Made by Sebby",
            tw_desc="Política de privacidad de Made by Sebby. Cómo recopilamos, usamos y protegemos tu información.",
        ),
    ),
    "terms.html": dict(
        es="terminos.html",
        meta=dict(
            title="Términos de Servicio | Made by Sebby",
            desc="Términos de servicio de Made by Sebby. Las reglas y condiciones que aplican al usar el sitio web o contratar nuestros servicios de diseño web.",
            og_title="Términos de Servicio | Made by Sebby",
            og_desc="Términos de servicio de Made by Sebby. Las reglas y condiciones que aplican al usar el sitio o contratar nuestros servicios.",
            tw_title="Términos de Servicio | Made by Sebby",
            tw_desc="Términos de servicio de Made by Sebby. Las reglas y condiciones que aplican al contratar nuestros servicios.",
        ),
    ),
    # noindex, but it still needs a Spanish twin: the Spanish contact form has to
    # land somewhere Spanish after submitting.
    "thank-you.html": dict(
        es="gracias.html",
        noindex=True,
        meta=dict(
            title="Mensaje Enviado — Made by Sebby",
            desc="Gracias por escribir. Te responderé pronto.",
            og_title="Mensaje Enviado — Made by Sebby",
            og_desc="Gracias por escribir. Te responderé pronto.",
            tw_title="Mensaje Enviado — Made by Sebby",
            tw_desc="Gracias por escribir. Te responderé pronto.",
        ),
    ),
    "blog/5-signs-your-website-is-losing-clients.html": dict(
        es="blog/5-senales-de-que-tu-sitio-web-pierde-clientes.html",
        meta=dict(
            title="5 Señales de Que Tu Sitio Web Pierde Clientes",
            desc="Tu sitio web debería atraer negocio, no ahuyentarlo. Aquí hay 5 señales de alerta de que tu sitio te está costando clientes — y qué hacer con cada una.",
            og_title="5 Señales de Que Tu Sitio Web Pierde Clientes",
            og_desc="Tu sitio web debería atraer negocio, no ahuyentarlo. Aquí hay 5 señales de alerta de que tu sitio te está costando clientes — y qué hacer con cada una.",
            tw_title="5 Señales de Que Tu Sitio Web Pierde Clientes",
            tw_desc="Tu sitio web debería atraer negocio, no ahuyentarlo. Aquí hay 5 señales de que tu sitio te está costando clientes.",
        ),
    ),
    "blog/what-is-website-care.html": dict(
        es="blog/que-es-el-cuidado-web.html",
        meta=dict(
            title="¿Qué Es el Cuidado Web y Por Qué Lo Necesitas?",
            desc="Qué cubre realmente el cuidado web: actualizaciones, seguridad, respaldos, velocidad y ediciones. Por qué omitirlo cuesta más de lo que crees.",
            og_title="¿Qué Es el Cuidado Web y Por Qué Lo Necesitas?",
            og_desc="La mayoría de los dueños de negocios lanzan un sitio web y no lo tocan nunca más. Esto es lo que cubre el cuidado web y por qué omitirlo cuesta más de lo que crees.",
            tw_title="¿Qué Es el Cuidado Web y Por Qué Lo Necesitas?",
            tw_desc="Esto es lo que cubre el cuidado web y por qué omitirlo cuesta más de lo que crees.",
        ),
    ),
    "blog/how-much-does-a-website-cost.html": dict(
        es="blog/cuanto-cuesta-un-sitio-web.html",
        meta=dict(
            title="¿Cuánto Cuesta un Sitio Web en 2026? | Made by Sebby",
            desc="Precios reales de sitios web para pequeños negocios en 2026. Constructores DIY, freelancers y agencias comparados. Sin ventas forzadas, solo hechos.",
            og_title="¿Cuánto Cuesta un Sitio Web en 2026? | Made by Sebby",
            og_desc="Precios reales de sitios web para pequeños negocios en 2026. Constructores DIY, freelancers y agencias comparados — con lo que realmente recibes en cada rango de precio.",
            tw_title="¿Cuánto Cuesta un Sitio Web en 2026? | Made by Sebby",
            tw_desc="Precios reales de sitios web para pequeños negocios en 2026. Constructores DIY, freelancers y agencias comparados.",
        ),
    ),
    "blog/why-your-competitor-gets-calls-from-google.html": dict(
        es="blog/por-que-tu-competencia-recibe-llamadas-de-google.html",
        meta=dict(
            title="Por Qué Tu Competencia Recibe Llamadas de Google",
            desc="El 80% de las personas busca negocios locales en línea cada semana. Si no apareces, tu competencia recibe la llamada. Aquí está el porqué y qué hacer.",
            og_title="Por Qué Tu Competencia Recibe Llamadas de Google",
            og_desc="El 80% de las personas busca negocios locales en línea cada semana. Si no apareces, tu competencia recibe la llamada. Aquí está el porqué y qué hacer al respecto.",
            tw_title="Por Qué Tu Competencia Recibe Llamadas de Google",
            tw_desc="El 80% de las personas busca negocios locales en línea cada semana. Si no apareces, tu competencia recibe la llamada.",
        ),
    ),
    "blog/what-a-website-does-for-a-law-firm.html": dict(
        es="blog/que-hace-un-sitio-web-por-un-bufete.html",
        meta=dict(
            title="Qué Hace un Sitio Web por un Bufete de Abogados",
            desc="La mayoría de quienes buscan abogado empiezan en Google. Esto es lo que hace un sitio web bien construido para un bufete — con datos reales y precios honestos.",
            og_title="Qué Hace un Sitio Web por un Bufete de Abogados",
            og_desc="La mayoría de quienes buscan abogado empiezan en Google. Esto es lo que hace un sitio web bien construido para un bufete — con datos reales, ejemplos reales y precios honestos.",
            tw_title="Qué Hace un Sitio Web por un Bufete de Abogados",
            tw_desc="La mayoría de quienes buscan abogado empiezan en Google. Esto es lo que hace un buen sitio web para un bufete.",
        ),
    ),
    "blog/does-your-restaurant-need-a-website.html": dict(
        es="blog/necesita-tu-restaurante-un-sitio-web.html",
        meta=dict(
            title="¿Necesita Tu Restaurante un Sitio Web? | Made by Sebby",
            desc="La mayoría de los restaurantes dependen solo de Instagram y Google Maps. Por qué un sitio web propio atrae más clientes — y qué debe incluir.",
            og_title="¿Necesita Tu Restaurante un Sitio Web? | Made by Sebby",
            og_desc="La mayoría de los restaurantes dependen solo de Instagram y Google Maps. Por qué un sitio web propio atrae más clientes — y qué debe incluir realmente.",
            tw_title="¿Necesita Tu Restaurante un Sitio Web? | Made by Sebby",
            tw_desc="La mayoría de los restaurantes dependen solo de Instagram y Google Maps. Por qué un sitio web propio atrae más clientes.",
        ),
    ),
}

# Copied to the root byte-for-byte. Neither is part of the two-tree model.
#   404.html    GitHub Pages serves ONE 404 for the whole site, /es/ included,
#               so it has to keep both languages inline to work for either.
#   precios.html  Spanish-only DR one-pager shared over WhatsApp. Deliberately
#               noindex, deliberately not in the nav, and NOT the same page as
#               /es/precios.html (which is the Spanish twin of pricing.html).
PASSTHROUGH = ["404.html", "precios.html"]

# old root path -> new URL. GitHub Pages cannot serve a 301, so these are
# meta-refresh stubs carrying a canonical to the destination.
REDIRECTS = {
    "diseno-web-santo-domingo.html": "/es/diseno-web-santo-domingo.html",
}

# aria-label is never inside a <span lang> pair -- it is an attribute, so the
# bilingual sibling trick cannot reach it. Under the old single-URL model that
# was invisible; now a Spanish page would announce "Toggle dark mode" to a
# Spanish screen reader. Untranslated labels are left as-is rather than guessed.
ARIA_ES = {
    "Toggle dark mode": "Alternar modo oscuro",
    "Menu": "Menú",
    "Made by Sebby home": "Inicio de Made by Sebby",
    "Breadcrumb": "Ruta de navegación",
    "Toggle annual billing": "Alternar facturación anual",
}

# alt text is an attribute too, so it has the same problem as aria-label: the
# sibling-span trick cannot reach it, and a Spanish page was describing its own
# images in English to screen readers and to image search.
#
# Only DESCRIPTIVE alts appear here. Brand names ("Riera Law Firm", "Made by
# Sebby") are correctly identical in both languages and are deliberately absent;
# translating a company's name would be wrong. check-consistency.py flags any
# descriptive alt that reaches a Spanish page untranslated.
ALT_ES = {
    "Made by Sebby — Web Design, Development, Website Care":
        "Made by Sebby — Diseño Web, Desarrollo, Cuidado Web",
    "Made by Sebby | Web Design, Development, Website Care":
        "Made by Sebby | Diseño Web, Desarrollo, Cuidado Web",
    "Sebby, web designer and developer, smiling at his workstation":
        "Sebby, diseñador y desarrollador web, sonriendo en su estación de trabajo",
    "Sebby, web designer and developer":
        "Sebby, diseñador y desarrollador web",
    "Website care dashboard with security, backups, and performance monitoring":
        "Panel de cuidado web con seguridad, respaldos y monitoreo de rendimiento",
    "Responsive website design shown across laptop, tablet, and phone":
        "Diseño web adaptable mostrado en laptop, tableta y teléfono",
    "Google search result and Business Profile for a local business":
        "Resultado de búsqueda de Google y Perfil de Empresa de un negocio local",
    "Riera Law Firm homepage — securities arbitration attorney with courthouse background":
        "Página de inicio de Riera Law Firm — abogado de arbitraje de valores con fondo de tribunal",
    "Elite Care Recovery homepage — premium cold and compression therapy for post-operative patients":
        "Página de inicio de Elite Care Recovery — terapia premium de frío y compresión para pacientes postoperatorios",
    "Riera Law Firm homepage hero": "Portada del sitio de Riera Law Firm",
    "Elite Care Recovery homepage hero": "Portada del sitio de Elite Care Recovery",
    "Elite Care Recovery, South Florida": "Elite Care Recovery, sur de Florida",
    "Elite Care Recovery — South Florida": "Elite Care Recovery — sur de Florida",
}


# ---------------------------------------------------------------------------
# HTML surgery
# ---------------------------------------------------------------------------

def matching_close(html, open_start, tag):
    """Index range of the </tag> that closes the <tag> beginning at open_start.

    Depth-aware, because the bilingual spans routinely wrap other spans --
    <span lang="en">Websites that <span class="accent">build trust.</span></span>
    A non-greedy regex stops at the first </span> and silently truncates the
    sentence, which is exactly the kind of failure that renders fine in a browser
    and destroys the copy.
    """
    pattern = re.compile(r"<(/?)%s\b[^>]*>" % tag, re.I)
    depth = 0
    for m in pattern.finditer(html, open_start):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.start(), m.end()
    raise ValueError("unclosed <%s> at offset %d" % (tag, open_start))


def split_language(html, keep):
    """Drop the other language's elements, unwrap this one's."""
    drop = "es" if keep == "en" else "en"
    for target, mode in ((drop, "remove"), (keep, "unwrap")):
        pattern = re.compile(r'<(span|div)\b[^>]*\blang="%s"[^>]*>' % target)
        while True:
            m = pattern.search(html)
            if not m:
                break
            close_start, close_end = matching_close(html, m.start(), m.group(1))
            inner = html[m.end():close_start]
            tail = html[close_end:]
            html = html[:m.start()] + ("" if mode == "remove" else inner) + tail
    return html


def rewrite_paths(html, source, lang):
    """Make every internal href/src root-absolute and point at the right tree.

    The sources mix three conventions -- 'services.html' from the root,
    '../services.html' from blog/, and '/favicon.ico'. Under /es/ the relative
    ones would resolve to /es/assets/... and 404. Generated output is uniformly
    root-absolute, which retires the entire class of bug.
    """
    src_dir = posixpath.dirname(source)

    def one_url(value):
        if value.startswith(("http://", "https://", "//", "#", "mailto:", "tel:", "data:")):
            return value
        path, sep, frag = value.partition("#")
        if not path:
            return value
        if path.startswith("/"):
            resolved = path.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(src_dir, path))
        if resolved in ("", "."):
            resolved = "index.html"
        target = url_for(resolved, lang) if resolved in PAGES else "/" + resolved
        return target + sep + frag

    def fix(m):
        attr, value = m.group(1), m.group(2)
        if attr == "srcset":
            # "a.webp 1x, b.webp 2x" -- rewrite each URL, keep its descriptor.
            parts = []
            for candidate in value.split(","):
                bits = candidate.strip().split(None, 1)
                if not bits:
                    continue
                parts.append(" ".join([one_url(bits[0])] + bits[1:]))
            return '%s="%s"' % (attr, ", ".join(parts))
        return '%s="%s"' % (attr, one_url(value))

    # srcset MUST be here. <picture><source srcset="assets/sebby.webp"> resolved
    # to /es/assets/sebby.webp and 404'd on every Spanish page -- the browser
    # picks the <source> over the <img>, so the hero image silently broke while
    # the <img src> beside it was perfectly correct.
    return re.sub(r'\b(href|src|action|srcset)="([^"]*)"', fix, html)


def url_for(source, lang):
    entry = PAGES[source]
    if lang == "en":
        slug = entry.get("en", source)
        if slug is None:                      # Spanish-only page
            return url_for(source, "es")
        return "/" if slug == "index.html" else "/" + slug
    slug = entry["es"]
    return "/es/" if slug == "index.html" else "/es/" + slug


def strip_toggle_machinery(html, lang):
    """Remove the localStorage language system. Both trees are single-language."""
    html = re.sub(
        r'<script>!function\(\)\{var l=localStorage\.getItem\("lang"\).*?</script>\n?',
        "", html, flags=re.S)
    html = re.sub(
        r"<script>!function\(\)\{var l=document\.documentElement\.getAttribute\('data-lang'\).*?</script>\n?",
        "", html, flags=re.S)
    html = re.sub(
        r'\s*:root:not\(\[data-lang="es"\]\) \[lang="es"\]\{display:none\}\n'
        r'\s*\[data-lang="es"\] \[lang="en"\]\{display:none\}\n',
        "\n", html)
    # The toggle is now two links, not two buttons, so the rules target children.
    html = html.replace(
        "  .lang-toggle button{",
        "  .lang-toggle>*{")
    html = html.replace(
        "  .lang-toggle button:hover{color:var(--purple)}",
        "  .lang-toggle>*:hover{color:var(--purple)}")
    html = html.replace(
        "  .lang-toggle button.active{background:var(--purple);color:#fff}",
        "  .lang-toggle>.active{background:var(--purple);color:#fff}")
    html = html.replace(
        "color:var(--muted);transition:background .15s,color .15s;font-weight:700;font-size:.76rem;letter-spacing:.04em;\n  }",
        "color:var(--muted);transition:background .15s,color .15s;font-weight:700;font-size:.76rem;letter-spacing:.04em;\n"
        "    text-decoration:none;display:flex;align-items:center;\n  }")

    # The toggle's other half is now an <a>, and it lives INSIDE .nav-links.
    # At mobile width `.nav-links a` sets `padding:16px 0; width:100%;
    # border-bottom:...` to render each nav item as a full-width row -- styling
    # the old <button> never matched. Applied to the toggle it collapsed the
    # EN|ES pill into a circle with the two labels overlapping.
    #
    # Scoping those rules to DIRECT children fixes it without touching any real
    # nav item: the links and the CTA are children of .nav-links, the toggle's
    # anchor is a grandchild.
    html = html.replace(".nav-links a", ".nav-links>a")

    # Language-conditional CSS beyond the show/hide pair. work.html and
    # website-care.html both carry
    #     [data-lang="es"] .plan.featured::before{content:"Más popular"}
    # overriding an English ::before label. With data-lang gone, the Spanish
    # pages would silently render "Most popular" in English -- CSS-generated
    # content that no copy check would ever catch, because it is not in the DOM.
    if lang == "es":
        html = re.sub(r'\[data-lang="es"\]\s+', "", html)
    else:
        html = re.sub(r'^[ \t]*\[data-lang="es"\][^\n]*\n', "", html, flags=re.M)
    return html


def rewrite_toggle(html, source, lang):
    """Replace the in-place toggle with a real link to the twin page.

    This is the part that makes the Spanish tree discoverable. A crawler follows
    an <a href>; it never fires an onclick that flips a data attribute.
    """
    m = re.search(r'<div class="lang-toggle">', html)
    if not m:
        return html
    close_start, close_end = matching_close(html, m.start(), "div")

    entry = PAGES[source]
    en_available = entry.get("en", source) is not None
    if lang == "en":
        block = (
            '<div class="lang-toggle">\n'
            '          <span class="active">EN</span>\n'
            '          <a href="%s" hreflang="es" lang="es" aria-label="Ver esta página en español">ES</a>\n'
            '        </div>' % url_for(source, "es"))
    else:
        # A Spanish-only page has no English twin; send those visitors home.
        en_href = url_for(source, "en") if en_available else "/"
        block = (
            '<div class="lang-toggle">\n'
            '          <a href="%s" hreflang="en" lang="en" aria-label="View this page in English">EN</a>\n'
            '          <span class="active">ES</span>\n'
            '        </div>' % en_href)
    return html[:m.start()] + block + html[close_end:]


def rewrite_head(html, source, lang):
    entry = PAGES[source]
    noindex = entry.get("noindex", False)
    self_url = DOMAIN + url_for(source, lang)
    en_slug = entry.get("en", source)

    if lang == "es":
        meta = entry["meta"]
        html = re.sub(r"<title>.*?</title>",
                      lambda _: "<title>%s</title>" % meta["title"], html, count=1, flags=re.S)
        for pattern, value in (
            (r'(<meta name="description" content=")[^"]*(">)', meta["desc"]),
            (r'(<meta property="og:title" content=")[^"]*(">)', meta["og_title"]),
            (r'(<meta property="og:description" content=")[^"]*(">)', meta["og_desc"]),
            (r'(<meta name="twitter:title" content=")[^"]*(">)', meta["tw_title"]),
            (r'(<meta name="twitter:description" content=")[^"]*(">)', meta["tw_desc"]),
        ):
            html = re.sub(pattern, lambda m, v=value: m.group(1) + v + m.group(2), html, count=1)

    html = re.sub(r'(<meta property="og:url" content=")[^"]*(">)',
                  lambda m: m.group(1) + self_url + m.group(2), html, count=1)

    # og:locale was never present. Both trees need it now that they are split.
    if "og:locale" not in html:
        html = html.replace(
            '<meta property="og:url"',
            '<meta property="og:locale" content="%s">\n<meta property="og:url"'
            % ("es_ES" if lang == "es" else "en_US"), 1)

    if not noindex:
        alternates = ['<link rel="canonical" href="%s">' % self_url]
        if en_slug is not None:
            alternates += [
                '<link rel="alternate" hreflang="en" href="%s">' % (DOMAIN + url_for(source, "en")),
                '<link rel="alternate" hreflang="es" href="%s">' % (DOMAIN + url_for(source, "es")),
                '<link rel="alternate" hreflang="x-default" href="%s">' % (DOMAIN + url_for(source, "en")),
            ]
        else:
            # Spanish-only page: it is nobody's translation, so it annotates
            # itself. A non-reciprocal pair is discarded by Google wholesale.
            alternates += [
                '<link rel="alternate" hreflang="es" href="%s">' % self_url,
                '<link rel="alternate" hreflang="x-default" href="%s">' % self_url,
            ]
        html = re.sub(
            r'<link rel="canonical"[^>]*>\n'
            r'(?:<link rel="alternate" hreflang="[^"]*"[^>]*>\n)*',
            "\n".join(alternates) + "\n", html, count=1)

    return html


def pair_map(html):
    """EN -> ES from the source's own <span lang> sibling pairs.

    The bilingual body is a translation memory: ~2,400 phrases Sebby already
    wrote and approved. The JSON-LD carries the same sentences as the visible
    copy, so most schema prose can be translated from the page itself rather
    than guessed at.
    """
    out = {}
    opener = re.compile(r'<(span|div)\b[^>]*\blang="en"[^>]*>')
    flatten = lambda s: html_unescape(" ".join(re.sub(r"<[^>]+>", "", s).split()))
    pos = 0
    while True:
        m = opener.search(html, pos)
        if not m:
            break
        close_start, close_end = matching_close(html, m.start(), m.group(1))
        english = html[m.end():close_start]
        rest = html[close_end:]
        sibling = re.match(r'\s*<(span|div)\b[^>]*\blang="es"[^>]*>', rest)
        if sibling:
            s_start, _ = matching_close(rest, sibling.start(), sibling.group(1))
            key = flatten(english)
            if key:
                out[key] = flatten(rest[sibling.end():s_start])
        pos = close_end
    return out


# ---------------------------------------------------------------------------
# Canonical NAP — Name, Address, Phone.
#
# Local ranking depends on these matching EXACTLY between the site and the
# Google Business Profile; Google cross-references them. They did not match: the
# profile is verified at a Miami address with a (786) number, while the site's
# only structured address said Santo Domingo, DO with no phone at all.
#
# Kept here, in one place, and injected into every LocalBusiness/ProfessionalService
# node at build time. Hand-copying an address into six schema blocks is precisely
# how NAP drifts, and NAP drift is the thing this is meant to prevent.
#
# NO streetAddress and NO geo on purpose. It is a family home, not a staffed
# office. Google requires service-area businesses to HIDE a residential address,
# and publishing its coordinates would broadcast what the profile hides.
# Locality/region/postal is enough to corroborate the profile.
NAP = {
    "name": "Made by Sebby",
    "telephone": "+1-786-543-1417",
    "email": "hello@madebysebby.com",
    "address": {
        "@type": "PostalAddress",
        "addressLocality": "Miami",
        "addressRegion": "FL",
        "postalCode": "33175",
        "addressCountry": "US",
    },
    "openingHoursSpecification": [{
        "@type": "OpeningHoursSpecification",
        "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "opens": "09:00",
        "closes": "18:00",
    }],
}

# Stable identity so every page's block is understood as ONE business rather
# than a separate branch per landing page.
BUSINESS_ID = DOMAIN + "/#business"
LOCAL_TYPES = ("ProfessionalService", "LocalBusiness")

# Schema keys holding human prose rather than identifiers or URLs.
PROSE_KEYS = ("name", "description", "headline", "slogan", "reviewBody", "text",
              "articleBody", "acceptedAnswer")


def flatten_text(fragment):
    return html_unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


def faq_from_dom(html):
    """Read the FAQ straight out of the rendered <details> blocks.

    Google requires FAQPage content to appear on the page. The hand-written
    schema had drifted from the visible copy -- it asked "Can I cancel my website
    care plan?" where the page asks "Can I cancel?", and several answers were
    tightened paraphrases that appeared nowhere. That is a compliance problem in
    English before it is a translation problem in Spanish.

    Deriving the schema from the DOM fixes both at once: it cannot drift again,
    and because this runs after the language split it produces correct Spanish
    schema with nothing to translate. Every page's <details> count already
    matched its schema count exactly, so nothing is lost in the change.
    """
    entries = []
    for block in re.finditer(r"<details[^>]*>(.*?)</details>", html, re.S):
        inner = block.group(1)
        summary = re.search(r"<summary[^>]*>(.*?)</summary>", inner, re.S)
        if not summary:
            continue
        question = flatten_text(summary.group(1))
        answer = flatten_text(inner[summary.end():])
        if question and answer:
            entries.append({
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            })
    return entries


def rewrite_jsonld(html, source, lang, memory):
    """Repoint schema URLs at this tree, stamp the language, translate prose.

    FAQPage is not translated -- it is regenerated from the page's own <details>
    blocks, which are already in the right language by the time this runs. See
    faq_from_dom().
    """
    faq_entries = faq_from_dom(html)

    def fix_block(m):
        raw = m.group(1)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return m.group(0)

        top_types = ("WebPage", "BlogPosting", "ProfessionalService", "LocalBusiness")

        # A string that is already one of the Spanish sides needs no translation.
        # diseno-web-santo-domingo.html authored its schema in Spanish from the
        # start; without this it would fail every EN->ES lookup and lose its
        # entire FAQ, which is the opposite of the intended outcome.
        already_spanish = set(memory.values())

        def resolved(value):
            text = html_unescape(value)
            if text in already_spanish:
                return value
            return memory.get(text)

        def translatable(node):
            """True if every prose string under this node can be translated."""
            if isinstance(node, dict):
                return all(
                    (resolved(v) is not None)
                    if (k in PROSE_KEYS and isinstance(v, str) and len(v.split()) > 2)
                    else translatable(v)
                    for k, v in node.items())
            if isinstance(node, list):
                return all(translatable(i) for i in node)
            return True

        def walk(node):
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    if isinstance(value, str) and value.startswith(DOMAIN):
                        path = value[len(DOMAIN):]
                        candidate = path.lstrip("/") or "index.html"
                        if candidate in PAGES:
                            node[key] = DOMAIN + url_for(candidate, lang)
                        continue
                    if lang == "es" and not spanish_source and key in PROSE_KEYS and isinstance(value, str):
                        translated = resolved(value)
                        if translated:
                            node[key] = translated
                            continue
                    walk(value)

                if node.get("@type") in LOCAL_TYPES:
                    # Overwrite rather than fill gaps: a stale address left in a
                    # source file is exactly the drift this exists to stop.
                    node["@id"] = BUSINESS_ID
                    for key, value in NAP.items():
                        node[key] = json.loads(json.dumps(value))
                    node.pop("geo", None)

                if "inLanguage" in node or node.get("@type") in top_types:
                    node["inLanguage"] = "es" if lang == "es" else "en"

                if lang == "es":
                    if node.get("@type") == "BlogPosting" and "headline" in node:
                        node["headline"] = PAGES[source]["meta"]["title"]
                    if "description" in node and node.get("@type") in top_types:
                        node["description"] = PAGES[source]["meta"]["desc"]
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        def regenerate_faq(node):
            """Replace every FAQPage's Q&A with what the page actually shows."""
            if isinstance(node, dict):
                if node.get("@type") == "FAQPage":
                    listed = len(node.get("mainEntity", []))
                    if not faq_entries:
                        # Schema claims an FAQ the page does not render. Better
                        # to emit nothing than to assert invisible content.
                        DROPPED[source] = DROPPED.get(source, 0) + listed
                        return False
                    node["mainEntity"] = faq_entries
                    if listed != len(faq_entries):
                        MISMATCHED.setdefault(source, (listed, len(faq_entries)))
                for key, value in list(node.items()):
                    if isinstance(value, (dict, list)) and not regenerate_faq(value):
                        del node[key]
            elif isinstance(node, list):
                node[:] = [i for i in node if regenerate_faq(i)]
            return True

        # A Spanish-only page authored its schema in Spanish to begin with, so
        # there is nothing to translate. Deciding that from the page table is
        # reliable; inferring it by string-matching the body is not, and cost
        # Santo Domingo its entire FAQ.
        spanish_source = PAGES[source].get("en", source) is None

        if not regenerate_faq(data):
            return ""           # an empty FAQPage is worse than no FAQPage

        walk(data)
        return '<script type="application/ld+json">\n%s\n</script>' % json.dumps(
            data, ensure_ascii=False, indent=2)

    return re.sub(r'<script type="application/ld\+json">(.*?)</script>',
                  fix_block, html, flags=re.S)


def render(source, html, lang):
    memory = pair_map(html)          # must be read BEFORE the split removes the pairs
    html = split_language(html, lang)
    html = strip_toggle_machinery(html, lang)
    html = rewrite_head(html, source, lang)
    html = rewrite_jsonld(html, source, lang, memory)
    if lang == "es":
        for attr, table in (("aria-label", ARIA_ES), ("alt", ALT_ES)):
            html = re.sub(
                r'%s="([^"]*)"' % attr,
                lambda m, t=table, a=attr: '%s="%s"' % (a, t.get(m.group(1), m.group(1))),
                html)
    html = rewrite_paths(html, source, lang)
    # MUST come after rewrite_paths. The toggle is the one link that deliberately
    # points at the OTHER tree, and the path rewriter would drag it back into
    # this one -- the Spanish page's "EN" link ended up pointing at itself.
    html = rewrite_toggle(html, source, lang)
    html = re.sub(r'<html lang="[^"]*"[^>]*>', '<html lang="%s">' % lang, html, count=1)
    return html


REDIRECT_STUB = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Redirigiendo… — Made by Sebby</title>
<meta name="robots" content="noindex, follow">
<link rel="canonical" href="{domain}{target}">
<meta http-equiv="refresh" content="0; url={target}">
</head>
<body>
<p>Esta página se movió a <a href="{target}">{target}</a>.</p>
</body>
</html>
"""


def build(src_dir, out_dir):
    written = {}
    for source in PAGES:
        raw = open(os.path.join(src_dir, source), encoding="utf-8").read()
        for lang in ("en", "es"):
            if lang == "en" and PAGES[source].get("en", source) is None:
                continue
            rel = url_for(source, lang)
            rel = rel[1:] + "index.html" if rel.endswith("/") else rel[1:]
            written[rel] = render(source, raw, lang)

    for name in PASSTHROUGH:
        written[name] = open(os.path.join(src_dir, name), encoding="utf-8").read()

    for old, target in REDIRECTS.items():
        written[old] = REDIRECT_STUB.format(domain=DOMAIN, target=target)

    for rel, content in written.items():
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="src")
    parser.add_argument("--out", default=".")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if committed output differs from a fresh build")
    args = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)

    if args.check:
        stale = []
        for source in PAGES:
            raw = open(os.path.join(args.src, source), encoding="utf-8").read()
            for lang in ("en", "es"):
                if lang == "en" and PAGES[source].get("en", source) is None:
                    continue
                rel = url_for(source, lang)
                rel = rel[1:] + "index.html" if rel.endswith("/") else rel[1:]
                expected = render(source, raw, lang)
                if not os.path.exists(rel) or open(rel, encoding="utf-8").read() != expected:
                    stale.append(rel)
        if stale:
            print("STALE -- these differ from a fresh build of src/:")
            for rel in stale:
                print("   ", rel)
            print("\nRun ./build.py")
            return 1
        print("build output is current (%d pages)" % len(PAGES))
        return 0

    written = build(args.src, args.out)
    en = sum(1 for k in written if not k.startswith("es/"))
    es = sum(1 for k in written if k.startswith("es/"))
    print("wrote %d files -> %s" % (len(written), os.path.abspath(args.out)))
    print("   %d English (incl. %d passthrough, %d redirect stub)"
          % (en, len(PASSTHROUGH), len(REDIRECTS)))
    print("   %d Spanish" % es)
    dropped = {k: v for k, v in DROPPED.items() if v}
    if dropped:
        print("\n   FAQPage schema removed -- these pages declare an FAQ but render no"
              "\n   <details> blocks, so the schema would assert invisible content:")
        for source in sorted(dropped):
            print("      %-46s %d entries" % (source, dropped[source]))
    if MISMATCHED:
        print("\n   FAQ schema regenerated from the page (counts differed from the"
              "\n   hand-written schema -- worth a look):")
        for source, (was, now) in sorted(MISMATCHED.items()):
            print("      %-46s %d -> %d" % (source, was, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
