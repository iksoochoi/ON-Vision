(function () {
  var seenKey = 'welcomeSeen';
  var showKey = 'showWelcome';
  var nav = performance.getEntriesByType('navigation')[0];
  var type = nav ? nav.type : 'navigate';
  var forced = false;
  var seen = false;
  try {
    forced = sessionStorage.getItem(showKey) === '1';
    sessionStorage.removeItem(showKey);
    seen = sessionStorage.getItem(seenKey) === '1';
  } catch (e) {}
  var show = type === 'reload' || forced || (type === 'navigate' && !seen);
  document.addEventListener('DOMContentLoaded', function () {
    var logo = document.querySelector('.brand-mark');
    if (!logo) return;
    logo.addEventListener('click', function (e) {
      try { sessionStorage.setItem(showKey, '1'); } catch (err) {}
      if (location.pathname === '/' || location.pathname === '') {
        e.preventDefault();
        location.reload();
      }
    });
  });
  if (!show) return;
  document.write(
    '<style>' +
    '#welcome-screen{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;' +
    'background:radial-gradient(circle at center,rgba(214,242,92,.16),#10140f 10%);z-index:9999;' +
    'animation:welcomeFade 1s forwards}' +
    '.welcome-wrap{position:relative;width:220px;height:220px;display:flex;align-items:center;justify-content:center}' +
    '.welcome-logo{width:92px;height:92px;color:#e7efe4;z-index:3;' +
    'filter:drop-shadow(0 0 15px rgba(214,242,92,.75)) drop-shadow(0 0 35px rgba(214,242,92,.35));' +
    'animation:welcomeIn .7s cubic-bezier(.17,.89,.32,1.49),welcomeFloat 2s ease-in-out infinite}' +
    '.welcome-ring{position:absolute;width:120px;height:120px;border-radius:50%;border:1px solid rgba(214,242,92,.55);animation:welcomeRing 2s ease-out infinite}' +
    '.welcome-ring:nth-child(2){animation-delay:.4s}' +
    '.welcome-ring:nth-child(3){animation-delay:.8s}' +
    '.welcome-particle{position:absolute;width:6px;height:6px;border-radius:50%;background:#d6f25c;box-shadow:0 0 10px #d6f25c}' +
    '.welcome-particle:nth-child(4){animation:welcomeP1 1.5s ease-out forwards}' +
    '.welcome-particle:nth-child(5){animation:welcomeP2 1.5s ease-out forwards}' +
    '.welcome-particle:nth-child(6){animation:welcomeP3 1.5s ease-out forwards}' +
    '.welcome-particle:nth-child(7){animation:welcomeP4 1.5s ease-out forwards}' +
    '.welcome-particle:nth-child(8){animation:welcomeP5 1.5s ease-out forwards}' +
    '@keyframes welcomeIn{from{transform:scale(.2) rotate(-180deg);opacity:0;filter:blur(12px)}to{transform:scale(1) rotate(0);opacity:1;filter:none}}' +
    '@keyframes welcomeFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}' +
    '@keyframes welcomeRing{from{transform:scale(.3);opacity:.9}to{transform:scale(2.8);opacity:0}}' +
    '@keyframes welcomeP1{from{transform:translate(-120px,-80px)}to{transform:translate(0,0);opacity:0}}' +
    '@keyframes welcomeP2{from{transform:translate(120px,-90px)}to{transform:translate(0,0);opacity:0}}' +
    '@keyframes welcomeP3{from{transform:translate(-140px,80px)}to{transform:translate(0,0);opacity:0}}' +
    '@keyframes welcomeP4{from{transform:translate(130px,100px)}to{transform:translate(0,0);opacity:0}}' +
    '@keyframes welcomeP5{from{transform:translate(0,-160px)}to{transform:translate(0,0);opacity:0}}' +
    '@keyframes welcomeFade{0%{opacity:1;visibility:visible}80%{opacity:1}100%{opacity:0;visibility:hidden}}' +
    '</style>' +
    '<div id="welcome-screen"><div class="welcome-wrap">' +
    '<div class="welcome-ring"></div><div class="welcome-ring"></div><div class="welcome-ring"></div>' +
    '<div class="welcome-particle"></div><div class="welcome-particle"></div><div class="welcome-particle"></div>' +
    '<div class="welcome-particle"></div><div class="welcome-particle"></div>' +
    '<svg class="welcome-logo" viewBox="0 0 54 54" fill="currentColor" aria-hidden="true">' +
    '<path d="M36.281 11.384c-.056.058-.114.13-.17.188l-2.364 2.41-.057.058c-.057.072-.057.145.014.247a8.331 8.331 0 0 1 1.096 3.309c.257 2.409-.342 4.571-1.822 6.473-1.268 1.625-2.92 2.64-4.899 3.09-.911.204-1.837.218-2.762.117-1.524-.16-2.891-.726-4.116-1.655-1.766-1.335-2.862-3.105-3.318-5.311-.17-.813-.199-1.64-.128-2.453.171-1.901.883-3.585 2.15-5.022 1.21-1.379 2.692-2.278 4.443-2.685a8.03 8.03 0 0 1 2.635-.189 8.5 8.5 0 0 1 3.588 1.147c.142.087.2.029.285-.058l2.35-2.395c.056-.058.113-.13.185-.217l2.889 2.945.001.001Zm-5.342 7.4c.071-2.8-2.093-4.832-4.529-4.905-2.748-.073-4.798 2.25-4.798 4.76 0 2.57 2.122 4.79 4.67 4.746 2.507.03 4.6-2.076 4.656-4.6Zm3.605 19.391c.014 2.351 0 4.717 0 7.068v.32h-4.13v-.29c0-2.468.015-4.935 0-7.403 0-1.291-.44-2.423-1.366-3.323a3.827 3.827 0 0 0-2.208-1.06c-1.11-.16-2.121.117-3.018.799-.898.696-1.424 1.64-1.595 2.757-.086.537-.1 1.089-.1 1.64-.014 2.192 0 4.369 0 6.56v.305h-4.101v-.276c0-2.656-.029-5.326.014-7.982.029-1.408.47-2.743 1.196-3.962 1.21-2.032 2.934-3.338 5.198-3.875a8.414 8.414 0 0 1 2.178-.203c2.265.101 4.201 1.015 5.74 2.742 1.096 1.248 1.794 2.7 2.036 4.369.098.608.155 1.217.155 1.812"/>' +
    '</svg></div></div>'
  );
  window.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () {
      var el = document.getElementById('welcome-screen');
      if (el) el.remove();
      try { sessionStorage.setItem(seenKey, '1'); } catch (e) {}
    }, 3100);
  });
})();
