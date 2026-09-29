/* Artist photo fallback (29 Sep 2026).
 *
 * When the server could not get an artist's photo (Deezer or Spotify refusing
 * the server, a cold cache), the page renders a placeholder carrying
 * data-wv-photo="<artist name>". This script asks Deezer for that photo from
 * the visitor's browser instead (JSONP, since Deezer sends no CORS headers)
 * and swaps the placeholder for the image. Same rules as the server: only an
 * exact name match counts (letters and digits, accents ignored) and the
 * best-known namesake wins; Deezer's blank placeholder image is ignored.
 */
(function () {
  var nodes = document.querySelectorAll('[data-wv-photo]');
  if (!nodes.length) return;

  var BLANK = 'd41d8cd98f00b204e9800998ecf8427e';
  var MAX_PARALLEL = 4;
  var byName = {};          // normalised name -> [elements]
  var queue = [];
  var seq = 0;

  function norm(s) {
    return (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, '');
  }

  function sized(url, px) {
    return url.replace(/\/(\d+)x\1-/, '/' + px + 'x' + px + '-');
  }

  function place(el, url) {
    var px = parseInt(el.getAttribute('data-wv-px') || '500', 10);
    var img = new Image();
    img.alt = el.getAttribute('data-wv-alt') || '';
    img.width = px; img.height = px;
    img.decoding = 'async';      // not lazy: a detached lazy image never loads
    if (el.getAttribute('data-wv-class')) img.className = el.getAttribute('data-wv-class');
    img.onload = function () { if (el.parentNode) el.parentNode.replaceChild(img, el); };
    img.src = sized(url, px);
  }

  function lookup(key, name, done) {
    var cb = '__wvdz' + (++seq);
    var s = document.createElement('script');
    var timer = setTimeout(finish, 6000);
    var finished = false;
    function finish(data) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      window[cb] = function () {};   // a late answer after the timeout is ignored
      if (s.parentNode) s.parentNode.removeChild(s);
      var best = null;
      ((data && data.data) || []).forEach(function (d) {
        if (norm(d.name) !== key) return;
        var pic = d.picture_xl || d.picture_big || d.picture_medium || '';
        if (!pic || pic.indexOf(BLANK) !== -1) return;
        if (!best || (d.nb_fan || 0) > (best.nb_fan || 0)) best = { nb_fan: d.nb_fan, pic: pic };
      });
      if (best) byName[key].forEach(function (el) { place(el, best.pic); });
      done();
    }
    window[cb] = finish;
    s.onerror = function () { finish(null); };
    s.src = 'https://api.deezer.com/search/artist?limit=10&output=jsonp&callback=' + cb +
            '&q=' + encodeURIComponent(name);
    document.head.appendChild(s);
  }

  Array.prototype.forEach.call(nodes, function (el) {
    var name = el.getAttribute('data-wv-photo');
    var key = norm(name);
    if (!key) return;
    if (!byName[key]) { byName[key] = []; queue.push([key, name]); }
    byName[key].push(el);
  });

  var running = 0;
  function next() {
    while (running < MAX_PARALLEL && queue.length) {
      var item = queue.shift();
      running++;
      lookup(item[0], item[1], function () { running--; next(); });
    }
  }
  next();
})();
