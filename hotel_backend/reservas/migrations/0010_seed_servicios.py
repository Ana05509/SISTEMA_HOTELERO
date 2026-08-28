from django.db import migrations

# Un ejemplo por categoría (sección 18). El administrador agrega el resto
# del catálogo real desde el admin.
SERVICIOS_INICIALES = [
    dict(nombre='Desayuno buffet', categoria='Restaurante', precio=8),
    dict(nombre='Gaseosa', categoria='Minibar', precio=2),
    dict(nombre='Lavado de ropa (por carga)', categoria='Lavanderia', precio=10),
    dict(nombre='Parqueadero por noche', categoria='Parqueadero', precio=5),
    dict(nombre='Traslado al aeropuerto', categoria='Transporte', precio=15),
    dict(nombre='Room service (recargo)', categoria='Room service', precio=5),
]


def crear_servicios(apps, schema_editor):
    Servicio = apps.get_model('reservas', 'Servicio')
    for datos in SERVICIOS_INICIALES:
        Servicio.objects.get_or_create(nombre=datos['nombre'], defaults=datos)


def eliminar_servicios(apps, schema_editor):
    Servicio = apps.get_model('reservas', 'Servicio')
    Servicio.objects.filter(
        nombre__in=[s['nombre'] for s in SERVICIOS_INICIALES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reservas', '0009_servicio_consumo'),
    ]

    operations = [
        migrations.RunPython(crear_servicios, eliminar_servicios),
    ]
