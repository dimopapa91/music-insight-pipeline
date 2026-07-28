/* Waveline — global command palette (Cmd/Ctrl+K).
   Connects to the real artist directory (/api/artists) and the real
   /search POST flow — no fake/decorative results. */
(function () {
  "use strict";

  var overlay, palette, input, list;
  var lastFocus = null;
  var artistCache = null;
  var activeIndex = -1;

  var DESTINATIONS = [
    { label: "Discover", href: "/", hint: "Home" },
    { label: "Community", href: "/feed", hint: "Page" },
    { label: "Compare artists", href: "/compare", hint: "Page" },
    { label: "News", href: "/news", hint: "Page" },
    { label: "Taste Profile", href: "/profile", hint: "Page" },
    { label: "How Waveline works", href: "/about", hint: "About" }
  ];

  function authItems() {
    if (window.WV && window.WV.authenticated) {
      return [
        { label: "View profile", href: "/me", hint: "@" + window.WV.username },
        { label: "Notifications", href: "/notifications", hint: "Account" },
        { label: "Log out", href: "/logout", hint: "Account" }
      ];
    }
    return [
      { label: "Log in", href: "/login", hint: "Account" },
      { label: "Create account", href: "/register", hint: "Account" }
    ];
  }

  function loadArtists() {
    if (artistCache) return Promise.resolve(artistCache);
    return fetch("/api/artists").then(function (r) { return r.json(); }).then(function (d) {
      artistCache = Array.isArray(d) ? d : [];
      return artistCache;
    }).catch(function () { artistCache = []; return artistCache; });
  }

  function submitArtistSearch(name) {
    var field = document.getElementById("wv-palette-search-artist");
    var form = document.getElementById("wv-palette-search-form");
    if (!field || !form) return;
    field.value = name;
    form.submit();
  }

  function render(query) {
    list.innerHTML = "";
    activeIndex = -1;
    var q = (query || "").trim().toLowerCase();

    var destMatches = DESTINATIONS.concat(authItems()).filter(function (d) {
      return !q || d.label.toLowerCase().indexOf(q) !== -1;
    });

    var artistMatches = q ? (artistCache || []).filter(function (a) {
      return a.toLowerCase().indexOf(q) !== -1;
    }).slice(0, 6) : [];

    if (!destMatches.length && !artistMatches.length && q) {
      var empty = document.createElement("div");
      empty.className = "wv-palette-empty";
      empty.innerHTML = 'No matches for "' + escapeHtml(query) + '". Press Enter to analyse it as a new artist.';
      list.appendChild(empty);
      addItem("Analyse “" + query + "”", null, "New search", function () { submitArtistSearch(query); });
      return;
    }

    if (artistMatches.length) {
      addGroup("Artists");
      artistMatches.forEach(function (name) {
        addItem(name, "/artist/" + encodeURIComponent(name), "Artist", function () { navigate("/artist/" + encodeURIComponent(name)); });
      });
    }
    if (q) {
      addGroup("Search");
      addItem("Analyse “" + query + "”", null, "New artist", function () { submitArtistSearch(query); });
    }
    addGroup("Go to");
    destMatches.forEach(function (d) {
      addItem(d.label, d.href, d.hint, function () { navigate(d.href); });
    });

    updateSelection();
  }

  function addGroup(label) {
    var g = document.createElement("div");
    g.className = "wv-palette-group";
    g.textContent = label;
    list.appendChild(g);
  }

  function addItem(label, href, hint, onActivate) {
    var el = document.createElement(href ? "a" : "button");
    el.className = "wv-palette-item";
    el.setAttribute("role", "option");
    if (href) el.setAttribute("href", href); else el.setAttribute("type", "button");
    el.innerHTML = '<span>' + escapeHtml(label) + '</span><span class="meta">' + escapeHtml(hint || "") + '</span>';
    el.addEventListener("click", function (e) { e.preventDefault(); onActivate(); });
    list.appendChild(el);
  }

  function items() { return Array.prototype.slice.call(list.querySelectorAll(".wv-palette-item")); }

  function updateSelection() {
    var els = items();
    els.forEach(function (el, i) { el.setAttribute("aria-selected", i === activeIndex ? "true" : "false"); });
    if (activeIndex >= 0 && els[activeIndex]) els[activeIndex].scrollIntoView({ block: "nearest" });
  }

  function navigate(href) { window.location.href = href; }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function focusable() {
    return Array.prototype.slice.call(palette.querySelectorAll("input, a, button")).filter(function (el) {
      return el.offsetParent !== null;
    });
  }

  function trapTab(e) {
    if (e.key !== "Tab") return;
    var els = focusable();
    if (!els.length) return;
    var first = els[0], last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function open() {
    lastFocus = document.activeElement;
    overlay.hidden = false;
    input.value = "";
    loadArtists().then(function () { render(""); });
    render("");
    setTimeout(function () { input.focus(); }, 0);
    document.body.style.overflow = "hidden";
  }

  function close() {
    overlay.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  document.addEventListener("DOMContentLoaded", function () {
    overlay = document.getElementById("wv-palette-overlay");
    palette = document.getElementById("wv-palette");
    input = document.getElementById("wv-palette-input");
    list = document.getElementById("wv-palette-list");
    if (!overlay || !palette || !input || !list) return;

    ["wv-search-trigger", "wv-search-trigger-mobile"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.addEventListener("click", open);
    });

    document.addEventListener("keydown", function (e) {
      var metaK = (e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K");
      if (metaK) { e.preventDefault(); overlay.hidden ? open() : close(); return; }
      if (overlay.hidden) return;
      if (e.key === "Escape") { close(); return; }
      trapTab(e);
    });

    overlay.addEventListener("mousedown", function (e) {
      if (e.target === overlay) close();
    });

    input.addEventListener("input", function () { render(input.value); });

    input.addEventListener("keydown", function (e) {
      var els = items();
      if (e.key === "ArrowDown") { e.preventDefault(); activeIndex = Math.min(activeIndex + 1, els.length - 1); updateSelection(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); activeIndex = Math.max(activeIndex - 1, 0); updateSelection(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (activeIndex >= 0 && els[activeIndex]) { els[activeIndex].click(); }
        else if (input.value.trim()) { submitArtistSearch(input.value.trim()); }
      }
    });
  });
})();
