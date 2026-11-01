/* Filter auto-submit for the CRM V2 pages (the prototype applies filters on
 * change). Works without JS: the forms are plain GET forms and Enter submits. */
(function () {
  "use strict";

  function init(form) {
    var timer = null;
    form.addEventListener("change", function (event) {
      if (event.target.matches("select, input[type=checkbox], input[type=number]")) {
        form.requestSubmit();
      }
    });
    form.addEventListener("input", function (event) {
      if (!event.target.matches("input[type=search]")) { return; }
      window.clearTimeout(timer);
      timer = window.setTimeout(function () { form.requestSubmit(); }, 400);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-v2-filters]").forEach(init);
  });
})();
