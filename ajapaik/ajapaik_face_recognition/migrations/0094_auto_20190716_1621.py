# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-07-16 13:21
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ajapaik_face_recognition', '0093_facerecognitionrectangle_facephoto'),
    ]

    operations = [
        migrations.AlterField(
            model_name='facerecognitionrectangle',
            name='facePhoto',
            field=models.ImageField(blank=True, max_length=255, null=True, upload_to='uploads',
                                    verbose_name='FacePhoto'),
        ),
    ]
