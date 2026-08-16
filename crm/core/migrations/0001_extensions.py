from django.contrib.postgres.operations import BtreeGistExtension, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """pg_trgm backs the trigram search indexes deferred in
    crm.imports/customers/orders models (TODO comments there);
    btree_gist backs the effective-dated team-assignment exclusion
    constraint planned for Phase 5 (crm.teams). Installing both now so
    later migrations can rely on them being present.
    """

    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        TrigramExtension(),
        BtreeGistExtension(),
    ]
