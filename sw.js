var CACHE='mbs-v6';
var PAGES=['/','/index.html','/services.html','/work.html','/about.html','/contact.html','/book.html','/website-care.html','/pricing.html','/privacy.html','/terms.html','/thank-you.html','/404.html','/case-study-rieralaw.html','/case-study-elitecare.html','/blog.html','/blog/5-signs-your-website-is-losing-clients.html','/blog/what-is-website-care.html','/blog/how-much-does-a-website-cost.html','/blog/why-your-competitor-gets-calls-from-google.html','/blog/what-a-website-does-for-a-law-firm.html','/blog/does-your-restaurant-need-a-website.html','/web-design-miami.html','/web-design-fort-lauderdale.html','/diseno-web-santo-domingo.html'];
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
