# Generated by Django 2.2.17 on 2020-12-30 00:21

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ajapaik', '0007_auto_20201230_0204'),
    ]

    operations = [
        migrations.AlterField(
            model_name='album',
            name='muis_person_ids',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.IntegerField(blank=True),
                blank=True,
                default=list,
                null=True,
                size=None
            ),
        ),
    ]
