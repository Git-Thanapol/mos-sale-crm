"""Drop UNIQUE on crm_customer.phone_key (client request, Nov 2026).

phone_key stays indexed for lookups. Reversing re-adds UNIQUE and fails while
duplicate keys exist, which is the correct refusal.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0005_customer_customer_uid"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="phone_key",
            field=models.CharField(db_index=True, max_length=64),
        ),
    ]
