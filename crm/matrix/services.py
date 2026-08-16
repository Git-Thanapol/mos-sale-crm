from __future__ import annotations

from crm.matrix.models import Holiday


def create_holiday(*, date, scope: str, status: str, user_id: int | None, note: str, created_by: str) -> Holiday:
    if scope == Holiday.SCOPE_INDIVIDUAL and not user_id:
        raise ValueError("ต้องเลือกพนักงานเมื่อขอบเขตเป็นรายคน")
    if scope == Holiday.SCOPE_ALL:
        user_id = None

    holiday, _ = Holiday.objects.get_or_create(
        date=date,
        scope=scope,
        status=status,
        user_id=user_id,
        defaults={"note": note, "created_by": created_by},
    )
    return holiday


def delete_holiday(holiday_id: int) -> None:
    Holiday.objects.filter(id=holiday_id).delete()
