from django import template

register = template.Library()

# Un solo lugar para el color de cada estado de reserva — antes cada
# plantilla lo pintaba a su manera (gris plano en Reservas/Huésped, azul
# fijo en Detalle de habitación/reserva), así que la misma reserva se veía
# distinta según la pantalla.
_COLORES_ESTADO_RESERVA = {
    'Confirmada': 'secondary',
    'Alojado': 'info',
    'Completada': 'success',
    'Cancelada': 'danger',
    'No-show': 'warning',
}


@register.filter
def estado_reserva_badge(estado_display):
    return _COLORES_ESTADO_RESERVA.get(estado_display, 'secondary')
