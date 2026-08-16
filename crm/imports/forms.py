from django import forms


class ExcelUploadForm(forms.Form):
    file = forms.FileField(
        label="ไฟล์ Excel (.xlsx)",
        widget=forms.ClearableFileInput(attrs={"class": "crm-input", "accept": ".xlsx"}),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("รองรับเฉพาะไฟล์ .xlsx")
        return f
