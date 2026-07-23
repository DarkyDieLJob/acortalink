// Google Ads (gtag.js) — shared across all landing pages
// ID centralized here to avoid duplicating the gtag block in every HTML file
(function () {
  var GOOGLE_ADS_ID = 'AW-18282918371';
  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GOOGLE_ADS_ID;
  document.head.appendChild(s);
  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }
  gtag('js', new Date());
  gtag('config', GOOGLE_ADS_ID);
})();
