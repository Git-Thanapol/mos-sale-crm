# CRM V2 prototype UI parity

The customer's prototype (`customer-crm-v2-codex`, `src/ui/App.tsx`,
`DecisionCenter.tsx`, `styles.css`) was ported page for page under
ติดตามลูกค้า. This folder is the visual record.

* `proto-*.jpg`: the prototype running at `http://127.0.0.1:5174/` (Admin role,
  synthetic data).
* `crm-*.jpg`: the Django pages at `http://127.0.0.1:8000/followup/...` on the
  demo data (`python manage.py seed_leadui_demo`, DEBUG only, `--clean` removes it).

Same markup, class names and Thai copy as the prototype; the stylesheet is the
prototype's own, re-scoped under `.v2` (`static/crm/css/v2-followup.css`). What
differs is only the host shell: this site's sidebar, fonts and colours.

| Screen | Route | Who sees it |
|---|---|---|
| งานของฉัน | `/followup/work/` | EDITOR (all), TEAM_LEAD (own teams), พนักงาน (own leads) |
| Global Unassigned | `/followup/global-unassigned/` | EDITOR |
| Team Unassigned | `/followup/team-unassigned/` | EDITOR, TEAM_LEAD |
| STOPPED Review | `/followup/stopped/` | EDITOR, TEAM_LEAD |
| รายละเอียด Lead | `/followup/lead/<id>/` | anyone who can view that lead |
| ข้อมูลลูกค้า (+ history) | `/followup/customer/<id>/` | customer in scope, or via a visible lead |
| นำเข้าข้อมูล | `/followup/import/` | EDITOR |
| แก้ Conflict | `/followup/import/conflicts/` | EDITOR, TEAM_LEAD (own team, never identity) |
| ประวัตินำเข้า | `/followup/import/history/` | EDITOR, TEAM_LEAD (no Rollback) |
| Migration Decisions | `/followup/import/decisions/` | EDITOR |

## Deliberate deviations from the prototype

| # | Prototype | Here |
|---|---|---|
| R1 | client-side screens with a loading skeleton | full page loads, same empty/error boxes |
| R2 | filters apply on change | same, via `v2-filters.js`; works without JS (Enter) |
| R3 | two hard-coded team buttons on Global Unassigned | one button per active Team |
| R4 | "เปลี่ยน caretaker" assigns a demo user | a dialog with a real caretaker list |
| R5 | "นัดพรุ่งนี้" sends now + 24 h | same, server-side |
| R6 | sidebar hides unreachable screens | same (children hidden, not greyed) |
| R7 | detail screens default to a demo id | need an id, no menu entry |
| R8 | one import path | coexists with the legacy `imports:upload` until cutover |
| R9 | `window.confirm` and a fixture file picker | `<dialog>`; the picker shows the real uploaded file |

Further rulings are in `docs/DECISIONS.md` ("CRM V2 follow-up UI and import").
