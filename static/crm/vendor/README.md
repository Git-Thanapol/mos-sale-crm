# Vendored JS

- `htmx.min.js` — htmx.org **2.0.4**, downloaded 2026-07-31 from https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
- `alpine.min.js` — alpinejs **3.14.9** (cdn build), downloaded 2026-07-31 from https://unpkg.com/alpinejs@3.14.9/dist/cdn.min.js

No CDN script tags at runtime — the no-online-services rule applies to the browser too; these are one-time downloads committed to the repo. Previously these files didn't exist (Phase 1 left only this README as a placeholder) — `templates/base.html`'s `<script defer>` tags 404'd harmlessly for every phase since, meaning no Alpine-driven interactivity anywhere in the app (order-line editor, address cascade) actually ran in the browser until this was fixed. To bump versions: download the new pinned dist file from unpkg, overwrite here, update this comment.
