/*
 * Site behaviour: theme switching, HTMX wiring, copy buttons, sidebar,
 * and the two Chart.js charts. Kept small on purpose.
 */
(function () {
  "use strict";

  // ---- Helpers ---------------------------------------------------------

  function qs(sel, el) {
    return (el || document).querySelector(sel);
  }

  function qsa(sel, el) {
    return Array.prototype.slice.call((el || document).querySelectorAll(sel));
  }

  var toastTimer = null;
  function showToast(message) {
    var toast = qs("[data-toast]");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.hidden = true;
    }, 2500);
  }

  function copyText(text, done) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, done);
    } else if (done) {
      done();
    }
  }

  // ---- Icons & charts --------------------------------------------------

  function refreshIcons(root) {
    if (window.lucide) window.lucide.createIcons();
  }

  function themeColors() {
    var doc = document.documentElement;
    return {
      grid: doc.classList.contains("dark") ? "#0e353c" : "#c3d6da",
      labels: doc.classList.contains("dark") ? "#7b989e" : "#49656d",
      accent: "#0ad1c8",
      accentFill: "rgba(10, 209, 200, 0.16)",
    };
  }

  function drawSeriesChart(canvas, data) {
    if (!canvas || !window.Chart || !data) return;
    var colors = themeColors();
    /* eslint-disable-next-line no-new */
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.map(function (point) {
          var d = new Date(point.date + "T00:00:00");
          return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        }),
        datasets: [
          {
            label: "Clicks",
            data: data.map(function (point) {
              return point.clicks;
            }),
            borderColor: colors.accent,
            backgroundColor: colors.accentFill,
            fill: true,
            tension: 0.35,
            pointRadius: 0,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { color: colors.labels, maxTicksLimit: 8 } },
          y: { beginAtZero: true, grid: { color: colors.grid }, ticks: { color: colors.labels, precision: 0 } },
        },
      },
    });
  }

  function renderCharts() {
    var dashData = qs("#dashboard-chart-data");
    if (dashData) {
      drawSeriesChart(qs("#dashboard-chart"), JSON.parse(dashData.textContent));
    }
    var analyticsData = qs("#analytics-chart-data");
    if (analyticsData) {
      drawSeriesChart(qs("#analytics-chart"), JSON.parse(analyticsData.textContent));
    }
  }

  // ---- Theme -----------------------------------------------------------

  var THEME_KEY = "theme";

  function applyTheme(pref) {
    var dark =
      pref === "dark" ||
      (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
    qsa("[data-theme-choice]").forEach(function (btn) {
      btn.dataset.active = btn.dataset.themeChoice === pref ? "true" : "false";
    });
    try {
      localStorage.setItem(THEME_KEY, pref);
    } catch (e) {
      /* private mode: theme won't persist */
    }
  }

  function currentTheme() {
    try {
      return localStorage.getItem(THEME_KEY) || "system";
    } catch (e) {
      return "system";
    }
  }

  // ---- Event wiring ----------------------------------------------------

  document.addEventListener("click", function (event) {
    var target = event.target;

    // Theme choices (profile page).
    var choice = target.closest("[data-theme-choice]");
    if (choice) {
      applyTheme(choice.dataset.themeChoice);
      return;
    }

    // Quick toggle in the sidebar.
    var toggle = target.closest("[data-theme-toggle]");
    if (toggle) {
      var current = currentTheme();
      var isDark =
        current === "dark" ||
        (current === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      applyTheme(isDark ? "light" : "dark");
      return;
    }

    // Mobile sidebar.
    var burger = target.closest("[data-toggle-sidebar]");
    if (burger) {
      var sidebar = qs("#sidebar");
      sidebar.classList.toggle("hidden");
      sidebar.classList.toggle("flex");
      return;
    }

    // Copy buttons.
    var copyBtn = target.closest("[data-copy]");
    if (copyBtn) {
      copyText(copyBtn.dataset.copy, function () {
        showToast("Copied " + copyBtn.dataset.copy);
      });
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(currentTheme());
    qsa("[data-theme-choice]").forEach(function (btn) {
      btn.dataset.active = btn.dataset.themeChoice === currentTheme() ? "true" : "false";
    });
    refreshIcons();
    renderCharts();
  });

  // Redraw icons after HTMX overwrites page fragments.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    refreshIcons(event.target);
  });

  document.body.addEventListener("links-changed", function () {
    // Table region refetches itself via its own hx-trigger; nothing else needed.
  });

  // ---- HTMX config ------------------------------------------------------

  document.body.addEventListener("htmx:configRequest", function (event) {
    var token = document.querySelector('meta[name="csrf-token"]');
    if (token) event.detail.headers["X-CSRFToken"] = token.content;
  });
})();
