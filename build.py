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
import datetime
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
            title="Portafolio. Proyectos y Resultados | Made by Sebby",
            desc="Sitios web diseñados por Sebby para pequeños negocios en Miami y el sur de Florida. Casos reales de clientes, testimonios y resultados.",
            og_title="Portafolio. Proyectos y Resultados | Made by Sebby",
            og_desc="Mira sitios web diseñados y construidos por Sebby para negocios reales, bufetes de abogados, empresas de equipo médico y más. Con testimonios reales de clientes.",
            tw_title="Portafolio. Proyectos y Resultados | Made by Sebby",
            tw_desc="Mira sitios web diseñados y construidos por Sebby para negocios reales, bufetes de abogados, empresas de equipo médico y más.",
        ),
    ),
    "about.html": dict(
        es="sobre-mi.html",
        meta=dict(
            title="Sobre Sebby. Diseñador Web Independiente",
            desc="Diseñador web independiente para pequeños negocios en Miami y República Dominicana. Bilingüe, atención personal, y sitios cuidados como propios.",
            og_title="Sobre Sebby. Diseñador Web Independiente",
            og_desc="Diseñador web independiente para pequeños negocios en Miami y República Dominicana. Bilingüe, atención personal, y sitios cuidados como propios.",
            tw_title="Sobre Sebby. Diseñador Web Independiente",
            tw_desc="Diseñador web independiente para pequeños negocios en Miami y República Dominicana. Bilingüe y con atención personal.",
        ),
    ),
    "contact.html": dict(
        es="contacto.html",
        meta=dict(
            title="Contacto. Consulta Gratis de Diseño Web",
            desc="Agenda una consulta gratuita de 15 minutos en Miami o República Dominicana. Respuestas honestas sobre qué necesita tu sitio web y cuánto cuesta.",
            og_title="Contacto. Consulta Gratis de Diseño Web",
            og_desc="Agenda una consulta gratuita de 15 minutos en Miami o República Dominicana. Respuestas honestas sobre qué necesita tu sitio web y cuánto cuesta.",
            tw_title="Contacto. Consulta Gratis de Diseño Web",
            tw_desc="Agenda una consulta gratuita de 15 minutos. Respuestas honestas sobre qué necesita tu sitio web y cuánto cuesta.",
        ),
    ),
    "book.html": dict(
        es="agendar.html",
        meta=dict(
            title="Agendar Llamada. Consulta Gratis | Made by Sebby",
            desc="Agenda una consulta gratuita de 15 minutos con Sebby. Sin presión, sin jerga. Elige la hora que te convenga y hablemos de tu negocio.",
            og_title="Agendar Llamada. Consulta Gratis de Diseño Web",
            og_desc="Agenda una consulta gratuita de 15 minutos con Sebby. Sin presión, sin jerga. Elige la hora que te convenga.",
            tw_title="Agendar Llamada. Consulta Gratis de Diseño Web",
            tw_desc="Agenda una consulta gratuita de 15 minutos con Sebby. Sin presión, sin jerga. Elige la hora que te convenga.",
        ),
    ),
    "pricing.html": dict(
        es="precios.html",
        meta=dict(
            title="Precios de Diseño Web para Negocios | Made by Sebby",
            desc="Precios claros de diseño web para pequeños negocios. Sitios básicos desde {{PRICE_STARTER}}, personalizados desde {{PRICE_CUSTOM}}. Sin costos ocultos. Consulta gratis.",
            og_title="Precios y Paquetes de Diseño Web | Made by Sebby",
            og_desc="Precios claros de diseño web para pequeños negocios. Sitios básicos desde {{PRICE_STARTER}}, personalizados desde {{PRICE_CUSTOM}}, proyectos premium desde {{PRICE_PREMIUM}}. Sin costos ocultos.",
            tw_title="Precios y Paquetes de Diseño Web | Made by Sebby",
            tw_desc="Precios claros de diseño web para pequeños negocios. Sitios básicos desde {{PRICE_STARTER}}, personalizados desde {{PRICE_CUSTOM}}. Sin costos ocultos.",
        ),
    ),
    "website-care.html": dict(
        es="cuidado-web.html",
        meta=dict(
            title="Planes de Cuidado Web. Mantenimiento Mensual",
            desc="Mantenimiento mensual de sitios web para negocios en Miami y el sur de Florida. Actualizaciones, respaldos, seguridad, velocidad y ediciones. Desde $99/mes.",
            og_title="Planes de Cuidado Web. Mantenimiento Mensual",
            og_desc="Planes accesibles de mantenimiento mensual. Actualizaciones de software, respaldos diarios, monitoreo de seguridad, optimización de velocidad y ediciones incluidas.",
            tw_title="Planes de Cuidado Web. Mantenimiento Mensual",
            tw_desc="Planes accesibles de mantenimiento mensual. Actualizaciones, respaldos diarios, seguridad, velocidad y ediciones incluidas.",
        ),
    ),
    "blog.html": dict(
        es="blog.html",
        meta=dict(
            title="Blog. Consejos de Diseño Web para Negocios",
            desc="Consejos prácticos de diseño web para dueños de pequeños negocios. Sin jerga, sin ventas forzadas, solo información directa de un diseñador independiente.",
            og_title="Blog. Consejos de Diseño Web para Negocios",
            og_desc="Consejos prácticos de diseño web para dueños de pequeños negocios. Sin jerga, sin ventas forzadas, solo información directa.",
            tw_title="Blog. Consejos de Diseño Web para Negocios",
            tw_desc="Consejos prácticos de diseño web para dueños de pequeños negocios. Sin jerga, solo información directa.",
        ),
    ),
    "case-study-rieralaw.html": dict(
        es="caso-riera-law.html",
        meta=dict(
            title="Caso de Éxito: Riera Law Firm | Made by Sebby",
            desc="Cómo Sebby reconstruyó la presencia web de un bufete de valores, más de 70 páginas bilingües, limpieza de DNS, correo restaurado y cuidado continuo.",
            og_title="Caso de Éxito: Riera Law Firm | Made by Sebby",
            og_desc="Cómo Sebby reconstruyó la presencia web completa de un bufete de valores, más de 70 páginas bilingües, limpieza de DNS, correo restaurado y cuidado web continuo.",
            tw_title="Caso de Éxito: Riera Law Firm | Made by Sebby",
            tw_desc="Cómo Sebby reconstruyó la presencia web de un bufete de valores, más de 70 páginas bilingües y cuidado web continuo.",
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
    "web-design-for-law-firms.html": dict(
        es="diseno-web-para-abogados.html",
        meta=dict(
            title="Diseño Web para Bufetes de Abogados",
            desc="Diseño web bilingüe para bufetes en Miami y el sur de Florida. Páginas de áreas de práctica que posicionan, biografías que convierten, y un sitio ya probado en un bufete de 70 páginas.",
            og_title="Diseño Web para Bufetes de Abogados",
            og_desc="Sitios web bilingües para bufetes que responden lo que un cliente preocupado realmente pregunta. Áreas de práctica, biografías y formularios que funcionan.",
            tw_title="Diseño Web para Bufetes de Abogados",
            tw_desc="Sitios web bilingües para bufetes. Áreas de práctica que posicionan, biografías que convierten, formularios que funcionan.",
        ),
    ),
    "web-design-miami.html": dict(
        es="diseno-web-miami.html",
        meta=dict(
            title="Diseño Web en Miami para Pequeños Negocios",
            desc="Diseño web en Miami para pequeños negocios. Sitios personalizados que generan confianza y convierten visitantes. Bilingüe, móvil primero. Consulta gratis.",
            og_title="Diseño Web en Miami para Pequeños Negocios",
            og_desc="Diseño web en Miami para pequeños negocios, sitios personalizados que generan confianza y convierten visitantes. Bilingüe (español e inglés), móvil primero, con cuidado web continuo.",
            tw_title="Diseño Web en Miami para Pequeños Negocios",
            tw_desc="Diseño web en Miami para pequeños negocios, sitios personalizados que generan confianza y convierten. Bilingüe y móvil primero.",
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
            title="Diseño Web Santo Domingo. Páginas Web | Made by Sebby",
            desc="Diseño web profesional en Santo Domingo y República Dominicana. Sitios personalizados, rápidos, modernos y optimizados para móvil.",
            og_title="Diseño Web Santo Domingo. Páginas Web para Negocios",
            og_desc="Diseño web profesional en Santo Domingo y República Dominicana. Sitios personalizados, rápidos, modernos y optimizados para móvil.",
            tw_title="Diseño Web Santo Domingo. Páginas Web para Negocios",
            tw_desc="Diseño web profesional en Santo Domingo y República Dominicana. Sitios web personalizados para negocios, rápidos, modernos y optimizados para móvil.",
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
            title="Mensaje Enviado. Made by Sebby",
            desc="Gracias por escribir. Te responderé pronto.",
            og_title="Mensaje Enviado. Made by Sebby",
            og_desc="Gracias por escribir. Te responderé pronto.",
            tw_title="Mensaje Enviado. Made by Sebby",
            tw_desc="Gracias por escribir. Te responderé pronto.",
        ),
    ),
    "blog/5-signs-your-website-is-losing-clients.html": dict(
        es="blog/5-senales-de-que-tu-sitio-web-pierde-clientes.html",
        meta=dict(
            title="5 Señales de Que Tu Sitio Web Pierde Clientes",
            desc="Tu sitio web debería atraer negocio, no ahuyentarlo. Aquí hay 5 señales de alerta de que tu sitio te está costando clientes, y qué hacer con cada una.",
            og_title="5 Señales de Que Tu Sitio Web Pierde Clientes",
            og_desc="Tu sitio web debería atraer negocio, no ahuyentarlo. Aquí hay 5 señales de alerta de que tu sitio te está costando clientes, y qué hacer con cada una.",
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
            og_desc="Precios reales de sitios web para pequeños negocios en 2026. Constructores DIY, freelancers y agencias comparados, con lo que realmente recibes en cada rango de precio.",
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
            desc="La mayoría de quienes buscan abogado empiezan en Google. Esto es lo que hace un sitio web bien construido para un bufete, con datos reales y precios honestos.",
            og_title="Qué Hace un Sitio Web por un Bufete de Abogados",
            og_desc="La mayoría de quienes buscan abogado empiezan en Google. Esto es lo que hace un sitio web bien construido para un bufete, con datos reales, ejemplos reales y precios honestos.",
            tw_title="Qué Hace un Sitio Web por un Bufete de Abogados",
            tw_desc="La mayoría de quienes buscan abogado empiezan en Google. Esto es lo que hace un buen sitio web para un bufete.",
        ),
    ),
    "blog/should-i-use-ai-to-build-my-website.html": dict(
        es="blog/deberia-usar-ia-para-crear-mi-sitio-web.html",
        meta=dict(
            title="¿Deberías Usar IA para Crear Tu Sitio Web?",
            desc="Puedes crear un sitio web con IA, y a veces deberías. Una mirada honesta a lo que hacen bien, dónde se detienen, y 12 cosas que revisar en cualquier sitio hecho con IA.",
            og_title="¿Deberías Usar IA para Crear Tu Sitio Web?",
            og_desc="Puedes crear un sitio web con IA, y a veces deberías. Qué hacen bien los constructores con IA, dónde se detienen, y la capa invisible que suelen pasar por alto.",
            tw_title="¿Deberías Usar IA para Crear Tu Sitio Web?",
            tw_desc="Puedes crear un sitio web con IA, y a veces deberías. Qué hacen bien, dónde se detienen, y la capa invisible que suelen pasar por alto.",
        ),
    ),
    "blog/does-your-restaurant-need-a-website.html": dict(
        es="blog/necesita-tu-restaurante-un-sitio-web.html",
        meta=dict(
            title="¿Necesita Tu Restaurante un Sitio Web? | Made by Sebby",
            desc="La mayoría de los restaurantes dependen solo de Instagram y Google Maps. Por qué un sitio web propio atrae más clientes, y qué debe incluir.",
            og_title="¿Necesita Tu Restaurante un Sitio Web? | Made by Sebby",
            og_desc="La mayoría de los restaurantes dependen solo de Instagram y Google Maps. Por qué un sitio web propio atrae más clientes, y qué debe incluir realmente.",
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

# Vanity short links for places where a URL is read by a human rather than
# clicked: an Instagram bio, a printed page left at a front desk, a voicemail.
#
# The point is attribution. Instagram's in-app browser frequently drops the
# referrer, so a visit from the bio arrives in GA4 as "direct" and cannot be told
# apart from someone typing the domain. The stub carries the campaign tags so the
# destination records where the visit came from, while the bio shows something
# short enough to read aloud.
#
# GA4 is deliberately NOT on the stub. It would log a pageview here as well as at
# the destination, turning one visit into two and attributing neither cleanly.
#
# WHEN TO ADD ONE. Only where the URL is READ by a human rather than clicked:
# an Instagram bio, a printed page left at a front desk, a number said aloud.
# Anywhere the link is genuinely clickable, put the tagged URL straight in the
# href instead and let the visible text stay clean. A stub adds a hop, and a hop
# is a place a redirect can be blocked or a visitor can bounce.
SHORTLINKS = {
    "ig/index.html": "/?utm_source=instagram&utm_medium=bio",
    "li/index.html": "/?utm_source=linkedin&utm_medium=bio",
    "fb/index.html": "/?utm_source=facebook&utm_medium=bio",
    # Printed material and walk-ins. Short enough to read off a page, and it is
    # the only way a handed-over sheet can ever be attributed at all.
    "card/index.html": "/?utm_source=print&utm_medium=card",
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
    "Email": "Correo electrónico",
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
    "Made by Sebby: Web Design, Development, Website Care":
        "Made by Sebby: Diseño Web, Desarrollo, Cuidado Web",
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
    "Riera Law Firm homepage: securities arbitration attorney with courthouse background":
        "Página de inicio de Riera Law Firm: abogado de arbitraje de valores con fondo de tribunal",
    "Elite Care Recovery homepage: premium cold and compression therapy for post-operative patients":
        "Página de inicio de Elite Care Recovery: terapia premium de frío y compresión para pacientes postoperatorios",
    "Riera Law Firm homepage hero": "Portada del sitio de Riera Law Firm",
    "Elite Care Recovery homepage hero": "Portada del sitio de Elite Care Recovery",
    "Elite Care Recovery, South Florida": "Elite Care Recovery, sur de Florida",
    "Elite Care Recovery: South Florida": "Elite Care Recovery: sur de Florida",
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
    # Head strings come from the PAGES table, which is inserted here, AFTER
    # substitute_prices has already run over the body. So any price token in a
    # meta description has to be resolved locally or it ships as a literal
    # {{PRICE_STARTER}} in the Google snippet. This is exactly how the Spanish
    # pricing page kept advertising the old $1,500 floor for two days after the
    # raise: the visible copy was tokenised and the head was not.
    entry = PAGES[source]
    noindex = entry.get("noindex", False)
    self_url = DOMAIN + url_for(source, lang)
    en_slug = entry.get("en", source)

    if lang == "es":
        meta = entry["meta"]
        html = re.sub(r"<title>.*?</title>",
                      lambda _: "<title>%s</title>" % substitute_prices(meta["title"]), html, count=1, flags=re.S)
        for pattern, value in (
            (r'(<meta name="description" content=")[^"]*(">)', substitute_prices(meta["desc"])),
            (r'(<meta property="og:title" content=")[^"]*(">)', substitute_prices(meta["og_title"])),
            (r'(<meta property="og:description" content=")[^"]*(">)', substitute_prices(meta["og_desc"])),
            (r'(<meta name="twitter:title" content=")[^"]*(">)', substitute_prices(meta["tw_title"])),
            (r'(<meta name="twitter:description" content=")[^"]*(">)', substitute_prices(meta["tw_desc"])),
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
# Canonical NAP: Name, Address, Phone.
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
    # Mirrors the Business Profile exactly, Saturday included. The profile lists
    # Sat 10:00-14:00; omitting it here would be a NAP mismatch on the one
    # attribute a customer acts on.
    "openingHoursSpecification": [
        {
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "opens": "09:00",
            "closes": "18:00",
        },
        {
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": ["Saturday"],
            "opens": "10:00",
            "closes": "14:00",
        },
    ],
    # The profiles the Business Profile itself lists under "Social profiles".
    # sameAs is how the site formally claims them as the same entity.
    "sameAs": [
        "https://www.linkedin.com/company/made-by-sebby/",
        "https://instagram.com/madebysebby",
        "https://clutch.co/profile/made-sebby",
        "https://github.com/SebbyServices",
    ],
}


# ---------------------------------------------------------------------------
# The footer, written once here and injected into every page
#
# It used to be copy-pasted into all 27 sources, which produced seven different
# footers and, more to the point, a footer that did almost nothing. An audit
# found it linked neither pricing, nor contact, nor book.html -- the three pages
# the whole site exists to funnel toward -- carried no phone, no address and no
# copyright line, and linked none of the social profiles the JSON-LD formally
# claims under sameAs. On a local-SEO site the footer is where NAP is expected
# to appear on every page, and it was the one place the business details were
# missing entirely.
#
# Injected as {{FOOTER}} at the very top of render(), which buys three things
# for free: the lang pairs get split like any other content, rewrite_paths turns
# the root-absolute hrefs into the right tree, and the em dash gate applies.
# ---------------------------------------------------------------------------
FOOTER_LINKS = {
    "services": (
        ("/services.html", "What I do", "Qué hago"),
        ("/pricing.html", "Pricing", "Precios"),
        ("/website-care.html", "Website Care", "Cuidado Web"),
        # Deliberately NOT a vertical link. The footer appears on all 52 pages,
        # so naming one industry there tells every other industry the site is
        # not for them, and the target vertical is not settled. The calculator
        # is the highest-intent destination on the site and works for anyone:
        # competitors gate theirs behind a form, this one just answers.
        ("/pricing.html#calculator", "Price calculator", "Calculadora de precios"),
    ),
    "studio": (
        ("/work.html", "Work", "Trabajo"),
        ("/about.html", "About", "Sobre mí"),
        ("/blog.html", "Blog", "Blog"),
        ("/contact.html", "Contact", "Contacto"),
    ),
    # Santo Domingo exists only in the Spanish tree, so the English footer must
    # not link it. This is the one place the two footers legitimately differ,
    # and check-consistency.py knows about it by name.
    "areas": (
        ("/web-design-miami.html", "Miami", "Miami"),
        ("/web-design-fort-lauderdale.html", "Fort Lauderdale", "Fort Lauderdale"),
        ("/diseno-web-santo-domingo.html", None, "Santo Domingo"),
    ),
}

SOCIAL_ICONS = {
    "https://www.linkedin.com/company/made-by-sebby/": (
        "LinkedIn",
        "M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.42v1.56h.04c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zm1.78 13.02H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z",
    ),
    "https://instagram.com/madebysebby": (
        "Instagram",
        "M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.72 3.72 0 0 1-1.38-.9c-.42-.42-.68-.82-.9-1.38-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16zM12 0C8.74 0 8.33.01 7.05.07 5.78.13 4.9.33 4.14.63c-.79.31-1.46.72-2.13 1.38C1.35 2.68.94 3.35.63 4.14.33 4.9.13 5.78.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.06 1.27.26 2.15.56 2.91.31.79.72 1.46 1.38 2.13.67.67 1.34 1.08 2.13 1.38.76.3 1.64.5 2.91.56C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c1.28-.06 2.15-.26 2.91-.56.79-.31 1.46-.72 2.13-1.38.67-.67 1.08-1.34 1.38-2.13.3-.76.5-1.64.56-2.91.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.06-1.28-.26-2.15-.56-2.91-.31-.79-.72-1.46-1.38-2.13C21.32 1.35 20.65.94 19.86.63c-.76-.3-1.64-.5-2.91-.56C15.67.01 15.26 0 12 0zm0 5.84a6.16 6.16 0 1 0 0 12.32 6.16 6.16 0 0 0 0-12.32zM12 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm7.85-10.4a1.44 1.44 0 1 1-2.88 0 1.44 1.44 0 0 1 2.88 0z",
    ),
}


def footer_html(lang):
    """The one footer. Root-absolute hrefs; rewrite_paths retargets them."""
    def col(key, en_head, es_head):
        items = []
        for href, en, es in FOOTER_LINKS[key]:
            if en is None and lang != "es":
                continue          # Spanish-only page, English tree skips it
            label = ('<span lang="en">%s</span><span lang="es">%s</span>' % (en, es)
                     if en is not None else es)
            items.append('        <li><a href="%s">%s</a></li>' % (href, label))
        return (
            '      <div class="ft-col">\n'
            '        <p class="ft-head" id="ft-%s"><span lang="en">%s</span>'
            '<span lang="es">%s</span></p>\n'
            '        <ul aria-labelledby="ft-%s">\n%s\n        </ul>\n'
            '      </div>' % (key, en_head, es_head, key, "\n".join(items)))

    social = "\n".join(
        '        <a href="%s" rel="me noopener" target="_blank" title="%s">'
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="%s"/></svg>'
        '<span class="sr-only">%s</span></a>' % (url, name, path, name)
        for url, (name, path) in SOCIAL_ICONS.items())

    tel_href = "tel:" + NAP["telephone"].replace("-", "")
    return FOOTER_TEMPLATE % dict(
        services=col("services", "What I do", "Qué hago"),
        studio=col("studio", "Studio", "Estudio"),
        areas=col("areas", "Where I work", "Dónde trabajo"),
        social=social,
        tel_href=tel_href,
        tel_display="(786) 543-1417",
        email=NAP["email"],
        year=datetime.date.today().year,
    )


FOOTER_TEMPLATE = '''<footer>
  <div class="wrap foot-inner">
      <div class="ft-brand">
        <img class="foot-logo-light" src="/assets/logo/sebby-horizontal.svg" alt="Made by Sebby: Web Design, Development, Website Care" width="200" height="52">
        <img class="foot-logo-dark" src="/assets/logo/sebby-horizontal-white.svg" alt="Made by Sebby: Web Design, Development, Website Care" width="200" height="52">
        <p class="ft-tag"><span lang="en">This site was <strong>Made by Sebby</strong>. Yours can be too.</span><span lang="es">Este sitio fue <strong>hecho por Sebby</strong>. El tuyo también puede serlo.</span></p>
        <address class="ft-nap">
          <span><span lang="en">Miami, Florida 33175, United States</span><span lang="es">Miami, Florida 33175, Estados Unidos</span></span>
          <a href="%(tel_href)s">%(tel_display)s</a>
          <a href="mailto:%(email)s">%(email)s</a>
          <span class="ft-hours"><span lang="en">Mon to Fri, 9am to 6pm ET. Sat, 10am to 2pm.</span><span lang="es">Lun a Vie, 9am a 6pm ET. Sáb, 10am a 2pm.</span></span>
        </address>
      </div>
%(services)s
%(studio)s
      <div class="ft-col ft-col-cta">
%(areas)s
        <a class="ft-cta" href="/book.html"><span lang="en">Book a free call</span><span lang="es">Agenda una llamada gratis</span></a>
      </div>
  </div>
  <div class="wrap ft-bottom">
    <p class="ft-copy">&copy; %(year)s Made by Sebby. <span lang="en">Web design and website care for small businesses in Miami, South Florida, and Santo Domingo.</span><span lang="es">Diseño web y cuidado de sitios para pequeños negocios en Miami, el sur de Florida y Santo Domingo.</span></p>
    <div class="ft-legal">
      <a href="/privacy.html"><span lang="en">Privacy</span><span lang="es">Privacidad</span></a>
      <a href="/terms.html"><span lang="en">Terms</span><span lang="es">Términos</span></a>
    </div>
    <div class="ft-social">
%(social)s
    </div>
  </div>
</footer>'''

# Appended to each page's single <style> block, so it lands last and wins the
# cascade over the old flex rules still sitting in the sources.
FOOTER_CSS = '''
/* Footer: generated by build.py. Do not edit here, edit FOOTER_TEMPLATE. */
footer{border-top:1px solid var(--line);padding:56px 0 0;margin-top:0}
.foot-inner{display:grid;grid-template-columns:1.6fr 1fr 1fr 1.1fr;gap:40px;align-items:start;justify-content:normal}
.ft-brand{display:flex;flex-direction:column;align-items:flex-start;gap:14px}
.ft-brand img{height:44px;width:auto;display:block}
.foot-logo-dark{display:none}
.ft-tag{font-size:.92rem;color:var(--muted);line-height:1.6;max-width:32ch;margin:0}
.ft-tag strong{color:var(--ink);font-weight:700}
.ft-nap{display:flex;flex-direction:column;gap:5px;font-size:.88rem;color:var(--muted);font-style:normal;line-height:1.5}
.ft-nap a{color:var(--muted)}
.ft-nap a:hover{color:var(--purple)}
.ft-hours{font-size:.82rem;opacity:.85}
.ft-head{font-size:.74rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--ink);margin:0 0 14px}
.ft-col ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:11px}
.ft-col a{font-size:.9rem;color:var(--muted)}
.ft-col a:hover{color:var(--purple)}
.ft-col-cta{display:flex;flex-direction:column;gap:22px;align-items:flex-start}
.ft-col-cta a.ft-cta{display:inline-block;padding:11px 20px;border-radius:var(--radius-sm);background:var(--purple);color:#fff;font-size:.88rem;font-weight:600;line-height:1}
.ft-col-cta a.ft-cta:hover{background:var(--purple-deep);color:#fff}
.ft-bottom{display:flex;flex-wrap:wrap;align-items:center;gap:14px 26px;margin-top:44px;padding-top:22px;padding-bottom:26px;border-top:1px solid var(--line)}
.ft-copy{font-size:.82rem;color:var(--muted);margin:0;flex:1 1 320px;line-height:1.6}
.ft-legal{display:flex;gap:20px;font-size:.82rem}
.ft-legal a{color:var(--muted)}
.ft-legal a:hover{color:var(--purple)}
.ft-social{display:flex;gap:10px;margin-left:auto}
.ft-social a{width:34px;height:34px;border:1px solid var(--line);border-radius:50%;display:grid;place-items:center;color:var(--muted)}
.ft-social a:hover{color:var(--purple);border-color:var(--purple)}
.ft-social svg{width:15px;height:15px;fill:currentColor}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:900px){.foot-inner{grid-template-columns:1fr 1fr;gap:34px}.ft-brand{grid-column:1/-1}}
@media(max-width:560px){.foot-inner{grid-template-columns:1fr;gap:30px}.ft-bottom{margin-top:34px}.ft-social{margin-left:0}}
:root:not([data-theme="light"]) .foot-logo-light{display:none}
:root:not([data-theme="light"]) .foot-logo-dark{display:block}
[data-theme="dark"] .foot-logo-light{display:none}
[data-theme="dark"] .foot-logo-dark{display:block}
[data-theme="light"] .foot-logo-light{display:block}
[data-theme="light"] .foot-logo-dark{display:none}
'''

# ---------------------------------------------------------------------------
# Canonical prices, Aug 13 2026.
#
# The site was telling prospects FOUR different things a website costs: the home
# page said $2,000-$8,000, the pricing page $1,500/$3,500/$7,000, the Miami and
# Fort Lauderdale pages $3,000-$5,000, and the DR one-pager $500-$2,500. Read the
# Miami page then click Pricing and the number halved. Transparent pricing is the
# stated differentiator against every agency that answers "it depends", so a
# contradiction here costs more than it would anywhere else.
#
# Floor raised from $1,500 to $2,500: the old entry sat in the dead zone between
# the commodity shops ($349-$700) and the South Florida boutique band
# ($2,500-$8,000) -- the one position with no story. Competitors deliver at
# $1,999 with published turnaround times and 11 five-star reviews.
#
# Sources are prose in three files, in JSON-LD and in BOTH language spans, so
# they are substituted from here rather than hand-edited. check-consistency.py
# fails on any bare price literal left in src/.
#
# USD is explicit because Sebby bills in USD and Dominican cards work: without
# it a DR reader may reasonably assume pesos and read a number 58x off.
PRICES = {
    "{{PRICE_STARTER}}": "$2,500",
    "{{PRICE_CUSTOM}}": "$5,000",
    "{{PRICE_PREMIUM}}": "$9,000",
    "{{PRICE_LOW}}": "$2,500",
    "{{PRICE_HIGH}}": "$9,000",
    # Care plans keep their prices -- they are already well placed against a
    # $79-$350/mo market. Tokenised anyway so a change is one edit.
    "{{CARE_ESSENTIALS}}": "$99",
    "{{CARE_STANDARD}}": "$249",
    "{{CARE_GROWTH}}": "$549",
    "{{CARE_OVERAGE}}": "$125",
    "{{PRICE_SPANISH_STRATEGY}}": "$750",
    "{{PRICE_EXTRA_PAGE}}": "$150",

    # Delivery windows and post-launch support. Tokenized 2026-08-15 because
    # precios.html promised the DR a faster build than pricing.html sold in
    # Miami (3-4 vs 4-6, 4-6 vs 6-10) on the same page that says the terms are
    # identical everywhere, and the Premium support period read 90 days on the
    # tier card and 30 days in the FAQ four hundred lines below it.
    "{{WEEKS_STARTER}}": "2\u20133",
    "{{WEEKS_CUSTOM}}": "4\u20136",
    "{{WEEKS_PREMIUM}}": "6\u201310",
    "{{SUPPORT_STARTER}}": "14",
    "{{SUPPORT_CUSTOM}}": "30",
    "{{SUPPORT_PREMIUM}}": "90",
    # Edit packs. These were bare literals in two source files, allow-listed in
    # the checker as "market figures" when they are nothing of the sort: they
    # are our prices, and a change would have had to be made by hand in both
    # places with nothing to catch a mismatch. That is the exact failure this
    # table exists to prevent, and it had quietly survived the last sweep.
    "{{EDIT_SINGLE}}": "$49",
    "{{EDIT_3PACK}}": "$129",
    "{{EDIT_5PACK}}": "$199",
    # Page counts per tier. Tokenised because pricing.html and the DR one-pager
    # had drifted apart on exactly this: the same $2,500 bought "up to 5 pages"
    # on one and "1-3 paginas" on the other. Prices matching is not enough if
    # what the price BUYS disagrees, and the DR page was the stingier of the two,
    # which is the opposite of the intent.
    "{{PAGES_STARTER}}": "5",
    "{{PAGES_CUSTOM}}": "10",
    # What competing agencies charge. A claim about others, not a price of ours.
    "{{AGENCY_LOW}}": "$10,000",
    "{{AGENCY_HIGH}}": "$30,000",
    "{{USD}}": "USD",
    # Numeric forms for the price calculator's JavaScript. Same source as the
    # display strings above, so the calculator cannot quote a number the pricing
    # page does not. The upper bounds are the honest top of each tier's scope --
    # the calculator reports a range, not a single figure, because a form cannot
    # see everything a conversation does.
    "{{N_STARTER}}": "2500",
    "{{N_STARTER_MAX}}": "3500",
    "{{N_CUSTOM}}": "5000",
    "{{N_CUSTOM_MAX}}": "6500",
    "{{N_PREMIUM}}": "9000",
    "{{N_PREMIUM_MAX}}": "12000",
    "{{N_CARE_ESSENTIALS}}": "99",
    "{{N_CARE_STANDARD}}": "249",
    "{{N_CARE_GROWTH}}": "549",
    "{{N_SPANISH_STRATEGY}}": "750",
    "{{N_EXTRA_PAGE}}": "150",
}


def load_env(path=".env"):
    """Minimal .env reader -- stdlib only, nothing to install."""
    values = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# Web3Forms access key: free, 250 submissions/month, no backend, which is what a
# GitHub Pages site needs.
#
# Read from .env so it lives in one editable place. But be precise about what that
# buys: .env keeps the key out of THIS file, and out of git. It does NOT make the
# key secret -- it is compiled into the built HTML and committed to a public repo,
# because a static site has nowhere else to put it. That is the documented design.
#
# The exposure is bounded. Someone holding the key can submit to the form, which
# is exactly what they could do by visiting the contact page. They cannot read
# submissions or reach anything else on the account. Rotate at web3forms.com if
# it is ever abused.
#
# A missing .env falls back to the placeholder, which check-consistency.py fails
# on. That is deliberate: a fresh clone rebuilding without .env would otherwise
# quietly replace a working key with nothing.
def _annual(monthly_token):
    """Annual care price: eleven months, since the twelfth is free.

    Derived rather than typed so the annual figure cannot contradict the monthly
    one. Both appear on website-care.html and pricing.html.
    """
    n = int(PRICES[monthly_token].lstrip("$").replace(",", ""))
    return "${:,}".format(n * 11)


def _times(token, n):
    v = int(PRICES[token].lstrip("$").replace(",", ""))
    return "${:,}".format(v * n)


def _per_edit(pack_token, count):
    """Rounded to the dollar, which is how the page has always shown it."""
    v = int(PRICES[pack_token].lstrip("$").replace(",", ""))
    return "${:,}".format(int(round(v / float(count))))


def _monthly_equivalent(monthly_token):
    """What the annual plan works out to per month: eleven months over twelve.

    Shown beside the annual price so the saving is legible without arithmetic.
    Derived for the same reason as _annual: three related numbers typed by hand
    are three chances to contradict each other.
    """
    n = int(PRICES[monthly_token].lstrip("$").replace(",", ""))
    return "${:,.2f}".format(n * 11 / 12.0)


PRICES.update({
    "{{CARE_ESSENTIALS_YR}}": _annual("{{CARE_ESSENTIALS}}"),
    "{{CARE_STANDARD_YR}}": _annual("{{CARE_STANDARD}}"),
    "{{CARE_GROWTH_YR}}": _annual("{{CARE_GROWTH}}"),
    "{{CARE_ESSENTIALS_MO_EQ}}": _monthly_equivalent("{{CARE_ESSENTIALS}}"),
    "{{CARE_STANDARD_MO_EQ}}": _monthly_equivalent("{{CARE_STANDARD}}"),
    "{{CARE_GROWTH_MO_EQ}}": _monthly_equivalent("{{CARE_GROWTH}}"),
    # What twelve monthly payments cost, shown beside the annual price so the
    # "one month free" claim is checkable rather than asserted.
    "{{CARE_ESSENTIALS_12X}}": _times("{{CARE_ESSENTIALS}}", 12),
    # Per-edit cost of each pack, which is the only reason the packs look like
    # value. Derived so it cannot survive a pack price change unnoticed.
    "{{EDIT_3PACK_EACH}}": _per_edit("{{EDIT_3PACK}}", 3),
    "{{EDIT_5PACK_EACH}}": _per_edit("{{EDIT_5PACK}}", 5),
})

FORM_ACCESS_KEY = load_env().get("WEB3FORMS_ACCESS_KEY", "REPLACE_WITH_WEB3FORMS_KEY")

TOKENS = {"{{FORM_ACCESS_KEY}}": FORM_ACCESS_KEY}


def substitute_prices(html):
    for token, value in PRICES.items():
        html = html.replace(token, value)
    for token, value in TOKENS.items():
        html = html.replace(token, value)
    return html


# Stable identity so every page's block is understood as ONE business rather
# than a separate branch per landing page.
BUSINESS_ID = DOMAIN + "/#business"
LOCAL_TYPES = ("ProfessionalService", "LocalBusiness")

# Schema keys holding human prose rather than identifiers or URLs.
PROSE_KEYS = ("name", "description", "headline", "slogan", "reviewBody", "text",
              "articleBody", "acceptedAnswer", "disambiguatingDescription")


# ---------------------------------------------------------------------------
# Breadcrumb labels.
#
# These were hand-written per page and had drifted badly: 13 Spanish pages said
# "Home" instead of "Inicio", and seven carried fully English labels -- "Portfolio",
# "Website Care Plans", "Riera Law Firm Case Study" -- on pages declaring
# inLanguage "es". Breadcrumbs render in search results, so that was English
# showing under a Spanish listing.
#
# Generated from here now, URLs included, so the label and the tree cannot
# disagree. Two English labels are also shortened: "Web Design, Website Care &
# SEO Services" and "Website Care Plans" are page titles, not breadcrumbs, and
# Google truncates them.
CRUMB_HOME = {"en": "Home", "es": "Inicio"}
CRUMBS = {
    "services.html": {"en": "Services", "es": "Servicios"},
    "work.html": {"en": "Portfolio", "es": "Portafolio"},
    "about.html": {"en": "About Sebby", "es": "Sobre Sebby"},
    "contact.html": {"en": "Contact", "es": "Contacto"},
    "book.html": {"en": "Book a Call", "es": "Reservar Llamada"},
    "pricing.html": {"en": "Pricing", "es": "Precios"},
    "website-care.html": {"en": "Website Care", "es": "Cuidado Web"},
    "blog.html": {"en": "Blog", "es": "Blog"},
    "privacy.html": {"en": "Privacy Policy", "es": "Política de Privacidad"},
    "terms.html": {"en": "Terms of Service", "es": "Términos de Servicio"},
    "case-study-rieralaw.html": {"en": "Riera Law Firm Case Study",
                                 "es": "Caso de Éxito: Riera Law Firm"},
    "case-study-elitecare.html": {"en": "Elite Care Recovery Case Study",
                                  "es": "Caso de Éxito: Elite Care Recovery"},
    "web-design-for-law-firms.html": {"en": "Web Design for Law Firms",
                                      "es": "Diseño Web para Bufetes"},
    "web-design-miami.html": {"en": "Web Design Miami", "es": "Diseño Web Miami"},
    "web-design-fort-lauderdale.html": {"en": "Web Design Fort Lauderdale",
                                        "es": "Diseño Web Fort Lauderdale"},
    "diseno-web-santo-domingo.html": {"en": "Web Design Santo Domingo",
                                      "es": "Diseño Web Santo Domingo"},
    "blog/5-signs-your-website-is-losing-clients.html": {
        "en": "5 Signs Your Website Is Losing You Clients",
        "es": "5 Señales de Que Tu Sitio Web Pierde Clientes"},
    "blog/what-is-website-care.html": {
        "en": "What Website Care Actually Means",
        "es": "Qué Significa Realmente el Cuidado Web"},
    "blog/how-much-does-a-website-cost.html": {
        "en": "How Much Does a Website Cost in 2026?",
        "es": "¿Cuánto Cuesta un Sitio Web en 2026?"},
    "blog/why-your-competitor-gets-calls-from-google.html": {
        "en": "Why Your Competitor Gets Calls From Google",
        "es": "Por Qué Tu Competencia Recibe Llamadas de Google"},
    "blog/what-a-website-does-for-a-law-firm.html": {
        "en": "What a Website Actually Does for a Law Firm",
        "es": "Qué Hace un Sitio Web por un Bufete de Abogados"},
    "blog/should-i-use-ai-to-build-my-website.html": {
        "en": "Should You Use AI to Build Your Website?",
        "es": "¿Deberías Usar IA para Crear Tu Sitio Web?"},
    "blog/does-your-restaurant-need-a-website.html": {
        "en": "Does Your Restaurant Need a Website?",
        "es": "¿Necesita Tu Restaurante un Sitio Web?"},
}


def breadcrumbs_for(source, lang):
    """Home > [Blog >] this page, in the right language with the right URLs."""
    if source not in CRUMBS:
        return None
    trail = [(CRUMB_HOME[lang], DOMAIN + url_for("index.html", lang))]
    if source.startswith("blog/"):
        trail.append((CRUMBS["blog.html"][lang], DOMAIN + url_for("blog.html", lang)))
    trail.append((CRUMBS[source][lang], DOMAIN + url_for(source, lang)))
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(trail, start=1)
        ],
    }


# Schema prose describing SERVICES rather than the page, so no page-level meta
# value covers it and it does not appear verbatim in the body either -- which is
# why the translation memory could not resolve it. Hand-written.
#
# Jorge's testimonial is the Spanish text already published on the portfolio page,
# not a re-translation, so the schema quotes him exactly as the site does.
SCHEMA_ES = {
    "Custom website design and development for small businesses. Mobile-first, fast-loading, built to convert.": "Diseño y desarrollo web personalizado para pequeños negocios. Móvil primero, carga rápida, hecho para convertir.",
    "Custom web design and development for small businesses. Mobile-first, fast-loading websites built to convert visitors into customers.": "Diseño y desarrollo web personalizado para pequeños negocios. Sitios rápidos, móvil primero, hechos para convertir visitantes en clientes.",
    "Custom Web Design & Development": "Diseño y Desarrollo Web Personalizado",
    "Monthly website maintenance including updates, backups, security, speed optimization, and small edits.": "Mantenimiento mensual de sitios web: actualizaciones, respaldos, seguridad, optimización de velocidad y ediciones pequeñas.",
    "Monthly website maintenance including software updates, daily backups, security monitoring, speed optimization, and small edits.": "Mantenimiento mensual: actualizaciones de software, respaldos diarios, monitoreo de seguridad, optimización de velocidad y ediciones pequeñas.",
    "Website Care. Monthly Website Maintenance": "Cuidado Web. Mantenimiento Mensual",
    "Local SEO, Google Business Profile setup, and honest search optimization that compounds over time.": "SEO local, configuración del Perfil de Empresa de Google y optimización de búsqueda honesta que se acumula con el tiempo.",
    "Free Web Design Consultation": "Consulta Gratuita de Diseño Web",
    "SEO. Get Found on Google": "SEO. Aparece en Google",
    "Weekly tested software and security updates, daily off-site backups, uptime and security monitoring around the clock, monthly health report in plain English, and response within two business days": "Actualizaciones semanales de software y seguridad verificadas, respaldos diarios fuera del sitio, monitoreo de disponibilidad y seguridad las 24 horas, informe mensual de salud en términos claros, y respuesta dentro de dos días hábiles",
    "Everything in Essentials plus 1 hour of content edits, quarterly speed and performance tuning, backup restoration if compromised, and one-business-day response": "Todo lo del plan Esencial más 1 hora de ediciones de contenido, optimización trimestral de velocidad y rendimiento, restauración desde respaldo si el sitio es comprometido, y respuesta en un día hábil",
    "Everything in Standard plus 2 hours monthly for edits and on-page SEO, monthly Search Console and Analytics review, quarterly 30-minute strategy call, and 4-business-hour critical outage response": "Todo lo del plan Estándar más 2 horas mensuales para ediciones y SEO on-page, revisión mensual de Search Console y Analytics, llamada trimestral de estrategia de 30 minutos, y respuesta a interrupciones críticas en 4 horas hábiles",
    "Up to {{PAGES_STARTER}} pages, mobile-responsive design, contact form, basic SEO setup, Google Analytics. Delivered in {{WEEKS_STARTER}} weeks.": "Hasta {{PAGES_STARTER}} páginas, diseño adaptable a móvil, formulario de contacto, configuración básica de SEO, Google Analytics. Entrega en {{WEEKS_STARTER}} semanas.",
    "Up to {{PAGES_CUSTOM}} pages, custom design from scratch, copywriting help, SEO optimization, speed optimization, bilingual as standard. Delivered in {{WEEKS_CUSTOM}} weeks.": "Hasta {{PAGES_CUSTOM}} páginas, diseño personalizado desde cero, ayuda con redacción, optimización SEO, optimización de velocidad, bilingüe de serie. Entrega en {{WEEKS_CUSTOM}} semanas.",
    "Unlimited pages, full custom design and strategy, professional copywriting, advanced SEO, integrations, {{SUPPORT_PREMIUM}} days post-launch optimization. Delivered in {{WEEKS_PREMIUM}} weeks.": "Páginas ilimitadas, diseño y estrategia totalmente personalizados, redacción profesional, SEO avanzado, integraciones, {{SUPPORT_PREMIUM}} días de optimización post-lanzamiento. Entrega en {{WEEKS_PREMIUM}} semanas.",
    "Freelance web design studio run by Sebastian (Sebby), building custom websites for small businesses. Not affiliated with the Sebby fashion/outerwear brand.": "Estudio de diseño web independiente dirigido por Sebastian (Sebby), que construye sitios web personalizados para pequeños negocios. Sin relación con la marca de ropa Sebby.",
    "Freelance web designer and developer who builds custom websites for small businesses with personal attention and plain English communication.": "Diseñador y desarrollador web independiente que construye sitios web personalizados para pequeños negocios, con atención personal y comunicación clara.",
    "Sebby rebuilt my law firm's entire web presence. He works fast, explains everything in plain English, and treats my site like it's his own. I trust him with the online face of my practice.": "Sebby reconstruyó toda la presencia web de mi bufete. Trabaja rápido, explica todo en términos claros, y trata mi sitio como si fuera suyo. Le confío la cara digital de mi práctica."
}

# Resolve tokens on BOTH sides at import. Keying on raw English is what broke on
# 2026-08-15: schema strings are price-substituted before the JSON-LD walk runs,
# so every literal in a key had to be hand-synced, and when "bilingual option"
# became "bilingual as standard" the lookup missed and the Spanish offer catalog
# silently shipped three English paragraphs to Google.
SCHEMA_ES = {substitute_prices(k): substitute_prices(v) for k, v in SCHEMA_ES.items()}


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
    crumbs = breadcrumbs_for(source, lang)

    def fix_block(m):
        raw = m.group(1)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return m.group(0)

        # Breadcrumbs are generated, not translated -- see CRUMBS.
        if isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
            if crumbs is None:
                return m.group(0)
            return '<script type="application/ld+json">\n%s\n</script>' % json.dumps(
                crumbs, ensure_ascii=False, indent=2)

        # Page-level types whose name/description describe THIS page, so the
        # Spanish head strings we already wrote are the correct values. Without
        # this, 31 English strings sat inside nodes declaring inLanguage "es".
        top_types = ("WebPage", "BlogPosting", "ProfessionalService", "LocalBusiness",
                     "ContactPage", "CollectionPage", "AboutPage", "ItemPage",
                     "WebSite", "Blog", "Article")

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
                        translated = SCHEMA_ES.get(html_unescape(value)) or resolved(value)
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

                if lang == "es" and not spanish_source:
                    meta = PAGES[source].get("meta")
                    if meta and node.get("@type") in top_types:
                        if "headline" in node:
                            node["headline"] = meta["title"]
                        if "description" in node:
                            node["description"] = meta["desc"]
                        # Brand identity keeps its real name; a page-level node's
                        # name describes the page, so it follows the title.
                        if "name" in node and node["name"] != NAP["name"]:
                            node["name"] = meta["title"]
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


# Cal.com event slugs, per language.
#
# Measured 2026-08-15: cal.com renders its own widget chrome from the visitor's
# Accept-Language header, so a Spanish-speaking browser already gets "Zona
# horaria", "Selecciona" and "Reservar" with no help from us. No URL parameter
# changes it, ?locale=es and ?lang=es were both tried and both returned English.
#
# What the header does NOT translate is the event's own name, because that is
# account content rather than interface. A Spanish visitor currently books
# something called "Free Website Chat".
#
# Both entries point at the same event on purpose. Pointing the Spanish tree at
# a slug that does not exist yet would 404 the only booking page a Spanish
# speaker can reach, which is worse than an English event title. Create the
# Spanish event type in cal.com, then change one string here.
CAL_LINKS = {
    "en": "madebysebby/chat",
    "es": "madebysebby/chat",
}


def render(source, html, lang):
    # The footer goes in FIRST, before anything reads or splits the document, so
    # it is treated as ordinary page content by every step that follows: its
    # lang pairs get split, its root-absolute hrefs get retargeted at the right
    # tree, and it is covered by the em dash gate like any other copy.
    html = html.replace("{{FOOTER}}", footer_html(lang))
    html = html.replace("{{CAL_LINK}}", CAL_LINKS[lang])
    # Appended to the page's single <style> so it lands last and beats the old
    # flex rules that are still sitting in the sources.
    head, sep, tail = html.rpartition("</style>")
    if sep:
        html = head + FOOTER_CSS + sep + tail
    # Prices resolve FIRST so everything downstream -- the translation memory,
    # the FAQ schema read out of the DOM -- sees real numbers rather than tokens.
    html = substitute_prices(html)
    memory = pair_map(html)          # must be read BEFORE the split removes the pairs
    html = split_language(html, lang)
    html = strip_toggle_machinery(html, lang)
    html = rewrite_head(html, source, lang)
    html = rewrite_jsonld(html, source, lang, memory)
    # Strings living inside inline JS, which no lang= span can reach. The contact
    # form's onsubmit handler hardcoded an English mail subject and redirected to
    # the English thank-you page even from the Spanish form -- so a Spanish
    # visitor who completed the form landed on "Message Sent" in English, while
    # es/gracias.html sat there reachable from nowhere.
    if lang == "es":
        html = html.replace("var s='Website project'", "var s='Proyecto web'")
        html = html.replace(
            "window.location.href='/thank-you.html'",
            "window.location.href='%s'" % url_for("thank-you.html", "es"))

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


SHORTLINK_STUB = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Made by Sebby</title>
<meta name="robots" content="noindex, follow">
<link rel="canonical" href="{domain}{clean}">
<meta http-equiv="refresh" content="0; url={target}">
</head>
<body>
<p>Taking you to <a href="{target}">madebysebby.com</a>.</p>
</body>
</html>
"""

REDIRECT_STUB = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Redirigiendo… | Made by Sebby</title>
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
        # Passthrough pages skip the language split, but NOT price substitution --
        # precios.html is the DR one-pager and quoting stale prices there is
        # exactly the contradiction this table exists to prevent.
        written[name] = substitute_prices(
            open(os.path.join(src_dir, name), encoding="utf-8").read())

    for old, target in REDIRECTS.items():
        written[old] = REDIRECT_STUB.format(domain=DOMAIN, target=target)

    for path, target in SHORTLINKS.items():
        # canonical points at the clean destination, without the campaign tags:
        # a canonical carrying UTM parameters is how a tracked URL ends up
        # indexed as the real one.
        clean = target.split("?")[0] or "/"
        # & must be escaped inside an HTML attribute. Browsers forgive it, the
        # validator does not, and one day a parser will read &utm as an entity.
        written[path] = SHORTLINK_STUB.format(
            domain=DOMAIN, target=target.replace("&", "&amp;"), clean=clean)

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
