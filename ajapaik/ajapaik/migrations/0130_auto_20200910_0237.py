# Generated by Django 2.2.16 on 2020-09-09 23:37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ajapaik', '0129_auto_20200907_0036'),
    ]

    operations = [
        migrations.AddField(
            model_name='photo',
            name='scene',
            field=models.PositiveSmallIntegerField(blank=True, choices=[(0, 'Interior'), (1, 'Exterior')], null=True,
                                                   verbose_name='Scene'),
        ),
    ]
