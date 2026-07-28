/* Waveline — single persistent Deezer preview mini-player.
   Lives once in base.html and is driven from any page via the global
   wvPlay(artist, track, triggerEl) function, replacing the three
   copy-pasted per-page implementations this consolidates. */
(function () {
  "use strict";

  var player, audio, titleEl, artistEl, toggleBtn, closeBtn, fillEl, announceEl;

  function els() {
    player = document.getElementById("wv-player");
    audio = document.getElementById("wv-audio");
    titleEl = document.getElementById("wv-player-title");
    artistEl = document.getElementById("wv-player-artist");
    toggleBtn = document.getElementById("wv-player-toggle");
    closeBtn = document.getElementById("wv-player-close");
    fillEl = document.getElementById("wv-player-fill");
    announceEl = document.getElementById("wv-player-announce");
    return !!(player && audio);
  }

  function announce(msg) { if (announceEl) announceEl.textContent = msg; }

  function openPlayer() { if (player) player.classList.add("is-open"); }
  function closePlayer() {
    if (audio) audio.pause();
    if (player) player.classList.remove("is-open");
  }

  window.wvClosePlayer = closePlayer;

  window.wvPlay = function (artist, track, triggerEl) {
    if (!els()) return;
    var restoreLabel = null;
    if (triggerEl) { restoreLabel = triggerEl.textContent; triggerEl.textContent = "⏳ " + track; }

    fetch("/preview?artist=" + encodeURIComponent(artist) + "&track=" + encodeURIComponent(track))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.preview_url) {
          titleEl.textContent = data.title || track;
          artistEl.textContent = (data.artist || artist) + " · 30s preview via Deezer";
          audio.src = data.preview_url;
          audio.load();
          var p = audio.play();
          if (p && p.catch) p.catch(function () {});
          toggleBtn.textContent = "⏸";
          toggleBtn.setAttribute("aria-label", "Pause");
          openPlayer();
          announce("Now previewing " + (data.title || track) + " by " + (data.artist || artist));
        } else {
          window.open("https://open.spotify.com/search/" + encodeURIComponent(artist + " " + track), "_blank", "noopener");
        }
      })
      .catch(function () {
        window.open("https://open.spotify.com/search/" + encodeURIComponent(artist + " " + track), "_blank", "noopener");
      })
      .finally(function () {
        if (triggerEl && restoreLabel !== null) triggerEl.textContent = restoreLabel;
      });
  };

  document.addEventListener("DOMContentLoaded", function () {
    if (!els()) return;

    toggleBtn.addEventListener("click", function () {
      if (audio.paused) {
        var p = audio.play();
        if (p && p.catch) p.catch(function () {});
        toggleBtn.textContent = "⏸"; toggleBtn.setAttribute("aria-label", "Pause");
      } else {
        audio.pause();
        toggleBtn.textContent = "▶"; toggleBtn.setAttribute("aria-label", "Play");
      }
    });
    closeBtn.addEventListener("click", closePlayer);

    audio.addEventListener("timeupdate", function () {
      if (!audio.duration) return;
      fillEl.style.width = ((audio.currentTime / audio.duration) * 100).toFixed(1) + "%";
    });
    audio.addEventListener("ended", function () {
      toggleBtn.textContent = "▶"; toggleBtn.setAttribute("aria-label", "Play");
      fillEl.style.width = "0%";
    });

    // Pause playback (but keep the bar) when the tab is hidden for a long time is unnecessary —
    // audio naturally pauses via browser autoplay policies on background tabs where needed.
  });
})();
