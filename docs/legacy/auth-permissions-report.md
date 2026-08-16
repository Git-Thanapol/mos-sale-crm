# CRM Streamlit → Django Migration Spec: Existing-System Documentation

Root: `C:\Laptop files\MOS\MOS_CRM_202607\crm_streamlit`

---

## 1. `auth_utils.py` — Authentication (669 lines)

### 1.1 Config / secret key names

`auth_utils.py:77-102` — secret resolution tries `st.secrets` first, then `os.getenv`, returning the first non-empty:

```python
def get_secret(*names: str) -> str:
    for name in names:
        if name in st.secrets:
            return str(st.secrets[name])
        value = os.getenv(name, "")
        if value:
            return value
    return ""

def supabase_url() -> str:
    return get_secret(
        "AUTH_SUPABASE_URL", "SUPABASE_AUTH_URL", "CRM_SUPABASE_URL", "SUPABASE_URL",
    ).rstrip("/")

def supabase_anon_key() -> str:
    return get_secret(
        "AUTH_SUPABASE_ANON_KEY", "SUPABASE_AUTH_ANON_KEY", "CRM_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY",
    )
```

**Key names expected** (4-deep fallback chain each, values redacted):
- URL: `AUTH_SUPABASE_URL` → `SUPABASE_AUTH_URL` → `CRM_SUPABASE_URL` → `SUPABASE_URL`
- Anon key: `AUTH_SUPABASE_ANON_KEY` → `SUPABASE_AUTH_ANON_KEY` → `CRM_SUPABASE_ANON_KEY` → `SUPABASE_ANON_KEY`

`C:\Laptop files\MOS\MOS_CRM_202607\crm_streamlit\.streamlit\secrets.toml` contains exactly **two** keys (values not printed):
1. `SUPABASE_URL`
2. `SUPABASE_ANON_KEY`

Note: **`NEON_DATABASE_URL` is NOT in the local `secrets.toml`** even though `neon_utils.py:319-322` requires it — so locally it must come from the `NEON_DATABASE_URL` env var, and in Streamlit Cloud from cloud secrets. `docs/SMOKE_TEST_CHECKLIST.md:175` asserts `NEON_DATABASE_URL` must be set in Streamlit Secrets. Legacy `archive/legacy/README.md` also documents `CRM_SUPABASE_URL` / `CRM_SUPABASE_ANON_KEY` and a legacy `CRM_SUPABASE_SERVICE_KEY` / `CRM_SYNC_ADMIN_PASSWORD` (`archive/legacy/DATA_RAW_SYNC.md`), all dead.

Other env vars in use: `CRM_PERF_DEBUG` (`ui/perf.py:11`).

### 1.2 Supabase Auth endpoints called via `requests`

Only three endpoints, all under `/auth/v1/*`. There is **no** Supabase SDK, **no** `/rest/v1/*`, **no** storage.

Shared header builder (`auth_utils.py:284-289`):
```python
def _auth_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
```

Transport wrapper with 20 s timeout and Thai-language error translation (`auth_utils.py:292-305`):
```python
def _supabase_auth_request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, url, timeout=20, **kwargs)
    except requests.Timeout as exc:
        raise RuntimeError("เชื่อมต่อ Supabase Auth timeout กรุณาลองใหม่อีกครั้ง") from exc
    except requests.RequestException as exc:
        raise RuntimeError("เชื่อมต่อ Supabase Auth ไม่สำเร็จ กรุณาตรวจอินเทอร์เน็ตหรือ Supabase project") from exc

def _raise_auth_error(response: requests.Response, default_message: str) -> None:
    if response.status_code == 402:
        raise RuntimeError("Supabase Auth ถูกจำกัดการใช้งาน กรุณาตรวจ Supabase project หรือ billing")
    if response.status_code >= 400:
        raise RuntimeError(default_message)
```

| Function | file:line | Method + URL | Body | Error message on ≥400 |
|---|---|---|---|---|
| `login_with_password(email, password)` | `auth_utils.py:308-321` | `POST {base}/auth/v1/token?grant_type=password` | `{"email": email.strip().lower(), "password": password}` | `"อีเมลหรือรหัสผ่านไม่ถูกต้อง หรือผู้ใช้ยังไม่ได้ถูกสร้างใน Supabase Auth"` |
| `refresh_auth_session(refresh_token)` | `auth_utils.py:324-337` | `POST {base}/auth/v1/token?grant_type=refresh_token` | `{"refresh_token": refresh_token}` | `"session หมดอายุ กรุณาเข้าสู่ระบบใหม่"` |
| `fetch_auth_user(access_token)` | `auth_utils.py:340-356` | `GET {base}/auth/v1/user` | — (uses `Authorization: Bearer <access_token>`, `apikey: <anon>`) | `"session ไม่ถูกต้องหรือหมดอายุ"` |

Missing config raises `RuntimeError("ยังไม่ได้ตั้งค่า CRM_SUPABASE_URL หรือ CRM_SUPABASE_ANON_KEY")` in all three (`:312`, `:328`, `:344`).

**There is no signup, no password reset, no magic link, no OAuth, no MFA, no email verification.** Users must be pre-created in Supabase Auth by an administrator — the login card states this explicitly (`auth_utils.py:447`): `ใช้บัญชีที่ได้รับสิทธิ์จาก Supabase Auth เท่านั้น`. `docs/STAFF_MAPPING_DECISION_REQUIRED.md:41` confirms the manual workflow: create the user in Supabase Auth first, then add the `crm_user_roles` mapping.

### 1.3 Login UI

`require_login()` (`auth_utils.py:459-507`) is the single entry gate. Every page calls it.

```python
def require_login() -> dict:
    inject_auth_css()
    if st.session_state.pop("auth_clear_browser_session", False):
        clear_browser_session()
    restore_status = "empty"
    if not st.session_state.get("auth_skip_restore"):
        restore_status = restore_browser_session()
    user = current_user()
    if user:
        if restore_status == "restored" and st.session_state.get("crm_sidebar_nav_last_disabled"):
            st.session_state.crm_sidebar_nav_last_disabled = False
            st.rerun()
        render_user_box(user)
        return user

    inject_login_css()
    if restore_status == "pending":
        render_login_shell("กำลังตรวจสอบ session จาก browser...")
        st.stop()

    render_login_shell()
    left, center, right = st.columns([1, 1.08, 1])
    with center:
        with st.form("crm_login_form"):
            email = st.text_input("อีเมล", value="", placeholder="name@example.com")
            password = st.text_input("รหัสผ่าน", value="", type="password")
            submitted = st.form_submit_button("เข้าสู่ระบบ", use_container_width=True)

    if submitted:
        try:
            payload = login_with_password(email, password)
            auth_user = payload.get("user") or {}
            user_email = (auth_user.get("email") or email).strip().lower()
            role = fetch_user_role(user_email)
            st.session_state.auth_access_token = payload.get("access_token")
            st.session_state.auth_refresh_token = payload.get("refresh_token")
            st.session_state.auth_user = auth_user
            st.session_state.auth_role = role
            st.session_state.auth_session_expires_at = int(time.time()) + LOCAL_STORAGE_TTL_SECONDS
            st.session_state.pop("auth_skip_restore", None)
            st.session_state.pop("auth_clear_browser_session", None)
            save_browser_session(payload, role)
            user = current_user()
            if user:
                render_user_box(user)
                return user
        except Exception as exc:
            st.error(str(exc))
    st.stop()
```

UI pieces:
- `inject_auth_css()` (`:105-158`) — sidebar auth card, orange pill buttons (`#F97316`), forced white inputs. Always injected.
- `inject_login_css()` (`:161-281`) — full-page radial-gradient login background, **hides sidebar nav** (`[data-testid="stSidebarNav"] { display:none }`), 430 px card, mobile breakpoint at 760 px. Injected only when not logged in.
- `render_login_shell(message=None)` (`:434-456`) — brand block "Sales CRM" / "เข้าสู่ระบบเพื่อจัดการข้อมูลลูกค้า" in a 3-column `[1, 1.08, 1]` layout.
- `render_user_box(user)` (`:407-421`) — sidebar card showing email / role / staff_name plus the logout button:
```python
if st.button("ออกจากระบบ", use_container_width=True, key="auth_logout_button"):
    logout()
```
- `html_escape(value)` (`:424-431`) — hand-rolled escaping for `&<>"` used in the HTML cards.

### 1.4 Session storage — `st.session_state` keys

Auth-owned keys:

| Key | Type | Set at | Meaning |
|---|---|---|---|
| `auth_access_token` | str | `:493`, `:541`, `:615`, `:626` | Supabase JWT |
| `auth_refresh_token` | str | `:494`, `:542`, `:616`, `:627` | Supabase refresh token |
| `auth_user` | dict | `:495`, `:543`, `:633` | Raw Supabase Auth user object |
| `auth_role` | dict | `:496`, `:544`, `:634` | `crm_user_roles` row (from Neon) |
| `auth_session_expires_at` | int (epoch) | `:497`, `:562`, `:617` | Hard 8-hour session wall-clock expiry |
| `auth_skip_restore` | bool | `:385` (logout), popped `:498` | Suppresses localStorage restore after explicit logout |
| `auth_clear_browser_session` | bool | `:386`, popped `:461` | One-shot flag telling the next run to wipe localStorage |
| `crm_sidebar_nav_last_disabled` | bool | `nav_utils.py:55`, read/reset `auth_utils.py:468-470` | Forces a rerun so the sidebar re-enables after a silent restore |

Constants (`auth_utils.py:29-37`):
```python
AUTH_STORAGE_KEY = "crm_core_auth_session"
TOKEN_REFRESH_GRACE_SECONDS = 90
LOCAL_STORAGE_TTL_SECONDS = 8 * 60 * 60      # 28800 s
BROWSER_SESSION_PENDING = "pending"
BROWSER_SESSION_EMPTY = "empty"
BROWSER_SESSION_HAS_SESSION = "has_session"
BROWSER_SESSION_INVALID = "invalid"
_BROWSER_SESSION_BRIDGE_READY_KEY = "bridge_ready"
_BROWSER_SESSION_BRIDGE_PAYLOAD_KEY = "session_payload"
```

### 1.5 localStorage persistence via `streamlit-js-eval`

Import is soft-failing (`auth_utils.py:23-26`) — if the package is missing, persistence degrades to session-only:
```python
try:
    from streamlit_js_eval import streamlit_js_eval
except ImportError:  # Local fallback until dependencies are installed.
    streamlit_js_eval = None
```

**Write** (`:558-574`) — the whole session (including both tokens, the Supabase user object and the Neon role row) is JSON-serialized twice and written to `localStorage['crm_core_auth_session']`. The `key=` is time-based so each write is a distinct component:
```python
def save_browser_session(payload: dict, role: dict) -> None:
    if streamlit_js_eval is None:
        return
    expires_at = int(st.session_state.get("auth_session_expires_at") or 0) or int(time.time()) + LOCAL_STORAGE_TTL_SECONDS
    st.session_state.auth_session_expires_at = expires_at
    session_payload = {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token") or st.session_state.get("auth_refresh_token"),
        "user": payload.get("user") or st.session_state.get("auth_user") or {},
        "role": role or st.session_state.get("auth_role") or {},
        "expires_at": expires_at,
    }
    js_value = json.dumps(json.dumps(session_payload, ensure_ascii=False))
    streamlit_js_eval(
        js_expressions=f"localStorage.setItem('{AUTH_STORAGE_KEY}', {js_value}); 'ok'",
        key=f"auth_save_{int(time.time() * 1000)}",
    )
```

**Clear** (`:577-583`): `localStorage.removeItem('crm_core_auth_session')`.

**Read / restore** (`:586-648`) — the "bridge" wrapper distinguishes "JS has not answered yet" (`None` → `pending`) from "JS answered, storage empty" (`{bridge_ready: true, session_payload: null}` → `empty`). Without this the app would flash the login form on every first paint.

```python
def restore_browser_session() -> str:
    if current_email() or streamlit_js_eval is None:
        return "ready"
    stored = streamlit_js_eval(
        js_expressions=(
            "JSON.stringify({"
            f"{_BROWSER_SESSION_BRIDGE_READY_KEY}:true,"
            f"{_BROWSER_SESSION_BRIDGE_PAYLOAD_KEY}:localStorage.getItem({json.dumps(AUTH_STORAGE_KEY)})"
            "})"
        ),
        key="auth_restore_session",
    )
    restore_state, payload = _decode_browser_session_payload(stored)
    if restore_state == BROWSER_SESSION_PENDING:
        return "pending"
    if restore_state == BROWSER_SESSION_EMPTY:
        return "empty"
    try:
        if restore_state == BROWSER_SESSION_INVALID:
            raise ValueError("invalid browser session payload")
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_at = int(payload.get("expires_at") or 0)
        if not access_token or not refresh_token or not expires_at:
            clear_browser_session(); return "empty"
        if expires_at <= int(time.time()):
            clear_browser_session(); return "empty"
        st.session_state.auth_access_token = access_token
        st.session_state.auth_refresh_token = refresh_token
        st.session_state.auth_session_expires_at = expires_at

        refreshed_payload = None
        try:
            verified_user = fetch_auth_user(access_token)     # GET /auth/v1/user
        except Exception:
            refreshed = refresh_auth_session(refresh_token)    # fall back to refresh grant
            refreshed_payload = refreshed
            verified_user = refreshed.get("user") or {}
            st.session_state.auth_access_token = refreshed.get("access_token")
            st.session_state.auth_refresh_token = refreshed.get("refresh_token") or refresh_token

        user_email = (verified_user.get("email") or "").strip().lower()
        if not user_email:
            clear_browser_session(); return "empty"
        st.session_state.auth_user = verified_user
        st.session_state.auth_role = fetch_user_role(user_email)
        if refreshed_payload is not None:
            save_browser_session({...}, st.session_state.auth_role)
        ensure_fresh_session()
        return "restored"
    except Exception:
        clear_browser_session()
        return "empty"
```

Return-value vocabulary of `restore_browser_session()`: `"ready" | "pending" | "empty" | "restored"` (note: distinct from the `BROWSER_SESSION_*` classification vocabulary).

