/* SKU autocomplete for each of the 6 SKU inputs on /orders/new/, fed by
   GET /products/options/?q= (crm.catalog.views.options — capped at 20
   active products, never the full table).

   Uses the native HTML5 <datalist> element (each SKU input already has
   list="sku-datalist-{i}" pointing at an empty <datalist> rendered right
   next to it — see crm/orders/forms.py and templates/orders/new.html) so
   the suggestion dropdown is the browser's own native autocomplete UI:
   no custom positioning/z-index code, no CSS grid layout conflicts.
*/
(function () {
  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function wireRow(skuInput) {
    const index = skuInput.id.replace("id_sku_", "");
    const nameInput = document.getElementById(`id_product_name_${index}`);
    const datalist = document.getElementById(`sku-datalist-${index}`);
    if (!nameInput || !datalist) return;

    let nameBySku = {};

    const search = debounce(async (q) => {
      if (!q) {
        datalist.innerHTML = "";
        nameBySku = {};
        return;
      }
      const res = await fetch(`/products/options/?q=${encodeURIComponent(q)}`);
      const items = await res.json();
      nameBySku = {};
      datalist.innerHTML = "";
      items.forEach((item) => {
        nameBySku[item.sku] = item.product_name;
        const option = document.createElement("option");
        option.value = item.sku;
        option.label = `${item.sku} — ${item.product_name}`;
        datalist.appendChild(option);
      });
    }, 250);

    skuInput.addEventListener("input", () => {
      const value = skuInput.value.trim();
      search(value);
      if (nameBySku[value]) {
        nameInput.value = nameBySku[value];
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('input[id^="id_sku_"]').forEach(wireRow);
  });
})();
