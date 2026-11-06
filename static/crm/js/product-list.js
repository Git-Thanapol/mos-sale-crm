/* Product list: select-all and the bulk bar. Without JS the bar simply stays
 * visible and the plain form still works; dialogs use v2-filters.js. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var bar = document.querySelector("[data-product-bulk]");
    if (!bar) { return; }
    var all = document.querySelector("[data-product-select-all]");
    var rows = Array.prototype.slice.call(document.querySelectorAll("[data-product-select]"));
    var count = bar.querySelector("[data-product-count]");

    function sync() {
      var selected = rows.filter(function (row) { return row.checked; }).length;
      count.textContent = selected;
      bar.hidden = selected === 0;
      all.checked = selected > 0 && selected === rows.length;
      all.indeterminate = selected > 0 && selected < rows.length;
    }

    all.addEventListener("change", function () {
      rows.forEach(function (row) { row.checked = all.checked; });
      sync();
    });
    rows.forEach(function (row) { row.addEventListener("change", sync); });
    sync();
  });
})();