Payload classifier (`:40-74`) — handles double-encoded strings, the bridge envelope, and requires *both* tokens to be considered a session:
```python
def _decode_browser_session_payload(payload) -> tuple[str, dict | None]:
    if payload is None:
        return BROWSER_SESSION_PENDING, None
    decoded = payload
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, json.JSONDecodeError):
            return BROWSER_SESSION_INVALID, None
    if not isinstance(decoded, dict):
        return BROWSER_SESSION_INVALID, None
    if decoded.get(_BROWSER_SESSION_BRIDGE_READY_KEY) is True:
        stored_payload = decoded.get(_BROWSER_SESSION_BRIDGE_PAYLOAD_KEY)
        if stored_payload in (None, ""):
            return BROWSER_SESSION_EMPTY, None
        try:
            decoded = json.loads(stored_payload) if isinstance(stored_payload, str) else stored_payload
        except (TypeError, json.JSONDecodeError):
            return BROWSER_SESSION_INVALID, None
        if not isinstance(decoded, dict):
            return BROWSER_SESSION_INVALID, None
    if not decoded:
        return BROWSER_SESSION_EMPTY, None
    if decoded.get("access_token") and decoded.get("refresh_token"):
        return BROWSER_SESSION_HAS_SESSION, decoded
    return BROWSER_SESSION_INVALID, None

def classify_browser_session_payload(payload) -> str:   # test seam
    state, _ = _decode_browser_session_payload(payload)
    return state
```

Note: the `role` field is written to localStorage but **never read back** — restore always re-queries `fetch_user_role(user_email)` from Neon.

### 1.6 Token refresh logic

JWT `exp` is decoded client-side without signature verification (`:510-519`):
```python
def _jwt_exp(access_token: str | None) -> int | None:
    if not access_token or access_token.count(".") < 2:
        return None
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        return int(json.loads(decoded).get("exp"))
    except Exception:
        return None
```

`ensure_fresh_session()` (`:522-549`) runs on **every** `current_user()` call, i.e. effectively on every page render:
```python
def ensure_fresh_session() -> None:
    expires_at = int(st.session_state.get("auth_session_expires_at") or 0)
    if expires_at and expires_at <= int(time.time()):
        clear_browser_session()
        for key in ("auth_access_token", "auth_refresh_token", "auth_user", "auth_role", "auth_session_expires_at"):
            st.session_state.pop(key, None)
        return

    refresh_token = st.session_state.get("auth_refresh_token")
    if not refresh_token:
        return
    exp = _jwt_exp(st.session_state.get("auth_access_token"))
    if exp and exp - int(time.time()) > TOKEN_REFRESH_GRACE_SECONDS:
        return
    try:
        payload = refresh_auth_session(refresh_token)
        auth_user = payload.get("user") or st.session_state.get("auth_user") or {}
        user_email = (auth_user.get("email") or current_email()).strip().lower()
        role = fetch_user_role(user_email)
        st.session_state.auth_access_token = payload.get("access_token")
        st.session_state.auth_refresh_token = payload.get("refresh_token") or refresh_token
        st.session_state.auth_user = auth_user
        st.session_state.auth_role = role
        save_browser_session(payload, role)
    except Exception:
        clear_browser_session()
        for key in ("auth_access_token", "auth_refresh_token", "auth_user", "auth_role", "auth_session_expires_at"):
            st.session_state.pop(key, None)
```

Two independent expiry mechanisms:
1. **Hard 8-hour app session** — `auth_session_expires_at`, set once at login and *not* extended by refresh (`save_browser_session` reuses the existing value if non-zero, `:561`). After 8 h the user must re-login regardless of Supabase token validity.
2. **Rolling JWT refresh** — when the access-token `exp` is within 90 s (`TOKEN_REFRESH_GRACE_SECONDS`), silently exchange the refresh token. Refresh also re-reads the role from Neon.

Any refresh failure = silent full logout (session keys popped, localStorage cleared), no error shown.

### 1.7 `current_user()` — the user dict shape

`auth_utils.py:392-404`:
```python
def current_user() -> dict | None:
    ensure_fresh_session()
    auth_user = st.session_state.get("auth_user")
    auth_role = st.session_state.get("auth_role")
    if not auth_user or not auth_role:
        return None
    return {
        "email": auth_role.get("email") or auth_user.get("email") or "",
        "role": auth_role.get("role") or ROLE_VIEWER,
        "staff_code": auth_role.get("staff_code") or "",
        "staff_name": auth_role.get("staff_name") or "",
        "raw_user": auth_user,
    }
```

**Exactly 5 keys** in the canonical user object:

| Key | Type | Source | Default |
|---|---|---|---|
| `email` | str | `auth_role.email` → `auth_user.email` | `""` |
| `role` | str | `auth_role.role` | `ROLE_VIEWER` = `"ทั่วไป"` |
| `staff_code` | str | `auth_role.staff_code` | `""` |
| `staff_name` | str | `auth_role.staff_name` | `""` |
| `raw_user` | dict | whole Supabase Auth user JSON | — |

**Important:** `owner_alias` and `is_active` come back from Neon in `auth_role` but are **dropped** by `current_user()`. Nothing downstream reads `owner_alias` from the user object; `is_active` filtering happens in SQL instead (`fetch_user_role_from_neon` has `and is_active = true`).

`current_email()` (`:552-555`) is a lighter helper that does **not** call `ensure_fresh_session()`:
```python
def current_email() -> str:
    auth_role = st.session_state.get("auth_role") or {}
    auth_user = st.session_state.get("auth_user") or {}
    return auth_role.get("email") or auth_user.get("email") or ""
```

### 1.8 `fetch_user_role(email)` — Neon wiring

`auth_utils.py:359-381` — cached 600 s, lazy import of `neon_utils` to avoid a circular import, and swallows all exceptions into a viewer-role default:
```python
@st.cache_data(ttl=600, show_spinner=False)
def fetch_user_role(email: str) -> dict:
    normalized_email = email.strip().lower()
    default_role = {
        "email": normalized_email,
        "role": ROLE_VIEWER,
        "staff_code": "",
        "staff_name": "",
        "is_active": True,
    }
    try:
        from neon_utils import fetch_user_role_from_neon
        row = fetch_user_role_from_neon(normalized_email)
    except Exception:
        return default_role
    if not row:
        return default_role
    row["email"] = normalized_email
    row["role"] = row.get("role") or ROLE_VIEWER
    row["staff_code"] = row.get("staff_code") or ""
    row["staff_name"] = row.get("staff_name") or ""
    return row
```

Backing query — `neon_utils.py:2616-2635`:
```python
def fetch_user_role_from_neon(email: str) -> dict | None:
    normalized_email = clean(email).lower()
    if not normalized_email:
        return None
    ensure_crm_data_imports_schema()
    with neon_connection() as conn:
        with conn.cursor() as cur:
            owner_alias_expr = "owner_alias" if table_has_column(cur, "crm_user_roles", "owner_alias") else "null::text as owner_alias"
            cur.execute(
                f"""
                select email, role, staff_code, staff_name, {owner_alias_expr}, is_active
                from public.crm_user_roles
                where email = %s
                  and is_active = true
                limit 1
                """,
                [normalized_email],
            )
            return cur.fetchone()
```

Returns a dict (psycopg `dict_row` factory, `neon_utils.py:337`) with keys `email, role, staff_code, staff_name, owner_alias, is_active` — or `None`.

**Behavioral consequences to preserve or deliberately change:**
- A user who authenticates in Supabase but has **no active** `crm_user_roles` row is silently downgraded to `role="ทั่วไป"`, `staff_code=""` — they are *logged in*, not rejected.
- A **Neon outage** produces the same viewer-role default (no error surfaced) because the `except Exception` catches connection failures too.
- Role changes take up to **600 s** to take effect for a logged-in user (mitigated by the global `st.cache_data.clear()` in `pages/users.py:90,170,177`). Documented as a HIGH risk in `CRM_WORKFLOW_LOGIC_AUDIT.md:405-410`.
- `ensure_crm_data_imports_schema()` runs DDL on the very first role lookup — see §4.

### 1.9 `logout()`

`auth_utils.py:384-389`:
```python
def logout() -> None:
    st.session_state.auth_skip_restore = True
    st.session_state.auth_clear_browser_session = True
    for key in ("auth_access_token", "auth_refresh_token", "auth_user", "auth_role", "auth_session_expires_at"):
        st.session_state.pop(key, None)
    st.rerun()
```
There is **no** call to Supabase `/auth/v1/logout` — the refresh token is never revoked server-side, only discarded locally. `auth_skip_restore` prevents the localStorage bridge from immediately re-restoring; `auth_clear_browser_session` is consumed on the next run by `require_login()` (`:461-462`).

### 1.10 Permission re-exports

`auth_utils.py:651-668` — thin pass-throughs so pages can import from either module (both import styles are in use across the codebase):
```python
def can_manage_all(user):        return permission_can_manage_all(user)
def can_view_system_page(user):  return permission_can_view_system_page(user)
def can_manage_system_page(user):return permission_can_manage_system_page(user)
def _clean(value):               return permission_clean(value)
def can_edit_customer_lead(user, customer): return permission_can_edit_customer_lead(user, customer)
```
`auth_utils.py:8-22` also re-exports the role constants `ROLE_EDITOR, ROLE_STAFF, ROLE_STAFF_ALIASES, ROLE_STAFF_READONLY, ROLE_TELESELL, ROLE_TELESELL_ALIASES, ROLE_VIEWER, SYSTEM_VIEW_ROLES` — `pages/4_เพิ่มข้อมูลลูกค้า.py:17` imports `ROLE_TELESELL_ALIASES` from `auth_utils`.

---

## 2. `permissions.py` — Authorization (115 lines, complete)

### 2.1 Role literals and sets — `permissions.py:1-12`

```python
ROLE_ADMIN = "ADMIN"
ROLE_EDITOR = "EDITOR"
ROLE_STAFF = "พนักงาน"          # Thai: "staff/employee"
ROLE_VIEWER = "ทั่วไป"           # Thai: "general"
ROLE_TELESELL = ROLE_STAFF       # alias → "พนักงาน"
ROLE_STAFF_READONLY = ROLE_VIEWER # alias → "ทั่วไป"

ROLE_TELESELL_ALIASES = {ROLE_TELESELL, "TELESELL"}     # {"พนักงาน", "TELESELL"}
ROLE_STAFF_ALIASES = {ROLE_STAFF_READONLY, "STAFF"}     # {"ทั่วไป", "STAFF"}
ROLE_USER_ALIASES = {"USER"}
SYSTEM_VIEW_ROLES = {ROLE_EDITOR}                       # {"EDITOR"}
ORDER_DELETE_ROLES = {ROLE_EDITOR, ROLE_STAFF, "STAFF"} # {"EDITOR", "พนักงาน", "STAFF"}
```

Naming trap for the rewrite: **`ROLE_STAFF` is the Thai `"พนักงาน"` and it is the *telesell* (write-capable) role. `ROLE_STAFF_READONLY` / the English `"STAFF"` literal is the *viewer* `"ทั่วไป"` bucket.** `ROLE_TELESELL is ROLE_STAFF` — they are the same string, so `is_telesell()` and "is พนักงาน" are the same predicate.

There are effectively **6 distinct role strings** that can be stored, per `pages/users.py:19`:
```python
ROLE_OPTIONS = ["EDITOR", "ADMIN", "พนักงาน", "TELESELL", "STAFF", "USER", "ทั่วไป"]
```
(7 options; the default for new users is `"พนักงาน"` — `pages/users.py:66`.) The Neon column default is `'ทั่วไป'` (`neon_utils.py:232`).

### 2.2 Normalization — `permissions.py:15-32`

```python
def clean(value) -> str:
    return str(value or "").strip()

def normalize_role(role) -> str:
    value = clean(role)
    upper_value = value.upper()
    if upper_value in {"ADMIN", "EDITOR", "TELESELL", "STAFF", "USER"}:
        return upper_value
    return value

def user_role(user: dict | None) -> str:
    return normalize_role((user or {}).get("role"))

def _normalized_roles(roles: set[str]) -> set[str]:
    return {normalize_role(role) for role in roles}
```

`normalize_role` upper-cases only the five ASCII role names; Thai strings pass through verbatim (case-folding is a no-op for Thai anyway). So `"editor"`, `"Editor"`, `" EDITOR "` all → `"EDITOR"`. `"พนักงาน"` → `"พนักงาน"`. An unknown value like `"MANAGER"` → `"MANAGER"` (falls through all checks → no permissions).

After normalization the alias sets become:
- `_normalized_roles(ROLE_TELESELL_ALIASES)` = `{"พนักงาน", "TELESELL"}`
- `_normalized_roles(ROLE_STAFF_ALIASES)` = `{"ทั่วไป", "STAFF"}`
- `_normalized_roles(ROLE_USER_ALIASES)` = `{"USER"}`
- `_normalized_roles(SYSTEM_VIEW_ROLES)` = `{"EDITOR"}`
- `_normalized_roles(ORDER_DELETE_ROLES)` = `{"EDITOR", "พนักงาน", "STAFF"}`

### 2.3 Predicates — `permissions.py:35-114`

```python
def is_telesell(user):      return user_role(user) in {"พนักงาน", "TELESELL"}
def is_staff_limited(user): return user_role(user) in {"พนักงาน","TELESELL","ทั่วไป","STAFF","USER"}
def can_manage_all(user):   return user_role(user) in {"ADMIN", "EDITOR"}
```
Exact truth table (rows = stored role after `normalize_role`):

| Helper | line | Logic | ADMIN | EDITOR | พนักงาน / TELESELL | ทั่วไป / STAFF | USER | unknown / None |
|---|---|---|---|---|---|---|---|---|
| `is_telesell` | 35 | `∈ {พนักงาน, TELESELL}` | ✗ | ✗ | **✓** | ✗ | ✗ | ✗ |
| `is_staff_limited` | 39 | `∈ telesell ∪ staff ∪ user` | ✗ | ✗ | **✓** | **✓** | **✓** | ✗ |
| `can_manage_all` | 45 | `∈ {ADMIN, EDITOR}` | **✓** | **✓** | ✗ | ✗ | ✗ | ✗ |
| `can_edit_users` | 49 | `= can_manage_all` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_edit_products` | 53 | `= can_manage_all` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_import_excel` | 57 | `= can_manage_all` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_add_manual_order` | 61 | `can_manage_all or is_telesell` | ✓ | ✓ | **✓** | ✗ | ✗ | ✗ |
| `can_export_customers` | 65 | `== EDITOR` | **✗** | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_assign_customer_owner` | 69 | `== EDITOR` | **✗** | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_delete_order` | 73 | `∈ {EDITOR, พนักงาน, STAFF}` | **✗** | ✓ | **✓** | **✓ (STAFF only)** | ✗ | ✗ |
| `can_view_system_page` | 77 | `∈ {EDITOR}` | **✗** | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_manage_system_page` | 81 | `= can_manage_all` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `can_view_followup` | 85 | `== EDITOR or is_staff_limited` | **✗** | ✓ | ✓ | ✓ | ✓ | ✗ |
| `can_view_followup_owner_filter` | 89 | `== EDITOR` | **✗** | ✓ | ✗ | ✗ | ✗ | ✗ |

