// Google Ads + GA4 (gtag.js) — shared across all landing pages
// IDs centralized here to avoid duplicating the gtag block in every HTML file
(function () {
  var GOOGLE_ADS_ID = 'AW-18282918371';
  var GA4_ID = ''; // Set your G-XXXXXX ID here or leave empty
  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GOOGLE_ADS_ID;
  document.head.appendChild(s);
  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }
  gtag('js', new Date());
  gtag('config', GOOGLE_ADS_ID);
  if (GA4_ID) {
    gtag('config', GA4_ID, {
      'linker': {
        'domains': ['acortalink.com.ar', 'app.acortalink.com.ar'],
        'decorate_forms': true,
      },
      'send_page_view': true,
    });
  }
  // Track clicks on registration links (conversion: landing -> registrar)
  document.addEventListener('click', function(e) {
    var link = e.target.closest('a[href*="app.acortalink.com.ar/registrar"]');
    if (link) {
      if (GA4_ID) gtag('event', 'click_register', { 'event_category': 'engagement', 'event_label': 'landing_cta' });
      gtag('event', 'conversion', { 'send_to': GOOGLE_ADS_ID + '/register_click' });
    }
  });
})();
