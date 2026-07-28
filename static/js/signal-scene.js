/* Waveline — hero "signal" scene.
   Hand-rolled WebGL (no Three.js/library dependency — see
   SIGNAL_SYSTEM_DESIGN.md for why). Draws a small set of flowing signal
   lines with pulsing network nodes: a music waveform + data-pipeline +
   connected-artist-nodes motif, reacting subtly to pointer, scroll and
   search-field focus.

   Progressive enhancement: a static CSS gradient (.wv-hero-scene-fallback)
   is always present underneath. This script only runs, and only fades the
   canvas in, when: WebGL is available, prefers-reduced-motion is not set,
   and the canvas element exists on the page. Any failure at any step is
   caught and simply leaves the CSS fallback visible — no console errors,
   no broken page. */
(function () {
  "use strict";

  var canvas = document.getElementById("wv-signal-canvas");
  if (!canvas) return;

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var coarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  if (reduceMotion) return; // CSS fallback stands in; nothing more to do.

  var VERT = "attribute vec2 aPos;varying vec2 vUv;void main(){vUv=aPos*0.5+0.5;gl_Position=vec4(aPos,0.0,1.0);}";
  var FRAG = [
    "precision mediump float;",
    "varying vec2 vUv;",
    "uniform vec2 uRes;",
    "uniform float uTime;",
    "uniform vec2 uPointer;",
    "uniform float uPointerActive;",
    "uniform float uFocus;",
    "uniform float uScroll;",
    "uniform float uQuality;",
    "uniform vec3 uAccent;",
    "uniform vec3 uInk;",
    "void main(){",
    "  vec2 uv = vUv;",
    "  float aspect = uRes.x / uRes.y;",
    "  float total = mix(4.0, 7.0, uQuality);",
    "  vec3 col = vec3(0.0);",
    "  float pdx = (uv.x - uPointer.x) * aspect;",
    "  float bump = uPointerActive * exp(-pdx*pdx*10.0) * 0.06;",
    "  for (int i = 0; i < 7; i++) {",
    "    float fi = float(i);",
    "    if (fi >= total) break;",
    "    float y0 = (fi + 0.5) / total;",
    "    float speed = 0.12 + fi * 0.035;",
    "    float freq = 3.0 + fi * 1.3;",
    "    float phase = fi * 1.7;",
    "    float amp = 0.028 + 0.012 * sin(fi * 2.1);",
    "    float wave = sin(uv.x * freq * 6.2831 + uTime * speed + phase) * (amp + bump + uFocus * 0.015);",
    "    float y = y0 + wave + uScroll * 0.02 * (fi - total * 0.5);",
    "    float d = abs(uv.y - y);",
    "    float glow = smoothstep(0.006, 0.0, d) * 0.55 + smoothstep(0.03, 0.0, d) * 0.12;",
    "    float hue = fract(fi / total);",
    "    vec3 lineCol = mix(uInk, uAccent, 0.35 + 0.5 * hue);",
    "    col += glow * lineCol * (0.35 + 0.15 * sin(uTime * 0.6 + fi));",
    "    float nodeSpacing = 0.16;",
    "    float nx = (mod(uv.x + uTime * 0.015 * (fi + 1.0), nodeSpacing) - nodeSpacing * 0.5) * aspect;",
    "    float nodePulse = 0.5 + 0.5 * sin(uTime * 1.4 + fi * 2.3 + uv.x * 10.0);",
    "    float nd = length(vec2(nx, (uv.y - y) * 1.0));",
    "    float node = smoothstep(0.01, 0.0, nd) * nodePulse * step(fi, 2.0);",
    "    col += node * uAccent * 0.9;",
    "  }",
    "  float vig = smoothstep(1.05, 0.25, length((uv - 0.5) * vec2(aspect, 1.0)) * 1.15);",
    "  col *= vig;",
    "  gl_FragColor = vec4(col, clamp(max(max(col.r,col.g),col.b) * 1.6, 0.0, 0.9));",
    "}"
  ].join("\n");

  function compile(gl, type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { gl.deleteShader(s); return null; }
    return s;
  }

  var gl;
  try {
    gl = canvas.getContext("webgl", { alpha: true, antialias: true, premultipliedAlpha: true })
      || canvas.getContext("experimental-webgl", { alpha: true });
  } catch (e) { gl = null; }
  if (!gl) return;

  var vs = compile(gl, gl.VERTEX_SHADER, VERT);
  var fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return;

  var program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
  gl.useProgram(program);

  var quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  var aPos = gl.getAttribLocation(program, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  var uRes = gl.getUniformLocation(program, "uRes");
  var uTime = gl.getUniformLocation(program, "uTime");
  var uPointer = gl.getUniformLocation(program, "uPointer");
  var uPointerActive = gl.getUniformLocation(program, "uPointerActive");
  var uFocus = gl.getUniformLocation(program, "uFocus");
  var uScroll = gl.getUniformLocation(program, "uScroll");
  var uQuality = gl.getUniformLocation(program, "uQuality");
  var uAccent = gl.getUniformLocation(program, "uAccent");
  var uInk = gl.getUniformLocation(program, "uInk");

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

  var quality = (coarsePointer || window.innerWidth < 768) ? 0.0 : 1.0;
  var dpr = Math.min(window.devicePixelRatio || 1, quality === 0.0 ? 1.25 : 2);

  var pointer = { x: 0.5, y: 0.5, active: 0 };
  var focusTarget = 0, focusCurrent = 0;
  var scrollTarget = 0, scrollCurrent = 0;
  var running = false, rafId = null, startTime = performance.now();
  // Idle throttle: full frame rate while the visitor is actually interacting
  // with the hero; after a stretch of no input, drop to ~15fps to save
  // battery/GPU rather than animating at full cost forever with nothing to show for it.
  var lastInteraction = performance.now();
  var lastDraw = 0;
  var IDLE_MS = 6000;
  var IDLE_FRAME_INTERVAL = 66;
  function markInteraction() { lastInteraction = performance.now(); }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    var w = Math.max(1, Math.round(rect.width * dpr));
    var h = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  }

  function onPointerMove(e) {
    if (coarsePointer) return;
    markInteraction();
    var rect = canvas.getBoundingClientRect();
    pointer.x = (e.clientX - rect.left) / rect.width;
    pointer.y = 1.0 - (e.clientY - rect.top) / rect.height;
    pointer.active = 1;
  }
  function onPointerLeave() { pointer.active = 0; }

  var heroSection = canvas.closest("[data-wv-hero]") || canvas.parentElement;
  if (!coarsePointer && heroSection) {
    heroSection.addEventListener("pointermove", onPointerMove, { passive: true });
    heroSection.addEventListener("pointerleave", onPointerLeave, { passive: true });
  }

  // Search-field focus gives the scene a brief, deliberate "tuning in" boost.
  var searchInput = document.getElementById("artist-input");
  if (searchInput) {
    searchInput.addEventListener("focus", function () { focusTarget = 1; markInteraction(); });
    searchInput.addEventListener("blur", function () { focusTarget = 0; });
  }

  window.addEventListener("scroll", function () {
    markInteraction();
    var h = window.innerHeight || 1;
    scrollTarget = Math.max(0, Math.min(1, window.scrollY / h));
  }, { passive: true });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 120);
  });

  function frame() {
    if (!running) return;
    var now = performance.now();
    var idle = (now - lastInteraction) > IDLE_MS;
    if (idle && (now - lastDraw) < IDLE_FRAME_INTERVAL) {
      rafId = requestAnimationFrame(frame);
      return;
    }
    lastDraw = now;
    resize();
    var t = (now - startTime) / 1000;
    focusCurrent += (focusTarget - focusCurrent) * 0.06;
    scrollCurrent += (scrollTarget - scrollCurrent) * 0.08;

    gl.useProgram(program);
    gl.uniform2f(uRes, canvas.width, canvas.height);
    gl.uniform1f(uTime, t);
    gl.uniform2f(uPointer, pointer.x, pointer.y);
    gl.uniform1f(uPointerActive, pointer.active);
    gl.uniform1f(uFocus, focusCurrent);
    gl.uniform1f(uScroll, scrollCurrent);
    gl.uniform1f(uQuality, quality);
    gl.uniform3f(uAccent, 1.0, 0.37, 0.24);
    gl.uniform3f(uInk, 0.95, 0.93, 0.9);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    if (!canvas.classList.contains("is-ready")) canvas.classList.add("is-ready");
    rafId = requestAnimationFrame(frame);
  }

  function start() { if (running) return; running = true; rafId = requestAnimationFrame(frame); }
  function stop() { running = false; if (rafId) cancelAnimationFrame(rafId); }

  document.addEventListener("visibilitychange", function () {
    document.hidden ? stop() : start();
  });

  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { entry.isIntersecting ? start() : stop(); });
    }, { threshold: 0.05 });
    io.observe(canvas);
  } else {
    start();
  }
})();
