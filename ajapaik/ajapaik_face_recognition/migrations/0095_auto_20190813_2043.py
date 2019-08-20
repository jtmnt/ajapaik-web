# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-08-13 17:43
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ajapaik_face_recognition', '0094_facerecognitionrectanglefeedback_alternative_subject'),
    ]

    operations = [
        migrations.AddField(
            model_name='facerecognitionrectanglefeedback',
            name='is_correct_person',
            field=models.NullBooleanField(),
        ),
        migrations.AlterField(
            model_name='facerecognitionrectanglefeedback',
            name='alternative_subject',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='ajapaik.Album'),
        ),
    ]
