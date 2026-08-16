from django import forms

from crm.core.thai import FOLLOWUP_PRIORITY_OPTIONS, FOLLOWUP_STATUS_LABELS, LEAD_STATUS_LABELS


class FollowupEditForm(forms.Form):
    lead_status = forms.ChoiceField(
        label="สถานะลูกค้า", choices=[(k, v) for k, v in LEAD_STATUS_LABELS.items()]
    )
    status = forms.ChoiceField(
        label="สถานะติดตาม", choices=[(k, v) for k, v in FOLLOWUP_STATUS_LABELS.items()]
    )
    priority = forms.ChoiceField(label="ความสำคัญ", choices=[(p, p) for p in FOLLOWUP_PRIORITY_OPTIONS])
    next_followup_date = forms.DateField(label="วันนัดติดตาม", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    clear_date = forms.BooleanField(label="ล้างวันที่", required=False)
    note = forms.CharField(label="โน้ตติดตาม", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "clear_date":
                continue
            css = "crm-select" if isinstance(field.widget, forms.Select) else "crm-input"
            field.widget.attrs.setdefault("class", css)


# A fixed number of line-item slots, since dynamic add/remove needs Alpine
# (not yet vendored — see static/crm/vendor/README.md). Each slot is
# optional; blank ones are ignored on save. Revisit once htmx/Alpine land.
ORDER_LINE_SLOTS = 6


class AddOrderForm(forms.Form):
    order_no = forms.CharField(label="หมายเลขคำสั่งซื้อ", required=True)
    sale_type = forms.ChoiceField(
        label="ประเภทการขาย", choices=[("NEW_ORDER", "NEW_ORDER"), ("UPSELL", "UPSELL"), ("FOLLOW", "FOLLOW")]
    )
    url = forms.CharField(label="URL", required=False)
    address = forms.CharField(label="ที่อยู่", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "crm-select" if isinstance(field.widget, forms.Select) else "crm-input"
            field.widget.attrs.setdefault("class", css)
        for i in range(ORDER_LINE_SLOTS):
            self.fields[f"sku_{i}"] = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "crm-input", "placeholder": "SKU"}))
            self.fields[f"product_name_{i}"] = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "crm-input", "placeholder": "ชื่อสินค้า"}))
            self.fields[f"qty_{i}"] = forms.IntegerField(required=False, min_value=1, widget=forms.NumberInput(attrs={"class": "crm-input"}))
            self.fields[f"amount_{i}"] = forms.DecimalField(required=False, min_value=0, widget=forms.NumberInput(attrs={"class": "crm-input", "step": "0.01"}))

    def line_items(self) -> list[dict]:
        items = []
        for i in range(ORDER_LINE_SLOTS):
            sku = self.cleaned_data.get(f"sku_{i}", "")
            name = self.cleaned_data.get(f"product_name_{i}", "")
            qty = self.cleaned_data.get(f"qty_{i}")
            amount = self.cleaned_data.get(f"amount_{i}")
            if sku or name:
                items.append({"sku": sku, "product_name": name, "qty": qty or 1, "amount": amount})
        return items
