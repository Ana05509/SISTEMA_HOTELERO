"""Notificaciones internas (sección 28) — se calculan al vuelo en cada
request a partir del estado real (nada de tablas de notificaciones que
haya que limpiar o marcar como leídas; si la condición ya no aplica, la
notificación desaparece sola)."""
from datetime import timedelta

from django.utils import timezone

from .models import FechaEspecial, Incidencia, Reserva, TareaLimpieza

# Paleta por tema (sección 32) — colores concretos, no libres, para que
# ninguna combinación quede ilegible o desentone con el resto del sistema.
# Un hotel que solo quiera el descuento sin cambiar la apariencia elige
# tema="ninguno" en la fecha especial y esto ni se consulta.
_ESTILOS_TEMA = {
    'navidad': {
        'acento': '#c0392b', 'acento_oscuro': '#962d20', 'acento_claro': '#e88a80',
        'sidebar_bg': '#0b3d24', 'icono': 'bi-tree-fill',
    },
    'fin_de_ano': {
        'acento': '#b8952d', 'acento_oscuro': '#96791f', 'acento_claro': '#e8cf7a',
        'sidebar_bg': '#141414', 'icono': 'bi-stars',
    },
    'san_valentin': {
        'acento': '#e91e63', 'acento_oscuro': '#c2185b', 'acento_claro': '#f48fb1',
        'sidebar_bg': '#3a1220', 'icono': 'bi-heart-fill',
    },
    'halloween': {
        'acento': '#e67e22', 'acento_oscuro': '#ca6510', 'acento_claro': '#f0b27a',
        'sidebar_bg': '#191919', 'icono': 'bi-emoji-dizzy-fill',
    },
    'aniversario': {
        'acento': '#b8952d', 'acento_oscuro': '#96791f', 'acento_claro': '#e4cd7a',
        'sidebar_bg': '#1e1b34', 'icono': 'bi-award-fill',
    },
    'verano': {
        'acento': '#0ea5e9', 'acento_oscuro': '#0284c7', 'acento_claro': '#7dd3fc',
        'sidebar_bg': '#0c4a6e', 'icono': 'bi-sun-fill',
    },
}


def tema_temporada(request):
    """Si hay una fecha especial vigente HOY con un tema visual elegido
    (no "ninguno"), le pasa a base.html los colores para recolorear el
    sistema y un pequeño aviso — sección 32."""
    if not request.user.is_authenticated:
        return {}

    hoy = timezone.localdate()
    especial = FechaEspecial.objects.filter(
        activo=True, fecha_inicio__lte=hoy, fecha_fin__gte=hoy,
    ).exclude(tema='ninguno').order_by('-fecha_inicio').first()

    if not especial:
        return {'tema_temporada': None}

    estilos = _ESTILOS_TEMA.get(especial.tema)
    if not estilos:
        return {'tema_temporada': None}

    return {'tema_temporada': {**estilos, 'fecha_especial': especial}}


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
