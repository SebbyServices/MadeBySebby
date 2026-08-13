var CACHE='mbs-v7';
var PAGES=['/','/index.html','/es/','/es/index.html','/services.html','/es/servicios.html','/work.html','/es/portafolio.html','/about.html','/es/sobre-mi.html','/contact.html','/es/contacto.html','/book.html','/es/agendar.html','/pricing.html','/es/precios.html','/website-care.html','/es/cuidado-web.html','/blog.html','/es/blog.html','/case-study-rieralaw.html','/es/caso-riera-law.html','/case-study-elitecare.html','/es/caso-elite-care.html','/web-design-miami.html','/es/diseno-web-miami.html','/web-design-fort-lauderdale.html','/es/diseno-web-fort-lauderdale.html','/es/diseno-web-santo-domingo.html','/privacy.html','/es/privacidad.html','/terms.html','/es/terminos.html','/thank-you.html','/es/gracias.html','/blog/5-signs-your-website-is-losing-clients.html','/es/blog/5-senales-de-que-tu-sitio-web-pierde-clientes.html','/blog/what-is-website-care.html','/es/blog/que-es-el-cuidado-web.html','/blog/how-much-does-a-website-cost.html','/es/blog/cuanto-cuesta-un-sitio-web.html','/blog/why-your-competitor-gets-calls-from-google.html','/es/blog/por-que-tu-competencia-recibe-llamadas-de-google.html','/blog/what-a-website-does-for-a-law-firm.html','/es/blog/que-hace-un-sitio-web-por-un-bufete.html','/blog/does-your-restaurant-need-a-website.html','/es/blog/necesita-tu-restaurante-un-sitio-web.html','/404.html','/precios.html'];
var ASSETS=['/assets/logo/sebby-nav.svg','/assets/logo/sebby-nav-white.svg','/assets/logo/sebby-horizontal.svg','/assets/logo/sebby-horizontal-white.svg','/favicon.ico','/favicon.svg'];

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

self.addEventListener('fetch',function(e){
  if(e.request.method!=='GET')return;
  e.respondWith(
    fetch(e.request).then(function(res){
      if(res.ok){var c=res.clone();caches.open(CACHE).then(function(cache){cache.put(e.request,c)})}
      return res;
    }).catch(function(){return caches.match(e.request)})
  );
});