**`ADMIN` is a second-class role**: it can manage users/products/import/system-manage but **cannot** export customers, assign owners, delete orders, view system pages, or view follow-up. Note the asymmetry at `can_view_system_page` (EDITOR only) vs `can_manage_system_page` (ADMIN+EDITOR) — an ADMIN can "manage" a page it cannot open. Also `can_delete_order` admits the English `"STAFF"` (= viewer bucket) but not `"ทั่วไป"`, even though those are aliases of each other elsewhere.

Row-level lead edit — `permissions.py:93-114`:
```python
def can_edit_customer_lead(user: dict | None, customer) -> bool:
    if not user:
        return False
    if user_role(user) == ROLE_EDITOR:
        return True
    if not is_telesell(user):
        return False

    staff_code = clean(user.get("staff_code"))
    if not staff_code:
        return False

    customer_staff_code = ""
    for key in ("staff_code",):
        try:
            value = customer.get(key)
        except AttributeError:
            value = ""
        if clean(value):
            customer_staff_code = clean(value)

    return bool(customer_staff_code and customer_staff_code == staff_code)
```
Notes: the `for key in ("staff_code",)` loop is a vestigial single-element loop (a fallback-key list that was reduced to one). `AttributeError` is swallowed so a non-dict `customer` yields `""` → denied. `ADMIN` is **not** allowed to edit leads (only EDITOR bypasses).

### 2.4 Every `can_*` / role-predicate call site (production code)

Page/module gates — file:line → what it protects:

