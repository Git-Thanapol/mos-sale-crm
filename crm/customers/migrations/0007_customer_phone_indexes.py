"""Index phone1 and phone2 so the import preview's phone lookup stops scanning.

crm.importv2.services.preview._resolve_customer filters on phone1 OR phone2 OR
phone_key. Only phone_key was indexed, so every lookup scanned crm_customer and
a 2,027-row preview outlasted nginx's 60 second proxy timeout (504).

Built CONCURRENTLY so writes to crm_customer keep working during a deploy, which
needs atomic = False. The connection's 15 second statement_timeout
(config/settings/base.py) is lifted for the build: hitting it would leave an
INVALID index behind. Build it when no long import transaction is open, because
CONCURRENTLY waits for those to finish.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("customers", "0006_phone_key_not_unique"),
    ]

    operations = [
        migrations.RunSQL("SET statement_timeout = 0", "RESET statement_timeout"),
        AddIndexConcurrently(
            model_name="customer",
            index=models.Index(fields=["phone1"], name="ix_customer_phone1"),
        ),
        AddIndexConcurrently(
            model_name="customer",
            index=models.Index(fields=["phone2"], name="ix_customer_phone2"),
        ),
        migrations.RunSQL("RESET statement_timeout", "SET statement_timeout = 0"),
    ]
