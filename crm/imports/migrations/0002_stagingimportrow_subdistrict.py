from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('imports', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='stagingimportrow',
            name='subdistrict',
            field=models.CharField(blank=True, default='', max_length=128),
            preserve_default=False,
        ),
    ]
