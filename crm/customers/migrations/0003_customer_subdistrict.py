from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='subdistrict',
            field=models.CharField(blank=True, default='', max_length=128),
            preserve_default=False,
        ),
    ]
