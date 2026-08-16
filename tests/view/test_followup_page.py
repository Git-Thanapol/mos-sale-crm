import pytest
from django.urls import reverse

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.followups.models import Followup

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def admin_user(client):
    user = User.objects.create_user(email="admin@example.com", password="x", role="ADMIN")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def test_followup_page_requires_can_view_followup(client, admin_user):
    """ADMIN cannot view follow-up (docs/DECISIONS.md preserved asymmetry)."""
    resp = client.get(reverse("followups:list"))
    assert resp.status_code == 403
    assert "หน้านี้ใช้ได้เฉพาะ EDITOR และพนักงานที่ดูแลลูกค้า" in str(resp.content, "utf-8")


def test_followup_page_renders_for_editor(client, editor):
    resp = client.get(reverse("followups:list"))
    assert resp.status_code == 200


def test_followup_page_pager_caption_format(client, editor):
    customer = Customer.objects.create(phone_key="0899999901", phone1="0899999901", customer_name="ทดสอบ")
    Followup.objects.create(customer=customer)
    resp = client.get(reverse("followups:list"))
    body = resp.content.decode("utf-8")
    assert "หน้า 1 / 1" in body
    assert "ทั้งหมด 1 รายการ" in body


def test_followup_filters_compose_keyword_with_digits_and_owner(client, editor):
    """The legacy bug: a keyword containing digits made build_followup_where
    return early, silently discarding every other filter (owner, status,
    etc). Must not reproduce here — both filters apply together.
    """
    c1 = Customer.objects.create(
        phone_key="0899999902", phone1="0899999902", customer_name="ตรงเงื่อนไข", owner_display="พนักงาน A"
    )
    c2 = Customer.objects.create(
        phone_key="0899999903", phone1="0899999903", customer_name="ไม่ตรงเงื่อนไข", owner_display="พนักงาน B"
    )
    Followup.objects.create(customer=c1)
    Followup.objects.create(customer=c2)

    resp = client.get(reverse("followups:list"), {"keyword": "0899999902", "owner": "พนักงาน A"})
    body = resp.content.decode("utf-8")
    assert "ตรงเงื่อนไข" in body
    assert "ไม่ตรงเงื่อนไข" not in body

    # owner filter narrows further: same keyword, wrong owner -> no match
    resp2 = client.get(reverse("followups:list"), {"keyword": "0899999902", "owner": "พนักงาน B"})
    assert "ตรงเงื่อนไข" not in resp2.content.decode("utf-8")
