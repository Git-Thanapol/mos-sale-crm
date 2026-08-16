# Data-Layer Map — `crm_streamlit` (Streamlit + Neon PostgreSQL)

All paths absolute. Everything in `public` schema. **There is no ORM; all access is raw SQL via `psycopg` 3 with `dict_row`.**

Source-of-truth files for DDL:

| File | Role |
|---|---|
| `C:\Laptop files\MOS\MOS_CRM_202607\crm_streamlit\neon_utils.py` lines 73–272 (`CRM_DATA_IMPORTS_DDL`) | **Runtime schema guard** — executed on almost every request path via `ensure_crm_data_imports_schema()`. This is the real authority. |
| `...\crm_streamlit\neon\migrations\*.sql` | Ordered migrations, partly overlapping / conflicting with the runtime DDL |
| `...\crm_streamlit\neon\manual_sql\*.sql` | Marked "DO NOT RUN UNTIL APPROVED" — indexes + a one-off staff_code normalization. May or may not be in prod. |
| `...\crm_streamlit\supabase\migrations\*.sql` | **Legacy / dead.** Different schema entirely (uuid PKs, `care_staff`, `dedupe_key not null unique`, RLS policies, `import_batches`/`import_staging` pipeline, `crm_customers`). Supabase is now used **only for Auth** (`DATABASE_CODE_OVERVIEW.md:10`). Do not port these. |

---

# 1. Full schema of every table

## 1.1 `public.crm_data_imports` — the god table

This single table stores lead + customer + order-header + order-line + address + attribution. It is the source for Customers, Follow-up, Dashboard KPIs, Sales Report, Team Sales, Customer 360, and Export.

Base create, `neon\migrations\202605290001_create_crm_data_imports.sql:1-31`:

```sql
create table if not exists public.crm_data_imports (
  id bigserial primary key,
  import_batch_id uuid not null,
  source_file_name text,
  sheet_name text,
  row_number integer,
  uploaded_by text,
  uploaded_at timestamptz not null default now(),
  raw_data jsonb not null default '{}'::jsonb,
  order_id text,
  url text,
  customer_name text,
  phone1 text,
  phone2 text,
  product_name text,
  sku text,
  order_date date,
  province text,
  city text,
  postal_code text,
  tracking_no text,
  carrier text,
  order_status text,
  total_amount numeric,
  owner text,
  source_type text,
  import_status text not null default 'valid',
  validation_error text,
  dedupe_key text,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

Additive columns, in the order they arrive:

```sql
-- neon\migrations\202606020001_add_product_master_and_order_items.sql:8
alter table public.crm_data_imports
  add column if not exists quantity integer;

-- neon_utils.py:264-268  (runtime DDL)
alter table public.crm_data_imports
  add column if not exists staff_code text;
create index if not exists idx_crm_data_imports_staff_code
  on public.crm_data_imports (staff_code);

-- neon\migrations\202606060001_add_sales_report_fields.sql:5-8
alter table public.crm_data_imports
  add column if not exists sale_type text,
  add column if not exists amount numeric(12,2),
  add column if not exists address text;
```

**Type conflict on `quantity`:** the migration declares `integer`; the runtime DDL (`neon_utils.py:111`) declares `add column if not exists quantity numeric`. Whichever executed first wins, so the live type is ambiguous. Note the base migration file's DDL block and the in-code `CRM_DATA_IMPORTS_DDL` also differ: the in-code version's `create table` body omits `source_type` / `updated_by` (added by the following `alter`) and omits `staff_code` from the create.

### Effective column list (34 columns)

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigserial / bigint | NO | identity seq |
| `import_batch_id` | uuid | **NO** | — |
| `source_file_name` | text | yes | — |
| `sheet_name` | text | yes | — |
| `row_number` | integer | yes | — |
| `uploaded_by` | text | yes | — |
| `uploaded_at` | timestamptz | **NO** | `now()` |
| `raw_data` | jsonb | **NO** | `'{}'::jsonb` |
| `order_id` | text | yes | — |
| `url` | text | yes | — |
| `customer_name` | text | yes | — |
| `phone1` | text | yes | — |
| `phone2` | text | yes | — |
| `product_name` | text | yes | — |
| `sku` | text | yes | — |
| `order_date` | date | yes | — |
| `province` | text | yes | — |
| `city` | text | yes | — |
| `postal_code` | text | yes | — |
| `tracking_no` | text | yes | — |
| `carrier` | text | yes | — |
| `order_status` | text | yes | — |
| `total_amount` | numeric (unconstrained) | yes | — |
| `owner` | text | yes | — |
| `staff_code` | text | yes | — |
| `source_type` | text | yes | — (app writes `'manual'`) |
| `import_status` | text | **NO** | `'valid'` (app writes `'valid'`/`'invalid'`) |
| `validation_error` | text | yes | — |
| `dedupe_key` | text | yes | — (**no unique constraint**; only written by Excel import, NULL for manual orders) |
| `updated_by` | text | yes | — |
| `created_at` | timestamptz | **NO** | `now()` |
| `updated_at` | timestamptz | **NO** | `now()` |
| `quantity` | integer **or** numeric | yes | — |
| `sale_type` | text | yes | — (app values `NEW_ORDER` / `UPSELL` / `FOLLOW`) |
| `amount` | numeric(12,2) | yes | — |
| `address` | text | yes | — |

**PK:** `id`. **Unique constraints:** none besides PK. **Foreign keys:** none (in or out). **Check constraints:** none.

### Indexes

From `202605290001_create_crm_data_imports.sql:38-72` + `neon_utils.py:113-142`:

```sql
idx_crm_data_imports_phone1            (phone1)
idx_crm_data_imports_phone2            (phone2)
idx_crm_data_imports_sku               (sku)
idx_crm_data_imports_order_id          (order_id)
idx_crm_data_imports_order_date        (order_date)
idx_crm_data_imports_uploaded_at       (uploaded_at desc)
idx_crm_data_imports_import_batch_id   (import_batch_id)
idx_crm_data_imports_owner             (owner)
idx_crm_data_imports_tracking_no       (tracking_no)
idx_crm_data_imports_staff_code        (staff_code)
```

The important expression index (the "phone key" — memorize this, it drives every dedup query):

```sql
create index if not exists idx_crm_data_imports_customer_phone_latest
  on public.crm_data_imports (
    (
      case
        when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
        else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
      end
    ),
    order_date desc,
    uploaded_at desc
  )
  where import_status = 'valid';