| file:line | Call | Protects |
|---|---|---|
| `pages/users.py:30` | `can_manage = can_edit_users(user)` | Whole User/Role page becomes read-only; drives `render_create_user`, `render_mapping_tester`, `render_user_table` edit affordances (`pages/users.py:43-46`, non-manage message at `:34`) |
| `pages/products.py:137` | `is_editor = can_edit_products(auth_user)` | Product Master create/edit/disable/archive controls (read-only otherwise) |
| `pages/import_excel.py:20` | `is_editor = can_import_excel(user)` | Excel upload / preview / import-history tabs (`pages/import_excel.py:41-47`); non-editors get the info notice at `:39` |
| `pages/import_excel.py:21` | `if not can_add_manual_order(user)` | **Hard `st.stop()`** on the entire Import/Manual Order page — `"หน้านี้ใช้ได้เฉพาะ EDITOR และพนักงานที่มีสิทธิเพิ่มคำสั่งซื้อ"` |
| `pages/customers.py:119` | `if not can_export_customers(user)` | `render_export_panel` early-returns → XLSX export UI hidden |
| `ui/customer_export_ui.py:52` | `if not can_export_customers(user)` | `render_customer_export_panel` early-returns (shared export panel used from Customers and Import pages) |
| `pages/customers.py:240` | `can_assign_owner = can_assign_customer_owner(user)` | Owner dropdown + `fetch_owner_user_options` lookup in the customer table |
| `pages/customers.py:425` | `if can_manage_all(user)` inside `can_edit_customer_follow_action` | Per-row follow-marker edit; otherwise `staff_code == staff_code` |
| `pages/customer_detail.py:97` | `if can_manage_all(user)` inside `can_view_customer_detail` | Customer 360 record access; enforced at `pages/customer_detail.py:66` |
| `pages/followup.py:82` | `if not can_view_followup(user)` | **Hard `st.stop()`** on Follow-up page — `"หน้านี้ใช้ได้เฉพาะ EDITOR และพนักงานที่ดูแลลูกค้า"` |
| `pages/followup.py:322` | `if can_view_followup_owner_filter(user)` | Owner selectbox in Follow-up filters; else forces `st.session_state['followup_filter_owner'] = ALL` (`:325`) |
| `pages/followup.py:692` | `if not can_manage_all(user)` | Owner-conflict check before saving an order from the follow-up popup (non-managers blocked from writing to another owner's phone) |
| `pages/dashboard.py:253` | `if not can_delete_order(user)` | `render_sales_delete_controls` early-return → sales-row delete UI hidden |
| `crm_data/dashboard.py:371` | `if not can_delete_order(user)` | Repository-layer re-check in `_delete_sales_report_records`; raises `PermissionError("User cannot delete sales report records")` |
| `pages/team_sales.py:269` | `if user_role(user) != ROLE_EDITOR` | **Hard `st.stop()`** on Team Sales — `"คุณไม่มีสิทธิ์เข้าดูหน้ายอดขายทีม"` (uses raw `user_role`, not a `can_*` helper) |
| `pages/system_status.py:14` | `if not can_view_system_page(auth_user)` | **Hard `st.stop()`** — `"หน้านี้เป็นระบบหลังบ้าน เฉพาะ EDITOR เท่านั้น"` |
| `pages/settings.py:14` | `if not can_view_system_page(auth_user)` | **Hard `st.stop()`** — same message |
| `pages/3_sync_status.py:10` | `if not can_view_system_page(auth_user)` | **Hard `st.stop()`** (legacy page) |
| `crm_dashboard.py:9` | `if not can_view_system_page(auth_user)` | **Hard `st.stop()`** — `"หน้านี้เป็น dashboard หลังบ้าน เฉพาะ CEO/EDITOR เท่านั้น"` (legacy Streamlit-Cloud entry file) |
| `pages/6_สินค้า.py:155` | `if not can_manage_all(auth_user)` | Legacy Products edit gate |
| `pages/7_พนักงาน.py:130` | `if not can_manage_all(auth_user)` | Legacy Staff-options edit gate |
| `pages/4_เพิ่มข้อมูลลูกค้า.py:89-91` | `is_editor = can_manage_all(user)`; `is_telesell = neon.clean(user.get("role")) in ROLE_TELESELL_ALIASES`; `if not is_editor and not is_telesell` | Legacy manual-order page. **Inlined, un-normalized role check** — bypasses `normalize_role`, so `"telesell"` lowercase fails here but passes `permissions.is_telesell` |
| `customer360.py:787` | `if not can_manage_all(auth_user)` | Legacy owner-assignment panel (body is dead — see §8) |
| `customer360.py:836` | `can_edit = can_edit_customer_lead(auth_user, customer)` | Legacy lead/follow-up panel editability |
| `archive/disabled_pages/4_upload_excel.py:128,131` | `can_view_system_page`, `can_manage_system_page` | Archived Supabase upload page (dead) |

Import styles present (both must be supported by any shim): `from permissions import ...` (`pages/customers.py:25`, `pages/customer_detail.py:22`, `pages/followup.py:21`, `pages/dashboard.py:18`, `pages/products.py:18`, `pages/import_excel.py:7`, `pages/users.py:16`, `pages/team_sales.py:17`, `ui/customer_export_ui.py:10`, `crm_data/dashboard.py:369` lazy) and `from auth_utils import ...` (`crm_dashboard.py:3`, `customer360.py:17`, `pages/3_sync_status.py:3`, `pages/settings.py:3`, `pages/system_status.py:3`, `pages/4_เพิ่มข้อมูลลูกค้า.py:17`, `pages/6_สินค้า.py:16`, `pages/7_พนักงาน.py:15`).

---

## 3. Role / staff model and row-level scoping

### 3.1 Table: `public.crm_user_roles`

DDL is embedded in the runtime schema string `CRM_DATA_IMPORTS_DDL` — `neon_utils.py:230-241`:
```sql
create table if not exists public.crm_user_roles (
  email text primary key,
  role text not null default 'ทั่วไป',
  staff_code text,
  staff_name text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_crm_user_roles_active_email
  on public.crm_user_roles (is_active, email);
```
Additive patches in the same DDL (`neon_utils.py:258-271`):
```sql
alter table public.crm_user_roles  add column if not exists staff_code text;
alter table public.crm_staff_options add column if not exists staff_code text;
alter table public.crm_data_imports add column if not exists staff_code text;

create index if not exists idx_crm_data_imports_staff_code on public.crm_data_imports (staff_code);
create index if not exists idx_crm_user_roles_staff_code   on public.crm_user_roles (staff_code);
```
`owner_alias` is added by migration `neon/migrations/202606020002_add_owner_alias_to_crm_user_roles.sql` and is treated as **optional at runtime** — every read/write does a live `information_schema` probe (`neon_utils.py:2637-2650`) and substitutes `null::text as owner_alias` when absent.

Related tables: `public.crm_staff_options` (`neon_utils.py:243-256`: `id bigserial pk, staff_code text, staff_name text not null unique, is_active, sort_order, created_by, updated_by, created_at, updated_at`) and the newer `crm_user_team_assignments` (`neon/migrations/202607010001_create_crm_user_team_assignments.sql`, whose header explicitly notes "It does not change crm_user_roles"). Legacy Supabase version of the table exists at `supabase/migrations/202605270003_create_crm_user_roles.sql` with RLS + `service_role_full_access_crm_user_roles` policy — historical only.

### 3.2 email → staff_code mapping chain

1. Supabase Auth authenticates and returns `user.email`.
2. `fetch_user_role(email)` → `fetch_user_role_from_neon(email)` → `select ... from crm_user_roles where email = lower(email) and is_active = true`.
3. `current_user()` exposes `staff_code` (and display-only `staff_name`).
4. Every scoped query compares `crm_data_imports.staff_code = <user staff_code>`.

`staff_code` is the **only** permission key. `owner` / `staff_name` / `owner_alias` are display/matching aids. This is enforced by convention plus an explicit warning on the one legacy derivation helper (`neon_utils.py:353-363`):
```python
def owner_to_staff_code(value) -> str:
    # Legacy/display-only helper. Never use this to write canonical staff_code.
    # Canonical staff_code must come from crm_user_roles/crm_staff_options.
    text = clean(value)
    if not text:
        return ""
    if "(" in text and ")" in text:
        inner = text.rsplit("(", 1)[-1].split(")", 1)[0].strip()
        if inner:
            return inner
    return text
```

### 3.3 The row-level scope predicate

`neon_utils.py:2334-2345` — the single source of row-level truth:
```python
def _followup_staff_scope(user: dict, alias: str = "d") -> tuple[str, list]:
    if clean(user.get("role")) in {"ADMIN", "EDITOR"}:
        return "", []

    staff_code = clean(user.get("staff_code"))
    if not staff_code:
        return "1 = 0", []
    return f"nullif(trim(coalesce({alias}.staff_code, '')), '') = %s", [staff_code]
```
Semantics: **ADMIN/EDITOR → unrestricted. Any other role with a staff_code → exact staff_code match. Any other role without a staff_code → `1 = 0`, i.e. sees nothing (fail-closed).** Rows with `NULL`/blank `staff_code` (the 730 unassigned rows, see §3.5) are invisible to every non-manager.

⚠️ This uses **raw `clean(role)`**, not `normalize_role(role)`. A stored role of `"editor"` (lowercase) would pass `can_manage_all()` (page opens, full-scope UI shown) but fail `_followup_staff_scope` (`1 = 0` if no staff_code, or self-scoped). Same raw-comparison pattern in `crm_data/dashboard.py:100-103`:
```python
def _can_view_all_sales(user: dict | None) -> bool:
    from neon_utils import clean
    return clean((user or {}).get("role")) in {"ADMIN", "EDITOR"}
```

Call sites of `_followup_staff_scope`:

| file:line | Consumer | Effect |
|---|---|---|
| `neon_utils.py:1739` | `build_customer_where(filters, user, enforce_user_scope=True)` | Customers list/search; scope is **opt-out-able** via `enforce_user_scope=False` |
| `neon_utils.py:2347` | `build_followup_where(filters, user)` | Follow-up list |
| `neon_utils.py:2413` | `fetch_followup_filter_options(user)` | Follow-up dropdown option values are themselves scoped |
| `neon_utils.py:2763` | `test_user_role_visibility(email, limit)` | Users-page "test mapping" tool: reports `total` + up to `limit` sample rows a given email would see |

`build_customer_where` (`neon_utils.py:1731-1757`), note the `enforce_user_scope` escape hatch:
```python
def build_customer_where(filters, user=None, enforce_user_scope: bool = True) -> tuple[str, list]:
    clauses = ["d.import_status = 'valid'"]
    params: list = []
    if enforce_user_scope:
        scope_clause, scope_params = _followup_staff_scope(user or {}, "d")
        if scope_clause:
            clauses.append(scope_clause)
            params.extend(scope_params)
    ...
```

Dashboard sales scoping is a separate, hand-written implementation (`crm_data/dashboard.py:129-152`) that does **not** call `_followup_staff_scope`:
```python
def _sales_report_where(user, owner_filter) -> tuple[list[str], list]:
    clauses = [
        "d.import_status = 'valid'",
        "d.created_at >= %s",
        "d.created_at < %s",
        "d.amount is not null",
        "coalesce(nullif(d.sale_type, ''), 'NEW_ORDER') in ('NEW_ORDER', 'UPSELL')",
    ]
    params: list = []
    if _can_view_all_sales(user):
        owner = clean(owner_filter)
        if owner and owner != "ทั้งหมด":
            clauses.append("d.owner = %s"); params.append(owner)
    else:
        staff_code = clean((user or {}).get("staff_code"))
        if staff_code:
            clauses.append("d.staff_code = %s"); params.append(staff_code)
        else:
            clauses.append("1 = 0")
    return clauses, params
```
Note: the manager-facing **owner filter uses `d.owner` (Thai display name)** while staff scoping uses `d.staff_code`. `FOLLOW` sale_type is excluded from revenue by the `in ('NEW_ORDER','UPSELL')` clause.

Two more hand-rolled row guards duplicating the same rule:
- `pages/customer_detail.py:96-101`:
```python
def can_view_customer_detail(user: dict, customer: dict) -> bool:
    if can_manage_all(user):
        return True
    user_staff_code = clean(user.get("staff_code"))
    customer_staff_code = clean(customer.get("staff_code"))
    return bool(user_staff_code and customer_staff_code and user_staff_code == customer_staff_code)
```
- `pages/customers.py:424-429` `can_edit_customer_follow_action(row, user)` — identical shape.

So the same "manager-or-own-staff_code" rule is implemented **four times**: `permissions.can_edit_customer_lead`, `_followup_staff_scope`, `can_view_customer_detail`, `can_edit_customer_follow_action` — with `can_manage_all` (normalized) in two and raw `clean(role)` in one.

### 3.4 Role write path

`upsert_user_role(payload)` — `neon_utils.py:2707-2731`. Dynamic column list based on the `owner_alias` probe, `on conflict (email) do update`:
```python
has_owner_alias = table_has_column(cur, "crm_user_roles", "owner_alias")
columns = ["email", "role", "staff_code", "staff_name", "is_active", "updated_at"]
if has_owner_alias:
    columns.insert(4, "owner_alias")
values = [payload.get(column) for column in columns]
update_fields = [column for column in columns if column != "email"]
cur.execute(f"""
    insert into public.crm_user_roles ({', '.join(columns)})
    values ({', '.join(['%s'] * len(columns))})
    on conflict (email) do update
    set {', '.join([f'{field} = excluded.{field}' for field in update_fields])}
""", values)
```
`set_user_role_active(email, is_active, updated_at)` — `neon_utils.py:2733-2754`, plain `update ... where email = %s`. **There is no hard delete of users.** Both wrap in try/commit/rollback/raise.

`pages/users.py` payload shape (`:78-88`, `:156-166`): `email` (lower-cased), `role`, `staff_code`, `staff_name`, `owner_alias` (`""` if the `"ไม่เลือก owner mapping"` placeholder was selected), `is_active`, `updated_at`. Owner-alias choices come from `fetch_crm_owner_options()` = `select distinct owner from crm_data_imports where import_status='valid'` (`neon_utils.py:2687-2705`).

Owner dropdowns elsewhere use `fetch_owner_user_options(active_only)` (`neon_utils.py:2824-2880`), a `union all` of `crm_user_roles` + `crm_staff_options` grouped by `(staff_code, staff_name)` with a synthetic `md5(staff_code||'|'||staff_name)` id — so assigning an owner in the UI carries a `staff_code` alongside the display name (`pages/customers.py:240-248`, `owner_staff_choices`, then `assign_owner_to_order_record(..., staff_code=selected_staff_code)` at `pages/customers.py:390-396`).

The Users page also documents the matching policy in-app (`pages/users.py:49-58`):
> ใช้ **staff_code** เป็นตัวจับคู่หลัก และ fallback เทียบ **owner** กับ **staff_name / owner_alias** แบบ trim และลดช่องว่างซ้ำก่อนเทียบ exact match

Normalization SQL helper for that fallback — `neon_utils.py:2330-2331`:
```python
def _normalized_text_sql(column: str) -> str:
    return f"regexp_replace(trim(coalesce({column}, '')), '\\s+', ' ', 'g')"
```

### 3.5 Decisions recorded in the three staff-mapping docs

#### `docs/OWNER_STAFF_MAPPING_APPROVAL.md` — status `PENDING REVIEW`

Hard prohibitions (`:7-12`): no real-data update until a mapping row is `APPROVED`; **never auto-assign rows with blank owner**; **never use Thai names as a permanent `staff_code`**; never touch production without a backup first.

Proposed `owner_alias` → canonical `staff_code` mapping with record counts (`:16-25`):

| owner_alias | records | current staff_code variants | proposed staff_code | proposed email | status |
|---|---:|---|---|---|---|
| สายฝน ราวิชัย (สายฝน) | 6,502 | `สายฝน`, full name | `SAIFON` | `swiftpassion.com18@gmail.com` | PENDING |
| พรณกมล ดวงจันทร์ (แต้ว) | 4,669 | `แต้ว`, full name | `TAEW` | `swiftpassion.com17@gmail.com` | PENDING |
| พรธนนันท์ กานต์รพีพร (หญิง) | 3,100 | `หญิง`, full name | `YING` | `swiftpassion.com21@gmail.com` | PENDING |
| กัญญพักฒ์ อิ่มยวง (เจี๊ยบ) | 3,087 | `เจี๊ยบ` | `JEEB` | **NEED_CONFIRM** | NEED_CONFIRM |
| ธัญญรัตน์ หอมระรื่น (เล็ก) | 9 | full name | `LEK` | `swiftpassion.com03@gmail.com` | PENDING |
| จินดามณี คงมี (ครีม) | 1 | full name | `CREAM` | `swiftpassion.com16@gmail.com` | PENDING |
| สุมนตรา ทัศน์ศรี (โก้) | 1 | full name | `KO` | `swiftpassion.com14@gmail.com` | PENDING |
| (owner blank) | 730 | blank | `NULL` | — | NEED_CONFIRM |

Standing rules (`:38-49`): (1) `staff_code` is **UPPERCASE ASCII only**; (2) it is the **permanent key** for permission, reporting, assignment and "Schema V2"; (3) `owner` / `staff_name` are Thai display-only; (4) `owner_alias` stores the normalized Thai name for matching legacy imports; (5) blank owner stays `NULL`, never auto-assigned; (6) no real update until `APPROVED`; (7) updates must use **normalized exact match** = trim ends, collapse runs of whitespace to one space, then full-string compare. The SQL realization is `regexp_replace(btrim(owner), '\s+', ' ', 'g') = '<alias>'` (`:77`, `:92`).

Rollback plan (`:100-117`): back up `id, owner, staff_code, customer_name, phone1, phone2, order_id, updated_at` from `crm_data_imports` into `crm_owner_staff_backup_yyyymmdd`; update only normalized-exact matches; skip blank owner; compare per-owner and per-staff_code counts before/after; restore by `id` on error; **re-test STAFF permissions on Customers and Follow-up after rollback**. All SQL in the doc is labelled `DRAFT ONLY - DO NOT EXECUTE WITHOUT APPROVAL`. Approval checklist (`:119-132`): 10 rows, all `PENDING` or `NEED_CONFIRM` — **nothing approved**.

#### `docs/STAFF_MAPPING_DECISION_REQUIRED.md` — status `PENDING EXECUTIVE DECISION`

The final decision gate. Output of "Stabilization Sprint Step 12": **`Ready For Real Update: NO`** (`:9`), blocked by three issues (`:10-13`):
1. `JEEB` has 3,087 records but no clear email / user role.
2. 730 rows have blank `owner` / `staff_code`.
3. `หนูนา` and `อุ๊` have user roles but zero matching owner records.

Decisions and recommendations (`:17-23`, `:43-92`):
- **JEEB (3,087 rows)** — executive must name the email. If a user exists, set `staff_code='JEEB'`, `staff_name='กัญญพักฒ์ อิ่มยวง (เจี๊ยบ)'`; if not, create the Supabase Auth user first, then add the `crm_user_roles` mapping. **Do not normalize these 3,087 rows until a login owner is confirmed.**
- **730 blank-owner rows** — recommended: keep `staff_code = NULL` and display "ยังไม่มอบหมาย" (not yet assigned). Explicitly forbidden: auto-assign; guess from product name / phone / date. EDITOR assigns later from the web UI. Options considered and rejected: an `UNASSIGNED` bucket, or assigning to one person.
- **หนูนา (`swiftpassion.com19@gmail.com`, พรนภา นันที) and อุ๊ (`swiftpassion.com22@gmail.com`, ศิวพร ถีติปริวัตร)** — 0 matching records each. Keep `active` if still employed, map to nothing, and **never overwrite someone else's owner just to make data appear**.
- **Canonical code style (17,369 rows affected)** — options were English code / full Thai name / Thai nickname; chosen: **permanent uppercase English code**.

Pre-approval checklist (`:106-116`) — 7 items, all `PENDING`/`NEED_CONFIRM`. Final gate (`:140-147`): not ready until JEEB email confirmed, 730 blank rows confirmed as NULL, full canonical set approved, and backup + rollback plans ready. All SQL is `DO NOT RUN` pseudo-SQL.

The corresponding executable-but-unrun script is `neon/manual_sql/202606_staff_code_normalization_plan.sql` — it backs up to `crm_user_roles_staff_backup_202606` (`:54-65`), then contains 8 `update public.crm_user_roles` blocks (`:182-245`) plus a restore-by-`id` block (`:356-364`).

#### `docs/UAT_STAFF_CODE_PERMISSION.md` — UAT script for the staff_code permission model

Scope (`:5-9`): CRM data read/write from Neon; **Supabase = Auth/Login only**; no migration or schema change tested; latest deployed environment. Test fixtures required (`:11-16`): an `EDITOR` account, a staff account with `staff_code = SAIFON`, a customer with `crm_data_imports.staff_code = SAIFON`, a customer owned by someone else, and >1 SKU for the multi-SKU case.

Four test blocks, each with Checklist / Expected Result / Fail Action:

1. **EDITOR: Customers Owner Assignment** — EDITOR sees all customers; owner dropdown shows names; save must not raise `TypeError`; **`owner` and `staff_code` must change together**; Export and Update-URL are EDITOR-only. Fail actions: check for custom objects passed into `st.selectbox` in `pages/customers.py`; if `owner` changed but `staff_code` did not, **stop UAT** and inspect callers of `assign_owner_to_order_record`.
2. **STAFF: SAIFON Visibility** — Dashboard, Follow-up must show only `staff_code = SAIFON`; own Customer Detail opens; another owner's Customer Detail **opened directly by URL must be denied**; and **"no fallback from `owner` or `staff_name` to the permission key"**. Fail actions point at `_followup_staff_scope`, the dashboard report scope, the `customer_detail` guard, and `crm_user_roles.staff_code` / `crm_data_imports.staff_code`.
3. **Manual Order: STAFF Owner Lock and Multi SKU** — STAFF cannot pick another owner; system locks owner/staff_code to the logged-in user; identical SKU **and** identical product name merges qty; on success the form resets; the created Neon row must carry the STAFF's `owner` **and** `staff_code = SAIFON`; **no deriving `staff_code` from the owner name**. Fail actions: `ui/manual_order_ui.py`; if `staff_code` is blank or Thai, **stop UAT**; if multi-SKU breaks, touch only UI/form state — **never the Import Excel pipeline**.
4. **Follow-up: STAFF Editing Scope** — STAFF sees and saves only own records; cannot see/edit others; EDITOR retains full access; **follow-up visibility uses `staff_code` only**. Fail actions: `fetch_followup_page` query, modal save payload/guard, `can_manage_all`.

Final sign-off (`:123-138`): 4 test blocks + "no Supabase `/rest/v1/*` in main runtime" + "`git status` shows no unintended source change"; result is one of PASS / PASS WITH NOTES / FAIL; on FAIL, **stop deploying** and record screenshot + user email + staff_code + customer/order id.

---

## 4. Caching layer

### 4.1 `crm_data/cache.py` — the entire file (7 lines)

```python
def clear_cached_data_functions(*functions) -> None:
    """Clear only the Streamlit cached functions passed in."""
    for function in functions:
        clear = getattr(function, "clear", None)
        if callable(clear):
            clear()
```
That is the whole abstraction: a variadic, **duck-typed and silently-tolerant** targeted invalidator. `None` arguments and undecorated functions are no-ops. Re-exported through `neon_utils.py:9` (`from crm_data.cache import clear_cached_data_functions`) so pages can call `neon.clear_cached_data_functions(...)`.

`pages/followup.py:584-592` adds a defensive wrapper that reimplements it if the attribute is missing from `neon_utils`:
```python
def clear_cached_functions_safely(*functions) -> None:
    clear_many = getattr(neon, "clear_cached_data_functions", None)
    if callable(clear_many):
        clear_many(*functions)
        return
    for function in functions:
        clear = getattr(function, "clear", None)
        if callable(clear):
            clear()
```

### 4.2 `crm_data/common.py` — the entire file (82 lines)

Pure, Streamlit-free value helpers. Re-exported through `neon_utils` (the characterization tests import them from `neon_utils`).

```python
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
import pandas as pd

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

def clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.upper() in {"NULL", "NONE", "NAN", "NAT"} else text

def normalize_phone(value) -> str:
    return "".join(ch for ch in clean(value) if ch.isdigit())

def make_dedupe_key(order_id: str, phone1: str, phone2: str, tracking_no: str) -> str:
    text = "|".join([clean(order_id), normalize_phone(phone1), normalize_phone(phone2), clean(tracking_no)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

PHONE_RULE_MESSAGE = "ต้องเป็นตัวเลข 10 หลัก ขึ้นต้นด้วย 0 และห้ามมีสัญลักษณ์"

def validate_phone_value(value, label: str) -> str:
    text = clean(value)
    if not text:
        return ""
    if not text.isdigit() or len(text) != 10 or not text.startswith("0"):
        return f"{label}ใส่ไม่ถูกต้อง {PHONE_RULE_MESSAGE}"
    return ""

def validate_phone_pair(phone1, phone2, require_one: bool = True) -> list[str]:
    first = clean(phone1)
    second = clean(phone2)
    if require_one and not first and not second:
        return ["กรุณากรอกเบอร์โทรหรือเบอร์สำรอง"]
    errors = []
    for value, label in ((first, "เบอร์โทร"), (second, "เบอร์สำรอง")):
        error = validate_phone_value(value, label)
        if error:
            errors.append(error)
    return errors

def to_number(value):
    text = clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None

def parse_date(value) -> str | None:
    text = clean(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()

def new_batch_id() -> str:
    return str(uuid4())

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

Contract details that the characterization test pins (see §7): `clean(pd.NA) == "<NA>"` (a *documented quirk*, not `""`); `to_number(False) is None` but `clean(False) == "False"`; `parse_date` is `dayfirst=True` so `"01/02/2026"` → `2026-02-01`; `normalize_phone("+66 81 234 5678") == "66812345678"` (does **not** convert `+66` → `0`), so a `+66`-formatted phone produces a **different** dedupe hash than the `0`-prefixed one; `make_dedupe_key` is case-sensitive and phone-order-significant.

`crm_data/__init__.py` is a single docstring: `"""Shared CRM data helpers."""`.

### 4.3 Every `@st.cache_data` / `@st.cache_resource` decorator

| file:line | Function | TTL | `show_spinner` |
|---|---|---|---|
| `auth_utils.py:359` | `fetch_user_role(email)` | **600 s** | `False` |
| `neon_utils.py:344` | `ensure_crm_data_imports_schema()` | `@st.cache_resource` — **no TTL, process-lifetime** | `False` |
| `neon_utils.py:366` | `neon_table_exists(table_name)` | 300 s | `False` |
| `neon_utils.py:386` | `neon_column_exists(table_name, column_name)` | 300 s | `False` |
| `neon_utils.py:2071` | `fetch_filter_options()` | 900 s | default (`True`) |
| `neon_utils.py:2204` | `fetch_import_history(limit=50)` | 300 s | `False` |
| `neon_utils.py:2408` | `fetch_followup_filter_options(user)` | 900 s | `False` |
| `neon_utils.py:2687` | `fetch_crm_owner_options(limit=1000)` | 900 s | default |
| `neon_utils.py:2824` | `fetch_owner_user_options(active_only=False)` | 900 s | `False` |
| `crm_data/products.py:204` | `fetch_product_options()` | 900 s | `False` |
| `crm_data/products.py:228` | `fetch_product_page(status_filter="active", …, sort_mode="sku_asc")` | 300 s | `False` |
| `crm_data/dashboard.py:106` | `fetch_sales_report_owner_options(user=None)` | 300 s | default |
| `customer360.py:440` *(legacy)* | `load_crm_customers(filters, page_size, page)` | 600 s (`CUSTOMER_CACHE_SECONDS`, `customer360.py:34`) | `"กำลังโหลดลูกค้า CRM..."` |
| `customer360.py:458` *(legacy)* | `load_customer_by_detail_key(detail_key)` | 600 s | `False` |
| `customer360.py:471` *(legacy)* | `load_customer_filter_options()` | 600 s | `False` |
| `customer360.py:479` *(legacy)* | `load_staff_options()` | 600 s | `False` |
| `customer360.py:493` *(legacy)* | `search_order_customer_terms(keyword)` | 600 s | `False` |
| `customer360.py:519` *(legacy)* | `search_orders_by_phones(phones, year)` | **120 s** | `"กำลังจับคู่ประวัติคำสั่งซื้อ..."` |
| `customer360.py:528` *(legacy)* | `load_lead_followups()` | 600 s | `"กำลังโหลดสถานะ Lead / Follow-up..."` |

TTL distribution: 8 × 900 s, 5 × 300 s, 1 × 600 s (auth) + 6 × 600 s (legacy), 1 × 120 s, 1 × unbounded resource.

**Not cached** (verified — `fetch_dashboard_kpis` at `crm_data/dashboard.py:8`, `fetch_sales_report` at `:189`, `fetch_sales_report_rows` at `:240`, `fetch_customer_page` at `neon_utils.py:1492`, `fetch_followup_page` at `neon_utils.py:2448`, `fetch_user_roles` at `neon_utils.py:2672`, `fetch_staff_options` at `neon_utils.py:2802`). These are the hot per-render list/KPI queries and they hit Neon on **every** rerun. `fetch_user_roles` being uncached is deliberate — `CRM_WORKFLOW_LOGIC_AUDIT.md:402`: "`neon_utils.fetch_user_roles()` should remain uncached unless there is a clear and explicit invalidation model."

⚠️ Consequence: `pages/dashboard.py:305` calls `clear_cached_data_functions(fetch_dashboard_kpis, fetch_sales_report, fetch_sales_report_rows)` — **all three are undecorated**, so this call is a complete no-op (the `getattr(fn, "clear", None)` guard swallows it). Same for `pages/customers.py:399-400`, which passes `getattr(neon, "fetch_sales_report_owner_options", None)` and `getattr(neon, "fetch_crm_owner_options", None)` — those *are* cached, but only if the attributes exist on `neon_utils` (both are re-exported, so they do resolve).

⚠️ `ensure_crm_data_imports_schema()` is `@st.cache_resource` and runs the full `CRM_DATA_IMPORTS_DDL` (`create table if not exists` + `alter table add column if not exists` + `create index if not exists`) **once per Streamlit process** — a runtime DDL execution triggered by ordinary reads. `CRM_WORKFLOW_LOGIC_AUDIT.md:520` rates this **HIGH** risk: "Can create/alter tables or indexes at runtime — Keep as-is unless a migration-control phase is approved."

### 4.4 Every global `st.cache_data.clear()` call site

| file:line | Trigger |
|---|---|
| `ui/import_excel_ui.py:204` | Import success |
| `ui/import_excel_ui.py:275` | Delete import batch success |
| `pages/products.py:224` | Add product |
| `pages/products.py:267` | Import products from XLSX |
| `pages/products.py:683` | Save product edits |
| `pages/products.py:699` | Disable product |
| `pages/users.py:90` | Create/update user (from create form) |
| `pages/users.py:170` | Update user row |
| `pages/users.py:177` | Deactivate user |
| `pages/system_status.py:19` | Manual "รีเฟรชข้อมูลตอนนี้" button |
| `pages/3_sync_status.py:18` | Legacy manual refresh |
| `pages/4_เพิ่มข้อมูลลูกค้า.py:354, 667, 738` | Legacy manual order save / other writes |
| `pages/6_สินค้า.py:185, 257, 294, 308` | Legacy product writes |
| `pages/7_พนักงาน.py:161, 209, 223` | Legacy staff-options writes |
| `pages/9_ติดตามลูกค้า.py:326` | Legacy follow-up save |

**17 global clears in canonical + legacy pages** (10 in canonical). Every one is immediately followed by `st.rerun()`.

### 4.5 Every targeted `clear_cached_data_functions(...)` / `.clear()` call site

| file:line | Trigger | Caches cleared |
|---|---|---|
| `ui/manual_order_ui.py:193-199` | Manual order save (wrapped in `perf_trace("manual_order.clear_caches", action="save")`) | `fetch_followup_filter_options`, `fetch_filter_options`, `fetch_sales_report_owner_options`, `fetch_crm_owner_options` |
| `pages/followup.py:453` | Follow-up save | Follow-up filters (via `clear_cached_functions_safely`) |
| `pages/followup.py:732` | Follow-up order-popup save | Follow-up filters, Customers filters, Dashboard owner options, CRM owner options |
| `pages/customers.py:397-402` | Owner assignment update | `fetch_filter_options`, `fetch_followup_filter_options`, `fetch_sales_report_owner_options`, `fetch_crm_owner_options` |
| `pages/customer_detail.py:291` | Customer 360 follow-up save | `fetch_followup_filter_options` |
| `pages/dashboard.py:305` | Sales-row delete | `fetch_dashboard_kpis`, `fetch_sales_report`, `fetch_sales_report_rows` — **no-op, none are cached** |
| `crm_data/products.py:448-449` | `bulk_update_product_active` | `fetch_product_page`, `fetch_product_options` |
| `crm_data/products.py:480-481` | `archive_products` | `fetch_product_page`, `fetch_product_options` |
| `crm_data/products.py:511-512` | `restore_archived_products` | `fetch_product_page`, `fetch_product_options` |
| `customer360.py:568` *(legacy)* | Lead read error path | `load_lead_followups` |
| `customer360.py:821-824` *(legacy)* | Owner update | `load_crm_customers`, `load_customer_by_detail_key`, `load_customer_filter_options`, `search_orders_by_phones` |

Unrelated but easy to confuse when grepping: `st.query_params.clear()` at `customer360.py:641, 657, 714, 725`.

**Direction of travel** (from `DATABASE_CODE_OVERVIEW.md:250-253`): avoid global `st.cache_data.clear()`; use `clear_cached_data_functions(...)`; dashboard auto-refresh made opt-in; limit customer order-history rendering. The product repository (`crm_data/products.py`) is the only module fully converted to targeted clears; Import/Users/legacy pages are still global.

---

## 5. Performance work already done + known bottlenecks

### 5.1 `SUPABASE_USAGE_OPTIMIZATION.md` (3.7 KB)

**Current policy:** Supabase is used **for Auth/Login only**; all CRM data must live in Neon PostgreSQL, not Supabase.

Allowed: Auth/Login; session refresh/restore via `/auth/v1/*`.
Forbidden at runtime: Supabase Database; Supabase Storage; Supabase REST Data API `/rest/v1/*`; `supabase.table(...)`; `supabase.storage...`; a service-role key in the browser/client.

**Why it changed:** the system previously used Supabase as the database for `order_history`, `crm_customers`, staging/import and sync jobs, causing high egress and database usage. New approach: Neon as the primary CRM DB; Supabase reduced to Auth to cut egress and Data-API risk; the old GitHub Actions syncs disabled; Streamlit Excel/Manual Order write to Neon only.

**Runtime checkpoint** the doc prescribes running periodically:
```powershell
rg -n --hidden -S "/rest/v1|supabase\.table|supabase\.storage|\.storage\.from|SUPABASE_SERVICE_ROLE_KEY|CRM_SUPABASE_SERVICE_KEY|CRM_SUPABASE_SERVICE_ROLE" .
```
Expected: no hits. The doc pre-empts a false positive: `service_role` may still appear in `supabase/migrations/` — historical schema, not a runtime call. (I confirmed: the only `service_role` hits are in `supabase/migrations/202605270003_create_crm_user_roles.sql:26-37`, and `/rest/v1` appears nowhere in runtime code.)

**Active data location** (`:52-58`): `crm_data_imports`, `crm_lead_followups`, `crm_user_roles`, `crm_product_options`, `crm_orders`, `crm_order_items`. Runtime access must go through `neon_utils.py` and **select only the columns needed**.

**Auth error handling requirements:** `auth_utils.py` must clearly handle wrong login, expired session, timeout, and a usage/billing-limited project; auth errors must never surface as a raw traceback if a human-readable message is possible.

**Operational rules:** no new Supabase DB/Storage calls without approval; never put the service-role key client-side; Auth changes touch only `/auth/v1/*`; new CRM data workflows use Neon only; any legacy Supabase-hitting sync must be disabled first, then a cleanup plan proposed.

**Legacy note:** docs/migrations mentioning `order_history`, `crm_customers`, `import_staging` on Supabase are historical context and must **not** be used as the source of truth for current architecture.

### 5.2 `ui/perf.py` (full file, 39 lines)

An opt-in, PII-safe stderr/stdout timing tracer — **not** a metrics system.

```python
import os
from contextlib import contextmanager
from time import perf_counter

_ENABLED_VALUES = {"1", "true", "yes", "on"}
_SAFE_META_KEYS = {"action", "count", "page", "page_size", "role", "sale_type"}

def perf_enabled() -> bool:
    return os.getenv("CRM_PERF_DEBUG", "").strip().lower() in _ENABLED_VALUES

def _safe_meta(meta: dict) -> dict:
    safe = {}
    for key, value in meta.items():
        if key not in _SAFE_META_KEYS or value is None:
            continue
        if isinstance(value, (bool, int, float)):
            safe[key] = value
        else:
            safe[key] = str(value)[:64]
    return safe

@contextmanager
def perf_trace(label: str, **meta):
    if not perf_enabled():
        yield
        return

    start = perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (perf_counter() - start) * 1000
        safe_meta = _safe_meta(meta)
        suffix = f" {safe_meta}" if safe_meta else ""
        print(f"[PERF] {label} {elapsed_ms:.1f}ms{suffix}", flush=True)
```

Design points worth carrying over: **allow-list** metadata (only `action, count, page, page_size, role, sale_type` — so customer names, phones, emails can never leak into logs), 64-char truncation, zero overhead when disabled (early `yield`), output format `[PERF] <label> <ms>ms {meta}`.

**Instrumented spans (40 call sites)** — the de-facto map of what the team considered hot:

- `crm_data/dashboard.py`: `repo.dashboard.fetch_kpis` (`:9`), `repo.dashboard.fetch_sales_report` (`:195`), `:247`, `:352`
- `pages/dashboard.py`: `dashboard.page_render` (`:26`), `dashboard.fetch_kpis` (`:36`), `dashboard.fetch_chart_report_data` (`:88`), `:287`, `:294`, `dashboard.clear_caches` (`:304`), `dashboard.rerun` (`:307`)
- `pages/followup.py`: `followup.page_render` (`:73`), `:98`, `followup.fetch_filter_options` (`:310`), `:446`, `followup.clear_caches` (`:453`), `followup.rerun` (`:457`), `followup.open_popup` (`:543` action=followup, `:548` action=order), `followup.dialog_render` (`:597`, `:608`), `followup.load_product_options` (`:623`), `followup.add_order_item` (`:659`), `followup.rerun` (`:671`), `:700`, `followup.clear_caches` (`:732`), `followup.rerun` (`:747`)
- `ui/manual_order_ui.py`: `manual_order.render_form` (`:27`), `manual_order.load_owner_options` (`:45`), `manual_order.load_product_options` (`:50`), `manual_order.add_item` (`:110`), `manual_order.rerun` (`:122`), `:159`, `manual_order.clear_caches` (`:193`), `manual_order.rerun` (`:202`)

The pattern `*.clear_caches` + `*.rerun` being traced separately shows the team specifically measured **post-write invalidation cost** — the exact cost that global `st.cache_data.clear()` inflates.

### 5.3 Other optimizations in place

- **Server-side pagination** everywhere: `fetch_customer_page` (`neon_utils.py:1492`), `fetch_followup_page` (`neon_utils.py:2448`), `fetch_product_page` (`crm_data/products.py:228`) all take `page_size, page` and emit `limit %s offset %s` — no full-table loads. Enforced by `docs/SMOKE_TEST_CHECKLIST.md:38, 180`.
- **Explicit column lists** — `CRM_COLUMNS` (`neon_utils.py:277+`); required by `docs/SMOKE_TEST_CHECKLIST.md:179`.
- **Schema-existence probes cached 300 s** (`neon_table_exists`, `neon_column_exists`) instead of per-query `information_schema` hits — though `table_has_column` (`neon_utils.py:2637`) is the *uncached* variant used inside every `crm_user_roles` read/write for the `owner_alias` probe.
- **Dashboard auto-refresh made opt-in** (`DATABASE_CODE_OVERVIEW.md:253`).
- **Customer order-history render capped** to reduce HTML/render cost (`DATABASE_CODE_OVERVIEW.md:254`).
- **Purpose-scoped indexes** in the DDL: `idx_crm_user_roles_active_email`, `idx_crm_user_roles_staff_code`, `idx_crm_data_imports_staff_code`, `idx_crm_product_options_active_sort`, `idx_crm_staff_options_active_sort`, `idx_crm_user_roles_owner_alias`.
- **Migration from global → targeted cache clears**, complete only in `crm_data/products.py`.
- **Legacy syncs disabled** to stop background DB load (§8).

### 5.4 Documented bottlenecks and risks

From `CRM_WORKFLOW_LOGIC_AUDIT.md:451-456` (cache risks):
- Global clears are correct but **can slow the first rerun after write actions**.
- Role/permission cache should stay conservative.
- Customer 360 query caching must not be introduced without user-scope **and** customer-scope analysis.
- Legacy `customer360.py` cache decorators must not be used as a model.

From the Risk Register (`CRM_WORKFLOW_LOGIC_AUDIT.md:517-533`):

| Risk | Severity | Note |
|---|---|---|
| Runtime schema mutation via `ensure_crm_data_imports_schema()` | **HIGH** | can create/alter tables/indexes at runtime |
| Auth/session/role cache staleness | **HIGH** | can temporarily mis-scope permissions; add no new auth caches |
| Duplicate `render_followup_table` definitions in `pages/followup.py` | MEDIUM | the later def silently overrides the earlier one |
| Global cache clears in Import/Product/Users | MEDIUM | correct but slow reruns |
| Customer follow-marker update may not clear follow-up caches | MEDIUM | filters/list can stay stale until rerun/TTL |
| Customer URL update doesn't clear broader customer/detail caches | MEDIUM | latent |
| Dashboard sales report depends on schema readiness | MEDIUM | `crm_sales_report_ready()` gate changes behavior when `sale_type`/`amount`/`address` are missing |
| Product Master global clear | LOW | broader than needed |
| Users page global clear | LOW–MEDIUM | role-sensitive |
| Untracked `apps_script/` | LOW | hygiene |
| Legacy `customer360.py` | LOW–MEDIUM | old route/helper could confuse future work; "Do not revive" |

Six recommended, **not yet executed** phases (`:535-565`): 4.1 Customers write-path cache audit (follow marker, URL assignment); 4.2 Product Master targeted clears; 4.3 Import Excel global-clear replacement feasibility; 4.4 Users/Auth cache safety audit; 4.5 Follow-up module cleanup (duplicate `render_followup_table`); 4.6 Customer 360 performance audit. All flagged **audit-only first**.

`DATABASE_CODE_OVERVIEW.md` "จุดที่ควรระวังมาก" (do not touch without an audit): `upsert_manual_order_items()`, owner/permission scope, duplicate phone lock, Dashboard totals/query, Team Sales query, Import Excel mapping/write, Product Master active/archived filter, Follow-up save logic, Customer 360 phone matching, DB schema/migration.

`DATABASE_CODE_OVERVIEW.md:230-246` lists a cache inventory that is now **partly stale**: it names `fetch_order_product_options()` — that function **does not exist anywhere in the codebase**. The audit's cache table (`CRM_WORKFLOW_LOGIC_AUDIT.md:413-426`) also mis-attributes `fetch_sales_report_owner_options` and `fetch_product_options` to `neon_utils.py` when they actually live in `crm_data/dashboard.py:106` and `crm_data/products.py:204`. It also omits `fetch_product_page` (300 s), which exists. **Trust the code, not these tables.**

### 5.5 Logs

`streamlit_stderr.log` and `streamlit_stdout.log` are both **0 bytes** (verified via `ls -la`, timestamped Jul 18 13:10). No errors, warnings, slow-query traces or `[PERF]` output are recorded — the files exist only as placeholders. `.gitignore` excludes `logs/` and `*.log`, and `.github/workflows/cleanup.yml` fails CI if any `.log` is tracked, so these two are anomalies that hygiene CI would flag. **There is no log-derived evidence about runtime behavior available in this repo.**

---

## 6. Deployment / configuration

### 6.1 `.streamlit/config.toml` (complete)

```toml
[client]
showSidebarNavigation = false

[theme]
base = "light"
primaryColor = "#F97316"
backgroundColor = "#FFF8F0"
secondaryBackgroundColor = "#FFF3E8"
textColor = "#1F2937"
font = "sans serif"
```
`showSidebarNavigation = false` suppresses Streamlit's automatic `pages/` menu — navigation is fully hand-built in `nav_utils.py` (`NAV_GROUPS`, `render_sidebar_nav`). This is what allows Thai/emoji labels over English route filenames, and what lets nav links be *rendered as disabled `<div>`s* pre-login (`nav_utils.py:60-70`). No `[server]` section at all — no port, no CORS, no XSRF, no `maxUploadSize` overrides. Theme: orange `#F97316` primary on cream `#FFF8F0`, grey-900 text.

### 6.2 `runtime.txt`
```
python-3.12
```
(Streamlit Cloud pin.)

### 6.3 `requirements.txt` (7 pins, all upper-bounded)
```
streamlit>=1.40,<2
pandas>=2.2,<3
requests>=2.31,<3
plotly>=5.24,<7
openpyxl>=3.1,<4
streamlit-js-eval>=0.1.7,<2
psycopg[binary]>=3.2,<4
```
No `supabase` SDK — Auth is raw `requests`. `psycopg[binary]` v3 is the Neon driver (imported defensively; `neon_utils.py:316` guards `if psycopg is None`). No test/lint/dev dependencies — `pytest` is not declared even though `tests/test_common_helpers_characterization.py` is pytest-style.

### 6.4 `requirements-sync.txt` (3 pins, for the dead sync jobs)
```
gspread==6.2.1
google-auth==2.43.0
requests>=2.31,<3
```
Exact pins for the Google Sheets → Supabase pipeline. **Nothing in the live app imports `gspread` or `google.auth`** — this file exists only for the disabled workflows.

### 6.5 `.devcontainer/devcontainer.json`
```json
{
  "name": "Python 3",
  "image": "mcr.microsoft.com/devcontainers/python:1-3.11-bookworm",
  "customizations": {
    "codespaces": { "openFiles": ["README.md", "crm_dashboard.py"] },
    "vscode": { "settings": {}, "extensions": ["ms-python.python", "ms-python.vscode-pylance"] }
  },
  "updateContentCommand": "[ -f packages.txt ] && sudo apt update && sudo apt upgrade -y && sudo xargs apt install -y <packages.txt; [ -f requirements.txt ] && pip3 install --user -r requirements.txt; pip3 install --user streamlit; echo '✅ Packages installed and Requirements met'",
  "postAttachCommand": { "server": "streamlit run crm_dashboard.py --server.enableCORS false --server.enableXsrfProtection false" },
  "portsAttributes": { "8501": { "label": "Application", "onAutoForward": "openPreview" } },
  "forwardPorts": [8501]
}
```
**Server flags:** `--server.enableCORS false --server.enableXsrfProtection false` — both protections disabled, dev/Codespaces only. Port **8501**.

**Three-way Python version mismatch:** devcontainer **3.11-bookworm**, `runtime.txt` **3.12**, CI `setup-python` **3.12**.

**Stale entrypoint:** the devcontainer runs `crm_dashboard.py`, which is now a 13-line EDITOR-gated placeholder (`crm_dashboard.py:12`: `render_placeholder_page("CRM Dashboard", "หน้านี้ปิดการโหลดข้อมูลจาก Supabase แล้ว…")`). The real entrypoint is `app.py` (KPI dashboard + Quick Access page links). `archive/legacy/README.md` also still instructs Streamlit Cloud to use `crm_dashboard.py` as the main file. Which file production actually runs is **not** resolvable from this repo.

### 6.6 `.github/workflows/` — 4 workflows (+ a stray `desktop.ini`)

**`deploy-dashboard.yml`** — despite the name, it **only validates; it does not deploy**. On `push` to `main` + `workflow_dispatch`: checkout → `setup-python@v5` with `python-version: "3.12"` → `pip install -r requirements.txt` → a byte-compile syntax check over a **hardcoded file list**:
```yaml
run: python -m py_compile app.py auth_utils.py neon_utils.py customer360.py crm_dashboard.py pages/3_sync_status.py pages/4_เพิ่มข้อมูลลูกค้า.py pages/5_ฐานข้อมูลลูกค้า.py pages/6_สินค้า.py pages/7_พนักงาน.py pages/9_ติดตามลูกค้า.py
```
This list is **stale and inverted**: it compiles legacy Thai-named pages while omitting every canonical route (`pages/dashboard.py`, `customers.py`, `followup.py`, `import_excel.py`, `products.py`, `users.py`, `system_status.py`, `settings.py`, `customer_detail.py`, `team_sales.py`), all of `crm_data/`, all of `ui/`, and `permissions.py`. **CI never runs the test suite.**

**`cleanup.yml`** — "Cleanup Audit", `workflow_dispatch` + weekly `cron: "0 20 * * 0"` (Sun 20:00 UTC). Fails the build if generated or sensitive files are tracked:
```bash
bad_files="$(git ls-files | grep -Ei '(^|/)(outputs?|reports?|backups?|logs?|archive|__pycache__|\\.cache)(/|$)|\\.(xlsx|xls|xlsm|csv|log|tmp|temp|bak|backup|pyc)$|(^|/)\\.streamlit/secrets\\.toml$' || true)"
if [ -n "$bad_files" ]; then ... exit 1; fi
```
Note the working tree contains `archive/`, `outputs/` (incl. `.xlsm` files), `__pycache__/`, `streamlit_*.log`, and `.streamlit/secrets.toml` — all matched by this pattern and all listed in `.gitignore`, so they should be untracked.

**`sync-crm-customers.yml`** — `workflow_dispatch` only, concurrency group `crm-customers-sync`, single job named `disabled`:
```yaml
- name: Legacy workflow disabled
  run: echo "Legacy crm_customers sync is disabled because crm_customers was replaced by crm_data_imports."
```

**`sync-data.yml`** — `workflow_dispatch` only, concurrency group `data-raw-sync`. Retains its old inputs `batch_size` (default `"100"`, described "Lower is gentler for Supabase Free Plan") and `min_batch_size` (default `"20"`) but the job is likewise `disabled`:
```yaml
run: echo "Legacy DATA_RAW sync is disabled because order_history was replaced by crm_data_imports Excel import."
```

`.github/workflows/desktop.ini` is a Google-Drive-File-Stream artifact (the whole tree is Drive-synced — `desktop.ini` files appear in most directories, plus a renamed-aside `.git.bak_inner/`, so **the project has no working git repo**).

### 6.7 `.gitignore` (complete, 546 B)

Sections: local secrets/credentials (`.env`, `.env.*`, `.streamlit/secrets.toml`, `service_account.json`, `*.pem`, `*.key`); Python/runtime cache (`.venv/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.cache/`); local outputs (`archive/`, `outputs/`, `output/`, `reports/`, `report/`, `backups/`, `backup/`, `logs/`, `*.log`, `*.tmp`, `*.temp`, `*.bak`, `*.backup`); spreadsheet exports (`*.xlsx`, `*.xls`, `*.xlsm`, `*.csv`, `*.tsv`); OS/editor noise (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `*.swp`, `*.swo`, `~$*`); and `.git.bak_inner/` with the comment "Renamed-aside former inner git repo (see PR setup notes)".

**There is no `Dockerfile`, no `docker-compose.yml`, no `Procfile`, no `packages.txt`, no `pytest.ini`/`pyproject.toml`/`setup.cfg`, no `conftest.py`, no linter config, and no `README.md` at the project root** (only `archive/legacy/README.md`).

---

## 7. Tests — `tests/` (12 files, 1008 lines)

### 7.1 Testing style — two incompatible idioms in one directory

**Style A — module-level `assert` scripts (11 of 12 files).** No test functions, no framework. Each file executes its assertions at import time and ends with a `print("... OK")`. Run as `python tests/test_x.py`. Under `pytest` they still "pass" (collection = execution), but a failure yields a bare `AssertionError` with no context, and any file whose assertions pass produces zero reported tests.

**Style B — pytest functions (1 of 12).** `test_common_helpers_characterization.py` is 20 proper `def test_*()` functions with docstring-free but descriptive names.

Every Style-A file bootstraps its own path (`pytest` is not configured, there is no `conftest.py`, and `pytest` is not in `requirements.txt`):
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

### 7.2 The critical split: source-text assertions vs. real behavior

**Group 1 — pure source-string / AST matching (does NOT execute the code under test).** These assert that specific literal strings exist in a `.py` file. They are *change detectors*, not behavior tests: they would pass on syntactically-broken code and fail on any harmless rename or reformat. **All of this value is lost in a rewrite — but each string is a spec statement about intended behavior.**

- **`test_followup_priority_tabs.py`** (30 lines) — reads `pages/followup.py` and `neon_utils.py` as text and asserts the literals `FOLLOWUP_PRIORITY_TAB_OPTIONS = tuple(FOLLOWUP_PRIORITY_OPTIONS)`, `def render_priority_tabs() -> None:`, `render_priority_tabs()`, `def set_followup_priority_filter_from_tab(priority: str) -> None:`, `st.session_state["followup_filter_priority"] = normalize_followup_priority(priority)`, `st.session_state.followup_page_v2 = 1`, `fetch_followup_page(filters, user, page_size, page)`, and — a permission assertion by grep — `_followup_staff_scope(user, "d")`. Also asserts each of the 6 priorities appears in both files and that no legacy priority appears on the tab-options line.
- **`test_customer_owner_assignment_filter_reset.py`** (42 lines) — reads `pages/customers.py`, asserts `OWNER_ASSIGNMENT_FOLLOWUP_FILTER_RESET_KEYS` and `def reset_owner_assignment_followup_filters() -> None:` exist and are called, then *slices the source by string index* (`split("OWNER_ASSIGNMENT_FOLLOWUP_FILTER_RESET_KEYS")[1].split("EXPORT_PERIOD_OPTIONS")[0]`) to assert that the reset block contains exactly `followup_filter_priority`, `followup_filter_lead_status`, `followup_filter_followup_status` and — the real point — that it does **not** contain `followup_filter_keyword`, `followup_filter_date_mode`, `followup_filter_single_date`, `followup_filter_date_range`, `followup_filter_owner`, `customers_page`. **Spec: after assigning an owner, reset only the 3 status filters; preserve keyword, date, owner filters and the page number.**
- **`test_product_archive_ui.py`** (53 lines) — the only file using `ast`: parses `pages/products.py` and extracts `render_product_archive_actions`, `clear_product_selection`, `render_product_table`, `render_product_row` via `ast.get_source_segment`, then string-matches inside each. Asserts `'"สินค้าที่เก็บถาวร": "archived"'`, `status_filter=PRODUCT_STATUS_OPTIONS[status_label]`, confirm-key constants, `clean(reason) or "Archived from Product Master"`, `is_archived = bool(row.get("archived_at"))`, `if is_editor and not is_archived`, `cols[4].write("เก็บถาวร")`, `if status_filter != "archived"`. Ends with two whole-file safety assertions: **`assert "delete from" not in LOWER_SOURCE`** and **`assert "delete_product_option(" not in LOWER_SOURCE`**.

**Group 2 — real behavior, pure functions (executes the code, no I/O).** These are genuine and directly portable.

- **`test_common_helpers_characterization.py`** (236 lines, pytest) — 20 tests over `clean`, `to_number`, `parse_date`, `normalize_phone`, `validate_phone_value`, `validate_phone_pair`, `make_dedupe_key`, `new_batch_id`, `now_iso`, `BANGKOK_TZ`, `PHONE_RULE_MESSAGE`. Notable: it **locks in quirks as intended behavior** (`test_clean_preserves_current_pd_na_behavior` asserts `clean(pd.NA) == "<NA>"`; `test_make_dedupe_key_preserves_plus66_behavior` asserts `+66…` hashes differently from `0…`). It pins **6 exact SHA-256 hex digests** — e.g. the all-empty key `be5be69f55e91af25e54ecc2154d4da359b67b3b27e25f5cc0b3ff54eb74dff3` and `make_dedupe_key("A001","0812345678","","TH123") == ec8dbb1b…`. It asserts dedupe-key phone **order is significant** (swapping phone1/phone2 changes the hash) and case sensitivity. Four timezone tests fix Bangkok = UTC+7, midnight Bangkok = previous-day 17:00 UTC, and an exclusive next-midnight range end. **This is the single most portable test file: any Django rewrite that must read existing `dedupe_key` values has to reproduce these hashes byte-for-byte.**
- **`test_followup_priority_options.py`** (56 lines) — imports `neon_utils` and asserts the real canonical priority list `["Super VIP", "VIP", "Premium", "Economy", "NEW", "Dismiss"]`, `DEFAULT_FOLLOWUP_PRIORITY == "NEW"`, and the full legacy→canonical `normalize_followup_priority` map: `urgent`/`ด่วนมาก`→`Super VIP`, `high`/`สูง`→`VIP`, `normal`/`ปกติ`→`NEW`, `low`/`ต่ำ`→`Economy`, `None`/`""`/`"unknown"`→`NEW`, and each canonical value idempotent. Asserts `followup_priority_filter_values("Super VIP") == {"Super VIP","urgent","ด่วนมาก"}` and `("NEW") == {"NEW","normal","ปกติ"}` (i.e. **filters must match legacy rows too**), and that no legacy value leaks into `FOLLOWUP_PRIORITY_OPTIONS`. Then falls back to source-text matching for the three pages. **This is a real data-migration contract: two priority vocabularies coexist in the DB.**
- **`test_product_sorting.py`** (31 lines) — exercises `crm_data.products.sku_sort_key`: `"SP 001"` and `"SP001"` both → `(0, 1)`; `"SP604"`→`(0,604)`; `"SP566-1ชิ้น"`→`(0,566)`; `"SP673-300W 12V"`→`(0,673)`; non-`SP` and empty/`None` → bucket `1`. Verifies the resulting total order puts all `SP*` numerically ascending before `SKU-100`.
- **`test_product_bulk_actions.py`** (86 lines, first half) — real `validate_product_ids`: `[] → []`, `[1,2,3] → [1,2,3]`, `[1,2,1] → [1,2]` (dedupe, order-preserving), and `ValueError` for each of `["1"]`, `[1.2]`, `[True]`, `[0]`, `[-1]` — i.e. **strict positive `int` only; `bool` rejected despite being an `int` subclass**. `bulk_update_product_active([], True) == 0` short-circuits without a connection.
- **`test_product_delete_readiness.py`** (73 lines) — real `build_product_delete_readiness` state machine over injected row dicts: `imports_sku_count=2` → `status="blocked_used"`, `usage_sources==["crm_data_imports.sku"]`; `order_items_name_count=1` → `blocked_used` / `["crm_order_items.product_name"]`; all-zero counts → `status="tentative_no_usage"`, `reason="no_usage_found_in_text_checks"`; product absent → `unsafe_unknown` / `"product_not_found"`; blank sku **and** name → `unsafe_unknown` / `"blank_sku_and_product_name"`; a `check_error="ConnectionTimeout"` → `unsafe_unknown` / `"usage_check_error:ConnectionTimeout"`. Then two static guards: the readiness SQL must be `select`-only (`for forbidden_statement in ("delete ","update ","insert ","alter ","drop ","truncate "): assert forbidden_statement not in normalized_sql`, plus `startswith("select ")`), and `inspect.getsource(fetch_product_delete_readiness)` must **not** contain `ensure_crm_data_imports_schema` (no DDL on a read path). **Note the deliberately conservative vocabulary: never "safe to delete", only "tentative_no_usage".**

**Group 3 — real behavior with fakes/mocks (executes SQL-building and transaction logic against hand-rolled fake DB objects).**

- **`test_product_archive_repository.py`** (127 lines) — the most rigorous file. Defines `FakeCursor` (records `(sql, params)`, configurable `rowcount`), `FakeConnection` (tracks `committed`/`rolled_back`), `FakeConnectionContext`. Normalizes and asserts the archive SQL contains `archived_at = now()`, `is_active = false`, `and archived_at is null`, `where id = any(%s::bigint[])` and **not** `delete `; the restore SQL nulls `archived_at`/`archived_by`/`archive_reason`, keeps `is_active = false` (**restore does NOT reactivate**), and is guarded by `and archived_at is not null`. Asserts `products.archive_products([1, "2"])` raises `ValueError`. Then, using `unittest.mock.patch.object` on `neon_utils.neon_connection` *and* on `products.fetch_product_page.clear` / `fetch_product_options.clear`, verifies `archive_products([3, 3, 8], archived_by=" editor@example.com ", reason=" duplicate product ")` returns exactly `{"requested": 2, "updated": 1, "skipped": 1}` (input dedupe → 2; `rowcount=1` → 1 updated, 1 skipped), that params are `["editor@example.com", "duplicate product", "editor@example.com", [3, 8]]` (**arguments trimmed**), that `committed is True` / `rolled_back is False`, and that **both cache clears were called exactly once**. Same for `restore_archived_products([5, 9])` → `{"requested": 2, "updated": 2, "skipped": 0}`, params `["editor@example.com", [5, 9]]`.
- **`test_crm_team_duplicate_phone_lock.py`** (194 lines) — real behavior via **monkeypatching module globals inside `try/finally`** (originals restored). Business rule: `should_enforce_duplicate_phone_lock("CRM_TEAM") is True`; `None`, `"UPSELL_TEAM"`, `"OTHER_TEAM"` all `False`. Then, with `fetch_current_user_team_code` stubbed: CRM_TEAM + a duplicate found → `allowed is False`, `team_code == "CRM_TEAM"`; CRM_TEAM + no duplicate → allowed; non-CRM teams → always allowed even with a duplicate present. **Fail-open behavior is explicitly asserted:** if the team lookup raises, `allowed is True` and `"ตรวจสอบทีมไม่สำเร็จ" in fail_open["warning"]`. It then proves the block happens at the **save layer before any schema probing**, by sabotaging `neon_column_exists` to raise `AssertionError("save-layer block should happen before column checks")` and asserting `upsert_manual_order_items(...)` raises `ValueError` containing `"ทีม CRM ไม่สามารถเพิ่มคำสั่งซื้อซ้ำได้"`. Finally, with a `FakeConnection`, it verifies `find_duplicate_valid_order_by_phones` emits `phone1 = any(%s) or phone2 = any(%s)` and passes the phone array **four times** (`[["0812345678","0912345678"]] * 4`). Last two lines drop back to source-text checking to assert the lock is **not** applied to the bulk import path:
```python
source = Path("neon_utils.py").read_text(encoding="utf-8")
insert_start = source.index("def insert_import_records")
manual_start = source.index("def upsert_manual_order")
assert "check_crm_team_duplicate_phone_lock" not in source[insert_start:manual_start]
```
(⚠️ this uses a **relative** path, so it only works when cwd is the project root.)
- **`test_product_bulk_actions.py`** (second half) — replaces `sys.modules["neon_utils"]` with a `types.SimpleNamespace` exposing only `ensure_crm_data_imports_schema` and `neon_connection`, calls `bulk_update_product_active([1, 2], False, "editor@example.com")`, asserts return `2`, SQL contains `where id = any(%s::bigint[])`, params `[False, "editor@example.com", [1, 2]]`, `committed is True`, `rolled_back is False`, and restores the original module in `finally`.

**Group 4 — auth (mixed, real logic, no network).**

- **`test_auth_restore_state.py`** (35 lines) — the **only auth test in the repo**. Exercises the real `classify_browser_session_payload` with no Streamlit or Supabase involved:
```python
assert classify_browser_session_payload(None) == "pending"
assert classify_browser_session_payload(None) != "empty"          # the whole point
assert classify_browser_session_payload({}) == "empty"
assert classify_browser_session_payload({"access_token": "access", "refresh_token": "refresh"}) == "has_session"
assert classify_browser_session_payload("not-json") == "invalid"
assert classify_browser_session_payload({"access_token": "access"}) == "invalid"   # one token is not enough
assert classify_browser_session_payload(bridge_value(None)) == "empty"
assert classify_browser_session_payload(bridge_value({"access_token": "access", "refresh_token": "refresh"})) == "has_session"
```
The redundant-looking `!= "empty"` line encodes the bug this abstraction was built to fix: `pending` must never be conflated with `empty`, or the login form flashes before localStorage answers.

### 7.3 Coverage gaps

**Nothing tests:** `permissions.py` (zero direct tests — the entire authorization matrix is untested), `_followup_staff_scope` (only asserted to *exist as a string*), `require_login`, `current_user`, `ensure_fresh_session`, `_jwt_exp`, `logout`, `fetch_user_role`, `login_with_password` / `refresh_auth_session` / `fetch_auth_user`, `clear_cached_data_functions`, any real database interaction, any HTTP interaction, and any end-to-end page render. There is no test runner config, no CI wiring (`deploy-dashboard.yml` only byte-compiles), and no fixtures/factories. Assurance for permissions and auth flows lives entirely in the **manual** checklists of §9.

---

## 8. Sync scripts and `archive/`

### 8.1 All three sync scripts are dead non-networking stubs

Each is a ~14-line file whose only behavior is to print a refusal. **None imports `requests`, `gspread`, `psycopg`, or anything else.**

`sync_to_supabase.py` (complete):
```python
"""Legacy Supabase sync entrypoint.

CRM data now imports to Neon via the Streamlit Excel workflow. This file is
kept as a non-networking stub so old manual commands fail safely.
"""


def main() -> None:
    print("Legacy Supabase sync is disabled. Use the Streamlit Excel import to Neon.")


if __name__ == "__main__":
    main()
```

`sync_crm_customers_to_supabase.py` (complete):
```python
"""Legacy CRM customers to Supabase sync.

Disabled intentionally. Customer data now lives in Neon crm_data_imports and is
managed through the Streamlit Excel import workflow.
"""


def main() -> None:
    print("Legacy CRM customers Supabase sync is disabled. Use Excel import to Neon.")


if __name__ == "__main__":
    main()
```

`sync_data_raw_to_supabase.py` (complete):
```python
"""Legacy DATA_RAW to Supabase sync.

Disabled intentionally to reduce Supabase egress and database usage. CRM data
is now imported from Excel into Neon table crm_data_imports.
"""


def main() -> None:
    print("Legacy DATA_RAW Supabase sync is disabled. Use Excel import to Neon.")


if __name__ == "__main__":
    main()
```

The intent is explicit — **"kept as a non-networking stub so old manual commands fail safely"** — a deliberate tombstone, defence-in-depth alongside the disabled workflows and the `SUPABASE_USAGE_OPTIMIZATION.md` prohibition. The matching `.github/workflows/sync-data.yml` and `sync-crm-customers.yml` are likewise `workflow_dispatch`-only `echo` jobs (§6.6).

**Data contracts to preserve from these scripts: none.** The scripts themselves carry zero schema, zero column mapping, zero dedupe logic. `requirements-sync.txt` (`gspread==6.2.1`, `google-auth==2.43.0`) is orphaned.

**However — historical contracts documented nearby that matter for understanding legacy rows:**
- `archive/legacy/DATA_RAW_SYNC.md` records the abandoned Google-Sheets pipeline: five spreadsheets, one per Thai fiscal year 2565–2569, each with a `DATA_RAW` sheet, IDs listed in a table (e.g. 2565 = `1Q9CyZi5ezvthVABg-aw6LvrYtSp7qRHiEr4pVMGMKHg`). Ran on a 30-minute GitHub Actions cron into Supabase `order_history`. **Dedupe key was `source_key = year + order_id`, e.g. `2565_OD221201003167`** — re-imports updated in place. Required secrets `CRM_SUPABASE_URL`, `CRM_SUPABASE_SERVICE_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON` (GitHub) and `CRM_SUPABASE_URL`/`_ANON_KEY`/`_SERVICE_KEY`/`CRM_SYNC_ADMIN_PASSWORD` (Streamlit, the last gating pause/resume buttons).
- `archive/disabled_pages/4_upload_excel.py` (713 lines) is the abandoned Supabase-based Excel uploader and is the only remaining record of the old target schema. `UPLOAD_BATCH_SIZE = 500`; tables `import_batches`, `import_staging`, `import_logs`, `import_backups`; and a `TARGETS` dict whose `order_history` entry declares `key: "source_key"`, `required: ["source_key", "order_id"]`, a ~31-column `fields` list (`source_key, order_id, year, month, day, date_text, customer, phone1, phone2, address, subdistrict, district, province, postcode, channel, sales_staff, upsell_staff, care_staff, product_group, total_sales, order_status, payment_method, delivery_status, shipping, tracking_no, channel_url, note, source_sheet, year_file`), plus a `synonyms` map of Thai header aliases (`"order_id": ["เลขคำสั่งซื้อ", "เลขออเดอร์", "order_id"]`, `"customer": ["ลูกค้า", "ชื่อลูกค้า", "customer"]`, …). This is the **ancestor** of today's Neon `crm_data_imports` / `raw_data` JSONB approach — `neon_utils.py` still queries `d.raw_data->>'เลขคำสั่งซื้อ'` (`neon_utils.py:1752`), so the Thai-header contract survives in live code.

**Both docs are marked non-authoritative.** `SUPABASE_USAGE_OPTIMIZATION.md` "Legacy Notes": documents/migrations mentioning `order_history`, `crm_customers`, `import_staging` on Supabase are historical context only — *do not use as a source of truth for current architecture*.

### 8.2 `archive/` contents (9 files, ~4 real ones)

```
archive/desktop.ini                                   (Drive artifact)
archive/disabled_pages/4_upload_excel.py              713 lines — Supabase Excel uploader, dead
archive/disabled_pages/desktop.ini
archive/disabled_pages/__pycache__/4_upload_excel.cpython-312.pyc
archive/disabled_pages/__pycache__/desktop.ini
archive/legacy/DATA_RAW_SYNC.md                       Google Sheets → Supabase pipeline spec
archive/legacy/desktop.ini
archive/legacy/README.md                              Old Streamlit Cloud deploy notes
archive/legacy/requirement.txt                        UTF-16-BOM garbage: "plotly"
```
`archive/legacy/README.md` names `crm_dashboard.py` as the Streamlit Cloud main file and the secrets `CRM_SUPABASE_URL` / `CRM_SUPABASE_ANON_KEY`, with the warning "Do not add the Supabase service role key to this repository or to Streamlit Cloud." `archive/legacy/requirement.txt` is a corrupt UTF-16 single-word file (`plotly`) — noise. The `.pyc` for the archived page indicates it was executed under Python 3.12 at some point. `archive/` is `.gitignore`d and would fail `cleanup.yml` if tracked.

Adjacent (not `archive/`, but equally non-runtime): `outputs/order_validation_20260616/` holds `summary.json`, `validate_order_files.py`, and two `.xlsm` workbooks (`ตรวจความถูกต้อง_ชุดใหม่_16-6-69.xlsm`, `พี่โก้_ตรวจความถูกต้อง_16-6-69.xlsm`) — a one-off order-validation exercise, also gitignored.

---

## 9. Acceptance criteria — the parity checklist

### 9.1 `docs/SMOKE_TEST_CHECKLIST.md` — 15 sections, ~150 checkboxes, run **before every deploy**

Scope rules (`:5-11`): test through the real Streamlit app; no secrets in logs or screenshots; **no migrations during smoke test**; do not accidentally mutate production; if testing writes, use clearly-identified test data that can be deleted/rolled back.

1. **Login/Logout** — login page appears; EDITOR login succeeds; STAFF/พนักงาน login succeeds; logout returns to login; **after logout internal pages are not reachable directly**; failed login shows a friendly message and does not crash.
2. **Supabase Auth Only** — login works; **no Supabase Database read/write**; no Supabase Storage; no service-role key in the browser; auth errors (401/timeout) do not crash the page.
3. **Customers Page** — page opens; EDITOR sees all customers; STAFF sees per current page behavior; search by **phone**, by **customer name**, by **order number**; **pagination works and does not load the whole table**; history button opens detail; URL renders as a `"เปิดลิงก์"` link when present; readable empty state.
4. **Follow-up Page** — opens; EDITOR sees all matching filters; STAFF sees own mapping; filters for lead status / follow-up status / priority / product-SKU all work; phone search works; popup opens; save closes the popup with no stuck state; **no popup opening by itself when searching or changing filters**.
5. **Manual Order** — page opens; EDITOR can add; STAFF can add per rights; fields: order number, customer name, **at least one of phone/backup phone**, URL, address; sale type selectable among **`NEW_ORDER`, `UPSELL`, `FOLLOW`**; **if `FOLLOW`, the amount is NOT counted in the sales report**; **on success the form resets; on failure the form is NOT cleared**.
6. **Multi SKU** — select from dropdown; ≥1 item before save; **qty > 0**; **same SKU + same product name → qty merges automatically; same SKU + different product name → separate lines**; can delete a line; one order with many SKUs does not crash; item list clears after a successful save.
7. **Import Excel** — EDITOR sees the section; STAFF cannot use it; `.xlsx` upload; worksheet selection; column mapping; preview before import; required-field validation; valid vs invalid row display; confirm import; import history shows; friendly failure message; **Manual Order UI and Import Excel UI do not interfere with each other**.
8. **Products** — opens from sidebar; EDITOR can add / edit SKU, name, group / disable; **STAFF/USER see read-only**; search by SKU and by name; `.xlsx` import previews before confirming; **identical SKU + name + group does not duplicate; a different SKU is added as a new row**.
9. **User / Role** — opens only for EDITOR/ADMIN per current policy; **shows users from the Neon table `crm_user_roles`**; add user / edit email / edit role / edit `staff_code` and `staff_name` / activate-deactivate, all subject to rights; **"test mapping" shows the number of customers a user would see**; STAFF cannot edit User/Role.
10. **Dashboard Report** — page opens; KPI cards load; sales report renders; date presets (today, yesterday, 7 days, 30 days, this month) and a custom date range; EDITOR sees grand totals and can filter by owner; STAFF sees only own data; **`NEW_ORDER` and `UPSELL` show correct sales / count / AOV; `FOLLOW` is not counted**.
11. **Export XLSX** — EDITOR sees the export button on Customers; STAFF does not / cannot; export all / daily / monthly / custom range; **the exported file's headers match the import template**; missing values render as blank cells; **1 row per order, or 1 row per the fallback rule when there is no order number**.
12. **Permission** — *EDITOR:* sees all customers, can Import Excel, add Manual Order, assign owner, edit User/Role, manage Product Master, export xlsx, view everyone's dashboard report. *STAFF/พนักงาน:* can log in, sees permitted menus, can add Manual Order per current rules, **cannot see/use Import Excel, cannot edit User/Role, cannot manage Product Master, cannot export xlsx**, sees Follow-up per current owner/staff mapping.
13. **Neon Connection** — `NEON_DATABASE_URL` set in Streamlit Secrets; the app connects; a clear message on failure; **never print the connection string**; main queries select only the needed explicit columns; **pagination/filtering happens server-side**.
14. **No Supabase `/rest/v1/*`** — code search finds no `/rest/v1`, no `supabase.table`, no Storage call; Supabase used only via `/auth/v1/*`; no GitHub Actions workflow syncs data into a Supabase database. Suggested command: `rg -n "/rest/v1|supabase\.table|storage\.|SUPABASE_SERVICE_ROLE|service_role" -S .`
15. **Streamlit Session / Form Reset** — session survives page changes; logout clears session correctly; Manual Order clears on success and **not** on failure; product selector resets after adding an item; multi-SKU list resets after a successful save; **a closed popup/modal does not reopen itself from stale session state**; changing filter/search does not leave an old popup open; **no `StreamlitAPIException` from setting `st.session_state` after a widget renders**.

Pre-deploy commands (`:208-216`), minimum two:
```powershell
$files = rg --files -g "*.py" -g "!__pycache__/**" -g "!.venv/**"
.\.venv\Scripts\python.exe -m py_compile @files
git diff --check
```
Result form (`:218-226`): test date, tester, commit, environment, Pass/Fail, failing items, notes.

Several items here are direct Streamlit artifacts that will simply vanish in Django (`StreamlitAPIException`, `st.session_state` popup re-opening, cache-driven staleness) — but the underlying user-visible requirements (idempotent forms, no phantom modals, no stale lists after writes) remain valid parity criteria.

### 9.2 `docs/CUSTOMER_360_SIGNOFF.md` — signed off, with named residual risks

**Scope delivered:** Customer Profile, Latest Order, URL / Owner, Follow-up, Order History, Products Bought.
**Files changed:** `neon_utils.py`, `pages/customer_detail.py`.
**UAT result:** EDITOR **PASS**; STAFF own detail **PASS**; STAFF other detail blocked **PASS**; Follow-up save **PASS**.
**Risks remaining:** `customer_id` is still `crm_data_imports.id`; **not yet Schema V2**; **no customer master table**.
**Decision:** *Customer 360 Core Approved.*
**Next phase:** Analytics / Manager Dashboard.

The three residual risks are the most consequential lines in the whole doc set for a rewrite: there is **no customer entity**. "A customer" is an import row in `crm_data_imports`, identified by that row's surrogate `id`, and customers are stitched together at query time by phone matching (`search_orders_by_phones`, `fetch_customer_followup`'s `(phone1 = %s or phone2 = %s)` clauses, `normalize_phone`). Deep links are `?customer_id=<crm_data_imports.id>` (`pages/customer_detail.py:80-88`).

### 9.3 `docs/UAT_RESULT_TEMPLATE.md` — the formal UAT result form

Scope (`:5-9`): latest deployed environment; CRM data read/write from Neon; Supabase Auth/Login only; migration/schema change out of scope this round. Header fields: test date, UAT coordinator, environment/URL.

**§1 UAT Summary — 12 named test cases** (Tester / Result PASS-FAIL / Notes). This is the crispest parity list in the repo:
1. EDITOR: Customers Owner Assignment
2. EDITOR: Export Customers XLSX
3. EDITOR: Update Customer URL
4. STAFF: Dashboard Own Data Only
5. STAFF: Follow-up Own Data Only
6. STAFF: Customer Detail Own Record
7. STAFF: Customer Detail Other Record **Denied**
8. Manual Order: STAFF Owner **Lock**
9. Manual Order: Multi SKU
10. Follow-up: STAFF Save Own Follow-up
11. Follow-up: STAFF **Cannot** Edit Other Owner
12. Supabase Auth Login / Logout

**§2 Defect Log** — ID / Severity / Screen / Steps / Expected / Actual / Owner / Status, pre-seeded UAT-001…003. Severity guideline: **High** = wrong permissions, seeing another person's data, cannot save, or a broken core workflow; **Medium** = usable but affects correctness/speed/confusion; **Low** = UI/copy/cosmetics not affecting core workflow. *Note that "sees another person's data" is classified High — data isolation is the top-severity class.*

**§3 Blocking Issues** — Issue / Impact / **Required Fix Before SQL Normalize? (Yes/No)** / Notes. This ties UAT directly to the §3.5 staff-code normalization gate.

**§4 Final Sign-off** — one of `Ready for SQL Normalize` / `Not Ready` / `Blocking Issues Found`, plus 8 checks:
- EDITOR can see and manage data per rights
- STAFF sees only data matching **its own `staff_code`** on restricted pages
- **Customers list can still be used to check all customers per the latest requirement** ← a deliberate carve-out: the Customers page is *not* fully scoped, unlike Dashboard/Follow-up/Detail (this is exactly why `build_customer_where` has the `enforce_user_scope` flag)
- Important actions — **Export, Update URL, Assign Owner — restricted to EDITOR only**
- Manual Order records the correct `owner` **and** `staff_code`
- Follow-up saves only for records the user has rights to
- **No Supabase Database/REST call in the CRM data flow**
- **No High-severity defect outstanding**

Plus approver name, approval date, and a free-text block for pre-normalization conditions.

### 9.4 Consolidated parity invariants (the union of all four docs)

1. **`staff_code` is the sole permission key.** Never fall back to `owner` / `staff_name` / `owner_alias` for authorization. (`UAT_STAFF_CODE_PERMISSION.md:61, 89`)
2. **Fail closed.** A non-manager with no `staff_code` sees nothing (`1 = 0`), never everything.
3. **Manager = ADMIN or EDITOR for row scope**, but the *action* matrix is finer-grained and ADMIN is deliberately weaker than EDITOR for Export / Assign Owner / Delete Order / System pages / Follow-up.
4. **`owner` and `staff_code` must always be written together.** If one changes without the other, stop and investigate `assign_owner_to_order_record`.
5. **Direct-URL access to another owner's Customer Detail must be denied** — not merely hidden from lists.
6. **Manual Order locks owner/staff_code to the logged-in staff member**; owner selection is EDITOR-only.
7. **`FOLLOW` sale_type is excluded from revenue**; only `NEW_ORDER` and `UPSELL` count toward sales / count / AOV.
8. **Multi-SKU merge rule:** same SKU **and** same product name → merge qty; same SKU, different name → separate lines. `qty > 0`.
9. **Product master: never hard-delete.** Archive (`archived_at = now()`, `is_active = false`) and restore (nulls the archive fields but **leaves `is_active = false`**). Delete-readiness reports `blocked_used` / `tentative_no_usage` / `unsafe_unknown` — never "safe".
10. **Export XLSX headers must match the import template**; blank for missing; 1 row per order (with a documented fallback when order_id is absent).
11. **Forms: reset on success, preserve on failure.**
12. **Server-side pagination and filtering; explicit column lists; never log the DSN.**
13. **No Supabase beyond `/auth/v1/*`**; no service-role key client-side.
14. **Two priority vocabularies coexist** — canonical `Super VIP / VIP / Premium / Economy / NEW / Dismiss`, and legacy `urgent|ด่วนมาก → Super VIP`, `high|สูง → VIP`, `normal|ปกติ → NEW`, `low|ต่ำ → Economy`, default `NEW`. Filters must match both.
15. **Dedupe key is `sha256("order_id|digits(phone1)|digits(phone2)|tracking_no")`** with exact hashes pinned by test — including the `+66`-vs-`0` divergence.
16. **Staff-code normalization is NOT approved.** `JEEB` (3,087 rows) has no confirmed email; 730 rows must keep `owner`/`staff_code` NULL and display "ยังไม่มอบหมาย"; canonical codes must be uppercase ASCII; any update requires backup, normalized-exact match only, before/after count comparison, and a rollback-by-`id` plan.