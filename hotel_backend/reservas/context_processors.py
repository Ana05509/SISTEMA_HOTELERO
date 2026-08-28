"""Notificaciones internas (sección 28) — se calculan al vuelo en cada
request a partir del estado real (nada de tablas de notificaciones que
haya que limpiar o marcar como leídas; si la condición ya no aplica, la
notificación desaparece sola)."""
from datetime import timedelta

from django.utils import timezone

from .models import Incidencia, Reserva, TareaLimpieza


def notificaciones(request):
    if not request.user.is_authenticated:
        return {}

    hoy = timezone.localdate()
    items = []

    if request.user.has_perm('reservas.view_reserva'):
        checkins_hoy = Reserva.objects.filter(
            fecha_ingreso=hoy, check_in_at__isnull=True, cancelada_en__isnull=True,
        ).count()
        if checkins_hoy:
            items.append({
                'texto': f'{checkins_hoy} ingreso(s) de huésped esperado(s) hoy',
                'url': 'checkin_lista', 'icono': 'bi-box-arrow-in-right',
            })

        checkouts_hoy = Reserva.objects.filter(
            check_in_at__isnull=False, check_out_at__isnull=True, fecha_salida=hoy,
        ).count()
        if checkouts_hoy:
            items.append({
                'texto': f'{checkouts_hoy} salida(s) de huésped esperada(s) hoy',
                'url': 'checkout_lista', 'icono': 'bi-box-arrow-right',
            })

        nuevas = Reserva.objects.filter(
            creada_en__gte=timezone.now() - timedelta(hours=24), cancelada_en__isnull=True,
        ).count()
        if nuevas:
            items.append({
                'texto': f'{nuevas} reserva(s) nueva(s) en las últimas 24h',
                'url': 'reservas_lista', 'icono': 'bi-journal-plus',
            })

    if request.user.has_perm('reservas.view_pago'):
        # saldo_pendiente() es un cálculo en Python (suma consumos/pagos),
        # no un campo de la base — se recorre solo a los alojados, que
        # normalmente son pocos.
        alojados = Reserva.objects.filter(check_in_at__isnull=False, check_out_at__isnull=True)
        con_saldo = sum(1 for r in alojados if r.saldo_pendiente() > 0)
        if con_saldo:
            items.append({
                'texto': f'{con_saldo} huésped(es) alojado(s) con saldo pendiente',
                'url': 'pagos_lista', 'icono': 'bi-credit-card',
            })

    if request.user.has_perm('reservas.view_tarealimpieza'):
        pendientes_limpieza = TareaLimpieza.objects.filter(estado__in=['Pendiente', 'En limpieza']).count()
        if pendientes_limpieza:
            items.append({
                'texto': f'{pendientes_limpieza} habitación(es) pendiente(s) de limpieza',
                'url': 'limpieza_lista', 'icono': 'bi-bucket',
            })

    if request.user.has_perm('reservas.view_incidencia'):
        mantenimiento_abierto = Incidencia.objects.exclude(estado='Cerrado').count()
        if mantenimiento_abierto:
            items.append({
                'texto': f'{mantenimiento_abierto} incidencia(s) de mantenimiento abierta(s)',
                'url': 'mantenimiento_lista', 'icono': 'bi-tools',
            })

    return {'notificaciones': items, 'notificaciones_count': len(items)}
