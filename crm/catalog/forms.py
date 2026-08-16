from django import forms

from crm.catalog.services import DEFAULT_PRODUCT_GROUP


class ProductCreateForm(forms.Form):
    sku = forms.CharField(max_length=64)
    product_name = forms.CharField(max_length=255)
    product_group = forms.CharField(max_length=128, required=False, initial=DEFAULT_PRODUCT_GROUP)


class ProductImportForm(forms.Form):
    file = forms.FileField()
