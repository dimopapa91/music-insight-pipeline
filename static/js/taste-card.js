/* Taste card (27 Sep 2026): a 1080×1350 PNG of someone's top artists and
 * sound, drawn on their device with <canvas> and downloaded directly.
 * Nothing is uploaded or published. Only same-origin images (the W mark)
 * are drawn, so the canvas is never tainted and toBlob() always works.
 */
(function () {
  "use strict";
  var btn = document.getElementById("tp-card-btn");
  var dataEl = document.getElementById("tp-card-data");
  if (!btn || !dataEl) return;
  var data;
  try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }

  var W = 1080, H = 1350, PAD = 90;
  var ACCENT = "#c9b8ff", INK = "#ffffff", MUTED = "#8e8e95", LINE = "#262628";

  function loadMark() {
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { resolve(null); };
      img.src = data.mark;
    });
  }

  function fit(ctx, text, maxWidth, size, weight, family) {
    var s = size;
    do {
      ctx.font = weight + " " + s + "px " + family;
      if (ctx.measureText(text).width <= maxWidth) break;
      s -= 4;
    } while (s > 28);
    return s;
  }

  function draw(mark) {
    var c = document.createElement("canvas");
    c.width = W; c.height = H;
    var ctx = c.getContext("2d");
    var display = "'Archivo Narrow', 'Arial Narrow', sans-serif";
    var body = "Inter, Arial, sans-serif";

    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = ACCENT; ctx.fillRect(0, 0, W, 10);

    if (mark) ctx.drawImage(mark, PAD, PAD, 84, 84);
    ctx.fillStyle = MUTED;
    ctx.font = "700 30px " + display;
    ctx.textBaseline = "middle";
    ctx.fillText("MY TASTE · " + String(data.period || "").toUpperCase(), PAD + (mark ? 110 : 0), PAD + 42);

    var y = PAD + 190;
    ctx.textBaseline = "alphabetic";
    (data.artists || []).slice(0, 5).forEach(function (name, i) {
      var label = String(name).toUpperCase();
      ctx.fillStyle = ACCENT;
      ctx.font = "700 40px " + display;
      ctx.fillText(String(i + 1).padStart(2, "0"), PAD, y);
      var size = fit(ctx, label, W - PAD * 2 - 90, i === 0 ? 104 : 76, "700", display);
      ctx.fillStyle = INK;
      ctx.font = "700 " + size + "px " + display;
      ctx.fillText(label, PAD + 90, y);
      y += (i === 0 ? 128 : 100);
    });

    y += 30;
    ctx.fillStyle = LINE; ctx.fillRect(PAD, y, W - PAD * 2, 2);
    y += 70;
    ctx.fillStyle = MUTED;
    ctx.font = "700 28px " + display;
    ctx.fillText("MY SOUND", PAD, y);
    y += 50;

    var genres = (data.genres || []).slice(0, 4);
    var top = genres.length ? (genres[0].share || 1) : 1;
    genres.forEach(function (g) {
      ctx.fillStyle = INK;
      ctx.font = "500 34px " + body;
      ctx.fillText(String(g.name), PAD, y);
      ctx.fillStyle = MUTED;
      ctx.textAlign = "right";
      ctx.fillText(g.share + "%", W - PAD, y);
      ctx.textAlign = "left";
      var barY = y + 18, barW = W - PAD * 2;
      ctx.fillStyle = "#17171a"; ctx.fillRect(PAD, barY, barW, 12);
      ctx.fillStyle = ACCENT; ctx.fillRect(PAD, barY, Math.max(8, barW * (g.share / top)), 12);
      y += 84;
    });

    ctx.fillStyle = MUTED;
    ctx.font = "600 28px " + body;
    ctx.fillText("wearewaveline.com", PAD, H - PAD + 10);
    return c;
  }

  btn.addEventListener("click", function () {
    btn.disabled = true;
    var ready = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();
    ready.then(loadMark).then(function (mark) {
      var canvas = draw(mark);
      canvas.toBlob(function (blob) {
        btn.disabled = false;
        if (!blob) return;
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "waveline-taste.png";
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      }, "image/png");
    });
  });
})();
