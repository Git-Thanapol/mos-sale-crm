"""Regenerates static/crm/vendor/th_address.json (province -> district ->
subdistrict -> postal code, plus a zip -> [province_code, district_code,
subdistrict_code] reverse index) used by the address cascade on
/orders/new/. Source data (not vendored raw — download fresh if re-running):

  https://raw.githubusercontent.com/thailand-geography-data/thailand-geography-json/main/src/provinces.json
  https://raw.githubusercontent.com/thailand-geography-data/thailand-geography-json/main/src/districts.json
  https://raw.githubusercontent.com/thailand-geography-data/thailand-geography-json/main/src/subdistricts.json

Run: download the 3 files above into a scratch dir, then
    python scripts/build_th_address.py <scratch_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(source_dir: Path) -> None:
    provinces = json.loads((source_dir / "th_provinces.json").read_text(encoding="utf-8"))
    districts = json.loads((source_dir / "th_districts.json").read_text(encoding="utf-8"))
    subdistricts = json.loads((source_dir / "th_subdistricts.json").read_text(encoding="utf-8"))

    districts_by_province: dict[int, list] = {}
    for d in districts:
        districts_by_province.setdefault(d["provinceCode"], []).append(d)

    subs_by_district: dict[int, list] = {}
    for s in subdistricts:
        subs_by_district.setdefault(s["districtCode"], []).append(s)

    by_zip: dict[str, list] = {}
    out_provinces = []
    for p in sorted(provinces, key=lambda x: x["provinceNameTh"]):
        p_code, p_name = p["provinceCode"], p["provinceNameTh"]
        out_districts = []
        for d in sorted(districts_by_province.get(p_code, []), key=lambda x: x["districtNameTh"]):
            d_code, d_name = d["districtCode"], d["districtNameTh"]
            out_subs = []
            for s in sorted(subs_by_district.get(d_code, []), key=lambda x: x["subdistrictNameTh"]):
                zip_code = str(s["postalCode"]).zfill(5)
                out_subs.append({"code": s["subdistrictCode"], "name": s["subdistrictNameTh"], "zip": zip_code})
                by_zip.setdefault(zip_code, []).append([p_code, d_code, s["subdistrictCode"]])
            out_districts.append({"code": d_code, "name": d_name, "subdistricts": out_subs})
        out_provinces.append({"code": p_code, "name": p_name, "districts": out_districts})

    result = {"provinces": out_provinces, "by_zip": by_zip}

    out_path = REPO_ROOT / "static" / "crm" / "vendor" / "th_address.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"provinces={len(out_provinces)} districts={len(districts)} "
          f"subdistricts={len(subdistricts)} zip_codes={len(by_zip)} "
          f"bytes={out_path.stat().st_size}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/build_th_address.py <dir containing the 3 source json files>")
    main(Path(sys.argv[1]))
