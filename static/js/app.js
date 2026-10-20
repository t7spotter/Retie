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
    if (window.lucide) window.lucide.createIcons({ attrs: { "stroke-width": 1.75 } });
  }

  // Read the design tokens out of computed styles so the charts follow the
  // active theme (light or dark) without duplicating hex values here.
  function themeColors() {
    var style = getComputedStyle(document.documentElement);
    function token(name, fallback) {
      var value = style.getPropertyValue(name).trim();
      return value || fallback;
    }
    return {
      grid: token("--line", "#E3DCF2"),
      labels: token("--ink-soft", "#575079"),
      accent: token("--violet", "#6C4DFF"),
      accentFill: toRgba(token("--violet", "#6C4DFF"), 0.16),
    };
  }

  function toRgba(hex, alpha) {
    var match = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!match) return hex;
    var n = parseInt(match[1], 16);
    return "rgba(" + (n >> 16) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + alpha + ")";
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
  //
  // system | light | dark, cycled by the sidebar button and chosen exactly
  // on the profile page, stored under "retie-theme" like the design system.
  // The system preference resolves to an explicit data-theme attribute so
  // token styles and Tailwind's dark: variant always agree.

  var THEME_KEY = "retie-theme";

  function applyTheme(pref) {
    var dark =
      pref === "dark" ||
      (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (pref === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      // Light (both chosen and resolved system light) is applied explicitly.
      document.documentElement.setAttribute("data-theme", "light");
    }
    qsa("[data-theme-choice]").forEach(function (btn) {
      btn.dataset.active = btn.dataset.themeChoice === pref ? "true" : "false";
    });
    syncThemeControls(pref);
    try {
      localStorage.setItem(THEME_KEY, pref);
      localStorage.removeItem("theme"); // pre-rename key; migrate once
    } catch (e) {
      /* private mode: theme won't persist */
    }
  }

  function syncThemeControls(pref) {
    var label = pref.charAt(0).toUpperCase() + pref.slice(1);
    qsa("[data-theme-toggle]").forEach(function (btn) {
      var text = btn.querySelector("[data-theme-label]");
      if (text) text.textContent = label;
      btn.setAttribute("aria-label", "Theme: " + label);
    });
    qsa("[data-theme-choice]").forEach(function (btn) {
      var on = btn.dataset.themeChoice === pref;
      btn.dataset.active = on ? "true" : "false";
      // Profile page choices are styled as design-system tabs.
      btn.classList.toggle("is-selected", on);
    });
  }

  function currentTheme() {
    try {
      return localStorage.getItem(THEME_KEY) || localStorage.getItem("theme") || "system";
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

    // Quick cycler in the sidebar: system -> light -> dark.
    var toggle = target.closest("[data-theme-toggle]");
    if (toggle) {
      var modes = ["system", "light", "dark"];
      var next = modes[(modes.indexOf(currentTheme()) + 1) % modes.length];
      applyTheme(next);
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
      return;
    }

    // QR modal — but only when the click isn't inside the already-open one.
    var qrModal = document.getElementById("qr-modal");
    if (qrModal) {
      if (event.target.closest("#qr-modal-copy")) {
        copyText(qrModal.dataset.url || "", function () {
          showToast("Copied " + (qrModal.dataset.url || ""));
        });
        return;
      }
      // Close button, or a click on the dark backdrop (outside the panel).
      if (event.target.closest("[data-qr-close]") ||
          (qrModal.open && !event.target.closest("#qr-modal > div"))) {
        qrModal.close();
        return;
      }
    }

    // QR popup: open from a row button.
    var qrBtn = target.closest("[data-qr]");
    if (qrBtn) openQrModal(qrBtn);
  });

  // ---- QR modal ----------------------------------------------------------

  function openQrModal(trigger) {
    var dialog = document.getElementById("qr-modal");
    if (!dialog) return;
    qs("#qr-modal-code").textContent = "/" + trigger.dataset.qr;
    var url = trigger.dataset.qrUrl;
    var urlEl = qs("#qr-modal-url");
    urlEl.textContent = url;
    urlEl.title = url;
    dialog.dataset.url = url;
    qs("#qr-modal-image").src = trigger.dataset.qrSrc;
    var download = qs("#qr-modal-download");
    download.href = trigger.dataset.qrSrc;
    download.setAttribute("download", "qr-" + trigger.dataset.qr + ".svg");
    dialog.showModal();
  }

  // ---- Date & time pickers (flatpickr) ----------------------------------
  //
  // Every datetime-local input gets a themed calendar+time widget. The
  // visible "alt" input shows a human date; the original hidden input
  // keeps the Y-m-dTH:i value Django's DateTimeField parses, so server
  // behavior is unchanged.

  var pickerFor = new WeakMap();

  function initPickers() {
    if (!window.flatpickr) return;
    qsa('input[type="datetime-local"]').forEach(function (input) {
      if (input.dataset.pickerInit) return;
      input.dataset.pickerInit = "1";
      var fp = window.flatpickr(input, {
        enableTime: true,
        dateFormat: "Y-m-dTH:i",      // submitted to Django
        altInput: true,
        altFormat: "D, M j, Y · h:i K", // shown to the human
        altInputClass: input.className + " cursor-pointer select-none",
        disableMobile: true,          // themed picker on phones too
        ariaDateFormat: "D, M j, Y, h:i K",
      });
      pickerFor.set(input, fp);
      // Retarget the field's label at the (now visible) alt input, which
      // also opens the calendar on click.
      var alt = fp.altInput;
      if (alt) {
        if (!alt.id) alt.id = input.id + "-alt";
        var label = document.querySelector('label[for="' + input.id + '"]');
        if (label) label.setAttribute("for", alt.id);
        alt.placeholder = "Pick a date & time";
      }
    });
  }

  document.addEventListener("click", function (event) {
    var clear = event.target.closest("[data-clear-picker]");
    if (!clear) return;
    var input = document.getElementById(clear.getAttribute("data-clear-picker"));
    if (!input) return;
    var fp = pickerFor.get(input);
    if (fp) fp.clear();
  });

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(currentTheme());
    qsa("[data-theme-choice]").forEach(function (btn) {
      btn.dataset.active = btn.dataset.themeChoice === currentTheme() ? "true" : "false";
    });
    refreshIcons();
    renderCharts();
    drawSparklines();
    initPickers();
  });

  // Redraw icons after HTMX overwrites page fragments.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    refreshIcons(event.target);
    refreshBulkBar(); // rows were re-rendered; resync selection bar
    drawSparklines();
  });

  // ---- Bulk selection ---------------------------------------------------

  function bulkBar() { return document.getElementById("bulk-bar"); }

  function bulkCodes() {
    return qsa('input[name="bulk-code"]:checked').map(function (box) {
      return box.value;
    });
  }

  function refreshBulkBar() {
    var bar = bulkBar();
    if (!bar) return;
    var codes = bulkCodes();
    bar.classList.toggle("hidden", codes.length === 0);
    bar.classList.toggle("flex", codes.length > 0);
    var count = document.getElementById("bulk-count");
    if (count) count.textContent = codes.length + " selected";
    var field = document.getElementById("bulk-codes");
    if (field) field.value = codes.join(",");
  }

  // ---- Row sparklines (14-day click micro-bars) -------------------------

  function drawSparklines() {
    var dataEl = qs("#spark-all-data");
    if (!dataEl) return;
    var spark;
    try { spark = JSON.parse(dataEl.textContent); } catch (e) { return; }
    qsa("[data-spark]").forEach(function (holder) {
      var series = spark[holder.getAttribute("data-spark")];
      holder.textContent = "";
      if (!series || !series.length) return;
      var max = Math.max.apply(null, series) || 1;
      series.forEach(function (n) {
        var bar = document.createElement("i");
        // At least 2px tall when zero so empty days stay visible.
        bar.style.height = (n > 0 ? Math.max(3, Math.round((n / max) * 14)) : 2) + "px";
        bar.style.width = "3px";
        bar.className = n > 0
          ? "inline-block rounded-sm bg-violet/60"
          : "inline-block rounded-sm bg-line";
        holder.appendChild(bar);
      });
    });
  }

  // Delegated so checkboxes inside HTMX-refreshed rows keep working.
  document.addEventListener("change", function (event) {
    if (event.target.name === "bulk-code") {
      refreshBulkBar();
    }
  });

  document.addEventListener("click", function (event) {
    var toggleAll = event.target.closest("[data-bulk-toggle-all]");
    if (!toggleAll) return;
    qsa('input[name="bulk-code"]').forEach(function (box) {
      box.checked = toggleAll.checked;
    });
    refreshBulkBar();
  });

  // Reset selection state after a bulk action reloads the dashboard.
  if (bulkBar()) {
    bulkBar().addEventListener("submit", function () {
      qsa('input[name="bulk-code"]').forEach(function (box) {
        box.checked = false;
      });
      var toggle = document.querySelector("[data-bulk-toggle-all]");
      if (toggle) toggle.checked = false;
      refreshBulkBar();
    });
  }

  document.body.addEventListener("links-changed", function () {
    // Table region refetches itself via its own hx-trigger; nothing else needed.
  });

  // ---- HTMX config ------------------------------------------------------

  document.body.addEventListener("htmx:configRequest", function (event) {
    var token = document.querySelector('meta[name="csrf-token"]');
    if (token) event.detail.headers["X-CSRFToken"] = token.content;
  });
})();
