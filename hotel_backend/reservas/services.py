"""Reglas de negocio de reservas, en un solo lugar para que la API, la web
y `interfaz.py` (Tkinter) se comporten igual — antes cada consumidor
reimplementaba a mano la actualización de `Habitacion.estado`, y quedaban
inconsistencias entre ellos (ver auditoría de la Fase 1).

Concurrencia (sección 15 del plan): dos usuarios podrían intentar reservar
la misma habitación al mismo tiempo. `crear_reserva` bloquea la fila de la
habitación con `SELECT ... FOR UPDATE` (`select_for_update()`) cuando el
motor de base de datos lo soporta, para serializar esos intentos dentro de
una transacción.

Limitación conocida: el proyecto corre sobre SQLite (ver decisión de
cambiar de MySQL a SQLite), que NO soporta `SELECT ... FOR UPDATE`
(`connection.features.has_select_for_update` es `False`); en ese caso el
bloqueo de fila se omite. SQLite igual serializa los `write` de todo el
archivo a nivel de proceso, lo cual mitiga bastante el problema para el
volumen de uso esperado, pero no es una garantía tan fuerte como el
bloqueo de fila real. Si el proyecto migra a MySQL/PostgreSQL, este mismo
código empieza a bloquear la fila automáticamente sin cambiar nada.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .models import Consumo, Factura, Habitacion, Incidencia, METODOS_CON_REFERENCIA, Pago, Reserva, TareaLimpieza

ESTADOS_NO_RESERVABLES = ('Mantenimiento', 'Fuera de servicio')


def habitaciones_disponibles(fecha_ingreso, fecha_salida, capacidad_minima=None):
    """Habitaciones operativas y sin ninguna reserva que se solape con el
    rango pedido. Es la disponibilidad real para reservar — distinta del
    campo `Habitacion.estado`, que refleja la situación de HOY, no de un
    rango de fechas a futuro."""
    queryset = (
        Habitacion.objects.select_related('tipo')
        .exclude(estado__in=ESTADOS_NO_RESERVABLES)
        .order_by('numero')
    )
    if capacidad_minima:
        queryset = queryset.filter(tipo__capacidad__gte=capacidad_minima)

    ocupadas = Reserva.objects.filter(
        fecha_ingreso__lt=fecha_salida,
        fecha_salida__gt=fecha_ingreso,
        cancelada_en__isnull=True,
    ).values_list('habitacion_id', flat=True)

    return queryset.exclude(codigo__in=ocupadas)


def _bloquear_habitacion(codigo):
    queryset = Habitacion.objects.filter(pk=codigo)
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    return queryset.get()


@transaction.atomic
def crear_reserva(cliente, habitacion, fecha_ingreso, fecha_salida):
    """Crea la reserva y actualiza el estado de la habitación de forma
    consistente. Lanza ValidationError si no es válida (fechas, solapamiento,
    habitación no operativa)."""
    habitacion = _bloquear_habitacion(habitacion.pk)

    if habitacion.estado in ESTADOS_NO_RESERVABLES:
        raise ValidationError({'habitacion': [
            f'La habitación no está operativa ({habitacion.estado}).'
        ]})

    reserva = Reserva(
        cliente=cliente,
        habitacion=habitacion,
        fecha_ingreso=fecha_ingreso,
        fecha_salida=fecha_salida,
    )
    reserva.save()  # full_clean(): valida fechas y rechaza solapamiento
    # full_clean() ya convirtió reserva.fecha_ingreso de string a date si
    # hacía falta (ej. viene de la API JSON) — usamos ese valor, no el
    # parámetro original, para no comparar str con date.

    if habitacion.estado == 'Disponible':
        habitacion.estado = 'Ocupada' if reserva.fecha_ingreso <= date.today() else 'Reservada'
        habitacion.save(update_fields=['estado'])

    return reserva


@transaction.atomic
def cancelar_reserva(reserva, motivo=''):
    """Cancelación 'suave': marca cancelada_en en vez de borrar la fila, para
    que quede en el reporte de reservas canceladas (sección 26) y para no
    perder en CASCADE ningún pago/depósito que ya se hubiera registrado.
    Libera la habitación si corresponde; no toca habitaciones en
    Limpieza/Mantenimiento/Fuera de servicio (estados operativos, no
    dependen de si hay o no una reserva)."""
    if reserva.check_in_at is not None:
        raise ValidationError(
            'No se puede cancelar una reserva con el ingreso del huésped ya registrado; '
            'corresponde registrar su salida.'
        )
    if reserva.cancelada_en is not None:
        raise ValidationError('Esta reserva ya estaba cancelada.')

    habitacion = _bloquear_habitacion(reserva.habitacion_id)
    reserva.cancelada_en = timezone.now()
    reserva.motivo_cancelacion = motivo
    reserva.save(update_fields=['cancelada_en', 'motivo_cancelacion'])

    if habitacion.estado in ('Ocupada', 'Reservada'):
        hoy = date.today()
        ocupada_hoy = Reserva.objects.filter(
            habitacion=habitacion, fecha_ingreso__lte=hoy, fecha_salida__gt=hoy,
            cancelada_en__isnull=True,
        ).exists()
        habitacion.estado = 'Ocupada' if ocupada_hoy else 'Disponible'
        habitacion.save(update_fields=['estado'])


@transaction.atomic
def hacer_checkin(reserva):
    """Sección 16: registra la hora real de ingreso y pone la habitación en
    Ocupada. No depende de que la reserva la haya dejado en 'Reservada' —
    cubre el caso de un check-in anticipado o tardío también."""
    if reserva.check_in_at is not None:
        raise ValidationError('Esta reserva ya tiene registrado el ingreso del huésped.')
    if reserva.check_out_at is not None:
        raise ValidationError('Esta reserva ya está cerrada (la salida ya fue registrada).')

    reserva.check_in_at = timezone.now()
    reserva.save(update_fields=['check_in_at'])

    habitacion = _bloquear_habitacion(reserva.habitacion_id)
    if habitacion.estado != 'Ocupada':
        habitacion.estado = 'Ocupada'
        habitacion.save(update_fields=['estado'])

    return reserva


@transaction.atomic
def hacer_checkout(reserva):
    """Sección 17: cierra la estadía, genera (o reutiliza) la factura, manda
    la habitación a Limpieza y crea la tarea de limpieza correspondiente
    (sección 21) — no directo a Disponible; eso lo hace el módulo de
    Limpieza cuando termine."""
    if reserva.check_in_at is None:
        raise ValidationError('No se puede registrar la salida sin haber registrado antes el ingreso.')
    if reserva.check_out_at is not None:
        raise ValidationError('Esta reserva ya tiene registrada la salida del huésped.')

    reserva.check_out_at = timezone.now()
    reserva.save(update_fields=['check_out_at'])

    factura, _creada = Factura.objects.get_or_create(reserva=reserva)

    habitacion = _bloquear_habitacion(reserva.habitacion_id)
    habitacion.estado = 'Limpieza'
    habitacion.save(update_fields=['estado'])
    TareaLimpieza.objects.create(habitacion=habitacion)

    return factura


def registrar_consumo(reserva, servicio, cantidad, usuario=None):
    """Sección 18: un consumo solo tiene sentido durante la estadía — antes
    del check-in no hay a quién cargárselo, después del check-out la
    factura ya se generó."""
    if not reserva.esta_alojado():
        raise ValidationError(
            'Solo se pueden agregar consumos a una estadía en curso '
            '(con el ingreso del huésped registrado y la salida todavía no).'
        )
    if not servicio.activo:
        raise ValidationError('Este servicio ya no está activo.')

    consumo = Consumo(reserva=reserva, servicio=servicio, cantidad=cantidad, usuario=usuario)
    consumo.full_clean()
    consumo.save()
    return consumo


@transaction.atomic
def registrar_pago(reserva, monto, metodo, usuario=None, observacion='', referencia=''):
    """Sección 19: un pago nunca puede superar el saldo pendiente. No hay
    mecanismo de "excepción explícita" para sobrepago todavía — si hace
    falta (ej. propina, depósito de garantía), se agrega como una regla
    aparte, no relajando esta por defecto.

    Transferencia y Tarjeta exigen número de comprobante/referencia (para
    poder conciliar contra el banco o la pasarela después); Efectivo no
    tiene nada que conciliar, así que ahí queda opcional."""
    if monto is None or monto <= 0:
        raise ValidationError('El monto del pago debe ser mayor a cero.')

    if metodo in METODOS_CON_REFERENCIA and not referencia:
        raise ValidationError(
            f'Para pagos por {metodo} hace falta el número de comprobante/referencia.'
        )

    # select_for_update (cuando el motor lo soporta) evita que dos pagos
    # simultáneos contra la misma reserva pasen ambos la validación de saldo
    # antes de que el otro se guarde.
    reserva_bloqueada = Reserva.objects.filter(pk=reserva.pk)
    if connection.features.has_select_for_update:
        reserva_bloqueada = reserva_bloqueada.select_for_update()
    reserva = reserva_bloqueada.get()

    saldo = reserva.saldo_pendiente()
    if monto > saldo:
        raise ValidationError(
            f'El pago (${monto}) supera el saldo pendiente (${saldo}).'
        )

    pago = Pago(
        reserva=reserva, monto=monto, metodo=metodo, usuario=usuario,
        observacion=observacion, referencia=referencia,
    )
    pago.full_clean()
    pago.save()
    return pago


def iniciar_limpieza(tarea, responsable):
    """Sección 21: Pendiente → En limpieza, con quién la está haciendo."""
    if tarea.estado != 'Pendiente':
        raise ValidationError('Esta tarea ya está en curso o ya se completó.')
    tarea.estado = 'En limpieza'
    tarea.responsable = responsable
    tarea.iniciada_en = timezone.now()
    tarea.save(update_fields=['estado', 'responsable', 'iniciada_en'])
    return tarea


@transaction.atomic
def completar_tarea_limpieza(tarea, observaciones=''):
    """Sección 21: En limpieza (o Pendiente, si se salta el paso de
    'iniciar') → Limpia, y libera la habitación a Disponible."""
    if tarea.estado == 'Limpia':
        raise ValidationError('Esta tarea ya está completada.')

    tarea.estado = 'Limpia'
    tarea.finalizada_en = timezone.now()
    if observaciones:
        tarea.observaciones = observaciones
    tarea.save(update_fields=['estado', 'finalizada_en', 'observaciones'])

    habitacion = _bloquear_habitacion(tarea.habitacion_id)
    if habitacion.estado == 'Limpieza':
        habitacion.estado = 'Disponible'
        habitacion.save(update_fields=['estado'])

    return tarea


@transaction.atomic
def finalizar_limpieza(habitacion, observaciones=''):
    """Acción rápida desde el detalle de habitación (sección 10): si hay
    una tarea de limpieza pendiente/en curso para esta habitación, la cierra
    (con eso alcanza para dejar todo consistente); si no hay ninguna —caso
    de una habitación puesta en 'Limpieza' a mano desde el admin—, solo
    corrige el estado."""
    habitacion = _bloquear_habitacion(habitacion.pk)
    if habitacion.estado != 'Limpieza':
        raise ValidationError('Esta habitación no está en limpieza.')

    tarea = habitacion.tareas_limpieza.filter(estado__in=['Pendiente', 'En limpieza']).first()
    if tarea:
        completar_tarea_limpieza(tarea, observaciones=observaciones)
    else:
        habitacion.estado = 'Disponible'
        habitacion.save(update_fields=['estado'])

    return habitacion


@transaction.atomic
def reportar_incidencia(habitacion, descripcion, prioridad='Media', reportado_por=None):
    """Sección 22: registra el problema. Si la habitación estaba
    'Disponible' la saca de circulación (ya no se puede reservar mientras
    se resuelve); si tiene un huésped alojado o ya reservada, no se le
    interrumpe la estadía solo por reportar el problema — queda a criterio
    de un humano escalar a Mantenimiento/Fuera de servicio a mano."""
    habitacion = _bloquear_habitacion(habitacion.pk)

    incidencia = Incidencia(
        habitacion=habitacion, descripcion=descripcion,
        prioridad=prioridad, reportado_por=reportado_por,
    )
    incidencia.full_clean()
    incidencia.save()

    if habitacion.estado == 'Disponible':
        habitacion.estado = 'Mantenimiento'
        habitacion.save(update_fields=['estado'])

    return incidencia


def asignar_responsable_incidencia(incidencia, responsable):
    if incidencia.estado == 'Cerrado':
        raise ValidationError('No se puede reasignar una incidencia cerrada.')
    incidencia.responsable = responsable
    if incidencia.estado == 'Reportado':
        incidencia.estado = 'En revisión'
    incidencia.save(update_fields=['responsable', 'estado'])
    return incidencia


def iniciar_reparacion(incidencia):
    if incidencia.responsable_id is None:
        raise ValidationError('Asigná un responsable antes de iniciar la reparación.')
    if incidencia.estado not in ('Reportado', 'En revisión'):
        raise ValidationError(f'No se puede iniciar la reparación desde el estado "{incidencia.estado}".')
    incidencia.estado = 'En reparación'
    incidencia.save(update_fields=['estado'])
    return incidencia


def resolver_incidencia(incidencia, solucion):
    if incidencia.estado == 'Cerrado':
        raise ValidationError('Esta incidencia ya está cerrada.')
    if not solucion:
        raise ValidationError('Describí la solución aplicada.')
    incidencia.estado = 'Resuelto'
    incidencia.solucion = solucion
    incidencia.resuelta_en = timezone.now()
    incidencia.save(update_fields=['estado', 'solucion', 'resuelta_en'])
    return incidencia


@transaction.atomic
def cerrar_incidencia(incidencia):
    """Cierra la incidencia y, si no queda ninguna otra abierta para esa
    habitación, la devuelve a Disponible."""
    if incidencia.estado != 'Resuelto':
        raise ValidationError('Solo se puede cerrar una incidencia ya resuelta.')

    incidencia.estado = 'Cerrado'
    incidencia.cerrada_en = timezone.now()
    incidencia.save(update_fields=['estado', 'cerrada_en'])

    habitacion = _bloquear_habitacion(incidencia.habitacion_id)
    if habitacion.estado == 'Mantenimiento':
        quedan_abiertas = habitacion.incidencias.exclude(estado='Cerrado').exists()
        if not quedan_abiertas:
            habitacion.estado = 'Disponible'
            habitacion.save(update_fields=['estado'])

    return incidencia
