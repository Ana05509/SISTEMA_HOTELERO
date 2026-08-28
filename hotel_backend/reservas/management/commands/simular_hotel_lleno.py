"""
Simula un hotel a ocupación completa para demos: le crea a cada habitación
disponible una reserva "de hoy" — algunas ya alojadas (con salida en unos
días o justo hoy), algunas con check-in pendiente para hoy, algunas
reservadas para los próximos días. Así el dashboard, el calendario y las
pantallas de check-in/check-out dejan de mostrar todo en cero.

No toca las habitaciones que ya tengan una reserva activa (para no romper
nada que el usuario haya cargado a mano); una habitación en Limpieza se
libera primero (se completa su tarea pendiente) para poder reservarla.

Uso:
    python manage.py simular_hotel_lleno
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from reservas import services
from reservas.models import Cliente, Habitacion, Reserva
from reservas.validadores_ecuador import _COEFICIENTES_CEDULA, _verificador_modulo10

NOMBRES = [
    'María', 'José', 'Carmen', 'Luis', 'Andrea', 'Carlos', 'Gabriela', 'Diego',
    'Valentina', 'Andrés', 'Camila', 'Fernando', 'Daniela', 'Pedro', 'Paola',
    'Miguel', 'Sofía', 'Jorge', 'Verónica', 'Iván', 'Mónica', 'Ricardo',
    'Adriana', 'Esteban', 'Cristina', 'Rodrigo', 'Patricia', 'Xavier',
    'Silvana', 'Marco',
]
APELLIDOS = [
    'Vásconez', 'Salazar', 'Chávez', 'Guerrero', 'Cevallos', 'Andrade',
    'Villacís', 'Ortega', 'Naranjo', 'Rivadeneira', 'Freire', 'Loor',
    'Quinde', 'Zambrano', 'Cedeño', 'Vera', 'Suárez', 'Toapanta', 'Bermeo',
    'Jarrín', 'Espinoza', 'Vinueza', 'Aucapiña', 'Herdoíza', 'Palacios',
    'Ruales', 'Játiva', 'Sánchez', 'Peñaherrera', 'Balseca',
]


def _cedula_valida_numero(provincia, secuencia):
    """Construye una cédula ecuatoriana válida de verdad (mismo algoritmo
    que valida validadores_ecuador.cedula_valida) — no un número inventado
    que después el propio sistema rechazaría."""
    base = f'{provincia:02d}{secuencia:07d}'
    digitos = [int(d) for d in base]
    verificador = _verificador_modulo10(digitos, _COEFICIENTES_CEDULA)
    return base + str(verificador)


class Command(BaseCommand):
    help = 'Llena de reservas todas las habitaciones disponibles, simulando un hotel a full para demos.'

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        habitaciones = list(Habitacion.objects.select_related('tipo').order_by('numero'))

        objetivo = []
        for h in habitaciones:
            if h.estado == 'Limpieza':
                try:
                    services.finalizar_limpieza(h)
                    h.refresh_from_db()
                except Exception as exc:
                    self.stderr.write(f'  Hab. {h.numero}: no se pudo liberar de Limpieza ({exc}), se omite.')
                    continue
            if h.estado in ('Mantenimiento', 'Fuera de servicio'):
                self.stdout.write(f'  Hab. {h.numero}: en {h.estado}, se deja como está.')
                continue
            # check_out_at__isnull=True: una reserva con el check-out ya
            # hecho no ocupa la habitación de verdad, aunque su rango de
            # fechas nominal siga incluyendo "hoy".
            if Reserva.objects.filter(
                habitacion=h, cancelada_en__isnull=True, check_out_at__isnull=True,
                fecha_ingreso__lte=hoy, fecha_salida__gte=hoy,
            ).exists():
                self.stdout.write(f'  Hab. {h.numero}: ya tiene una reserva activa, se deja como está.')
                continue
            objetivo.append(h)

        total = len(objetivo)
        if not total:
            self.stdout.write(self.style.WARNING('No hay habitaciones libres para simular — el hotel ya está lleno.'))
            return

        # Se reparte el objetivo en 4 grupos: alojados con salida en unos
        # días, alojados con salida HOY, check-in pendiente para HOY, y
        # reservados a futuro — así todas las pantallas (dashboard,
        # check-in, check-out, calendario) muestran movimiento real.
        n_salida_hoy = max(1, round(total * 0.20))
        n_ingreso_hoy = max(1, round(total * 0.20))
        n_futuro = max(1, round(total * 0.15))
        n_alojados = total - n_salida_hoy - n_ingreso_hoy - n_futuro

        grupos = (
            ['alojado'] * n_alojados
            + ['salida_hoy'] * n_salida_hoy
            + ['ingreso_hoy'] * n_ingreso_hoy
            + ['futuro'] * n_futuro
        )

        creados = {'alojado': 0, 'salida_hoy': 0, 'ingreso_hoy': 0, 'futuro': 0}
        secuencia = 1
        provincia = 17  # Pichincha — cualquiera entre 01 y 24 sirve; la
        # secuencia (creciente y única por huésped) ya garantiza cédulas
        # distintas sin necesidad de variar la provincia.

        for habitacion, tipo in zip(objetivo, grupos):
            with transaction.atomic():
                nombre = NOMBRES[secuencia % len(NOMBRES)]
                apellido = APELLIDOS[(secuencia * 7) % len(APELLIDOS)]
                cedula = _cedula_valida_numero(provincia, secuencia)
                telefono = f'09{secuencia:08d}'[:10]
                correo = f'{nombre.lower()}.{apellido.lower()}{secuencia}@example.com'.replace('í', 'i').replace(
                    'á', 'a').replace('é', 'e').replace('ó', 'o').replace('ú', 'u')

                cliente, _creado = Cliente.objects.get_or_create(
                    cedula=cedula,
                    defaults=dict(
                        nombre=nombre, apellido=apellido, telefono=telefono,
                        correo=correo, nacionalidad='Ecuatoriana',
                    ),
                )

                if tipo == 'alojado':
                    ingreso = hoy - timedelta(days=2)
                    salida = hoy + timedelta(days=2 + (secuencia % 3))
                elif tipo == 'salida_hoy':
                    ingreso = hoy - timedelta(days=2 + (secuencia % 3))
                    salida = hoy
                elif tipo == 'ingreso_hoy':
                    ingreso = hoy
                    salida = hoy + timedelta(days=2 + (secuencia % 3))
                else:  # futuro
                    ingreso = hoy + timedelta(days=1 + (secuencia % 4))
                    salida = ingreso + timedelta(days=2)

                try:
                    reserva = services.crear_reserva(cliente, habitacion, ingreso, salida)
                    if tipo in ('alojado', 'salida_hoy'):
                        services.hacer_checkin(reserva)
                    creados[tipo] += 1
                except Exception as exc:
                    self.stderr.write(f'  Hab. {habitacion.numero}: no se pudo simular ({exc}).')

                secuencia += 1

        self.stdout.write(self.style.SUCCESS(
            f'Listo — {sum(creados.values())} habitación(es) con movimiento nuevo:\n'
            f'  Alojados (salida en unos días): {creados["alojado"]}\n'
            f'  Salidas de hoy (check-out pendiente): {creados["salida_hoy"]}\n'
            f'  Ingresos de hoy (check-in pendiente): {creados["ingreso_hoy"]}\n'
            f'  Reservados a futuro: {creados["futuro"]}'
        ))
