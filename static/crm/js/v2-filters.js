/* Filter auto-submit for the CRM V2 pages (the prototype applies filters on
 * change). Works without JS: the forms are plain GET forms and Enter submits. */
(function () {
  "use strict";

  function init(form) {
    var timer = null;
    form.addEventListener("change", function (event) {
      var target = event.target;
      // The prototype treats a preset and an explicit day range as exclusive:
      // choosing one clears the other (resetAgePreset / resetMinimum).
      if (target.matches("[data-v2-age-preset]")) {
        form.querySelectorAll("[data-v2-age-days]").forEach(function (el) { el.value = ""; });
      } else if (target.matches("[data-v2-age-days]")) {
        var preset = form.querySelector("[data-v2-age-preset]");
        if (preset) { preset.value = ""; }
      }
      if (target.matches("select, input[type=checkbox], input[type=number]")) {
        form.requestSubmit();
      }
    });
    form.addEventListener("input", function (event) {
      if (!event.target.matches("input[type=search]")) { return; }
      window.clearTimeout(timer);
      timer = window.setTimeout(function () { form.requestSubmit(); }, 400);
    });
  }

  // <dialog> openers/closers (used instead of window.confirm).
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-v2-open]");
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute("data-v2-open"));
      if (dialog && dialog.showModal) { dialog.showModal(); }
      return;
    }
    var closer = event.target.closest("[data-v2-close]");
    if (closer) {
      var parent = closer.closest("dialog");
      if (parent) { parent.close(); }
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-v2-filters]").forEach(init);
  });
})();
