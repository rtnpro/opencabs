# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2018-12-16 11:05
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0006_auto_20181203_1713'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='details',
            field=models.TextField(blank=True, max_length=1024, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='status',
            field=models.CharField(blank=True, choices=[('WAT', 'Waiting'), ('INP', 'Input'), ('RFN', 'Refunded'), ('REJ', 'Rejected'), ('CNF', 'Confirmed'), ('ERR', 'Error')], max_length=3, null=True),
        ),
    ]