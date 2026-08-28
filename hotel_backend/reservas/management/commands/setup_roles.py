"""
Crea los grupos de roles del hotel y les asigna los permisos estándar de
Django (add/change/delete/view) sobre los modelos de `reservas`.

Es idempotente: se puede correr las veces que haga falta (por ejemplo, cada
vez que se agregue un modelo nuevo) sin duplicar grupos ni permisos.

Uso:
    python manage.py setup_roles

El rol "Administrador" se implementa como cuenta con is_superuser=True
(creada con `createsuperuser`), no como grupo: un superusuario ya tiene
todos los permisos automáticamente en Django. El grupo "Administrador"
igual se crea, con todos los permisos, por si se prefiere asignarlo a una
cuenta is_staff no superusuario.
"""
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from reservas.models import (
    AuditLog, Cliente, ConfiguracionHotel, Consumo, Factura, Habitacion, Incidencia, Pago, Reserva, Servicio,
    TareaLimpieza, TipoHabitacion,
)

# rol -> {modelo: [acciones]}. Acciones válidas: add, change, delete, view.
# AuditLog es la única excepción deliberada: ni Administrador tiene
# add/change/delete — se llena solo por código (ver auditoria.py) y el
# ModelAdmin ya bloquea esas acciones aunque alguien tuviera el permiso
# (sección 25: nadie debe poder alterar el registro de auditoría).
ROLES = {
    'Administrador': {
        Cliente: ['add', 'change', 'delete', 'view'],
        TipoHabitacion: ['add', 'change', 'delete', 'view'],
        Habitacion: ['add', 'change', 'delete', 'view'],
        Reserva: ['add', 'change', 'delete', 'view'],
        Factura: ['add', 'change', 'delete', 'view'],
        Servicio: ['add', 'change', 'delete', 'view'],
        Consumo: ['add', 'change', 'delete', 'view'],
        Pago: ['add', 'change', 'delete', 'view'],
        TareaLimpieza: ['add', 'change', 'delete', 'view'],
        Incidencia: ['add', 'change', 'delete', 'view'],
        # Sin 'add'/'delete': ConfiguracionHotel es un singleton (ver
        # models.py), no se crean ni se borran filas desde la UI.
        ConfiguracionHotel: ['view', 'change'],
        AuditLog: ['view'],
    },
    'Recepcionista': {
        Cliente: ['add', 'change', 'view'],
        TipoHabitacion: ['view'],
        Habitacion: ['view'],
        # 'delete' en Reserva = poder cancelar (sección 23: reservas,
        # check-in/out son tareas de recepción, incluye cancelaciones).
        Reserva: ['add', 'change', 'delete', 'view'],
        Factura: ['add', 'view'],
        Servicio: ['view'],
        Consumo: ['add', 'view'],
        Pago: ['add', 'view'],
        # Puede reportar un problema (lo nota al atender al huésped), pero
        # no gestionar el ciclo de la incidencia — eso es de Mantenimiento.
        Incidencia: ['add', 'view'],
    },
    'Limpieza': {
        Habitacion: ['view', 'change'],
        TareaLimpieza: ['add', 'change', 'view'],
        Incidencia: ['add', 'view'],
    },
    'Mantenimiento': {
        Habitacion: ['view', 'change'],
        Incidencia: ['add', 'change', 'view'],
    },
    'Gerencia': {
        Cliente: ['view'],
        TipoHabitacion: ['view'],
        Habitacion: ['view'],
        Reserva: ['view'],
        Factura: ['view'],
        Servicio: ['view'],
        Consumo: ['view'],
        Pago: ['view'],
        TareaLimpieza: ['view'],
        Incidencia: ['view'],
        ConfiguracionHotel: ['view'],
        AuditLog: ['view'],
    },
}


class Command(BaseCommand):
    help = 'Crea/actualiza los grupos de roles del hotel y sus permisos.'

    def handle(self, *args, **options):
        verbosity = options.get('verbosity', 1)
        for rol, specs in ROLES.items():
            grupo, creado = Group.objects.get_or_create(name=rol)
            permisos = []
            for modelo, acciones in specs.items():
                content_type = ContentType.objects.get_for_model(modelo)
                for accion in acciones:
                    codename = f'{accion}_{modelo._meta.model_name}'
                    try:
                        permisos.append(
                            Permission.objects.get(content_type=content_type, codename=codename)
                        )
                    except Permission.DoesNotExist:
                        self.stderr.write(self.style.WARNING(
                            f'Permiso "{codename}" no existe todavía — '
                            f'corré "migrate" antes de "setup_roles".'
                        ))
            grupo.permissions.set(permisos)
            if verbosity >= 1:
                verbo = 'creado' if creado else 'actualizado'
                self.stdout.write(self.style.SUCCESS(
                    f'Rol "{rol}" {verbo} con {len(permisos)} permiso(s).'
                ))
