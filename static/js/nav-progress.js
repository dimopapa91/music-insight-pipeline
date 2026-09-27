/* Instant feedback on navigation (27 Sep 2026).
 *
 * Pages are server-rendered, so after a click the old page used to sit
 * unchanged until the new one arrived, which read as "nothing happened".
 * A thin accent bar now starts the moment a same-site link is clicked or a
 * form is submitted, and is cleared when a page is shown again (including
 * back/forward cache restores).
 */
(function () {
  "use strict";
  var bar = document.getElementById("wv-progress");
  if (!bar) return;

  function start() {
    bar.classList.remove("is-done");
    // restart the animation even if it was already running
    void bar.offsetWidth;
    bar.classList.add("is-loading");
  }

  function reset() {
    bar.classList.remove("is-loading");
    bar.classList.remove("is-done");
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a) return;
    if (a.target && a.target !== "_self") return;
    if (a.hasAttribute("download")) return;
    var url;
    try { url = new URL(a.href, location.href); } catch (err) { return; }
    if (url.origin !== location.origin) return;
    // same-page anchors don't load anything
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    start();
  });

  document.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    var form = e.target;
    if (form && form.getAttribute("target") === "_blank") return;
    start();
  });

  window.addEventListener("pageshow", reset);
})();
