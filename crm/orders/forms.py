from django import forms

from crm.core.identity import validate_phone_pair

ORDER_LINE_SLOTS = 6


class ManualOrderForm(forms.Form):
    customer_name = forms.CharField(label="ชื่อลูกค้า", required=True)
    phone1 = forms.CharField(
        label="เบอร์โทร", required=False,
        widget=forms.TextInput(attrs={
            "inputmode": "numeric", "pattern": r"0\d{9}", "maxlength": "10",
            "title": "ต้องเป็นตัวเลข 10 หลัก ขึ้นต้นด้วย 0",
        }),
    )
    phone2 = forms.CharField(
        label="เบอร์สำรอง", required=False,
        widget=forms.TextInput(attrs={
            "inputmode": "numeric", "pattern": r"0\d{9}", "maxlength": "10",
            "title": "ต้องเป็นตัวเลข 10 หลัก ขึ้นต้นด้วย 0",
        }),
    )
    order_no = forms.CharField(label="หมายเลขคำสั่งซื้อ", required=True)
    sale_type = forms.ChoiceField(
        label="ประเภทการขาย", choices=[("NEW_ORDER", "NEW_ORDER"), ("UPSELL", "UPSELL"), ("FOLLOW", "FOLLOW")]
    )
    url = forms.CharField(label="URL", required=False)
    province = forms.CharField(label="จังหวัด", required=False)
    city = forms.CharField(label="อำเภอ", required=False)
    postal_code = forms.CharField(
        label="รหัสไปรษณีย์", required=False,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "maxlength": "5"}),
    )
    address = forms.CharField(label="ที่อยู่", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    # Populated per-request in the view with the real (name, staff_code) choices
    # from owner_assignment_options() — only rendered/used for manager actors.
    staff_code = forms.ChoiceField(label="ผู้ดูแล", required=False, choices=[])

    def __init__(self, *args, owner_choices: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["staff_code"].choices = [(code, name) for name, code in (owner_choices or [])]
        for name, field in self.fields.items():
            css = "crm-select" if isinstance(field.widget, (forms.Select,)) else "crm-input"
            field.widget.attrs.setdefault("class", css)
        for i in range(ORDER_LINE_SLOTS):
            self.fields[f"sku_{i}"] = forms.CharField(required=False, widget=forms.TextInput(attrs={
                "class": "crm-input", "placeholder": "SKU", "list": f"sku-datalist-{i}", "autocomplete": "off",
            }))
            self.fields[f"product_name_{i}"] = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "crm-input", "placeholder": "ชื่อสินค้า"}))
            self.fields[f"qty_{i}"] = forms.IntegerField(required=False, min_value=1, widget=forms.NumberInput(attrs={"class": "crm-input"}))
            self.fields[f"amount_{i}"] = forms.DecimalField(required=False, min_value=0, widget=forms.NumberInput(attrs={"class": "crm-input", "step": "0.01"}))

    def clean(self):
        cleaned = super().clean()
        phone1, phone2 = cleaned.get("phone1", ""), cleaned.get("phone2", "")
        errors = validate_phone_pair(phone1, phone2, require_one=True)
        if errors:
            raise forms.ValidationError(errors)
        return cleaned

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
