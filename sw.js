var CACHE='mbs-v10';
var PAGES=['/','/index.html','/es/','/es/index.html','/services.html','/es/servicios.html','/work.html','/es/portafolio.html','/about.html','/es/sobre-mi.html','/contact.html','/es/contacto.html','/book.html','/es/agendar.html','/pricing.html','/es/precios.html','/website-care.html','/es/cuidado-web.html','/blog.html','/es/blog.html','/case-study-rieralaw.html','/es/caso-riera-law.html','/case-study-elitecare.html','/es/caso-elite-care.html','/web-design-miami.html','/es/diseno-web-miami.html','/web-design-fort-lauderdale.html','/es/diseno-web-fort-lauderdale.html','/es/diseno-web-santo-domingo.html','/privacy.html','/es/privacidad.html','/terms.html','/es/terminos.html','/thank-you.html','/es/gracias.html','/blog/5-signs-your-website-is-losing-clients.html','/es/blog/5-senales-de-que-tu-sitio-web-pierde-clientes.html','/blog/what-is-website-care.html','/es/blog/que-es-el-cuidado-web.html','/blog/how-much-does-a-website-cost.html','/es/blog/cuanto-cuesta-un-sitio-web.html','/blog/why-your-competitor-gets-calls-from-google.html','/es/blog/por-que-tu-competencia-recibe-llamadas-de-google.html','/blog/what-a-website-does-for-a-law-firm.html','/es/blog/que-hace-un-sitio-web-por-un-bufete.html','/blog/does-your-restaurant-need-a-website.html','/es/blog/necesita-tu-restaurante-un-sitio-web.html','/404.html','/precios.html','/blog/should-i-use-ai-to-build-my-website.html','/es/blog/deberia-usar-ia-para-crear-mi-sitio-web.html','/web-design-for-law-firms.html','/es/diseno-web-para-abogados.html'];
// Images shared across many pages. The client logos are here because one of
// them showed a broken-image box on a phone: the fetch below is network-first,
// so a momentary network failure on an asset that was NOT precached had nothing
// to fall back to. Only widely-reused images earn a slot -- the hero photos and
// illustrations are 90-210 KB each and would nearly double the install.
var ASSETS=['/assets/logo/sebby-nav.svg','/assets/logo/sebby-nav-white.svg','/assets/logo/sebby-horizontal.svg','/assets/logo/sebby-horizontal-white.svg','/favicon.ico','/favicon.svg','/assets/client-logos/rieralaw-light.webp','/assets/client-logos/rieralaw-dark.webp','/assets/client-logos/elitecare-light.webp','/assets/client-logos/elitecare-dark.webp','/assets/sebby.webp'];

self.addEventListener('install',function(e){
  e.waitUntil(caches.open(CACHE).then(function(c){return c.addAll(PAGES.concat(ASSETS))}));
  self.skipWaiting();
});

self.addEventListener('activate',function(e){
  e.waitUntil(caches.keys().then(function(ks){
    return Promise.all(ks.filter(function(k){return k!==CACHE}).map(function(k){return caches.delete(k)}));
  }));
  self.clients.claim();
});

// Network-first, cache as we go, fall back to cache when the network fails.
//
// The catch MUST NOT resolve to undefined. caches.match() resolves to undefined
// on a miss, and respondWith(undefined) is a hard network error -- which is how
// a single flaky image request turned into a broken-image box that survived
// until the page was reloaded. Every path below ends in a real Response.
self.addEventListener('fetch',function(e){
  if(e.request.method!=='GET')return;
  e.respondWith(
    fetch(e.request).then(function(res){
      if(res.ok){var c=res.clone();caches.open(CACHE).then(function(cache){cache.put(e.request,c)})}
      return res;
    }).catch(function(){
      return caches.match(e.request).then(function(hit){
        if(hit)return hit;
        // An uncached page request offline gets our own 404 rather than the
        // browser's error screen. It carries both languages inline.
        if(e.request.mode==='navigate')return caches.match('/404.html');
        return null;
      }).then(function(res){return res||Response.error()});
    })
  );
});
