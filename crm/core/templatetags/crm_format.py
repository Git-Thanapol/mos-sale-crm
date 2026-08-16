from django import template

from crm.core.thai import ALL_OPTION_LABEL, FOLLOWUP_STATUS_LABELS, LEAD_STATUS_LABELS, PRIORITY_TONE

register = template.Library()


@register.filter
def thai_int(value) -> str:
    """1990 -> "1,990". Used for the pager caption
    "หน้า {page} / {total_pages} · ทั้งหมด {total_rows} รายการ".
    """
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


@register.filter
def thai_amount(value) -> str:
    """1990.5 -> "1,990.50". Same comma grouping as thai_int, for money
    and other 2-decimal aggregates.
    """
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


@register.filter
def lead_label(value: str) -> str:
    if value == ALL_OPTION_LABEL:
        return ALL_OPTION_LABEL
    return LEAD_STATUS_LABELS.get(value, value)


@register.filter
def followup_status_label(value: str) -> str:
    if value == ALL_OPTION_LABEL:
        return ALL_OPTION_LABEL
    return FOLLOWUP_STATUS_LABELS.get(value, value)


@register.filter
def priority_tone(value: str) -> str:
    return PRIORITY_TONE.get(value, "gray")


@register.filter
def get_item(collection, key):
    """Dict/list lookup by a variable key — {{ my_dict|get_item:some_var }}.
    Used by the daily sell matrix table (cells keyed by member user_id).
    """
    try:
        return collection[key]
    except (KeyError, IndexError, TypeError):
        return None


@register.filter
def field(form, name: str):
    """Look up a form field by a computed name, e.g. "sku_" + i.
    Usage: {% with name="sku_"|add:i %}{{ form|field:name }}{% endwith %}
    """
    return form[name]
