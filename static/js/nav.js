/* Waveline — shared shell behaviour: theme toggle, mobile "More" panel,
   scroll-reveal, magnetic buttons, in-page section scroll-spy.
   Vanilla JS, no dependencies. Runs on every page (included from base.html). */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia && window.matchMedia("(pointer: fine)").matches;

  /* ── Theme toggle (dark signal is default; "light" is the alt theme) ── */
  function initTheme() {
    var btns = [document.getElementById("wv-theme-btn"), document.getElementById("wv-theme-btn-mobile")].filter(Boolean);
    if (!btns.length) return;

    function apply(light) {
      document.body.classList.toggle("light", light);
      document.documentElement.classList.remove("pre-light");
      btns.forEach(function (b) {
        if (b.id === "wv-theme-btn") b.textContent = light ? "☾" : "◑";
      });
    }
    var stored;
    try { stored = localStorage.getItem("wv-theme"); } catch (e) { stored = null; }
    apply(stored === "light");

    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        var light = !document.body.classList.contains("light");
        try { localStorage.setItem("wv-theme", light ? "light" : "dark"); } catch (e) {}
        apply(light);
      });
    });
  }

  /* ── Mobile "More" panel ── */
  function initMorePanel() {
    var trigger = document.getElementById("wv-more-btn");
    var panel = document.getElementById("wv-morepanel");
    var scrim = document.getElementById("wv-more-scrim");
    if (!trigger || !panel) return;
    var lastFocus = null;

    function focusable() {
      return Array.prototype.slice.call(panel.querySelectorAll("a, button, input, [tabindex]"));
    }
    function open() {
      lastFocus = document.activeElement;
      panel.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      document.body.style.overflow = "hidden";
      var items = focusable();
      if (items.length) items[0].focus();
    }
    function close() {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      document.body.style.overflow = "";
      if (lastFocus) lastFocus.focus();
    }
    trigger.addEventListener("click", function () {
      panel.hidden ? open() : close();
    });
    if (scrim) scrim.addEventListener("click", close);
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { close(); return; }
      if (e.key === "Tab") {
        var items = focusable();
        if (!items.length) return;
        var first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panel.hidden) close();
    });
  }

  /* ── Desktop profile menu (logged-in state): accessible disclosure ──
     Enter/Space open it natively (it's a <button>); Escape and click-outside
     close it; Tab is trapped inside while open; focus returns to the
     trigger on close. Never hover-only. */
  function initProfileMenu() {
    var trigger = document.getElementById("wv-profile-trigger");
    var panel = document.getElementById("wv-profile-menu");
    if (!trigger || !panel) return;
    var lastFocus = null;

    function focusable() {
      return Array.prototype.slice.call(panel.querySelectorAll("a, button, [tabindex]"));
    }
    function isOpen() { return !panel.hidden; }
    function open() {
      lastFocus = document.activeElement;
      panel.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      var items = focusable();
      if (items.length) items[0].focus();
    }
    function close(returnFocus) {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (returnFocus !== false && lastFocus) lastFocus.focus();
    }

    trigger.addEventListener("click", function () {
      isOpen() ? close() : open();
    });

    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); return; }
      if (e.key === "Tab") {
        var items = focusable();
        if (!items.length) return;
        var first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) close();
    });

    document.addEventListener("click", function (e) {
      if (!isOpen()) return;
      if (trigger.contains(e.target) || panel.contains(e.target)) return;
      close(false);
    });
  }

  /* ── Scroll-reveal for elements marked .wv-reveal ── */
  function initReveal() {
    if (reduceMotion || !("IntersectionObserver" in window)) {
      document.querySelectorAll(".wv-reveal").forEach(function (el) { el.classList.add("is-visible"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    document.querySelectorAll(".wv-reveal").forEach(function (el) { io.observe(el); });
  }

  /* ── Magnetic primary buttons (pointer-fine devices only) ── */
  function initMagnetic() {
    if (!finePointer || reduceMotion) return;
    document.querySelectorAll(".wv-magnetic").forEach(function (el) {
      el.addEventListener("pointermove", function (e) {
        var r = el.getBoundingClientRect();
        var x = e.clientX - r.left - r.width / 2;
        var y = e.clientY - r.top - r.height / 2;
        el.style.transform = "translate(" + (x * 0.18).toFixed(1) + "px," + (y * 0.28).toFixed(1) + "px)";
      });
      el.addEventListener("pointerleave", function () { el.style.transform = ""; });
    });
  }

  /* ── Sticky in-page section nav: scroll-spy ──
     Any element with [data-wv-scrollspy] auto-highlights its <a href="#id">
     children as the matching section scrolls into view, and gains a slim
     progress affordance on the currently-active link. Used on the artist page. */
  function initScrollSpy() {
    var navs = document.querySelectorAll("[data-wv-scrollspy]");
    if (!navs.length || !("IntersectionObserver" in window)) return;
    navs.forEach(function (nav) {
      var links = Array.prototype.slice.call(nav.querySelectorAll("a[href^='#']"));
      var sections = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); }).filter(Boolean);
      if (!sections.length) return;
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          var link = nav.querySelector('a[href="#' + entry.target.id + '"]');
          if (!link) return;
          if (entry.isIntersecting) {
            links.forEach(function (l) { l.removeAttribute("aria-current"); });
            link.setAttribute("aria-current", "true");
            var lr = link.getBoundingClientRect(), nr = nav.getBoundingClientRect();
            if (lr.left < nr.left || lr.right > nr.right) link.scrollIntoView({ block: "nearest", inline: "center" });
          }
        });
      }, { rootMargin: "-40% 0px -50% 0px", threshold: 0 });
      sections.forEach(function (s) { io.observe(s); });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initMorePanel();
    initProfileMenu();
    initReveal();
    initMagnetic();
    initScrollSpy();
  });
})();