```

From `202606060001_add_sales_report_fields.sql:10-14`:

```sql
idx_crm_data_imports_created_staff_sale  (created_at, staff_code, sale_type)
idx_crm_data_imports_owner_created_sale  (owner, created_at, sale_type)
```

From `neon\manual_sql\202606_followup_order_entry_performance_indexes.sql` (**CREATE INDEX CONCURRENTLY, approval-gated, may not exist in prod**):

```sql
idx_crm_data_imports_valid_staff_order  (staff_code, order_date desc, uploaded_at desc, id desc) where import_status='valid'
idx_crm_data_imports_valid_phone1       (phone1) where import_status='valid' and phone1 is not null and phone1 <> ''
idx_crm_data_imports_valid_phone2       (phone2) where import_status='valid' and phone2 is not null and phone2 <> ''
idx_crm_data_imports_valid_order_id     (order_id, uploaded_at desc, id desc) where import_status='valid' and order_id is not null and order_id <> ''
idx_crm_data_imports_valid_sku          (sku, uploaded_at desc, id desc) where import_status='valid' and sku is not null and sku <> ''
```

## 1.2 `public.crm_lead_followups`

Two generations of columns coexist — the old `follow_up_*` set and the new `followup_*`/`next_followup_date` set. **Every read query coalesces both.**

Base (`202605290001_create_crm_data_imports.sql:83-100`) then the runtime DDL (`neon_utils.py:154-211`) which is the effective definition:

```sql
create table if not exists public.crm_lead_followups (
  id bigserial,
  customer_key text primary key,
  crm_data_import_id bigint,
  order_id text,
  customer_id text,
  customer_name text,
  phone_key text,
  phone1 text,
  phone2 text,
  product_group text,
  product_name text,
  sku text,
  staff_code text,
  owner text,
  lead_status text,
  followup_status text,
  next_followup_date date,
  followup_note text,
  priority text,
  updated_by text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table public.crm_lead_followups
  add column if not exists id bigserial,
  add column if not exists crm_data_import_id bigint,
  add column if not exists order_id text,
  add column if not exists product_name text,
  add column if not exists sku text,
  add column if not exists staff_code text,
  add column if not exists owner text,
  add column if not exists followup_status text,
  add column if not exists next_followup_date date,
  add column if not exists followup_note text;

alter table public.crm_lead_followups
  add column if not exists follow_up_status text,
  add column if not exists follow_up_date date,
  add column if not exists follow_up_note text;

update public.crm_lead_followups
set followup_status = coalesce(nullif(followup_status, ''), follow_up_status),
    next_followup_date = coalesce(next_followup_date, follow_up_date),
    followup_note = coalesce(nullif(followup_note, ''), follow_up_note)
where follow_up_status is not null
   or follow_up_date is not null
   or follow_up_note is not null;
```

Note: `id` is `bigserial` but **NOT** the primary key. **PK is `customer_key text`.** `id` has no unique constraint. There is **no FK** from `crm_data_import_id` → `crm_data_imports.id`.

Indexes (`neon_utils.py:204-211`):

```sql
idx_crm_lead_followups_phone_key    (phone_key)
idx_crm_lead_followups_updated_at   (updated_at desc)
idx_crm_lead_followups_staff_next   (staff_code, next_followup_date, priority, updated_at desc)
idx_crm_lead_followups_status       (lead_status, followup_status, priority)
```

Plus, approval-gated in `manual_sql`:
```sql
idx_crm_lead_followups_status_date          (next_followup_date, priority, followup_status, updated_at desc)
idx_crm_lead_followups_customer_key_updated (customer_key, updated_at desc)
```

**No CHECK constraints in Neon.** (The dead Supabase version at `supabase\migrations\202605270001_create_crm_lead_followups.sql:22-30` did have `lead_status in ('new','contacted','interested','follow_up','won','lost','dormant')`, `follow_up_status in ('none','scheduled','done','missed')`, `priority in ('normal','high','urgent')`. Those checks are gone.)

Enumerations now live in Python only, `neon_utils.py:31-50`:

```python
FOLLOWUP_PRIORITY_OPTIONS = ("Super VIP", "VIP", "Premium", "Economy", "NEW", "Dismiss")
DEFAULT_FOLLOWUP_PRIORITY = "NEW"
LEGACY_FOLLOWUP_PRIORITY_MAP = {"urgent":"Super VIP","ด่วนมาก":"Super VIP","high":"VIP","สูง":"VIP",
                                "normal":"NEW","ปกติ":"NEW","low":"Economy","ต่ำ":"Economy"}
```
`lead_status` values seen in SQL: `'new'` (default via coalesce), `'interested'`, `'won'`. `followup_status`: `'none'`, `'done'`, plus numeric round markers `'0'`,`'1'`,`'2'`,… and `'RESET'` written by `pages\customers.py:446-481`.

### customer_key is written in two incompatible formats

- `pages\customer_detail.py:266` → `f"customer_id:{customer_id}"`
- `customer360.py:965-969` (`customer_detail_key`) → `f"customer_id:{value}"`
- `pages\followup.py:422` and `pages\9_ติดตามลูกค้า.py:299` → passes through `row["customer_key"]`, which `fetch_followup_page` produced as `concat('customer_id:', d.id::text)`
- **`pages\customers.py:458` → `"customer_key": phone_key or clean(row.get("id"))`** — i.e. a bare phone number like `0812345678`

`fetch_followup_page` joins on `l.customer_key = concat('customer_id:', d.id::text)`, so follow-up markers written from the Customers page are invisible to the Follow-up page. `fetch_customer_page` compensates by joining on phone/`crm_data_import_id` instead of `customer_key`. This is why the two pages use structurally different joins.

## 1.3 `public.crm_user_roles`

`neon_utils.py:230-241` (runtime, effective) + `202606020002_add_owner_alias_to_crm_user_roles.sql`:

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

alter table public.crm_user_roles
  add column if not exists staff_code text;

create index if not exists idx_crm_user_roles_staff_code
  on public.crm_user_roles (staff_code);

-- 202606020002
alter table public.crm_user_roles
  add column if not exists owner_alias text;
create index if not exists idx_crm_user_roles_owner_alias
  on public.crm_user_roles (owner_alias);
```

**PK:** `email` (text). No FK. **No CHECK on `role`** in the Neon version (the dead Supabase one had `check (role in ('CEO','EDITOR','พนักงาน','ทั่วไป'))`). Role values actually used, `permissions.py:1-12` and `pages\users.py:19`:

```python
ROLE_ADMIN="ADMIN"; ROLE_EDITOR="EDITOR"; ROLE_STAFF="พนักงาน"; ROLE_VIEWER="ทั่วไป"
ROLE_OPTIONS = ["EDITOR","ADMIN","พนักงาน","TELESELL","STAFF","USER","ทั่วไป"]
```

`owner_alias` existence is probed at runtime every read (`table_has_column`, `neon_utils.py:2637`), because code must tolerate the pre-migration schema.

## 1.4 `public.crm_staff_options`

`neon_utils.py:243-262`:

```sql
create table if not exists public.crm_staff_options (
  id bigserial primary key,
  staff_code text,
  staff_name text not null unique,
  is_active boolean not null default true,
  sort_order integer not null default 0,
  created_by text,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_crm_staff_options_active_sort
  on public.crm_staff_options (is_active, sort_order, staff_name);

alter table public.crm_staff_options
  add column if not exists staff_code text;
```

**PK** `id`; **UNIQUE** `staff_name` (used as the ON CONFLICT target). Note `staff_code` is **not** unique. No FK to `crm_user_roles`.

## 1.5 `public.crm_product_options`

Base `202605290001_create_crm_data_imports.sql:104-118`, then three mutations:

```sql
create table if not exists public.crm_product_options (
  id bigserial primary key,
  sku text,
  product_group text not null,
  product_name text not null,
  sort_order integer not null default 0,
  is_active boolean not null default true,
  created_by text,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (product_group, product_name)     -- ORIGINAL, later dropped
);
create index if not exists idx_crm_product_options_active_sort
  on public.crm_product_options (is_active, sku, sort_order, product_group, product_name);
```

```sql
-- 202606020001_add_product_master_and_order_items.sql:1-6  → SHADOW COLUMN
alter table public.crm_product_options
  add column if not exists active boolean not null default true;
update public.crm_product_options
set active = is_active
where active is distinct from is_active;

create index if not exists idx_crm_product_options_active_sku
  on public.crm_product_options (active, sku, product_name);
```

```sql
-- 202606030001_update_product_options_unique_sku_name_group.sql:7-21
alter table public.crm_product_options
  drop constraint if exists crm_product_options_product_group_product_name_key;

do $$
begin
  alter table public.crm_product_options
    add constraint crm_product_options_sku_group_name_key
    unique (sku, product_group, product_name);
exception
  when duplicate_object then null;
end $$;

create index if not exists idx_crm_product_options_sku_group_name
  on public.crm_product_options (sku, product_group, product_name);
```

```sql
-- 202607020001_add_product_archive_columns.sql:3-6
ALTER TABLE public.crm_product_options
  ADD COLUMN IF NOT EXISTS archived_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS archived_by text NULL,
  ADD COLUMN IF NOT EXISTS archive_reason text NULL;
```

Effective columns: `id, sku, product_group, product_name, sort_order, is_active, active, created_by, updated_by, created_at, updated_at, archived_at, archived_by, archive_reason`.

Gotchas for Django modelling:
- **`is_active` and `active` are two separate booleans**, both `not null default true`. All application code reads/writes **`is_active`** only. `active` is written once by the migration and then goes stale forever. Index `idx_crm_product_options_active_sku` indexes the dead column.
- The `unique (sku, product_group, product_name)` constraint is **not enforced when `sku IS NULL`** (Postgres NULL semantics). `upsert_product_options` (`crm_data\products.py:319-329`) works around it with `coalesce(sku,'') = coalesce(%s,'')`.
- `DATABASE_CODE_OVERVIEW.md:79,206` claims there is an `image_url` column. **There is no `image_url` anywhere in DDL or code** — the doc is stale.
- Archiving is soft-delete: `archive_products` sets `archived_at=now(), is_active=false`.

## 1.6 `public.crm_orders` (order header — only written by manual-order path)

`202606020001_add_product_master_and_order_items.sql:11-24`:

```sql
create table if not exists public.crm_orders (
  id bigserial primary key,
  order_id text not null,
  customer_name text,
  phone1 text,
  phone2 text,
  url text,
  owner text,
  staff_code text,
  source_type text not null default 'manual',
  created_by text,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists ux_crm_orders_order_phone
  on public.crm_orders (
    order_id,
    (coalesce(phone1, '')),
    (coalesce(phone2, ''))
  );

create index if not exists idx_crm_orders_order_id on public.crm_orders (order_id);
create index if not exists idx_crm_orders_phone1   on public.crm_orders (phone1);
create index if not exists idx_crm_orders_phone2   on public.crm_orders (phone2);
```

**Not** created by the runtime DDL — existence is probed via `neon_table_exists("crm_orders")` (`neon_utils.py:967`). No FK to `crm_data_imports`.

## 1.7 `public.crm_order_items`

`202606020001_add_product_master_and_order_items.sql:26-40`:

```sql
create table if not exists public.crm_order_items (
  id bigserial primary key,
  crm_order_id bigint references public.crm_orders(id) on delete cascade,
  crm_data_import_id bigint,
  order_id text not null,
  sku text not null,
  product_name text not null,
  qty integer not null default 1 check (qty > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists ux_crm_order_items_order_sku
  on public.crm_order_items (crm_order_id, sku);

create index if not exists idx_crm_order_items_order_id on public.crm_order_items (order_id);
create index if not exists idx_crm_order_items_sku      on public.crm_order_items (sku);
```

**The only real FK in the whole schema:** `crm_order_id → crm_orders(id) ON DELETE CASCADE`, and it is **nullable**. `crm_data_import_id` is a dangling bigint with no FK. **`crm_order_items` has no amount/price column** — money lives only in `crm_data_imports.amount` / `total_amount`. `ux_crm_order_items_order_sku` on `(crm_order_id, sku)` means **one line per SKU per order** — re-adding the same SKU with a different qty overwrites.

## 1.8 `public.crm_owner_assignments` (phone → owner sticky map)

`202605290001_create_crm_data_imports.sql:74-81` / `neon_utils.py:144-152`:

```sql
create table if not exists public.crm_owner_assignments (
  phone_key text primary key,
  owner text not null,
  updated_by text,
  updated_at timestamptz not null default now()
);

create index if not exists idx_crm_owner_assignments_owner
  on public.crm_owner_assignments (owner);
```

**PK is the normalized phone string.** Note it stores only `owner` (display name), **not** `staff_code`.

## 1.9 `public.crm_user_team_assignments` (effective-dated team membership)

`neon\migrations\202607010001_create_crm_user_team_assignments.sql:8-43`. Not in runtime DDL; queries against it are wrapped in try/except.

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS public.crm_user_team_assignments (
  id bigserial PRIMARY KEY,
  user_email text NOT NULL,
  team_code text NOT NULL,
  team_name text GENERATED ALWAYS AS (
    CASE team_code
      WHEN 'CRM_TEAM' THEN 'CRM Team'
      WHEN 'UPSELL_TEAM' THEN 'Upsell Team'
    END
  ) STORED,
  effective_from timestamptz NOT NULL,
  effective_to timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text,
  CONSTRAINT chk_team_code
    CHECK (team_code IN ('CRM_TEAM', 'UPSELL_TEAM')),
  CONSTRAINT chk_normalized_user_email
    CHECK (user_email <> '' AND user_email = lower(btrim(user_email))),
  CONSTRAINT chk_assignment_period
    CHECK (effective_to IS NULL OR effective_to > effective_from),
  CONSTRAINT ex_assignment_period
    EXCLUDE USING gist (
      user_email WITH =,
      tstzrange(effective_from, effective_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_team_assignment_current_user
  ON public.crm_user_team_assignments (user_email)
  WHERE effective_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_team_assignment_user_period
  ON public.crm_user_team_assignments (user_email, effective_from DESC);

CREATE INDEX IF NOT EXISTS idx_team_assignment_team_period
  ON public.crm_user_team_assignments (team_code, effective_from, effective_to);
```

Django cannot express the GiST exclusion constraint or the STORED generated column via plain model fields — needs `ExclusionConstraint` + `GeneratedField`/raw SQL. No FK to `crm_user_roles.email` despite joining on it.

## 1.10 Backup tables (from `neon\manual_sql\202606_staff_code_normalization_plan.sql:47-70`)

`public.crm_data_imports_staff_backup_202606` (`id, owner, staff_code, updated_at`) and `public.crm_user_roles_staff_backup_202606` (`email, role, staff_code, staff_name, owner_alias, is_active, updated_at`). May exist in prod; not referenced by app code.

## 1.11 Canonical staff_code values

From the normalization plan (`202606_staff_code_normalization_plan.sql:28-38`) — these are the live `staff_code` domain: `SAIFON, TAEW, YING, NOONA, AU, LEK, CREAM, KO`, mapped 1:1 to Thai owner display names. Post-migration assertion block expects the entire table to be covered by those 8 codes (`lines 425-452`), with row counts `SAIFON=6502, TAEW=4669, YING=3100, NOONA=3087, AU=730, LEK=9, CREAM=1, KO=1` → **~18,099 rows total in `crm_data_imports`** as of 2026-06.

---

# 2. Read query functions

## 2.1 `neon_utils.py`

### Schema/metadata probes
| Loc | Signature | SQL | Returns |
|---|---|---|---|
| `:344` | `ensure_crm_data_imports_schema() -> bool` — `@st.cache_resource` | executes the whole 200-line `CRM_DATA_IMPORTS_DDL` as one `cur.execute` + `commit` | `True` |
| `:366` | `neon_table_exists(table_name) -> bool` — `@st.cache_data(ttl=300)` | `select exists (select 1 from information_schema.tables where table_schema='public' and table_name=%s)` | bool |
| `:386` | `neon_column_exists(table_name, column_name) -> bool` — `@st.cache_data(ttl=300)` | same against `information_schema.columns` | bool |
| `:2637` | `table_has_column(cur, table, column) -> bool` | `select 1 from information_schema.columns where table_schema='public' and table_name=%s and column_name=%s limit 1` — **uncached, uses caller's cursor** | bool |

### `fetch_customer_page` — `neon_utils.py:1492-1576`

```python
def fetch_customer_page(filters: dict[str,str], page_size: int, page: int,
                        user: dict | None = None,
                        enforce_user_scope: bool = True) -> tuple[list[dict], int]
```

Builds `source_sql` (a derived table `keyed` that computes `phone_key`), then runs **two** queries against it. Returns `(list[dict], total_int)`.

```sql
      from (
        select
          id, customer_name, owner, staff_code, order_id, url, product_name,
          phone1, phone2, sku, province, city, postal_code, tracking_no, carrier,
          order_status, total_amount, order_date, raw_data, uploaded_at, updated_at,
          case
            when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
            else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
          end as phone_key
        from public.crm_data_imports d
        {where}
      ) keyed
```

Query 1 (count):
```sql
select count(distinct phone_key) as total  {source_sql}
```

Query 2 (page) — **the nastiest read in the app**:
```sql
with ranked as (
  select
    keyed.*,
    row_number() over (
      partition by phone_key
      order by order_date desc nulls last, uploaded_at desc, id desc
    ) as rn
  {source_sql}
)
select
  {select_cols},
  coalesce(latest_followup.followup_status, '0') as followup_status
from ranked
left join lateral (
  select coalesce(l.followup_status, l.follow_up_status) as followup_status
  from public.crm_lead_followups l
  where l.crm_data_import_id = ranked.id
     or (
       nullif(l.phone1, '') is not null
       and (l.phone1 = ranked.phone1 or l.phone1 = ranked.phone2)
     )
     or (
       nullif(l.phone2, '') is not null
       and (l.phone2 = ranked.phone1 or l.phone2 = ranked.phone2)
     )
  order by l.updated_at desc nulls last, l.created_at desc nulls last
  limit 1
) latest_followup on true
where rn = 1
order by order_date desc nulls last, uploaded_at desc, id desc
limit %s offset %s
```

`{select_cols}` = `customer_select_columns()` (`:1761-1786`), which aliases heavily:
```sql
      id::text as id,
      id::text as customer_id,
      customer_name as customer,
      owner as sales_staff,
      staff_code,
      order_id,
      url as product_url,
      url as channel_url,
      product_name,
      phone1, phone2, sku, province, city,
      postal_code as postcode,
      tracking_no, carrier, order_status, total_amount, order_date,
      order_date::text as order_date_text,
      raw_data, updated_at
```

`build_customer_where` — `neon_utils.py:1731-1758`:
```python
clauses = ["d.import_status = 'valid'"]
if enforce_user_scope:  clauses.append(_followup_staff_scope(user or {}, "d"))
if staff and staff != "ทั้งหมด":  clauses.append("d.owner = %s")
if keyword: clauses.append(
    "("
    "d.customer_name ilike %s or d.phone1 ilike %s or d.phone2 ilike %s or "
    "d.postal_code ilike %s or d.tracking_no ilike %s or d.sku ilike %s or "
    "d.order_id ilike %s or d.raw_data->>'เลขคำสั่งซื้อ' ilike %s"
    ")")
```
8 `ilike '%kw%'` params, all leading-wildcard, including a JSONB text extraction on a Thai key.

Callers: `pages\customers.py:83` passes `enforce_user_scope=False` (relies on `_followup_staff_scope` never running, so **the Customers page shows every staff's rows to everyone who can reach it**). `customer360.py:446` calls it with no `user`, so scope=True with `user={}` → `_followup_staff_scope` returns `"1 = 0"` → always zero rows. (`customer360.py` is orphaned — no page imports it.)

### `fetch_followup_page` — `neon_utils.py:2448-2613`

```python
def fetch_followup_page(filters: dict[str,str], user: dict,
                        page_size: int, page: int) -> tuple[list[dict], int]
```

`source_sql` (`:2453-2479`):
```sql
      from (
        select
          id, customer_name, phone1, phone2, order_id, sku, product_name, url,
          owner, staff_code, order_date, uploaded_at, updated_at, import_status,
          case
            when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
            else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
          end as phone_key
        from public.crm_data_imports
        where import_status = 'valid'
      ) d
      left join public.crm_lead_followups l
        on l.customer_key = concat('customer_id:', d.id::text)
```

Count query (`:2482-2509`) — note `crm_lead_followups` is joined **twice**, once inside the CTE (via `source_sql`) and once outside:
```sql
              with ranked as (
                select
                       d.id, d.phone_key, d.customer_name, d.phone1, d.phone2,
                       d.order_id, d.sku, d.product_name, d.url, d.owner,
                       d.staff_code, d.import_status,
                       row_number() over (
                         partition by d.phone_key
                         order by d.order_date desc nulls last, d.uploaded_at desc, d.id desc
                       ) as rn
                {source_sql}
              )
              select count(*) as total
              from ranked d
              left join public.crm_lead_followups l
                on l.customer_key = concat('customer_id:', d.id::text)
              {where}
                and d.rn = 1
```

Page query (`:2512-2609`) — the inner derived table is **re-inlined without** the followup join, so count and page have different join shapes:
```sql
with ranked as (
  select
    d.id, d.phone_key, d.customer_name, d.phone1, d.phone2, d.order_id, d.sku,
    d.product_name, d.url, d.owner, d.staff_code, d.import_status, d.order_date,
    d.updated_at as customer_updated_at,
    row_number() over (
      partition by d.phone_key
      order by d.order_date desc nulls last, d.uploaded_at desc, d.id desc
    ) as rn
  from (
    select id, customer_name, phone1, phone2, order_id, sku, product_name, url,
           owner, staff_code, order_date, uploaded_at, updated_at, import_status,
           case
             when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
             else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
           end as phone_key
    from public.crm_data_imports
    where import_status = 'valid'
  ) d
)
select
  d.id::text as crm_data_import_id,
  concat('customer_id:', d.id::text) as customer_key,
  d.customer_name, d.phone1, d.phone2, d.order_id, d.sku, d.product_name, d.url,
  d.owner, d.staff_code,
  coalesce(l.lead_status, 'new') as lead_status,
  coalesce(l.followup_status, l.follow_up_status, 'none') as followup_status,
  coalesce(l.priority, 'NEW') as priority,
  coalesce(l.next_followup_date, l.follow_up_date)::text as next_followup_date,
  coalesce(l.followup_note, l.follow_up_note, '') as followup_note,
  l.updated_by,
  coalesce(l.updated_at, d.customer_updated_at) as updated_at
from ranked d
left join public.crm_lead_followups l
  on l.customer_key = concat('customer_id:', d.id::text)
{where}
  and d.rn = 1
order by
  coalesce(l.next_followup_date, l.follow_up_date) asc nulls last,
  case coalesce(l.priority, 'NEW')
    when 'Super VIP' then 6
    when 'urgent' then 6
    when 'เธ”เนเธงเธเธกเธฒเธ' then 6
    when 'VIP' then 5
    when 'high' then 5
    when 'เธชเธนเธ' then 5
    when 'Premium' then 4
    when 'Economy' then 3
    when 'low' then 3
    when 'เธ•เนเธณ' then 3
    when 'NEW' then 2
    when 'normal' then 2
    when 'เธเธเธ•เธด' then 2
    when 'Dismiss' then 0
    else 1
  end desc,
  case coalesce(l.followup_status, l.follow_up_status, 'none')
    when 'done' then 1
    else 0
  end asc,
  coalesce(l.updated_at, d.customer_updated_at) desc
limit %s offset %s
```

**Bug worth carrying into the rewrite as a known-broken behaviour:** the four Thai literals in that `CASE` (`neon_utils.py:2587, 2590, 2594, 2597`) are mojibake — UTF-8 bytes of `ด่วนมาก` / `สูง` / `ต่ำ` / `ปกติ` re-decoded as CP874. They can never equal a real stored value, so legacy Thai priorities fall through to `else 1`. The `WHERE` side (`FOLLOWUP_PRIORITY_FILTER_ALIASES`, `:43-50`) uses proper `\u` escapes and *does* match them — so filtering and sorting disagree. Same mojibake at `:934` in an error message.

Post-processing: `:2611-2612` normalizes `priority` in Python via `normalize_followup_priority`.

`build_followup_where` — `neon_utils.py:2344-2405`:
```python
clauses = ["d.import_status = 'valid'"]
scope_clause, scope_params = _followup_staff_scope(user, "d")   # see §4

phone = normalize_phone(filters.get("phone"))
if phone:
    clauses.append("(d.phone1 like %s or d.phone2 like %s)")   # '%<digits>%'
    return ...   # early return: phone search ignores all other filters

if keyword:
    clauses.append("(d.customer_name ilike %s or d.order_id ilike %s or d.sku ilike %s or d.product_name ilike %s)")
if owner and owner != "ทั้งหมด":
    clauses.append("d.owner = %s")
if lead_status ...:
    clauses.append("coalesce(l.lead_status, 'new') = %s")
if followup_status ...:
    clauses.append("coalesce(l.followup_status, l.follow_up_status, 'none') = %s")
if priority ...:
    # NEW also matches null/blank
    clauses.append("(l.priority = any(%s) or l.priority is null or btrim(l.priority) = '')")
    #  or, non-NEW:
    clauses.append("l.priority = any(%s)")
if product ...:
    clauses.append("(d.product_name ilike %s or d.sku ilike %s)")
if date_start and date_end:
    clauses.append('coalesce(l.next_followup_date, l.follow_up_date) between %s::date and %s::date')
elif date_start:
    clauses.append('coalesce(l.next_followup_date, l.follow_up_date) = %s::date')
```

### `fetch_followup_filter_options` — `neon_utils.py:2408-2445`, `@st.cache_data(ttl=900)`
Two queries, both scoped by `_followup_staff_scope`. **`user: dict` is the cache key — an unhashable dict arg to `st.cache_data`; Streamlit hashes it by content, so any change to the user dict busts the cache.**
```sql
select distinct d.owner from public.crm_data_imports d {where} and d.owner is not null and d.owner <> '' order by d.owner limit 500
```
```sql
select distinct concat_ws(' ', nullif(d.sku, ''), nullif(d.product_name, '')) as product
from public.crm_data_imports d {where} and (d.sku is not null or d.product_name is not null)
order by product limit 1000
```
Returns `{"owners": [...], "products": [...]}`.

### `fetch_customer_export_rows` — `neon_utils.py:1579-1716`

```python
def fetch_customer_export_rows(filters, user=None, start_date=None, end_date=None,
                               latest_owner_only=False) -> list[dict]
```
- Calls `build_customer_where(filters, user, enforce_user_scope=False)` → **export bypasses row scoping entirely**; gated only by `can_export_customers` (EDITOR).
- Strips the leading `where ` via `where.replace("where ", "", 1)` (`:1588`) and re-concatenates with optional `d.created_at >= %s` / `< %s`, with Bangkok→UTC boundary conversion (`:1590-1594`).
- Column presence probed at runtime: `quantity_expr = "d.quantity" if has_quantity else "null::numeric as quantity"` etc. (`:1597-1602`).
- Two shapes. `latest_owner_only=True` → the `keyed`/`ranked` phone_key + `row_number()` + `where rn = 1` pattern (`:1608-1682`). Otherwise a flat select ordered `d.created_at desc nulls last, d.order_date desc nulls last, d.id desc` (`:1684-1715`).
- **No LIMIT on either branch** — full result set into memory.

### Customer 360 reads
| Loc | Function | SQL shape |
|---|---|---|
| `:1789` | `fetch_customer_by_id(customer_id) -> list[dict]` | `select {customer_select_columns()} from public.crm_data_imports where id = %s limit 1`. **No `import_status` filter.** |
| `:1806` | `fetch_customer_360_base(customer_id) -> list[dict]` | Byte-identical SQL to `fetch_customer_by_id`; only difference is a `clean()` guard on the arg. Pure duplication. |
| `:1826` | `fetch_customer_360_orders(phone1, phone2, limit=20) -> list[dict]` | see below |
| `:1884` | `fetch_customer_360_products(phone1, phone2, limit=50) -> list[dict]` | see below |

`fetch_customer_360_orders` (`:1847-1880`) — dynamically builds a quantity fallback across three sources:
```python
raw_qty_expr      = "case when nullif(raw_data->>'qty', '') ~ '^[0-9]+(\\.[0-9]+)?$' then (raw_data->>'qty')::numeric end"
raw_thai_qty_expr = "case when nullif(raw_data->>'จำนวน', '') ~ '^[0-9]+(\\.[0-9]+)?$' then (raw_data->>'จำนวน')::numeric end"
quantity_expr = f"coalesce(quantity, {raw_qty_expr}, {raw_thai_qty_expr})"   # or without `quantity` if column absent
```
```sql
select
  id::text as source_key, order_id, order_date::text as date_text, order_date,
  customer_name as customer, phone1, phone2, sku, product_name,
  owner as care_staff, staff_code,
  {quantity_expr} as quantity,
  total_amount as total_sales, amount, sale_type,
  carrier as shipping, tracking_no, order_status,
  url as channel_url, province, city, postal_code as postcode, updated_at
from public.crm_data_imports
where import_status = 'valid'
  and (phone1 = any(%s) or phone2 = any(%s))
order by order_date desc nulls last, uploaded_at desc nulls last, id desc
limit %s
```
Note `amount` and `sale_type` are referenced **unconditionally** here (unlike everywhere else, which probes first) → this query hard-fails if migration `202606060001` hasn't run.

`fetch_customer_360_products` (`:1893-1911`) — aggregate:
```sql
select
  coalesce(nullif(trim(sku), ''), '-') as sku,
  coalesce(nullif(trim(product_name), ''), '-') as product_name,
  count(*)::int as purchase_count,
  max(order_date)::text as latest_order_date,
  max(updated_at)::text as latest_updated_at
from public.crm_data_imports
where import_status = 'valid'
  and (phone1 = any(%s) or phone2 = any(%s))
group by
  coalesce(nullif(trim(sku), ''), '-'),
  coalesce(nullif(trim(product_name), ''), '-')
order by purchase_count desc, latest_order_date desc nulls last
limit %s
```

### `fetch_orders_by_phones` — `neon_utils.py:2125-2201`
`limit` defaults to **5000**. Builds a per-phone OR chain, **capped at the first 6 phones** (`:2146`):
```python
for phone in clean_phones[:6]:
    clauses.append("(phone1 = %s or phone2 = %s)")
```
```sql
select
  id::text as source_key, order_id, order_date::text as date_text, order_date,
  customer_name as customer, phone1, phone2,
  raw_data->>'ที่อยู่จัดส่ง' as address,
  raw_data->>'ตำบล' as subdistrict,
  city as district, province, postal_code as postcode,
  raw_data->>'ช่องทางขาย' as channel,
  raw_data->>'พนักงานเปิดบิล' as sales_staff,
  raw_data->>'พนักงานอัพเซลล์' as upsell_staff,
  owner as care_staff,
  {quantity_expr} as quantity,
  total_amount as total_sales,
  {amount_expr},          -- "amount"  |  "null::numeric as amount"
  {sale_type_expr},       -- "sale_type" | "null::text as sale_type"
  order_status,
  raw_data->>'วิธีการชำระ' as payment_method,
  carrier as shipping, tracking_no,
  url as channel_url, sku, product_name, raw_data, updated_at
from public.crm_data_imports
where import_status = 'valid'
  and ({' or '.join(clauses)})
order by order_date desc nulls last, uploaded_at desc, id desc
limit %s
```
Then in Python (`:2192-2200`) each row gets a synthetic single-element `row["products"]` list. **Six unmapped Thai JSONB keys are read here** (`ที่อยู่จัดส่ง`, `ตำบล`, `ช่องทางขาย`, `พนักงานเปิดบิล`, `พนักงานอัพเซลล์`, `วิธีการชำระ`) — these are Excel-header names never promoted to real columns. Django models will need to keep `raw_data` JSONB.

### Owner/phone lookup reads
| Loc | Function | SQL |
|---|---|---|
| `:730` | `fetch_existing_owner_rows_by_phones(phone1, phone2, limit=20)` | `select id::text, order_id, customer_name, phone1, phone2, owner, staff_code, updated_at from crm_data_imports where import_status='valid' and (phone1=any(%s) or phone2=any(%s)) and (nullif(trim(coalesce(owner,'')),'') is not null or nullif(trim(coalesce(staff_code,'')),'') is not null) order by updated_at desc nulls last, uploaded_at desc nulls last, id desc limit %s` |
| `:763` | `fetch_current_user_team_code(user_email)` | `select team_code from crm_user_team_assignments where lower(btrim(user_email))=lower(btrim(%s)) and effective_to is null order by effective_from desc limit 1` — **`lower(btrim(...))` on both sides defeats `ux_team_assignment_current_user`** |
| `:815` | `find_duplicate_valid_order_by_phones(...)` | see §5 |
| `:1358` | `fetch_existing_order_ids(cur, records) -> set[str]` | `select order_id from crm_data_imports where order_id = any(%s) and import_status='valid'` |
| `:1374` | `fetch_latest_customer_rows_by_phone(cur, records) -> dict[str, list[dict]]` | see §5 |

### Option / lookup reads
| Loc | Function | Cache | SQL |
|---|---|---|---|
| `:2071` | `fetch_filter_options() -> dict[str,list[str]]` | `ttl=900` | `select distinct owner from crm_data_imports where owner is not null and owner <> '' order by owner limit 1000`. **No `import_status` filter.** Returns `{"product_group": [], "sales_staff": owners, "owners": owners}` — `product_group` is hardcoded empty. |
| `:2089` | `search_terms(keyword) -> dict[str,tuple]` | none | `select customer_name, phone1, phone2 from crm_data_imports where customer_name ilike %s or phone1 ilike %s or phone2 ilike %s or postal_code ilike %s or tracking_no ilike %s or order_id ilike %s limit 80`, then dedups in Python and truncates to 30 phones / 20 names |
| `:2204` | `fetch_import_history(limit=50)` | `ttl=300` | `select import_batch_id::text, max(source_file_name), max(sheet_name), max(uploaded_by), max(uploaded_at), count(*) as total_rows, count(*) filter (where import_status='valid') as valid_rows, count(*) filter (where import_status='invalid') as invalid_rows from crm_data_imports group by import_batch_id order by max(uploaded_at) desc limit %s` — **full-table GROUP BY, no date bound** |
| `:2240` | `fetch_lead_followups(limit=100000)` | none | `select id::text, customer_key, crm_data_import_id::text, order_id, customer_id, customer_name, phone_key, phone1, phone2, product_group, product_name, sku, staff_code, owner, lead_status, coalesce(followup_status, follow_up_status) as follow_up_status, coalesce(next_followup_date, follow_up_date)::text as follow_up_date, coalesce(followup_note, follow_up_note) as follow_up_note, followup_status, next_followup_date::text, followup_note, priority, updated_by, updated_at, created_at from crm_lead_followups order by updated_at desc limit %s` — **the entire table, then dict-ified in Python (`customer360.py:536`)** |
| `:2688` | `fetch_crm_owner_options(limit=1000) -> list[str]` | `ttl=900` | `select distinct owner from crm_data_imports where import_status='valid' and owner is not null and owner <> '' order by owner limit %s` |
| `:2802` | `fetch_staff_options(active_only=False) -> list[dict]` | none | `select id::text, staff_code, staff_name, is_active, sort_order, updated_at from crm_staff_options {where is_active=true} order by sort_order asc, staff_name asc` |
| `:2824` | `fetch_owner_user_options(active_only=False) -> list[dict]` | `ttl=900` | see below |

`fetch_owner_user_options` (`:2831-2866`) — UNION of both staff sources with an md5 synthetic id:
```sql
with source_rows as (
  select staff_code, staff_name, is_active, 0 as sort_order, updated_at
  from public.crm_user_roles
  where staff_name is not null and staff_name <> '' {and is_active = true}
  union all
  select staff_code, staff_name, is_active, sort_order, updated_at
  from public.crm_staff_options
  where staff_name is not null and staff_name <> '' {and is_active = true}
)
select
  min(md5(coalesce(staff_code, '') || '|' || staff_name)) as id,
  staff_code, staff_name,
  bool_or(is_active) as is_active,
  min(sort_order) as sort_order,
  max(updated_at) as updated_at
from source_rows
group by staff_code, staff_name
order by min(sort_order) asc, staff_name asc
```

### User/role reads
| Loc | Function | SQL |
|---|---|---|
| `:2616` | `fetch_user_role_from_neon(email) -> dict\|None` | probes `owner_alias` via `table_has_column` then `select email, role, staff_code, staff_name, {owner_alias_expr}, is_active from crm_user_roles where email = %s and is_active = true limit 1`. Email lowercased in Python. |
| `:2652` | `fetch_user_role_record(email) -> dict\|None` | same + `created_at, updated_at`, **no `is_active` filter** |
| `:2672` | `fetch_user_roles() -> list[dict]` | `select email, role, staff_code, staff_name, {owner_alias_expr}, is_active, created_at, updated_at from crm_user_roles order by is_active desc, email asc` — no limit |
| `:2756` | `test_user_role_visibility(email, limit=10) -> dict` | Admin diagnostic. `select count(*) as total from crm_data_imports d {where_clause}` then a sample `select d.customer_name, d.phone1, d.phone2, d.order_id, d.sku, d.product_name, d.owner, d.staff_code, d.source_type, d.updated_at ... order by d.order_date desc nulls last, d.uploaded_at desc, d.id desc limit %s`, both scoped by `_followup_staff_scope`. Returns `{"user":…, "total":int, "samples":[…]}` |

### Dead code
`_normalized_text_sql(column)` (`:2330`) returns `regexp_replace(trim(coalesce({column}, '')), '\s+', ' ', 'g')` — **never called**. `owner_to_staff_code` (`:353`) — **never called** (comment says "Legacy/display-only helper. Never use this to write canonical staff_code.").

## 2.2 `crm_data\dashboard.py`

### `fetch_dashboard_kpis` — `:8` wrapper → `_fetch_dashboard_kpis` `:13-88`

Scope built inline (**duplicates `_followup_staff_scope` rather than calling it**, `:19-27`):
```python
if role not in {"ADMIN", "EDITOR"}:
    if staff_code: where.append("d.staff_code = %s")
    else:          where.append("1 = 0")
```

```sql
with latest_customers as (
  select distinct on (
    case
      when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
      else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
    end
  )
    id, phone1, phone2, updated_at
  from public.crm_data_imports d
  {where_sql}
  order by
    case
      when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
      else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
    end,
    order_date desc nulls last,
    uploaded_at desc,
    id desc
)
select
  (select count(*) from latest_customers) as total_customers,
  count(*) filter (where coalesce(l.next_followup_date, l.follow_up_date) = current_date and coalesce(l.followup_status, l.follow_up_status, 'none') <> 'done') as due_today,
  count(*) filter (where coalesce(l.next_followup_date, l.follow_up_date) < current_date and coalesce(l.followup_status, l.follow_up_status, 'none') <> 'done') as overdue,
  count(*) filter (where coalesce(l.lead_status, 'new') = 'interested') as interested,
  count(*) filter (where coalesce(l.lead_status, 'new') = 'won') as won,
  max(greatest(coalesce(l.updated_at, 'epoch'::timestamptz), coalesce(d.updated_at, 'epoch'::timestamptz))) as latest_update
from latest_customers d
left join lateral (
  select lead_status, followup_status, follow_up_status, next_followup_date, follow_up_date, updated_at
  from public.crm_lead_followups l
  where l.crm_data_import_id = d.id
     or (nullif(l.phone1, '') is not null and (l.phone1 = d.phone1 or l.phone1 = d.phone2))
     or (nullif(l.phone2, '') is not null and (l.phone2 = d.phone1 or l.phone2 = d.phone2))
  order by updated_at desc nulls last, id desc
  limit 1
) l on true
```
Returns `dict` of 5 ints + `latest_update` string. `current_date` is **server timezone**, not Asia/Bangkok — inconsistent with the sales report which explicitly converts.

### `crm_sales_report_ready` — `:91-97`
```python
return all(neon_column_exists("crm_data_imports", c) for c in ("sale_type","amount","address"))
```
Three cached `information_schema` round trips.

### `_can_view_all_sales` — `:100-103`  → `role in {"ADMIN","EDITOR"}`

### `_sales_report_where` — `:130-153`
```python
clauses = [
    "d.import_status = 'valid'",
    "d.created_at >= %s",
    "d.created_at < %s",
    "d.amount is not null",
    "coalesce(nullif(d.sale_type, ''), 'NEW_ORDER') in ('NEW_ORDER', 'UPSELL')",
]
if _can_view_all_sales(user):
    if owner and owner != "ทั้งหมด":  clauses.append("d.owner = %s")
else:
    if staff_code: clauses.append("d.staff_code = %s")
    else:          clauses.append("1 = 0")
```
Positional param contract: caller must prepend `[start_ts, end_ts]`. **Note `FOLLOW` sale_type rows are excluded from all sales reporting.**

### `fetch_sales_report_owner_options` — `:106-127`, `@st.cache_data(ttl=300)`
```sql
select distinct owner from public.crm_data_imports
where import_status = 'valid' and owner is not null and owner <> ''
  and amount is not null
  and coalesce(nullif(sale_type, ''), 'NEW_ORDER') in ('NEW_ORDER', 'UPSELL')
order by owner
```
No LIMIT. Returns `[]` for non-ADMIN/EDITOR.

### `fetch_sales_report` — `:189` → `_fetch_sales_report` `:199-237`
Returns `{"ready": bool, "summary": dict, "daily": list[dict], "rows": list[dict]}`.
1. Calls `fetch_sales_report_rows(...)` (a **second full query**).
2. Aggregates `summary` **in Python** — `summarize_sales_report_rows` (`:156-186`) sums `amount` and counts *distinct* `order_id` per sale_type, then computes AOV. Adds a `"TOTAL"` bucket.
3. Runs the daily chart query:
```sql
select
  (d.created_at at time zone 'Asia/Bangkok')::date as sales_date,
  coalesce(nullif(d.sale_type, ''), 'NEW_ORDER') as sale_type,
  coalesce(sum(d.amount), 0) as sales_amount
from public.crm_data_imports d
{where_sql}
group by (d.created_at at time zone 'Asia/Bangkok')::date, coalesce(nullif(d.sale_type, ''), 'NEW_ORDER')
order by (d.created_at at time zone 'Asia/Bangkok')::date asc
```
Date bounds: `datetime.combine(start_date, min.time(), tzinfo=BANGKOK_TZ).astimezone(utc)` and `end_date + 1 day` (`:214-215`).

### `fetch_sales_report_rows` — `:240` → `_fetch_sales_report_rows` `:255-348`
`limit=1000` default. Probes `quantity`, `created_by`, `uploaded_by`. `created_by` **does not exist** in any DDL, so:
```python
creator_expr = "coalesce(nullif(d.uploaded_by, ''), '')"
qty_expr     = "coalesce(d.quantity, {raw_qty}, {raw_thai_qty}, 1)"
```
```sql
with base as (
  select
    min(d.created_at) as created_at,
    coalesce(nullif(d.sale_type, ''), 'NEW_ORDER') as sale_type,
    coalesce(nullif(d.order_id, ''), d.id::text) as order_id,
    coalesce(nullif(d.sku, ''), '-') as sku,
    coalesce(nullif(d.product_name, ''), '-') as product_name,
    {qty_expr} as quantity,
    coalesce(d.amount, 0) as amount,
    coalesce(nullif(creator.staff_name, ''), {creator_expr}) as created_staff,
    min(d.id) as first_id,
    array_agg(d.id::text order by d.id) as record_ids,
    bool_and(
      coalesce(nullif(d.source_type, ''), '') = 'manual'
      or coalesce(nullif(d.source_file_name, ''), '') = 'manual_order'
      or coalesce(nullif(d.raw_data->>'source', ''), '') = 'manual_order'
    ) as can_delete
  from public.crm_data_imports d
  left join public.crm_user_roles creator
    on lower(creator.email) = lower({creator_expr})
  {where_sql}
  group by
    coalesce(nullif(d.sale_type, ''), 'NEW_ORDER'),
    coalesce(nullif(d.order_id, ''), d.id::text),
    coalesce(nullif(d.sku, ''), '-'),
    coalesce(nullif(d.product_name, ''), '-'),
    {qty_expr},
    coalesce(d.amount, 0),
    coalesce(nullif(creator.staff_name, ''), {creator_expr})
)
select
  to_char(created_at at time zone 'Asia/Bangkok', 'HH24:MI') as sale_time,
  sale_type, order_id, sku, product_name, quantity, amount, created_staff,
  record_ids, can_delete
from base
order by created_at asc, first_id asc
limit %s
```
Notice the group-by collapses identical `(sale_type, order_id, sku, product_name, qty, amount, staff)` tuples into one row and returns `record_ids text[]` so the UI can bulk-delete the collapsed set. `qty_expr` — a 3-branch CASE/regex expression — appears in both SELECT and GROUP BY.

## 2.3 `crm_data\team_sales.py`

Reusable SQL fragments (`:16-42`):
```python
_MANUAL_ROW_SQL = """
(
  coalesce(d.source_type, '') = 'manual'
  or coalesce(d.source_file_name, '') = 'manual_order'
  or coalesce(d.raw_data->>'source', '') = 'manual_order'
)
"""
_RAW_QUANTITY_SQL      = "case when nullif(btrim(d.raw_data->>'qty'), '') ~ '^[0-9]+(\\.[0-9]+)?$' then (d.raw_data->>'qty')::numeric end"
_RAW_THAI_QUANTITY_SQL = "case when nullif(btrim(d.raw_data->>'จำนวน'), '') ~ '^[0-9]+(\\.[0-9]+)?$' then (d.raw_data->>'จำนวน')::numeric end"
_EFFECTIVE_QUANTITY_SQL = f"coalesce(d.quantity, {_RAW_QUANTITY_SQL}, {_RAW_THAI_QUANTITY_SQL}, 1)"
_BASE_RAW_QUANTITY_SQL      = _RAW_QUANTITY_SQL.replace("d.", "")       # alias-stripping by string replace
_BASE_RAW_THAI_QUANTITY_SQL = _RAW_THAI_QUANTITY_SQL.replace("d.", "")
```

`_connection(conn_or_none)` (`:45-54`) — allows injecting a connection (used by tests). `_fetch_all(sql, params, conn)` (`:87-91`) returns `[dict(row) ...]`.

### `fetch_team_assignment_users(conn_or_none=None) -> list[dict]` — `:94-116`
```sql
select
  u.email, u.role, u.staff_code, u.staff_name, u.is_active,
  a.team_code as current_team_code,
  a.team_name as current_team_name,
  a.effective_from, a.effective_to
from public.crm_user_roles u
left join public.crm_user_team_assignments a
  on a.user_email = lower(btrim(u.email))
 and a.effective_to is null
where u.is_active = true
order by coalesce(nullif(u.staff_name, ''), u.email), u.email
```

### `fetch_team_sales_summary(start_date, end_date, sale_type_filter=None) -> dict` — `:119-198`

Attribution is **by `uploaded_by` email → team, as-of `created_at`** (not by owner or staff_code):
```sql
with attributed as (
  select d.order_id, d.amount, a.team_code, a.team_name
  from public.crm_data_imports d
  left join public.crm_user_team_assignments a
    on a.user_email = lower(btrim(d.uploaded_by))
   and d.created_at >= a.effective_from
   and (a.effective_to is null or d.created_at < a.effective_to)
  where d.created_at >= %s
    and d.created_at < %s
    and {_MANUAL_ROW_SQL}
    and d.sale_type in ('NEW_ORDER', 'UPSELL')
    {sale_clause}          -- "and d.sale_type = %s"
)
select
  coalesce(team_code, %s) as team_code,
  coalesce(team_name, %s) as team_name,
  count(distinct nullif(btrim(order_id), '')) as order_count,
  coalesce(sum(amount), 0) as sales_amount,
  count(*) as row_count
from attributed
group by team_code, team_name
order by team_code nulls last
```
`UNASSIGNED_TEAM_CODE = "UNASSIGNED"`, `UNASSIGNED_TEAM_NAME = "ยังไม่เลือกทีม"` (`:12-13`). Python then normalizes into a fixed `{"teams":[CRM_TEAM, UPSELL_TEAM], "unassigned":…, "unassigned_count":int}` shape. **Note `d.sale_type in (…)` here is a bare comparison — NULL sale_type rows are dropped, unlike the dashboard which coalesces to `NEW_ORDER`. Team totals and dashboard totals can therefore disagree.**

### `fetch_team_top_products(start_date, end_date, team_code=None, sale_type_filter=None, limit=10) -> list[dict]` — `:201-252`
Uses an **INNER JOIN** to team assignments (so unassigned rows vanish), unlike the summary's LEFT JOIN:
```sql
select
  coalesce(nullif(btrim(d.sku), ''), '') as sku,
  coalesce(nullif(btrim(d.product_name), ''), '') as product_name,
  {team_select} as team_code,          -- "a.team_code" or literal "null::text"
  {team_name_select} as team_name,
  sum({_EFFECTIVE_QUANTITY_SQL}) as total_quantity,
  count(distinct nullif(btrim(d.order_id), '')) as order_count
from public.crm_data_imports d
join public.crm_user_team_assignments a
  on a.user_email = lower(btrim(d.uploaded_by))
 and d.created_at >= a.effective_from
 and (a.effective_to is null or d.created_at < a.effective_to)
where d.created_at >= %s
  and d.created_at < %s
  and {_MANUAL_ROW_SQL}
  and d.sale_type in ('NEW_ORDER', 'UPSELL')
  {sale_clause}
  {team_clause}                        -- "and a.team_code = %s"
group by
  coalesce(nullif(btrim(d.sku), ''), ''),
  coalesce(nullif(btrim(d.product_name), ''), ''),
  {team_select},
  {team_name_select}
order by total_quantity desc, product_name, sku
limit %s
```
`limit` validated 1..100 (`:212-213`). When `team_code is None`, `{team_select}` becomes the literal `null::text` — so `group by null::text` and the returned `team_code` is NULL.

### `fetch_team_sales_data_quality(start_date, end_date) -> dict[str,int]` — `:255-296`
```sql
with base as (
  select d.*, a.id as assignment_id
  from public.crm_data_imports d
  left join public.crm_user_team_assignments a
    on a.user_email = lower(btrim(d.uploaded_by))
   and d.created_at >= a.effective_from
   and (a.effective_to is null or d.created_at < a.effective_to)
  where d.created_at >= %s and d.created_at < %s
    and {_MANUAL_ROW_SQL}
    and d.sale_type in ('NEW_ORDER', 'UPSELL')
), creator_conflicts as (
  select order_id from base
  where nullif(btrim(order_id), '') is not null
  group by order_id
  having count(distinct lower(btrim(uploaded_by))) > 1
)
select
  count(*) as manual_row_count,
  count(*) filter (where assignment_id is null) as unassigned_row_count,
  count(*) filter (where amount is null) as null_amount_count,
  count(*) filter (where amount = 0) as zero_amount_count,
  count(*) filter (
    where quantity is null
      and ({_BASE_RAW_QUANTITY_SQL}) is null
      and ({_BASE_RAW_THAI_QUANTITY_SQL}) is null
  ) as quantity_default_one_count,
  count(*) filter (where nullif(btrim(coalesce(sku, '')), '') is null) as blank_sku_count,
  (select count(*) from creator_conflicts) as multiple_creator_order_count
from base
```
`select d.*` in the CTE materializes all 34 columns including `raw_data` jsonb.

## 2.4 `crm_data\products.py`

### `fetch_product_options() -> list[dict]` — `:204-225`, `@st.cache_data(ttl=900)`
```sql
select id::text as id, sku, product_group, product_name, is_active, sort_order, updated_at
from public.crm_product_options
order by sku asc nulls last, sort_order asc, product_group asc, product_name asc
```
**No WHERE, no LIMIT — every product row.** `ui\manual_order_ui.py:257-266` (`fetch_manual_product_options`) then filters `is_active` and non-blank sku/name **in Python**. Same in `pages\followup.py` (`fetch_popup_product_options`). Archived products are excluded only incidentally, because `archive_products` also sets `is_active=false`.

### `fetch_product_page(status_filter="active", sort_mode="sku_asc", page=1, page_size=10, search="") -> tuple[list[dict], int]` — `:228-296`, `@st.cache_data(ttl=300)`

Status clauses (`:9-14`):
```python
_PRODUCT_STATUS_CLAUSES = {
    "active":   ("is_active = true",  "archived_at is null"),
    "inactive": ("is_active = false", "archived_at is null"),
    "all":      ("archived_at is null",),
    "archived": ("archived_at is not null",),
}
```
Sort key expression (`:16-22`):
```sql
case
  when upper(btrim(coalesce(sku, ''))) ~ '^SP[[:space:]]*[0-9]+'
    then substring(upper(btrim(sku)) from '^SP[[:space:]]*0*([0-9]+)')::bigint
  else null
end
```
Count: `select count(*)::int as total from public.crm_product_options {where_sql}`.
Page:
```sql
select id::text as id, sku, product_group, product_name, is_active,
       archived_at, archived_by, archive_reason, sort_order, created_at, updated_at
from (
  select *, {_SKU_NUMBER_SQL} as sku_number
  from public.crm_product_options
  {where_sql}
) product_page
order by {sort_sql}
limit %s offset %s
```
`sort_sql` is one of (`:256-261`):
```
sku_asc      → sku_number asc nulls last, lower(btrim(coalesce(sku, ''))) asc,  id asc
sku_desc     → sku_number desc nulls last, lower(btrim(coalesce(sku, ''))) desc, id desc
created_asc  → created_at asc,  lower(btrim(coalesce(sku, ''))) asc, id asc
created_desc → created_at desc, lower(btrim(coalesce(sku, ''))) asc, id desc
```
`status_filter`/`sort_mode` are validated against whitelists (`:240-243`) before interpolation — this is the safe pattern; `search` is parameterized.

### `fetch_product_delete_readiness(product_ids) -> dict[int,dict]` — `:183-201`, SQL at `:23-61`
**Five correlated scalar subqueries per product**, all with `lower(btrim(...))` on both sides so no index can be used:
```sql
select
  p.id::bigint as product_id, p.sku, p.product_name,
  (select count(*)::int from public.crm_data_imports d
    where nullif(btrim(p.sku), '') is not null
      and lower(btrim(coalesce(d.sku, ''))) = lower(btrim(p.sku))) as imports_sku_count,
  (select count(*)::int from public.crm_data_imports d
    where nullif(btrim(p.product_name), '') is not null
      and lower(btrim(coalesce(d.product_name, ''))) = lower(btrim(p.product_name))) as imports_name_count,
  (select count(*)::int from public.crm_data_imports d
    where nullif(btrim(p.sku), '') is not null
      and lower(btrim(coalesce(d.raw_data->>'sku', ''))) = lower(btrim(p.sku))) as imports_raw_sku_count,
  (select count(*)::int from public.crm_order_items i
    where nullif(btrim(p.sku), '') is not null
      and lower(btrim(coalesce(i.sku, ''))) = lower(btrim(p.sku))) as order_items_sku_count,
  (select count(*)::int from public.crm_order_items i
    where nullif(btrim(p.product_name), '') is not null
      and lower(btrim(coalesce(i.product_name, ''))) = lower(btrim(p.product_name))) as order_items_name_count
from public.crm_product_options p
where p.id = any(%s::bigint[])
order by p.id
```
→ **3 full scans of `crm_data_imports` (~18k rows) × N selected products.** For 10 selected products that's 30 seq scans + 20 over `crm_order_items`.

## 2.5 `pages\customer_detail.py`

### `fetch_customer_followup(customer) -> dict` — `:104-150`
The only raw SQL outside `neon_utils.py`/`crm_data\`. Clauses are combined with **`OR`**, not `AND`:
```python
clauses = ["customer_key = %s"];  params = [f"customer_id:{customer_id}"]
if customer_id.isdigit(): clauses.append("crm_data_import_id = %s")
if phone1: clauses.append("(phone1 = %s or phone2 = %s)")
if phone2: clauses.append("(phone1 = %s or phone2 = %s)")
```
```sql
select
  id::text, customer_key, crm_data_import_id::text, order_id, customer_name,
  phone1, phone2, product_name, sku, staff_code, owner,
  coalesce(lead_status, 'new') as lead_status,
  coalesce(followup_status, follow_up_status, 'none') as followup_status,
  coalesce(priority, 'NEW') as priority,
  coalesce(next_followup_date, follow_up_date)::text as next_followup_date,
  coalesce(followup_note, follow_up_note, '') as followup_note,
  updated_by, updated_at
from public.crm_lead_followups
where {" or ".join(clauses)}
order by updated_at desc nulls last, created_at desc nulls last
limit 1
```
The OR-chain across four differently-indexed predicates means Postgres will typically BitmapOr or seq-scan `crm_lead_followups`.

---

# 3. Write functions

| Function | Loc | Tables | Kind | Transaction |
|---|---|---|---|---|
| `insert_import_records` | `neon_utils.py:465` | `crm_data_imports` (+ reads `crm_owner_assignments`, mutates `crm_data_imports`) | INSERT batched | one txn, `except: rollback; raise` |
| `upsert_manual_order` | `:522` | `crm_data_imports` | SELECT-then-UPDATE-or-INSERT | one txn |
| `upsert_manual_order_items` | `:907` | `crm_data_imports`, `crm_orders`, `crm_order_items` | mixed | one txn |
| `apply_latest_customer_updates` | `:1431` | `crm_data_imports` | UPDATE per row | caller's txn |
| `assign_owner_to_phones` | `:1915` | `crm_data_imports`, `crm_owner_assignments` | UPDATE + UPSERT | one txn |
| `assign_url_to_phones` | `:1973` | `crm_data_imports` | UPDATE | one txn |
| `assign_owner_to_order_record` | `:2001` | `crm_data_imports` | UPDATE | one txn |
| `delete_import_batch` | `:2230` | `crm_data_imports` | DELETE | one txn (**no rollback handler**) |
| `upsert_lead_followup` | `:2281` | `crm_lead_followups` | UPSERT | one txn |
| `upsert_user_role` | `:2707` | `crm_user_roles` | UPSERT | one txn |
| `set_user_role_active` | `:2733` | `crm_user_roles` | UPDATE | one txn |
| `upsert_staff_option` | `:2870` | `crm_staff_options` | UPSERT | one txn |
| `update_staff_option` | `:2894` | `crm_staff_options` | UPDATE | one txn |
| `delete_staff_option` | `:2914` | `crm_staff_options` | DELETE | one txn |
| `upsert_product_options` | `products.py:299` | `crm_product_options` | SELECT-then-UPDATE-or-INSERT, **per record in a loop** | one txn for all records |
| `insert_product_options` | `products.py:363` | `crm_product_options` | `executemany` INSERT | one txn |
| `update_product_option` | `products.py:395` | `crm_product_options` | UPDATE | one txn |
| `bulk_update_product_active` | `products.py:417` | `crm_product_options` | UPDATE … `id = any(%s::bigint[])` | one txn |
| `archive_products` | `products.py:453` | `crm_product_options` | UPDATE (soft delete) | one txn |
| `restore_archived_products` | `products.py:489` | `crm_product_options` | UPDATE | one txn |
| `delete_product_option` | `products.py:520` | `crm_product_options` | DELETE | one txn |
| `delete_sales_report_records` | `dashboard.py:351` | `crm_order_items`, `crm_data_imports`, `crm_orders` | 4-step DELETE | one txn |
| `_set_user_team_assignment` | `team_sales.py:306` | `crm_user_team_assignments` | SELECT FOR UPDATE + UPDATE + INSERT | one txn |

**Universal pattern:** `with neon_connection() as conn: try: with conn.cursor() as cur: … ; conn.commit(); except: conn.rollback(); raise`. Autocommit is **off** (psycopg 3 default), so every write is an explicit transaction over one fresh connection. There is **no** cross-function transaction — e.g. saving an order then updating follow-up status are two separate connections/transactions.

### `insert_import_records(records, batch_size=500)` — `:465-519`

28 columns (**no `staff_code`, no `sale_type`/`amount`/`address`, no `source_type`, no `quantity`** — despite `build_record_from_mapping` writing `staff_code: ""`):
```python
columns = ["import_batch_id","source_file_name","sheet_name","row_number","uploaded_by",
           "uploaded_at","raw_data","order_id","url","customer_name","phone1","phone2",
           "product_name","sku","order_date","province","city","postal_code","tracking_no",
           "carrier","order_status","total_amount","owner","import_status","validation_error",
           "dedupe_key","created_at","updated_at"]
sql = f"insert into public.crm_data_imports ({', '.join(columns)}) values ({placeholders})"
```
Plain INSERT — **no ON CONFLICT anywhere.** Dedup is done entirely in application code by `prepare_import_records` → `build_import_plan` (`:1290-1355`) before the insert, inside the same cursor/transaction:
1. `apply_owner_assignments(cur, records)` — reads `crm_owner_assignments` for all phones, back-fills `record["owner"]` if blank.
2. `fetch_existing_order_ids(cur, records)` — set of already-present valid `order_id`s.
3. `fetch_latest_customer_rows_by_phone(cur, records)` — latest row per phone.
4. Per record: skip if `import_status == 'invalid'` or no phone (`"ไม่มีเบอร์โทร"`); skip if `order_id` already in DB (`"ซ้ำเลขออเดอร์ในฐานข้อมูล"`); skip if duplicate within the file (`"ซ้ำเลขออเดอร์ในไฟล์"`); if phone already exists, record a `phone_duplicate` and queue a `url`/`owner` back-fill onto the existing latest row **but still insert the new row**.
5. `apply_latest_customer_updates` (`:1431-1447`) runs one UPDATE per queued row:
```sql
update public.crm_data_imports
set url = coalesce(nullif(%s, ''), url),
    owner = coalesce(nullif(%s, ''), owner),
    updated_at = now()
where id = %s
```
Then `cur.executemany(sql, values)` in chunks of `batch_size` (500), `raw_data` wrapped in `Jsonb`. `analyze_import_records` (`:1271`) runs the same plan with `mutate_records=False` for the dry-run preview.

### `upsert_manual_order(payload) -> dict` — `:522-727`

**Dead code — no page calls it** (only `neon_utils.py:522` and a test that string-scans the source, `tests\test_crm_team_duplicate_phone_lock.py:191`). Kept for the record because the doc lists it.

Validates `order_id, customer_name, phone pair, product_name, owner` → raises `ValueError("; ".join(errors))`. Then:
```sql
-- count matches
select count(*) as match_count from public.crm_data_imports
where import_status = 'valid' and (phone1 = any(%s) or phone2 = any(%s))
-- pick target
select id::text as id from public.crm_data_imports
where import_status = 'valid' and (phone1 = any(%s) or phone2 = any(%s))
order by order_date desc nulls last, updated_at desc nulls last, uploaded_at desc, id desc
limit 1
```
If a target exists → UPDATE with a per-column `coalesce(nullif(%s,''), col)` guard, except for the always-overwritten set (`:632`):
```python
f"{column} = coalesce(nullif(%s, ''), {column})"
  if column not in {"import_status","validation_error","updated_at","order_date","source_type","updated_by"}
  else f"{column} = %s"
```
Otherwise a 30-column INSERT with `returning id::text as id`. Returns `{"action": "updated"|"inserted", "id": str, "match_count": int}`. **Matching is by phone only — `order_id` is not part of the match**, so a new order for an existing phone overwrites the latest existing row.

### `upsert_manual_order_items(payload, items) -> dict` — `:907-1268`

**The single most important write path.** Called from `ui\manual_order_ui.py:166` and `pages\followup.py:707`.

Validation (`:925-956`): `order_id`, `customer_name`, phone pair, `owner`, `staff_code`, ≥1 item with `sku && product_name && qty>0`. `sale_type` defaults `"NEW_ORDER"`; **if `sale_type == "FOLLOW"` every item's `amount` is forced to 0** (`:944`).

Duplicate-phone lock (`:958-961`) — see §5.

Runtime feature detection (`:963-968`): `quantity`, `amount`, `sale_type`, `address` columns; `crm_orders`, `crm_order_items` tables. The generated SQL differs per environment.

Match expressions (`:972-981`):
```python
raw_qty_match_expr    = "case when nullif(raw_data->>'qty', '') ~ '^[0-9]+(\\.[0-9]+)?$' then (raw_data->>'qty')::numeric end"
raw_amount_match_expr = "case when nullif(raw_data->>'amount', '') ~ '^[0-9]+(\\.[0-9]+)?$' then (raw_data->>'amount')::numeric end"
qty_match_expr    = f"coalesce(quantity, {raw_qty_match_expr}, 0)"
amount_match_expr = f"coalesce(amount, {raw_amount_match_expr}, 0)"
```

Steps inside one transaction:

**(a)** If `force_owner_update` (set only when the actor is EDITOR, `ui\manual_order_ui.py:178`) — **mass reassignment of every row sharing either phone**:
```sql
update public.crm_data_imports
set owner = %s, staff_code = %s, updated_by = %s, updated_at = %s
where import_status = 'valid'
  and (phone1 = any(%s) or phone2 = any(%s))
```

**(b)** `crm_orders` header, if the table exists:
```sql
select id from public.crm_orders
where order_id = %s and (phone1 = any(%s) or phone2 = any(%s))
order by updated_at desc nulls last, created_at desc nulls last, id desc
limit 1
```
then either
```sql
update public.crm_orders
set customer_name = coalesce(nullif(%s, ''), customer_name),
    url = coalesce(nullif(%s, ''), url),
    owner = coalesce(nullif(%s, ''), owner),
    staff_code = coalesce(nullif(%s, ''), staff_code),
    updated_by = %s, updated_at = %s
where id = %s
```
or
```sql
insert into public.crm_orders (
  order_id, customer_name, phone1, phone2, url, owner, staff_code,
  source_type, created_by, updated_by, created_at, updated_at
)
values (%s, %s, %s, %s, %s, %s, %s, 'manual', %s, %s, %s, %s)
returning id
```
Note: **manual SELECT-then-insert, not `ON CONFLICT`**, even though `ux_crm_orders_order_phone` exists. Two concurrent saves race → unique-violation.

**(c)** Then **per item**, a SELECT + (UPDATE or INSERT) + a `crm_order_items` UPSERT — i.e. a genuine N+1 write loop:

Match query (`:1064-1087`):
```sql
select id::text as id
from public.crm_data_imports
where import_status = 'valid'
  and order_id = %s
  and sku = %s
  and coalesce(nullif(trim(product_name), ''), '') = %s
  and {qty_match_expr} = %s
  and {amount_match_expr} = %s
  and (phone1 = any(%s) or phone2 = any(%s))
order by order_date desc nulls last, updated_at desc nulls last, uploaded_at desc, id desc
limit 1
```
On hit, an UPDATE whose SET list is assembled by **`list.insert(-3, …)`** positional splicing (`:1091-1131`) — fragile, order-dependent:
```sql
update public.crm_data_imports
set customer_name = coalesce(nullif(%s, ''), customer_name),
    phone1 = coalesce(nullif(%s, ''), phone1),
    phone2 = coalesce(nullif(%s, ''), phone2),
    product_name = coalesce(nullif(%s, ''), product_name),
    sku = coalesce(nullif(%s, ''), sku),
    url = coalesce(nullif(%s, ''), url),
    order_date = %s,
    owner = coalesce(nullif(%s, ''), owner),
    staff_code = coalesce(nullif(%s, ''), staff_code),
    source_type = 'manual',
    -- optionally spliced in here: quantity = %s,
    --                             amount = coalesce(%s, amount),
    --                             sale_type = coalesce(nullif(%s, ''), sale_type),
    --                             address = coalesce(nullif(%s, ''), address),
    updated_by = %s,
    updated_at = %s,
    raw_data = %s
where id = %s
returning id::text as id
```
On miss, a 30-column INSERT (`:1221-1228`) plus optional `quantity`/`amount`/`sale_type`/`address`, `returning id::text as id`. `raw_data` for manual orders is a fixed dict (`:1045-1063`) containing `source: "manual_order"` plus `qty`, `amount`, `address`, `sale_type`, `owner`, `staff_code`, `uploaded_by`, `updated_by`.

**(d)** The **only real UPSERT** in the whole codebase's order path (`:1236-1258`):
```sql
insert into public.crm_order_items (
  crm_order_id, crm_data_import_id, order_id, sku, product_name, qty, created_at, updated_at
)
values (%s, %s, %s, %s, %s, %s, %s, %s)
on conflict (crm_order_id, sku) do update
set crm_data_import_id = excluded.crm_data_import_id,
    product_name = excluded.product_name,
    qty = excluded.qty,
    updated_at = excluded.updated_at
```
`crm_data_import_id` = `int(record_id) if str(record_id).isdigit() else None`.

Returns `{"actions": {"inserted": n, "updated": m}, "ids": [...], "item_count": n, "duplicate_lock_warning": str}`.

Caveat: `check_crm_team_duplicate_phone_lock` (step before) runs on **its own connection**, outside this transaction — a TOCTOU gap.

### `upsert_lead_followup(payload)` — `:2281-2327`

23 columns, `ON CONFLICT (customer_key)` (the PK), updating **all 22 non-key columns from `excluded`**:
```python
columns = ["customer_key","crm_data_import_id","order_id","customer_id","customer_name",
           "phone_key","phone1","phone2","product_group","product_name","sku","staff_code",
           "owner","lead_status","followup_status","next_followup_date","followup_note",
           "follow_up_status","follow_up_date","follow_up_note","priority","updated_by","updated_at"]
set_clause = ", ".join([f"{c} = excluded.{c}" for c in columns[1:]])
```
```sql
insert into public.crm_lead_followups ({columns})
values (%s, ... )
on conflict (customer_key) do update
set {set_clause}
```
`payload["priority"]` is normalized through `normalize_followup_priority` first (`:2284`). **Both the new and legacy column sets are written on every save** — callers duplicate values into `followup_status`/`follow_up_status`, `next_followup_date`/`follow_up_date`, `followup_note`/`follow_up_note` (e.g. `pages\customers.py:470-476`). `created_at` is never supplied → relies on the column default, and is therefore **not preserved across upserts**… actually it is, because `do update` doesn't touch it.

### `assign_owner_to_phones(phones, owner, updated_by, staff_code="", allow_owner_only=False) -> int` — `:1915-1970`

Guard: `if not staff_code and not allow_owner_only: raise ValueError("staff_code is required when assigning an owner")`.

Two variants — **note neither filters `import_status`**, so invalid rows are reassigned too:
```sql
update public.crm_data_imports
set owner = %s, staff_code = %s, updated_at = now()
where phone1 = any(%s) or phone2 = any(%s)
```
```sql
update public.crm_data_imports
set owner = %s, updated_at = now()
where phone1 = any(%s) or phone2 = any(%s)
```
Then the sticky map, via `executemany` (one statement per phone):
```sql
insert into public.crm_owner_assignments (phone_key, owner, updated_by, updated_at)
values (%s, %s, %s, now())
on conflict (phone_key) do update
set owner = excluded.owner,
    updated_by = excluded.updated_by,
    updated_at = now()
```
Returns `cur.rowcount` **of the first UPDATE only**. `updated_by` is not written to `crm_data_imports` here.

Only caller: `customer360.py:816` — which is orphaned. So in practice `crm_owner_assignments` is only *read* (by `apply_owner_assignments` during Excel import) and never written by any reachable page.

### `assign_url_to_phones(phones, url, updated_by) -> int` — `:1973-1998`
```sql
update public.crm_data_imports
set url = %s, updated_by = %s, updated_at = now()
where import_status = 'valid'
  and (phone1 = any(%s) or phone2 = any(%s))
```
Returns `rowcount`. Caller: `pages\customers.py:417`.

### `assign_owner_to_order_record(record_id, order_id, owner, updated_by, staff_code="", allow_owner_only=False) -> int` — `:2001-2068`
Same `staff_code` guard. **Four mutually exclusive branches**; `updated_by` is accepted but **never written**:
```sql
-- order_id and staff_code            (updates EVERY row with that order_id)
update public.crm_data_imports set owner=%s, staff_code=%s, updated_at=now() where order_id = %s
-- order_id only
update public.crm_data_imports set owner=%s,                updated_at=now() where order_id = %s
-- staff_code only
update public.crm_data_imports set owner=%s, staff_code=%s, updated_at=now() where id = %s
-- neither
update public.crm_data_imports set owner=%s,                updated_at=now() where id = %s
```
When `order_id` is present the scope silently widens from one record to the whole order. Also **no `import_status` filter.** Caller: `pages\customers.py:390`.

### `delete_import_batch(batch_id) -> int` — `:2230-2237`
```sql
delete from public.crm_data_imports where import_batch_id = %s
```
Returns `rowcount`. **No try/except, no rollback handler** (the only write function lacking one), and no cascade to `crm_lead_followups` / `crm_order_items` — orphan follow-up and order-item rows survive.

### `upsert_user_role(payload)` — `:2707-2730`
```python
has_owner_alias = table_has_column(cur, "crm_user_roles", "owner_alias")
columns = ["email","role","staff_code","staff_name","is_active","updated_at"]
if has_owner_alias: columns.insert(4, "owner_alias")
```
```sql
insert into public.crm_user_roles ({columns})
values (%s, ...)
on conflict (email) do update
set role = excluded.role, staff_code = excluded.staff_code, staff_name = excluded.staff_name,
    [owner_alias = excluded.owner_alias,] is_active = excluded.is_active, updated_at = excluded.updated_at
```
Email lowercasing is the caller's job (`pages\users.py`).

### `set_user_role_active(email, is_active, updated_at)` — `:2733-2753`
```sql
update public.crm_user_roles
set is_active = %s, updated_at = %s
where email = %s
```
Email lowercased in-function.

### `upsert_staff_option(payload)` — `:2870-2891`
```sql
insert into public.crm_staff_options (staff_name, sort_order, is_active, created_by, updated_by, updated_at)
values (%s, %s, %s, %s, %s, %s)
on conflict (staff_name) do update
set sort_order = excluded.sort_order,
    is_active = excluded.is_active,
    updated_by = excluded.updated_by,
    updated_at = excluded.updated_at
```
**Conflict target is `staff_name`, and `staff_code` is never written by this function.** `update_staff_option` (`:2894`) sets `staff_name, sort_order, is_active, updated_by, updated_at where id = %s` — also never `staff_code`. So `crm_staff_options.staff_code` is populated only by the manual SQL plan / by hand.

### `upsert_product_options(records)` — `products.py:299-360`
Not a SQL upsert — a **Python loop of SELECT-then-UPDATE-or-INSERT** inside one transaction:
```sql
select id from public.crm_product_options
where coalesce(sku, '') = coalesce(%s, '')
  and product_group = %s
  and product_name = %s
limit 1
```
then
```sql
update public.crm_product_options
set sort_order = %s, is_active = %s, updated_by = %s, updated_at = %s
where id = %s
```
or
```sql
insert into public.crm_product_options
  (sku, product_group, product_name, sort_order, is_active, created_by, updated_by, updated_at)
values (%s, %s, %s, %s, %s, %s, %s, %s)
```
The UPDATE branch **never touches `sku`/`product_group`/`product_name`** (they're the match key) and never touches `active`. It also can't hit `crm_product_options_sku_group_name_key` deliberately, hence the manual pattern.

### `update_product_option(option_id, payload)` — `products.py:395-414`
```sql
update public.crm_product_options
set sku = %s, product_group = %s, product_name = %s, sort_order = %s,
    is_active = %s, updated_by = %s, updated_at = %s
where id = %s
```

### `archive_products` / `restore_archived_products` — `products.py:62-83, 453-517`
```sql
update public.crm_product_options
set archived_at = now(), archived_by = %s, archive_reason = %s,
    is_active = false, updated_at = now(), updated_by = %s
where id = any(%s::bigint[]) and archived_at is null
```
```sql
update public.crm_product_options
set archived_at = null, archived_by = null, archive_reason = null,
    is_active = false, updated_at = now(), updated_by = %s
where id = any(%s::bigint[]) and archived_at is not null
```
Restore intentionally leaves `is_active = false`. Both return `{"requested", "updated", "skipped"}` and call `fetch_product_page.clear()` + `fetch_product_options.clear()` only if `updated_count`.

### `delete_sales_report_records(record_ids, user) -> int` — `dashboard.py:351-446`
Permission-gated in Python (`can_delete_order`, raises `PermissionError`). Four statements in one txn; note the "is manual" predicate is repeated verbatim:
```sql
select distinct order_id from public.crm_data_imports
where id::text = any(%s)
  and (coalesce(nullif(source_type, ''), '') = 'manual'
    or coalesce(nullif(source_file_name, ''), '') = 'manual_order'
    or coalesce(nullif(raw_data->>'source', ''), '') = 'manual_order')
```
```sql
delete from public.crm_order_items where crm_data_import_id::text = any(%s)
```
```sql
delete from public.crm_data_imports
where id::text = any(%s)
  and (coalesce(nullif(source_type, ''), '') = 'manual'
    or coalesce(nullif(source_file_name, ''), '') = 'manual_order'
    or coalesce(nullif(raw_data->>'source', ''), '') = 'manual_order')
```
```sql
delete from public.crm_orders o
where o.order_id = any(%s)
  and not exists (select 1 from public.crm_order_items i where i.crm_order_id = o.id)
  and not exists (select 1 from public.crm_data_imports d
                  where d.order_id = o.order_id and d.import_status = 'valid')
```
`id::text = any(%s)` casts the bigint PK to text → **the PK index is unusable, forcing a seq scan.** No delete of matching `crm_lead_followups`.

### `_set_user_team_assignment(*, user_email, team_code, actor_email) -> dict` — `team_sales.py:306-422`
The only place using row locking:
```sql
select id, user_email, team_code, team_name, effective_from, effective_to
from public.crm_user_team_assignments
where user_email = %s and effective_to is null
order by effective_from desc
limit 1
for update
```
```sql
select clock_timestamp() as now_ts
```
with `if current and now_ts <= current["effective_from"]: now_ts = effective_from + 1µs` to satisfy `chk_assignment_period`. Then close the open row:
```sql
update public.crm_user_team_assignments
set effective_to = %s, updated_at = %s, updated_by = %s
where id = %s
```
and, unless clearing:
```sql
insert into public.crm_user_team_assignments (user_email, team_code, effective_from, created_by, updated_by)
values (%s, %s, %s, %s, %s)
returning id, user_email, team_code, team_name, effective_from, effective_to
```
Returns `{..., "changed": bool, "action": "unchanged"|"created"|"changed"|"cleared"}`. Public wrappers `save_user_team_assignment` (`:425`) and `clear_user_team_assignment` (`:438`).

---

# 4. Owner / staff scoping (row-level visibility)

## The one helper: `_followup_staff_scope`

`neon_utils.py:2334-2341`, verbatim:

```python
def _followup_staff_scope(user: dict, alias: str = "d") -> tuple[str, list]:
    if clean(user.get("role")) in {"ADMIN", "EDITOR"}:
        return "", []

    staff_code = clean(user.get("staff_code"))
    if not staff_code:
        return "1 = 0", []
    return f"nullif(trim(coalesce({alias}.staff_code, '')), '') = %s", [staff_code]
```

Semantics:
- Role **exactly** `"ADMIN"` or `"EDITOR"` (case-sensitive, raw string from `crm_user_roles.role`) → **no clause**, sees everything.
- Any other role with a non-blank `staff_code` → `nullif(trim(coalesce(d.staff_code,'')),'') = '<STAFF_CODE>'`.
- Any other role with blank `staff_code` → **`1 = 0`**, sees nothing.

The compared column is **`crm_data_imports.staff_code`** — never `owner`, never `phone`, never `crm_lead_followups.staff_code`. The `nullif(trim(coalesce(...)))` wrapper makes `idx_crm_data_imports_staff_code` unusable as a plain equality index (Postgres cannot prove `nullif(trim(coalesce(x,'')),'') = 'AU'` ⇒ `x = 'AU'`), so scoping falls back to a seq scan unless the partial index `idx_crm_data_imports_valid_staff_order` happens to be chosen by other predicates. The comparison is also **exact, case-sensitive, whitespace-normalized only on the column side** — the parameter is used raw.

### Where it is applied

| Caller | Loc | Alias | Effect |
|---|---|---|---|
| `build_followup_where` | `:2347` | `d` (= `ranked d`, i.e. `crm_data_imports.staff_code`) | Follow-up list is scoped |
| `build_customer_where` | `:1739` | `d` (= `crm_data_imports`) | **only if `enforce_user_scope=True`** |
| `fetch_followup_filter_options` | `:2413` | `d` | Owner/product dropdowns scoped |
| `test_user_role_visibility` | `:2763` | `d` | Diagnostic |

**Not applied** (deliberately or by omission):
- `pages\customers.py:83` → `fetch_customer_page(filters, page_size, page, user, enforce_user_scope=False)` — **the Customers list is unscoped for every logged-in user.** The page instead does per-row *edit* gating in Python (`can_edit_customer_follow_action`, `pages\customers.py:424-429`).
- `fetch_customer_export_rows` (`:1587`) — hardcoded `enforce_user_scope=False`.
- `fetch_customer_by_id`, `fetch_customer_360_base`, `fetch_customer_360_orders`, `fetch_customer_360_products`, `fetch_orders_by_phones`, `fetch_lead_followups`, `fetch_filter_options`, `fetch_crm_owner_options`, `fetch_import_history`, `pages\customer_detail.py:fetch_customer_followup`, all of `crm_data\team_sales.py` — **no scoping at all**.

### Duplicated (not shared) scope logic

`crm_data\dashboard.py` re-implements the same rule twice rather than importing the helper:

`_fetch_dashboard_kpis`, `dashboard.py:19-27`:
```python
role = clean((user or {}).get("role"))
staff_code = clean((user or {}).get("staff_code"))
if role not in {"ADMIN", "EDITOR"}:
    if staff_code:
        where.append("d.staff_code = %s"); params.append(staff_code)
    else:
        where.append("1 = 0")
```
`_sales_report_where`, `dashboard.py:141-152`:
```python
if _can_view_all_sales(user):                       # role in {"ADMIN","EDITOR"}
    if owner and owner != "ทั้งหมด":
        clauses.append("d.owner = %s"); params.append(owner)
else:
    staff_code = clean((user or {}).get("staff_code"))
    if staff_code: clauses.append("d.staff_code = %s"); params.append(staff_code)
    else:          clauses.append("1 = 0")
```
These use **bare `d.staff_code = %s`** (index-usable) whereas `_followup_staff_scope` uses the `nullif(trim(coalesce(...)))` form. Same intent, different SQL, different index behaviour, and drift risk.

### Python-side per-row gating (complements SQL scoping)

`permissions.py:93-114`:
```python
def can_edit_customer_lead(user, customer):
    if user_role(user) == ROLE_EDITOR: return True
    if not is_telesell(user): return False
    staff_code = clean(user.get("staff_code"))
    if not staff_code: return False
    customer_staff_code = ""
    for key in ("staff_code",):
        value = customer.get(key)
        if clean(value): customer_staff_code = clean(value)
    return bool(customer_staff_code and customer_staff_code == staff_code)
```
Duplicated inline at `pages\customers.py:424-429` and `pages\customer_detail.py:96-101`. `permissions.py:45-46`: `can_manage_all` = `role in {"ADMIN","EDITOR"}` — **after** `normalize_role` upper-cases known Latin aliases, so `"TELESELL"`/`"STAFF"`/`"USER"` normalize but Thai `"พนักงาน"`/`"ทั่วไป"` pass through as-is. `_followup_staff_scope` does **not** call `normalize_role`, so it compares the raw string. A user stored with role `"editor"` (lowercase) would pass `can_manage_all` but be scoped to `1 = 0`.

### Owner display name ↔ staff_code mapping

There are three name-ish fields and one code field:
- `crm_data_imports.owner` — Thai display name, e.g. `สายฝน ราวิชัย (สายฝน)`. Used for the "ผู้ดูแล" filter (`d.owner = %s`) and sales-report owner filter.
- `crm_data_imports.staff_code` — canonical code (`SAIFON`, …). Used for row visibility.
- `crm_user_roles.staff_name` — display name of the logged-in user.
- `crm_user_roles.owner_alias` — the *legacy* `owner` string this user's rows carry, which may differ from `staff_name` (see `202606_staff_code_normalization_plan.sql:346-353`: `staff_code='NOONA'`, `staff_name='พรนภา นันที (หนูนา)'`, `owner_alias='กัญญพักฒ์ อิ่มยวง (เจี๊ยบ)'`). `owner_alias` is **read into the session dict but never used in any WHERE clause.**

Owner→code resolution for writes goes through `fetch_owner_user_options` + `owner_staff_choices` (`pages\customers.py:432-443`), which builds `{staff_name: staff_code}` and rejects entries missing either. `owner_to_staff_code` (`neon_utils.py:353-363`) parses the code out of trailing parentheses but carries the comment *"Never use this to write canonical staff_code"* and is unused.

---

# 5. Phone matching / dedup / duplicate-phone lock

## Normalization

`crm_data\common.py:21-22` — **strip everything non-digit, no length/format enforcement**:
```python
def normalize_phone(value) -> str:
    return "".join(ch for ch in clean(value) if ch.isdigit())
```
`clean` (`common.py:12-18`) trims and maps the literals `NULL/NONE/NAN/NAT` (case-insensitive) to `""`.

Validation is separate and only used on write paths (`common.py:30-53`):
```python
PHONE_RULE_MESSAGE = "ต้องเป็นตัวเลข 10 หลัก ขึ้นต้นด้วย 0 และห้ามมีสัญลักษณ์"

def validate_phone_value(value, label):
    text = clean(value)
    if not text: return ""
    if not text.isdigit() or len(text) != 10 or not text.startswith("0"):
        return f"{label}ใส่ไม่ถูกต้อง {PHONE_RULE_MESSAGE}"
    return ""

def validate_phone_pair(phone1, phone2, require_one=True):
    if require_one and not first and not second: return ["กรุณากรอกเบอร์โทรหรือเบอร์สำรอง"]
    # then validate each with labels "เบอร์โทร" / "เบอร์สำรอง"
```
**`normalize_phone` is applied before storage but `validate_phone_value` is applied to the *raw* input** — so `08-1234-5678` fails validation rather than being normalized. Legacy Excel-imported rows can hold any digit string.

## The canonical "phone_key" expression (customer identity)

Repeated **verbatim in 6 places** — `neon_utils.py:134-137` (index), `:1528-1531` (`fetch_customer_page`), `:1636-1639` (`fetch_customer_export_rows`), `:2470-2473` and `:2550-2553` (`fetch_followup_page`, twice), `crm_data\dashboard.py:34-37` and `:46-49` (`distinct on` + `order by`):

```sql
case
  when nullif(phone1, '') is not null and nullif(phone2, '') is not null then least(phone1, phone2)
  else coalesce(nullif(phone1, ''), nullif(phone2, ''), id::text)
end
```

I.e. **one customer = `least(phone1, phone2)` lexicographically** when both are present, otherwise the single non-blank phone, otherwise the row's own `id` as a text fallback (so a phone-less row is its own customer). Because it's `least()` on *text*, a row with `(phone1='0899999999', phone2='0811111111')` and another with `(phone1='0811111111', phone2=NULL)` both key to `0811111111` — that's intentional. But `(phone1='0899999999', phone2=NULL)` keys to `0899999999` and does **not** merge with the first row. Phone-pair identity is therefore not transitive.

`pages\customers.py:490-496` reimplements it in Python as `min(phones)` (same result), and `customer360.py:954-962` (`customer_group_key`) as `phones[0]` (**different** — first non-empty, not min).

Dedup selection is always `row_number() over (partition by phone_key order by order_date desc nulls last, uploaded_at desc, id desc)` then `where rn = 1`, or the equivalent `distinct on (...) order by ..., order_date desc nulls last, uploaded_at desc, id desc` in the dashboard.

## Phone-based row matching in queries

Two different idioms, both non-sargable-ish:
- `(phone1 = any(%s) or phone2 = any(%s))` with a `list[str]` param — `fetch_existing_owner_rows_by_phones`, `find_duplicate_valid_order_by_phones`, `fetch_customer_360_orders/products`, `upsert_manual_order`, `upsert_manual_order_items`, `assign_owner_to_phones`, `assign_url_to_phones`.
- `(phone1 = %s or phone2 = %s)` repeated per phone, capped at 6 — `fetch_orders_by_phones` (`:2146-2148`).
- `(d.phone1 like %s or d.phone2 like %s)` with `'%<digits>%'` — the Follow-up phone filter (`build_followup_where:2353-2355`), leading wildcard.

## Import-time phone dedup: `fetch_latest_customer_rows_by_phone`

`neon_utils.py:1374-1416` — one round trip for the whole batch, using a UNION ALL to project each row twice (once per phone column) then a window pick:

```sql
with matched as (
  select
    id,
    phone1,
    phone2,
    row_number() over (
      partition by matched_phone
      order by order_date desc nulls last, uploaded_at desc, id desc
    ) as rn,
    matched_phone
  from (
    select id, phone1, phone2, order_date, uploaded_at, phone1 as matched_phone
    from public.crm_data_imports
    where import_status = 'valid' and phone1 = any(%s)
    union all
    select id, phone1, phone2, order_date, uploaded_at, phone2 as matched_phone
    from public.crm_data_imports
    where import_status = 'valid' and phone2 = any(%s)
  ) src
)
select id::text, phone1, phone2, matched_phone
from matched
where rn = 1
```
Result bucketed in Python into `{phone: [rows]}`; `unique_latest_matches` (`:1419-1428`) then dedups by row id across the record's two phones. A phone match does **not** block the insert — it produces a `phone_duplicate_records` warning row and queues a `url`/`owner` back-fill onto the existing latest row (`build_import_plan:1317-1338`).

## Import-time order-id dedup

`fetch_existing_order_ids` (`:1358-1371`) + an in-file `seen_order_ids` set. Skip reasons: `"ซ้ำเลขออเดอร์ในฐานข้อมูล"` (DB) and `"ซ้ำเลขออเดอร์ในไฟล์"` (file). Note there is no unique index on `order_id`, so this is purely advisory.

## `dedupe_key`

`crm_data\common.py:25-27`:
```python
def make_dedupe_key(order_id, phone1, phone2, tracking_no) -> str:
    text = "|".join([clean(order_id), normalize_phone(phone1), normalize_phone(phone2), clean(tracking_no)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```
Written only by `build_record_from_mapping` (`:459`) → `insert_import_records`. **Never queried, never unique-constrained, and NULL for all manual orders.** Effectively vestigial (it was `not null unique` in the dead Supabase schema).

## The duplicate phone lock — `neon_utils.py:274-278, 784-904`

Constant (`:274-278`):
```python
CRM_TEAM_CODE = "CRM_TEAM"
CRM_TEAM_DUPLICATE_PHONE_LOCK_MESSAGE = (
    "เบอร์นี้มีอยู่ในระบบแล้ว ทีม CRM ไม่สามารถเพิ่มคำสั่งซื้อซ้ำได้ "
    "หากต้องการดำเนินการต่อ กรุณาให้หัวหน้าทีมหรือผู้มีสิทธิ์ตรวจสอบ"
)
```

**Step 1 — who is locked** (`:784-786`). Only members of `CRM_TEAM`:
```python
def should_enforce_duplicate_phone_lock(team_code: str | None) -> bool:
    return clean(team_code).upper() == CRM_TEAM_CODE
```
Team resolved from `crm_user_team_assignments` by `uploaded_by` email (`fetch_current_user_team_code`, `:763-781`).

**Step 2 — which phones count** (`:788-794`). Strictly-formatted phones only; a malformed phone silently bypasses the lock:
```python
def _valid_duplicate_lock_phones(phone1, phone2) -> list[str]:
    phones = []
    for value in (phone1, phone2):
        phone = normalize_phone(value)
        if phone and len(phone) == 10 and phone.startswith("0") and phone not in phones:
            phones.append(phone)
    return phones
```

**Step 3 — find a conflicting order** (`:815-854`):
```python
def find_duplicate_valid_order_by_phones(phone1, phone2, owner=None, staff_code=None) -> dict | None:
```
```sql
select
  id::text as id,
  order_id,
  owner,
  staff_code,
  uploaded_by,
  case
    when phone1 = any(%s) then phone1
    when phone2 = any(%s) then phone2
    else ''
  end as matched_phone
from public.crm_data_imports
where import_status = 'valid'
  and (phone1 = any(%s) or phone2 = any(%s))
order by order_date desc nulls last, updated_at desc nulls last, uploaded_at desc, id desc
limit 50
```
```python
rows = [dict(row) for row in cur.fetchall()]
if owner or staff_code:
    for row in rows:
        if not _is_same_order_owner(row, owner, staff_code):
            return row          # first row belonging to SOMEONE ELSE
    return None                 # all 50 are mine → allowed
return rows[0] if rows else None
```
So the lock fires on the **first of the newest 50 matching rows that is not owned by the current actor**. With >50 matching rows, an other-owner row beyond position 50 is invisible → lock silently bypassed.

**Step 4 — ownership comparison** (`:797-812`). `staff_code` wins if both sides have one; otherwise fall back to `owner` display name; if either side is blank on both fields → **`False` (treated as a different owner → locked)**:
```python
def _normalize_owner_compare(value: str | None) -> str:
    return " ".join(clean(value).split()).casefold()

def _is_same_order_owner(row, owner, staff_code) -> bool:
    current_staff_code  = _normalize_owner_compare(staff_code)
    existing_staff_code = _normalize_owner_compare(row.get("staff_code"))
    if current_staff_code and existing_staff_code:
        return current_staff_code == existing_staff_code

    current_owner  = _normalize_owner_compare(owner)
    existing_owner = _normalize_owner_compare(row.get("owner"))
    if current_owner and existing_owner:
        return current_owner == existing_owner

    return False
```
Comparison is whitespace-collapsed + `casefold()` on **both** sides — unlike `_followup_staff_scope`, which is exact.

**Step 5 — orchestration** (`:857-886`). **Fail-open** on any error resolving the team (e.g. `crm_user_team_assignments` missing):
```python
try:
    team_code = fetch_current_user_team_code(user_email)
except Exception as exc:
    return {"allowed": True, "team_code": None, "duplicate": None,
            "warning": f"ตรวจสอบทีมไม่สำเร็จ จึงข้าม duplicate phone lock: {exc}"}
if not should_enforce_duplicate_phone_lock(team_code):
    return {"allowed": True, "team_code": team_code, "duplicate": None, "warning": ""}
duplicate = find_duplicate_valid_order_by_phones(phone1, phone2, owner, staff_code)
return {"allowed": duplicate is None, "team_code": team_code, "duplicate": duplicate, "warning": ""}
```

**Step 6 — enforcement** (`:958-961`, inside `upsert_manual_order_items`):
```python
lock_result = check_crm_team_duplicate_phone_lock(uploaded_by or updated_by, phone1, phone2, owner, staff_code)
if not lock_result.get("allowed", True):
    raise ValueError(format_duplicate_phone_lock_error(lock_result.get("duplicate")))
duplicate_lock_warning = clean(lock_result.get("warning"))
```
`format_duplicate_phone_lock_error` (`:889-904`) appends `เบอร์ที่พบ:` / `คำสั่งซื้อเดิม:` / `ผู้ดูแลเดิม:` details. Note the lock runs on its **own** connection *before* the write transaction opens → TOCTOU window. `upsert_manual_order` (the unused single-item variant) does **not** call the lock at all.

## A second, independent owner-conflict check (UI layer)

`ui\manual_order_ui.py:214-231` (`find_manual_order_owner_conflict`) and `pages\followup.py:750-767` (`find_popup_order_owner_conflict`) — near-duplicate implementations, run **only when the actor is not EDITOR / not `can_manage_all`**:
```python
rows = neon.fetch_existing_owner_rows_by_phones(phone1, phone2)   # limit 20
allowed_codes = {normalize_staff_code(clean(v)).casefold()
                 for v in [staff_code, user.get("staff_code")] if normalize_staff_code(clean(v))}
for row in rows:
    existing_code = normalize_staff_code(clean(row.get("staff_code"))).casefold()
    if existing_code and existing_code in allowed_codes: continue
    return dict(row)         # → UI error "มีผู้ดูแลแล้ว: <owner>"
return {}
```
So three overlapping rules guard the same thing: `find_manual_order_owner_conflict` (UI, non-editor, 20 rows), `check_crm_team_duplicate_phone_lock` (repo, CRM_TEAM only, 50 rows), and `force_owner_update` (repo, EDITOR only, mass reassign). Tests: `tests\test_crm_team_duplicate_phone_lock.py`.

---

# 6. Connection handling

`neon_utils.py:316-350`:

```python
def get_neon_database_url() -> str:
    value = ""
    try:
        value = str(st.secrets.get("NEON_DATABASE_URL", "")).strip()
    except Exception:
        value = ""
    return value or os.getenv("NEON_DATABASE_URL", "").strip()


def require_neon_config() -> None:
    if psycopg is None:
        st.error("ยังไม่ได้ติดตั้ง dependency `psycopg[binary]` สำหรับเชื่อม Neon PostgreSQL")
        st.stop()
    if not get_neon_database_url():
        st.error("ยังไม่ได้ตั้งค่า `NEON_DATABASE_URL` ใน Streamlit Secrets")
        st.stop()


@contextmanager
def neon_connection():
    require_neon_config()
    conn = psycopg.connect(get_neon_database_url(), row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


@st.cache_resource(show_spinner=False)
def ensure_crm_data_imports_schema() -> bool:
    with neon_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CRM_DATA_IMPORTS_DDL)
        conn.commit()
    return True
```

- **Driver:** `psycopg` 3 (`import psycopg; from psycopg.rows import dict_row; from psycopg.types.json import Jsonb`, `:52-59`), wrapped in try/except ImportError so the module imports without it. `requirements.txt` should be checked for the exact pin.
- **No pooling of any kind.** No `psycopg_pool`, no `ConnectionPool`, no `st.connection`, no `@st.cache_resource` on the connection. **`psycopg.connect()` per function call, closed in `finally`.** Every read function on a page render opens and TCP+TLS-handshakes a fresh connection to Neon. A single Follow-up page render does: `ensure_crm_data_imports_schema` (cached after first), `fetch_followup_filter_options` (2 queries, 1 conn), `fetch_followup_page` (2 queries, 1 conn), plus `fetch_product_options` on dialog open, plus `neon_column_exists` probes. Dashboard: `fetch_dashboard_kpis` (1 conn) + `crm_sales_report_ready` (3 metadata conns unless cached) + `fetch_sales_report_rows` (1 conn) + `fetch_sales_report` daily (1 conn) + `fetch_sales_report_owner_options` (1 conn).
- **No transaction isolation configuration, no statement_timeout, no `application_name`.** Autocommit off.
- Errors surface as `st.error(...) + st.stop()` inside the connection helper — the data layer is coupled to Streamlit.
- **Config key name: `NEON_DATABASE_URL`.** Looked up first in `st.secrets`, then falling back to the `NEON_DATABASE_URL` environment variable.
- `.streamlit\secrets.toml` currently contains only `SUPABASE_URL` and `SUPABASE_ANON_KEY` (Auth) — **`NEON_DATABASE_URL` is not in the local secrets file**, so locally it must come from the env var / deployment secrets.
- Auth secret key names, `auth_utils.py:77-102` (first non-empty wins, `st.secrets` then env per name):
  - `AUTH_SUPABASE_URL`, `SUPABASE_AUTH_URL`, `CRM_SUPABASE_URL`, `SUPABASE_URL`
  - `AUTH_SUPABASE_ANON_KEY`, `SUPABASE_AUTH_ANON_KEY`, `CRM_SUPABASE_ANON_KEY`, `SUPABASE_ANON_KEY`
- Other env var: `CRM_PERF_DEBUG` (`ui\perf.py:11`) — enables `[PERF] label Xms` stdout tracing when set to `1/true/yes/on`.
- **`ensure_crm_data_imports_schema()` is a runtime DDL migration** called at the top of ~30 functions. `@st.cache_resource` means it executes once per Python process, but on that first call it runs ~40 DDL statements **plus a full `UPDATE public.crm_lead_followups`** (`neon_utils.py:196-202`) that rewrites every row where any legacy `follow_up_*` column is non-null. Any process restart / new Streamlit worker replays this. It also means the app requires DDL privileges on the production database at runtime.

---

# 7. Performance problems in the SQL

## 7.1 Connection churn (biggest single win)
No pool; one `psycopg.connect()` (TCP + TLS + Neon compute wake) per repository function. Multi-query functions like `fetch_customer_page`, `fetch_followup_page`, `fetch_followup_filter_options`, `fetch_product_page`, `_fetch_sales_report` at least reuse one connection for their 2 statements, but a page render still opens 4–8 connections. `neon_table_exists`/`neon_column_exists` each open their own connection (mitigated by `ttl=300` caching, but the cache is per-process and keyed on `(table, column)`, so ~8 distinct probes exist).

## 7.2 Count + page executed as two independent queries with the same expensive body
- `fetch_customer_page` (`:1538` and `:1540`): the entire `source_sql` derived table is scanned twice — once for `count(distinct phone_key)`, once for the window+lateral page query. Same for `fetch_followup_page` (`:2510`, `:2512`), `fetch_product_page` (`:266`, `:272`), and `_fetch_sales_report` which runs `fetch_sales_report_rows` **and** the daily aggregate over the same predicate.
- `count(distinct <case expression>)` cannot use `idx_crm_data_imports_customer_phone_latest` for a count — it must hash/sort every qualifying row.

## 7.3 Window function over the whole table before filtering
`fetch_followup_page`'s inner derived table is literally `select … from public.crm_data_imports where import_status = 'valid'` with **no scope, no filters**. The `row_number() over (partition by phone_key …)` is computed over all ~18k valid rows, and only then does the outer `{where}` (staff scope, keyword, owner, priority, dates) plus `d.rn = 1` apply. A staff user paginating with `page_size=10` still pays a full-table sort + window on every page click. `fetch_customer_page` is slightly better — `{where}` is inside the derived table — but the `row_number()` is still over the full filtered set with no `LIMIT` pushdown possible before `rn = 1`.

## 7.4 Non-indexable LATERAL joins on OR-chains (the worst)
`fetch_customer_page:1555-1569` and `_fetch_dashboard_kpis:62-76`:
```sql
left join lateral (
  select ...
  from public.crm_lead_followups l
  where l.crm_data_import_id = ranked.id
     or (nullif(l.phone1, '') is not null and (l.phone1 = ranked.phone1 or l.phone1 = ranked.phone2))
     or (nullif(l.phone2, '') is not null and (l.phone2 = ranked.phone1 or l.phone2 = ranked.phone2))
  order by l.updated_at desc nulls last, l.created_at desc nulls last
  limit 1
) l on true
```
There is **no index on `crm_lead_followups.crm_data_import_id`, `.phone1`, or `.phone2`** (only `phone_key`, `updated_at`, `(staff_code,…)`, `(lead_status,followup_status,priority)`). Combined with the 5-way `OR`, this is a **seq scan of `crm_lead_followups` per outer row** — a textbook N+1 executed inside the database. The dashboard version runs it once per deduped customer, so `total_customers` × full scan.

## 7.5 Non-sargable predicates vs. the indexes that exist
| Predicate | Loc | Index that can't be used |
|---|---|---|
| `nullif(trim(coalesce(d.staff_code, '')), '') = %s` | `:2341` | `idx_crm_data_imports_staff_code` |
| `id::text = any(%s)` | `dashboard.py:389, 411` | `crm_data_imports_pkey` (bigint→text cast) |
| `lower(btrim(user_email)) = lower(btrim(%s))` | `:773` | `ux_team_assignment_current_user` |
| `a.user_email = lower(btrim(d.uploaded_by))` | `team_sales.py:136, 226, 265` | index on `user_email` is usable, but there is **no index on `crm_data_imports.uploaded_by`** for the other side |
| `lower(creator.email) = lower({creator_expr})` | `dashboard.py:320` | `crm_user_roles_pkey` |
| `lower(btrim(coalesce(d.sku,''))) = lower(btrim(p.sku))` ×5 | `products.py:23-61` | `idx_crm_data_imports_sku`, `idx_crm_order_items_sku` |
| `d.phone1 like '%…%'` / 8× `ilike '%kw%'` | `:1750-1757`, `:2354`, `:2362`, `:2393` | `idx_crm_data_imports_phone1/2/sku/order_id` — leading wildcard, no `pg_trgm` extension anywhere |
| `d.raw_data->>'เลขคำสั่งซื้อ' ilike %s` | `:1754` | no expression/GIN index on `raw_data` |
| `coalesce(nullif(d.sale_type,''),'NEW_ORDER') in (...)` in WHERE and GROUP BY | `dashboard.py:138, 230, 304, 323` | `idx_crm_data_imports_created_staff_sale` (expression, not the bare column) |
| `(d.created_at at time zone 'Asia/Bangkok')::date` in GROUP BY/ORDER BY | `dashboard.py:225-231` | no expression index |

## 7.6 Missing indexes vs. actual WHERE/ORDER BY/JOIN columns
- `crm_lead_followups`: **`crm_data_import_id`** (joined in 2 hot laterals + `pages\customer_detail.py:111`), **`phone1`**, **`phone2`** (both in the same laterals and in `customer_detail.py:114-118`). None exist.
- `crm_data_imports`: **`uploaded_by`** — joined in all 3 team_sales queries and grouped in `fetch_sales_report_rows`. No index.
- `crm_data_imports`: **`created_at` alone** — the sales report and team_sales all range-filter on `d.created_at`. Existing composites lead with `created_at` (`idx_crm_data_imports_created_staff_sale`) so that's covered; `idx_crm_data_imports_owner_created_sale` leads with `owner`, useless for a date-only scan.
- `crm_data_imports`: **no index supporting `order by order_date desc nulls last, uploaded_at desc, id desc`** except via the partial `idx_crm_data_imports_valid_staff_order` (approval-gated) — and every single paginated query uses exactly that ORDER BY.
- `crm_orders`: no index on `(owner)` / `(staff_code)`; `updated_at`/`created_at` used in ORDER BY are unindexed.
- `crm_order_items`: `crm_data_import_id` is deleted by (`dashboard.py:404`) with no index.
- `crm_product_options`: no index supports the `sku_number` expression sort or `lower(btrim(sku))`; `archived_at` (in every status filter) is unindexed.
- Conversely, several indexes exist for nothing: `idx_crm_data_imports_tracking_no` (only used inside a leading-wildcard `ilike`), `idx_crm_data_imports_owner` (used by `d.owner = %s`, fine), `idx_crm_product_options_active_sku` (indexes the **dead `active` column**).

## 7.7 Unbounded / all-rows-then-filter-in-Python
| Loc | Query | Problem |
|---|---|---|
| `products.py:211-224` | `fetch_product_options` — `select … from crm_product_options` **no WHERE, no LIMIT** | Every product row pulled, then `ui\manual_order_ui.py:257-266` and `pages\followup.py` filter `is_active` + non-blank sku/name **in Python**. Runs on every manual-order form render and every follow-up order dialog open. |
| `:2240-2278` | `fetch_lead_followups(limit=100000)` | Entire `crm_lead_followups` table, then `customer360.py:536` builds a dict keyed by `customer_key`. |
| `:2205-2227` | `fetch_import_history` | `group by import_batch_id` over the whole table, no date bound, before `limit 50`. |
| `:1579-1716` | `fetch_customer_export_rows` | **No LIMIT on either branch.** The `latest_owner_only` branch also does full window ranking. |
| `dashboard.py:106-127` | `fetch_sales_report_owner_options` | `select distinct owner … order by owner` with **no LIMIT**. |
| `:2672-2684` | `fetch_user_roles` | no LIMIT (small table, acceptable). |
| `dashboard.py:156-186` | `summarize_sales_report_rows` | Sales-report summary (sum + distinct order count + AOV per sale_type) is computed **in Python over up to `limit=1000` rows** instead of SQL. If the real result exceeds 1000 rows the summary is silently wrong. |
| `:2125-2201` | `fetch_orders_by_phones(limit=5000)` | 5000-row default, `raw_data` jsonb in the SELECT list, plus phones **silently truncated to the first 6** (`clean_phones[:6]`). |

## 7.8 N+1 patterns
- **`upsert_manual_order_items` write loop** (`:1044-1258`): per item, 1 SELECT + 1 UPDATE-or-INSERT + 1 UPSERT into `crm_order_items` = 3 statements per line item, all in one round-trip-per-statement. A 5-line order is 15+ statements.
- **`fetch_product_delete_readiness`** (`products.py:23-61`): 5 correlated subqueries × N products; 3 of them scan `crm_data_imports` in full with `lower(btrim(...))`. Selecting 10 products ≈ 30 full scans of the 18k-row table + 20 of `crm_order_items`.
- **`apply_latest_customer_updates`** (`:1431-1447`): one `UPDATE … where id = %s` per queued row in a Python `for` loop, rather than one `update … from (values …)`.
- **`assign_owner_to_phones`** (`:1955-1965`): `executemany` of the `crm_owner_assignments` upsert — one statement per phone.
- **`upsert_product_options`** (`products.py:318-356`): SELECT + UPDATE-or-INSERT per record, in a Python loop, for the whole Excel product import. `pages\products.py:262` uses `insert_product_options` (`executemany`) for bulk, but `pages\products.py:210` and `pages\6_สินค้า.py:255` use the loop version.
- **`pages\customers.py:499-518`** (`unique_order_history` → `fetch_orders_by_phones(phones, limit=500)`): fires a full 500-row order query **from inside the per-row render loop** whenever a customer row is expanded (`render_customer_detail:304`). Uncached — `fetch_orders_by_phones` has no `@st.cache_data`, so it re-executes on every Streamlit rerun while the row is expanded. Then dedups in Python.

## 7.9 Repeated identical queries per render
- `crm_sales_report_ready()` (`dashboard.py:91-97`) calls `neon_column_exists` 3× and is itself called from `_fetch_sales_report`, `_fetch_sales_report_rows`, and `fetch_sales_report_owner_options` — so up to 9 metadata lookups per dashboard render (deduped by the 300 s `st.cache_data` on `neon_column_exists`, then re-paid every 5 minutes).
- `neon_column_exists("crm_data_imports","quantity")` is called from `fetch_customer_360_orders`, `fetch_orders_by_phones`, `fetch_customer_export_rows`, `_fetch_sales_report_rows`, `upsert_manual_order_items` — the same probe scattered across 5 call sites.
- `ensure_crm_data_imports_schema()` at the head of ~30 functions (cheap after the first call thanks to `cache_resource`, but it is a function-call-per-query pattern).
- `fetch_customer_by_id` and `fetch_customer_360_base` are byte-identical SQL — two functions, same query.
- The `_MANUAL_ROW_SQL` "is this a manual order" 3-way OR predicate appears **6 times** (`team_sales.py:16-22` ×3 uses, `dashboard.py:313-317, 390-394, 414-418`), each time forcing a `raw_data->>'source'` extraction on every scanned row.
- `fetch_followup_filter_options` is `@st.cache_data(ttl=900)` keyed on the **whole `user` dict** — any mutation to the session user dict (e.g. a re-fetched role) invalidates it and re-runs 2 `select distinct` queries.

## 7.10 Correctness-flavoured issues that will bite the port
- **`crm_lead_followups` joined twice in `fetch_followup_page`'s count query** but once in the page query — count and page can only agree because `customer_key` is the PK.
- **Count/page semantic mismatch in `fetch_customer_page`**: `count(distinct phone_key)` counts distinct customers, but the page's `where rn = 1` + `limit/offset` is applied after a LATERAL that could theoretically not be 1:1 — it is 1:1 here only because of `limit 1` inside the lateral.
- **The Thai priority mojibake in `fetch_followup_page`'s ORDER BY** (`:2587, 2590, 2594, 2597`) — sorting ignores legacy Thai priorities while filtering honours them.
- **`current_date` (server TZ) in the dashboard KPIs vs. explicit `at time zone 'Asia/Bangkok'` in the sales report** — "due today" / "overdue" are computed in the DB's timezone.
- **`d.sale_type in ('NEW_ORDER','UPSELL')` (team_sales) vs `coalesce(nullif(d.sale_type,''),'NEW_ORDER') in (...)` (dashboard)** — NULL/blank `sale_type` rows count toward the dashboard total but not the team total.
- **`fetch_filter_options` has no `import_status = 'valid'` filter**; `fetch_crm_owner_options` does. Two owner dropdowns, two different populations.
- **`assign_owner_to_phones` and `assign_owner_to_order_record` have no `import_status` filter**, so they mutate `invalid` rows too; `assign_url_to_phones` does filter.
- **`delete_import_batch` and `delete_sales_report_records` never clean `crm_lead_followups`** — follow-up rows outlive their `crm_data_imports` parent (there's no FK to enforce it).
- `pages\customers.py:458` writes `customer_key` as a bare phone number while every other writer uses `customer_id:<id>` — see §1.2.

---

## Appendix: dead / orphaned data-layer code
| Item | Loc | Status |
|---|---|---|
| `customer360.py` (1342 lines, 8 data functions) | — | **Not imported by any page**; `nav_utils.py:13-43` routes only to the English page files. Its `load_crm_customers` would also return 0 rows (scope=`1 = 0`). |
| `upsert_manual_order` | `neon_utils.py:522-727` | No caller |
| `_normalized_text_sql` | `neon_utils.py:2330` | No caller |
| `owner_to_staff_code` | `neon_utils.py:353` | No caller |
| `assign_owner_to_phones` + all writes to `crm_owner_assignments` | `neon_utils.py:1915` | Only caller is `customer360.py:816` (orphaned) → the table is read-only in practice |
| `crm_product_options.active` | migration `202606020001` | Shadow column, written once, never read by app code |
| `crm_data_imports.dedupe_key` | — | Written on Excel import only, never queried, no unique constraint |
| `crm_user_roles.owner_alias` | migration `202606020002` | Read into the session dict, never used in a WHERE clause |
| `supabase\migrations\*` (11 files, incl. `import_batches`/`import_staging`/`import_logs`/`import_backups`, `crm_customers`, `order_history`, `crm_customers_deduped`, `crm_customer_filter_options`) | — | Legacy schema, dropped by `202605290004`; Supabase is Auth-only |
| `pages\1_รายงาน.py`, `2_KPI.py`, `8_ประวัติการซื้อ.py`, `10_System_Settings.py` | — | Placeholders |
| `pages\4_เพิ่มข้อมูลลูกค้า.py`, `5_ฐานข้อมูลลูกค้า.py`, `6_สินค้า.py`, `7_พนักงาน.py`, `9_ติดตามลูกค้า.py` | — | `st.switch_page(...)` + `st.stop()` at the top; the code below is unreachable but still contains full data-layer call sites |
| `sync_to_supabase.py`, `sync_crm_customers_to_supabase.py`, `sync_data_raw_to_supabase.py` | — | 3-line legacy stubs |
| `DATABASE_CODE_OVERVIEW.md:79, 139, 206, 244` | — | References a nonexistent `image_url` column and a nonexistent `fetch_order_product_options()` |