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

  // Lazy order history: the first button swaps the container, "โหลดเพิ่ม"
  // appends its rows in place of itself.
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-v2-history]");
    if (!button) { return; }
    button.disabled = true;
    fetch(button.getAttribute("data-v2-history"), { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) { throw new Error("history " + response.status); }
        return response.text();
      })
      .then(function (html) {
        var target = button.getAttribute("data-v2-into");
        if (target) {
          document.querySelector(target).innerHTML = html;
          button.disabled = false;
        } else {
          button.insertAdjacentHTML("beforebegin", html);
          button.remove();
        }
      })
      .catch(function () {
        button.disabled = false;
        button.insertAdjacentHTML("afterend", '<div class="state-box error">โหลดไม่สำเร็จ</div>');
      });
  });

  // Decision forms show only the payload fields of the chosen action (the
  // prototype re-renders them from React state; without JS every field shows).
  function syncDecisionForm(select) {
    var form = select.closest("form");
    form.querySelectorAll("[data-v2-for]").forEach(function (el) {
      el.hidden = el.getAttribute("data-v2-for").split(" ").indexOf(select.value) === -1;
    });
  }
  document.addEventListener("change", function (event) {
    if (event.target.matches("select[data-v2-action]")) { syncDecisionForm(event.target); }
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select[data-v2-action]").forEach(syncDecisionForm);
  });

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

  // Forms the prototype submits the moment a file or date is chosen.
  document.addEventListener("change", function (event) {
    var form = event.target.closest("form[data-v2-autosubmit]");
    if (form && event.target.value) { form.requestSubmit(); }
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-v2-filters]").forEach(init);
  });
})();
