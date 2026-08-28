from django.db import migrations

# Catálogo inicial (sección 11 del plan). El administrador puede agregar,
# editar o borrar tipos desde el admin sin tocar código.
TIPOS_INICIALES = [
    dict(nombre='Individual', capacidad=1, camas=1, precio_base=30,
         descripcion='Habitación para una persona.'),
    dict(nombre='Doble', capacidad=2, camas=1, precio_base=45,
         descripcion='Habitación para dos personas, una cama grande.'),
    dict(nombre='Matrimonial', capacidad=2, camas=1, precio_base=50,
         descripcion='Habitación con cama matrimonial.'),
    dict(nombre='Triple', capacidad=3, camas=2, precio_base=65,
         descripcion='Habitación para tres personas.'),
    dict(nombre='Familiar', capacidad=4, camas=2, precio_base=80,
         descripcion='Habitación amplia para familias.'),
    dict(nombre='Suite', capacidad=2, camas=1, precio_base=120,
         descripcion='Habitación superior con sala de estar.'),
]


def crear_tipos(apps, schema_editor):
    TipoHabitacion = apps.get_model('reservas', 'TipoHabitacion')
    for datos in TIPOS_INICIALES:
        TipoHabitacion.objects.get_or_create(nombre=datos['nombre'], defaults=datos)


def eliminar_tipos(apps, schema_editor):
    TipoHabitacion = apps.get_model('reservas', 'TipoHabitacion')
    TipoHabitacion.objects.filter(
        nombre__in=[t['nombre'] for t in TIPOS_INICIALES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reservas', '0006_tipohabitacion_alter_cliente_options_and_more'),
    ]

    operations = [
        migrations.RunPython(crear_tipos, eliminar_tipos),
    ]
