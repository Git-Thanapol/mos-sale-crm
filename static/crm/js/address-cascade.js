/* Province -> district -> subdistrict cascade for /orders/new/, backed by
   the vendored dataset (static/crm/vendor/th_address.json, built by
   scripts/build_th_address.py). Also supports typing a postal code
   directly and having it resolve back to province/district/subdistrict —
   a postal code is not 1:1 with a subdistrict in Thailand, so multiple
   matches show a small picker instead of guessing.
*/
/* Registered on the alpine:init hook (not a bare global function) because
   both this file and alpine.min.js load with `defer` — Alpine auto-starts
   as soon as its own script runs (document.readyState is already
   "interactive" by then, not "loading"), which is BEFORE this file's
   script tag executes. A plain global `function thAddressCascade(){}`
   would not exist yet when Alpine first scans the DOM for x-data.
*/
document.addEventListener("alpine:init", () => {
  Alpine.data("thAddressCascade", (initial) => ({
    data: null,
    provinceCode: "", districtCode: "", subdistrictCode: "",
    provinceName: initial.province || "", districtName: initial.city || "",
    subdistrictName: "", postalCode: initial.postal_code || "",
    districts: [], subdistricts: [], zipMatches: [],

    async init() {
      const res = await fetch("/static/crm/vendor/th_address.json");
      this.data = await res.json();
      if (this.provinceName) {
        const p = this.data.provinces.find((p) => p.name === this.provinceName);
        if (p) {
          this.provinceCode = p.code;
          this.districts = p.districts;
          if (this.districtName) {
            const d = p.districts.find((d) => d.name === this.districtName);
            if (d) {
              this.districtCode = d.code;
              this.subdistricts = d.subdistricts;
            }
          }
        }
      }
    },

    onProvinceChange() {
      const p = this.data.provinces.find((p) => String(p.code) === String(this.provinceCode));
      this.provinceName = p ? p.name : "";
      this.districts = p ? p.districts : [];
      this.districtCode = ""; this.districtName = "";
      this.subdistricts = []; this.subdistrictCode = ""; this.subdistrictName = "";
    },

    onDistrictChange() {
      const d = this.districts.find((d) => String(d.code) === String(this.districtCode));
      this.districtName = d ? d.name : "";
      this.subdistricts = d ? d.subdistricts : [];
      this.subdistrictCode = ""; this.subdistrictName = "";
    },

    onSubdistrictChange() {
      const s = this.subdistricts.find((s) => String(s.code) === String(this.subdistrictCode));
      this.subdistrictName = s ? s.name : "";
      if (s) {
        this.postalCode = s.zip;
        this.zipMatches = [];
        this.applySubdistrictToAddress();
      }
    },

    applySubdistrictToAddress() {
      const el = document.getElementById("id_address");
      if (el && this.subdistrictName && !el.value.includes("ตำบล")) {
        el.value = `ตำบล${this.subdistrictName} ${el.value}`;
      }
    },

    onPostalInput() {
      this.zipMatches = [];
      if (!/^[0-9]{5}$/.test(this.postalCode)) return;
      const matches = this.data.by_zip[this.postalCode];
      if (!matches) return;
      if (matches.length === 1) {
        this.applyZipMatch(matches[0]);
      } else {
        this.zipMatches = matches;
      }
    },

    labelFor(m) {
      const p = this.data.provinces.find((p) => p.code === m[0]);
      const d = p ? p.districts.find((d) => d.code === m[1]) : null;
      const s = d ? d.subdistricts.find((s) => s.code === m[2]) : null;
      return [s && s.name, d && d.name, p && p.name].filter(Boolean).join(" ");
    },

    chooseZipMatch(idx) {
      if (idx === "") return;
      this.applyZipMatch(this.zipMatches[idx]);
      this.zipMatches = [];
    },

    applyZipMatch([pCode, dCode, sCode]) {
      const p = this.data.provinces.find((p) => p.code === pCode);
      this.provinceCode = pCode; this.provinceName = p.name; this.districts = p.districts;
      const d = p.districts.find((d) => d.code === dCode);
      this.districtCode = dCode; this.districtName = d.name; this.subdistricts = d.subdistricts;
      const s = d.subdistricts.find((s) => s.code === sCode);
      this.subdistrictCode = sCode; this.subdistrictName = s.name;
      this.applySubdistrictToAddress();
    },
  }));
});
