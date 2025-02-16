# Generated by Django 5.1.5 on 2025-01-20 13:27

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Cliente',
            fields=[
                ('cedula', models.CharField(max_length=10, primary_key=True, serialize=False)),
                ('nombre', models.CharField(max_length=50)),
                ('apellido', models.CharField(max_length=50)),
                ('telefone', models.CharField(max_length=10)),
                ('correo', models.EmailField(max_length=254)),
                ('direccion', models.CharField(max_length=50, null=True)),
            ],
        ),
    ]
