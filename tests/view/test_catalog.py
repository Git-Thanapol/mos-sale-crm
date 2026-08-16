from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook

from crm.accounts.models import User
from crm.catalog import selectors, services
from crm.catalog.models import Product
from crm.customers.models import Customer
from crm.imports.models import StagingImportRow
from crm.orders.models import Order, OrderLine

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def viewer(client):
    user = User.objects.create_user(email="viewer@example.com", password="x", role="ทั่วไป")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def _make_xlsx(rows: list[list], with_header=True) -> bytes:
    wb = Workbook()
    ws = wb.active
    if with_header:
        ws.append(["SKU", "ชื่อสินค้า", "กลุ่มสินค้า"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- index view ---

def test_index_renders_for_any_logged_in_user(client, viewer):
    resp = client.get(reverse("catalog:list"))
    assert resp.status_code == 200
    assert "is_editor" in resp.context
    assert resp.context["is_editor"] is False


def test_index_shows_edit_controls_for_editor(client, editor):
    resp = client.get(reverse("catalog:list"))
    assert resp.status_code == 200
    assert resp.context["is_editor"] is True
    assert resp.context["create_form"] is not None


# --- services: create_or_merge_product ---

def test_create_or_merge_creates_new_product():
    product = services.create_or_merge_product(
        sku="SP0001", product_name="Widget", product_group="ทั่วไป", actor_email="editor@example.com"
    )
    assert product.is_active is True
    assert product.sku_number == 1
    assert Product.objects.count() == 1


def test_create_or_merge_reactivates_exact_match_instead_of_erroring():
    existing = Product.objects.create(
        sku="SP0002", product_name="Widget2", product_group="ทั่วไป", is_active=False
    )
    result = services.create_or_merge_product(
        sku="SP0002", product_name="Widget2", product_group="ทั่วไป", actor_email="editor@example.com"
    )
    assert result.id == existing.id
    assert Product.objects.count() == 1  # merged, not a second row
    result.refresh_from_db()
    assert result.is_active is True
    assert result.updated_by == "editor@example.com"


def test_create_or_merge_defaults_blank_group_to_thai_general():
    product = services.create_or_merge_product(
        sku="SP0003", product_name="Widget3", product_group="", actor_email="editor@example.com"
    )
    assert product.product_group == "ทั่วไป"


# --- services: bulk activate/deactivate ---

def test_bulk_set_active_updates_only_selected_ids():
    p1 = Product.objects.create(sku="A", product_name="A", product_group="G", is_active=True)
    p2 = Product.objects.create(sku="B", product_name="B", product_group="G", is_active=True)
    updated = services.bulk_set_active([p1.id], False, "editor@example.com")
    assert updated == 1
    p1.refresh_from_db()
    p2.refresh_from_db()
    assert p1.is_active is False
    assert p2.is_active is True


# --- services: archive / restore ---

def test_archive_sets_fields_and_deactivates():
    product = Product.objects.create(sku="A", product_name="A", product_group="G", is_active=True)
    result = services.archive_products([product.id], "no longer sold", "editor@example.com")
    assert result == {"requested": 1, "updated": 1, "skipped": 0}
    product.refresh_from_db()
    assert product.archived_at is not None
    assert product.archived_by == "editor@example.com"
    assert product.archive_reason == "no longer sold"
    assert product.is_active is False


def test_archive_blank_reason_falls_back_to_default_english_string():
    product = Product.objects.create(sku="A", product_name="A", product_group="G")
    services.archive_products([product.id], "", "editor@example.com")
    product.refresh_from_db()
    assert product.archive_reason == "Archived from Product Master"


def test_archive_skips_already_archived_rows():
    product = Product.objects.create(sku="A", product_name="A", product_group="G", archived_at=None)
    services.archive_products([product.id], "r1", "editor@example.com")
    result = services.archive_products([product.id], "r2", "editor@example.com")
    assert result == {"requested": 1, "updated": 0, "skipped": 1}


def test_restore_leaves_is_active_false():
    product = Product.objects.create(sku="A", product_name="A", product_group="G")
    services.archive_products([product.id], "r1", "editor@example.com")
    result = services.restore_products([product.id], "editor@example.com")
    assert result == {"requested": 1, "updated": 1, "skipped": 0}
    product.refresh_from_db()
    assert product.archived_at is None
    assert product.archived_by == ""
    assert product.archive_reason == ""
    assert product.is_active is False  # never auto-reactivated


# --- selectors: product_page filters ---

def test_product_page_active_filter_excludes_inactive_and_archived():
    Product.objects.create(sku="A", product_name="Active", product_group="G", is_active=True)
    Product.objects.create(sku="B", product_name="Inactive", product_group="G", is_active=False)
    archived = Product.objects.create(sku="C", product_name="Archived", product_group="G", is_active=True)
    services.archive_products([archived.id], "r", "e@example.com")

    page = selectors.product_page("สินค้าที่เปิดใช้งาน", "SP น้อยไปมาก", "", 1)
    names = [p.product_name for p in page.items]
    assert names == ["Active"]


def test_product_page_all_status_still_excludes_archived():
    Product.objects.create(sku="A", product_name="Active", product_group="G", is_active=True)
    Product.objects.create(sku="B", product_name="Inactive", product_group="G", is_active=False)
    archived = Product.objects.create(sku="C", product_name="Archived", product_group="G", is_active=True)
    services.archive_products([archived.id], "r", "e@example.com")

    page = selectors.product_page("สินค้าทั้งหมด", "SP น้อยไปมาก", "", 1)
    names = {p.product_name for p in page.items}
    assert names == {"Active", "Inactive"}


def test_product_page_archived_filter_shows_only_archived():
    archived = Product.objects.create(sku="C", product_name="Archived", product_group="G", is_active=True)
    services.archive_products([archived.id], "r", "e@example.com")
    Product.objects.create(sku="A", product_name="Active", product_group="G", is_active=True)

    page = selectors.product_page("สินค้าที่เก็บถาวร", "SP น้อยไปมาก", "", 1)
    names = [p.product_name for p in page.items]
    assert names == ["Archived"]


def test_product_page_search_matches_sku_or_name():
    Product.objects.create(sku="SP9", product_name="Match Me", product_group="G")
    Product.objects.create(sku="SP1", product_name="Other", product_group="G")

    page = selectors.product_page("สินค้าทั้งหมด", "SP น้อยไปมาก", "Match", 1)
    assert [p.sku for p in page.items] == ["SP9"]


# --- selectors: delete readiness ---

def test_readiness_blocked_when_used_in_order_line():
    product = Product.objects.create(sku="SP1", product_name="Widget", product_group="G")
    customer = Customer.objects.create(phone_key="0891111111", phone1="0891111111", customer_name="C")
    order = Order.objects.create(customer=customer, order_no="O1")
    OrderLine.objects.create(order=order, sku="SP1", product_name="Widget", quantity=1, amount=10)

    [result] = selectors.product_delete_readiness([product.id])
    assert result["status"] == "blocked_used"
    assert result["reason"] == "usage_found"
    assert "order_items_sku" in result["usage_sources"]


def test_readiness_blocked_when_used_in_staging_import_row():
    product = Product.objects.create(sku="SP2", product_name="Gadget", product_group="G")
    StagingImportRow.objects.create(import_batch_id="11111111-1111-1111-1111-111111111111", sku="SP2")

    [result] = selectors.product_delete_readiness([product.id])
    assert result["status"] == "blocked_used"
    assert "imports_sku" in result["usage_sources"]


def test_readiness_tentative_when_no_usage_found():
    product = Product.objects.create(sku="SP3", product_name="Unused", product_group="G")
    [result] = selectors.product_delete_readiness([product.id])
    assert result["status"] == "tentative_no_usage"
    assert result["reason"] == "no_usage_found_in_text_checks"


def test_readiness_unknown_for_missing_product():
    [result] = selectors.product_delete_readiness([999999])
    assert result["status"] == "unsafe_unknown"
    assert result["reason"] == "product_not_found"


def test_readiness_unknown_for_blank_sku_and_name():
    product = Product.objects.create(sku="", product_name="", product_group="G")
    [result] = selectors.product_delete_readiness([product.id])
    assert result["status"] == "unsafe_unknown"
    assert result["reason"] == "blank_sku_and_product_name"


# --- services: Excel import ---

def test_import_workbook_creates_new_rows_and_skips_header():
    xlsx = _make_xlsx([["SP1", "Widget", "General"], ["SP2", "Gadget", "General"]])
    result = services.import_products_workbook(BytesIO(xlsx), "editor@example.com")
    assert result == {"created": 2, "duplicate": 0, "invalid": 0}
    assert Product.objects.count() == 2


def test_import_workbook_skips_duplicate_against_existing_db_row():
    Product.objects.create(sku="SP1", product_name="Widget", product_group="General")
    xlsx = _make_xlsx([["SP1", "Widget", "General"]])
    result = services.import_products_workbook(BytesIO(xlsx), "editor@example.com")
    assert result == {"created": 0, "duplicate": 1, "invalid": 0}


def test_import_workbook_skips_duplicate_within_same_file():
    xlsx = _make_xlsx([["SP1", "Widget", "General"], ["SP1", "Widget", "General"]])
    result = services.import_products_workbook(BytesIO(xlsx), "editor@example.com")
    assert result == {"created": 1, "duplicate": 1, "invalid": 0}


def test_import_workbook_flags_incomplete_rows():
    xlsx = _make_xlsx([["SP1", "", "General"]])
    result = services.import_products_workbook(BytesIO(xlsx), "editor@example.com")
    assert result == {"created": 0, "duplicate": 0, "invalid": 1}


def test_import_workbook_without_header_row_still_works():
    xlsx = _make_xlsx([["SP1", "Widget", "General"]], with_header=False)
    result = services.import_products_workbook(BytesIO(xlsx), "editor@example.com")
    assert result == {"created": 1, "duplicate": 0, "invalid": 0}


def test_import_workbook_empty_file_raises():
    wb = Workbook()
    buf = BytesIO()
    wb.save(buf)
    with pytest.raises(services.WorkbookFormatError):
        services.import_products_workbook(BytesIO(buf.getvalue()), "editor@example.com")


# --- permission gate on write views ---

@pytest.mark.parametrize("url_name,args", [
    ("catalog:create", []),
    ("catalog:bulk_action", []),
    ("catalog:import", []),
])
def test_write_views_block_non_editor(client, viewer, url_name, args):
    resp = client.post(reverse(url_name, args=args), {})
    assert resp.status_code == 403


def test_save_row_blocks_non_editor(client, viewer):
    product = Product.objects.create(sku="A", product_name="A", product_group="G")
    resp = client.post(reverse("catalog:save_row", args=[product.id]), {})
    assert resp.status_code == 403


def test_deactivate_blocks_non_editor(client, viewer):
    product = Product.objects.create(sku="A", product_name="A", product_group="G")
    resp = client.post(reverse("catalog:deactivate", args=[product.id]), {})
    assert resp.status_code == 403


# --- views: create/save_row/deactivate/bulk_action end-to-end ---

def test_create_view_success(client, editor):
    resp = client.post(
        reverse("catalog:create"),
        {"sku": "SP1", "product_name": "Widget", "product_group": "General"},
        follow=True,
    )
    assert resp.status_code == 200
    assert Product.objects.filter(sku="SP1", product_name="Widget").exists()


def test_create_view_requires_sku_and_name(client, editor):
    resp = client.post(reverse("catalog:create"), {"sku": "", "product_name": ""}, follow=True)
    assert resp.status_code == 200
    assert Product.objects.count() == 0


def test_save_row_view_updates_fields(client, editor):
    product = Product.objects.create(sku="A", product_name="A", product_group="G", is_active=True)
    resp = client.post(
        reverse("catalog:save_row", args=[product.id]),
        {"sku": "A2", "product_name": "A2 name", "product_group": "G2", "is_active": "on"},
        follow=True,
    )
    assert resp.status_code == 200
    product.refresh_from_db()
    assert product.sku == "A2"
    assert product.product_name == "A2 name"
    assert product.product_group == "G2"


def test_save_row_view_rejects_archived_product(client, editor):
    product = Product.objects.create(sku="A", product_name="A", product_group="G")
    services.archive_products([product.id], "r", editor.email)
    resp = client.post(
        reverse("catalog:save_row", args=[product.id]),
        {"sku": "A2", "product_name": "A2", "product_group": "G", "is_active": "on"},
    )
    assert resp.status_code == 403


def test_deactivate_view(client, editor):
    product = Product.objects.create(sku="A", product_name="A", product_group="G", is_active=True)
    resp = client.post(reverse("catalog:deactivate", args=[product.id]), {}, follow=True)
    assert resp.status_code == 200
    product.refresh_from_db()
    assert product.is_active is False


def test_bulk_action_requires_confirmation_checkbox(client, editor):
    product = Product.objects.create(sku="A", product_name="A", product_group="G", is_active=True)
    client.post(
        reverse("catalog:bulk_action"),
        {"action": "deactivate", "product_id": [str(product.id)]},  # no confirm
        follow=True,
    )
    product.refresh_from_db()
    assert product.is_active is True  # unchanged: confirmation was missing


def test_bulk_action_activate_with_confirmation(client, editor):
    product = Product.objects.create(sku="A", product_name="A", product_group="G", is_active=False)
    client.post(
        reverse("catalog:bulk_action"),
        {"action": "activate", "product_id": [str(product.id)], "confirm": "on"},
        follow=True,
    )
    product.refresh_from_db()
    assert product.is_active is True


def test_bulk_action_check_readiness_does_not_require_confirmation(client, editor):
    product = Product.objects.create(sku="A", product_name="A", product_group="G")
    resp = client.post(
        reverse("catalog:bulk_action"),
        {"action": "check_readiness", "product_id": [str(product.id)]},  # no confirm
        follow=True,
    )
    assert resp.status_code == 200
    assert resp.context["readiness_report"] is not None


def test_bulk_action_rejects_non_integer_ids(client, editor):
    resp = client.post(
        reverse("catalog:bulk_action"),
        {"action": "activate", "product_id": ["not-a-number"], "confirm": "on"},
        follow=True,
    )
    assert resp.status_code == 200


def test_import_view_end_to_end(client, editor):
    xlsx = _make_xlsx([["SP1", "Widget", "General"]])
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    upload = SimpleUploadedFile("products.xlsx", xlsx, content_type=content_type)
    resp = client.post(reverse("catalog:import"), {"file": upload}, follow=True)
    assert resp.status_code == 200
    assert Product.objects.filter(sku="SP1").exists()


def test_options_returns_active_matching_products(client, viewer):
    Product.objects.create(sku="SP001", product_name="Widget A", product_group="ทั่วไป")
    Product.objects.create(sku="SP002", product_name="Widget B", product_group="ทั่วไป", is_active=False)
    Product.objects.create(sku="SP003", product_name="Gadget C", product_group="ทั่วไป")

    resp = client.get(reverse("catalog:options"), {"q": "widget"})
    assert resp.status_code == 200
    skus = [row["sku"] for row in resp.json()]
    assert skus == ["SP001"]  # SP002 inactive, SP003 doesn't match "widget"


def test_options_empty_query_still_capped(client, viewer):
    for i in range(25):
        Product.objects.create(sku=f"SP{i:03d}", product_name=f"P{i}", product_group="ทั่วไป")
    resp = client.get(reverse("catalog:options"))
    assert resp.status_code == 200
    assert len(resp.json()) == 20
