"""Generación de PDFs (factura, comprobante de pago) y su envío por correo.

Separado de `views.py` porque tanto la descarga por HTTP como el envío por
mail necesitan los mismos bytes de PDF — generarlos en un solo lugar evita
tener la plantilla de la factura escrita dos veces y que se desincronicen.

El PDF se arma con `reportlab.platypus` (tablas reales, no líneas de texto
sueltas con `canvas.drawString`) para que se vea prolijo tanto en pantalla
como impreso — sección 31: papel A4 (no Carta/Letter), que es el estándar
en Ecuador.

Envío de correo: usa el framework de mail de Django (`django.core.mail`),
no una librería nueva. Si no hay un servidor SMTP real configurado en
`.env`, `settings.EMAIL_BACKEND` cae en la consola (los mails se imprimen
en el log del servidor en vez de salir de verdad) — así nunca se rompe ni
se miente sobre si "se mandó" el correo; ver `settings.py` y `.env.example`.
"""
import logging
from io import BytesIO

from django.conf import settings
from django.core.mail import EmailMessage

from .models import ConfiguracionHotel

logger = logging.getLogger(__name__)

# Nombre de respaldo si todavía no se guardó ninguna configuración (o la
# tabla está vacía en una base recién migrada) — ConfiguracionHotel.actual()
# ya trae este mismo valor como default, esto es solo un cinturón extra.
NOMBRE_SISTEMA = 'Sistema Hotelero'


def correo_es_real():
    """False mientras se use el backend de consola (sin SMTP configurado
    en .env) — para no decirle al usuario que "se envió" un correo que en
    realidad solo se imprimió en el log."""
    return settings.EMAIL_BACKEND != 'django.core.mail.backends.console.EmailBackend'


def _colores():
    # Import perezoso: nada de reportlab se carga si nunca se genera un PDF.
    from reportlab.lib import colors
    return {
        'blanco': colors.white,
        'indigo': colors.HexColor('#4f46e5'),
        'indigo_claro': colors.HexColor('#eef0ff'),
        'gris': colors.HexColor('#6b7280'),
        'gris_claro': colors.HexColor('#f4f6f9'),
        'texto': colors.HexColor('#1e1b34'),
        'rojo': colors.HexColor('#dc2626'),
        'verde': colors.HexColor('#16a34a'),
        'borde': colors.HexColor('#dee2e6'),
    }


def _estilos():
    # OJO: cada estilo fija su propio `leading` (interlineado) a mano.
    # ParagraphStyle hereda el leading del padre si no se lo pisa, así que
    # un estilo con fontSize grande pero leading heredado de uno chico
    # hace que el texto siguiente arranque antes de que termine este
    # párrafo — se ven superpuestos. No confiar en el default.
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    base = getSampleStyleSheet()['Normal']
    c = _colores()
    return {
        'titulo_hotel': ParagraphStyle('titulo_hotel', parent=base, fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=c['texto']),
        'dato_hotel': ParagraphStyle('dato_hotel', parent=base, fontName='Helvetica', fontSize=8.5, leading=12, textColor=c['gris']),
        'titulo_doc': ParagraphStyle('titulo_doc', parent=base, fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=c['indigo'], alignment=2),
        'subtitulo_doc': ParagraphStyle('subtitulo_doc', parent=base, fontName='Helvetica', fontSize=9, leading=12, textColor=c['gris'], alignment=2),
        'seccion': ParagraphStyle('seccion', parent=base, fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=c['gris'], spaceAfter=4),
        'texto': ParagraphStyle('texto', parent=base, fontName='Helvetica', fontSize=9.5, leading=14, textColor=c['texto']),
        'texto_bold': ParagraphStyle('texto_bold', parent=base, fontName='Helvetica-Bold', fontSize=9.5, leading=14, textColor=c['texto']),
        'celda': ParagraphStyle('celda', parent=base, fontName='Helvetica', fontSize=9, leading=12, textColor=c['texto']),
        'celda_header': ParagraphStyle('celda_header', parent=base, fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=c['blanco']),
        'total_label': ParagraphStyle('total_label', parent=base, fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=c['texto'], alignment=2),
        'total_valor': ParagraphStyle('total_valor', parent=base, fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=c['texto'], alignment=2),
        'pie': ParagraphStyle('pie', parent=base, fontName='Helvetica-Oblique', fontSize=8.5, leading=11, textColor=c['gris'], alignment=1),
    }


# Tamaño del logo tal como se muestra en el PDF (70x42pt) — a esta
# resolución (2x para que no se vea pixelado ni en pantallas de alta
# densidad) alcanza y sobra; sin este paso, un logo subido a resolución de
# cámara/IA (varios MB) infla cada factura a varios MB también, porque
# ReportLab embebe el archivo tal cual, no lo re-escala al insertarlo.
_LOGO_MAX_PX = (140, 84)


def _logo_redimensionado(ruta_archivo):
    """Devuelve un BytesIO con el logo reducido a _LOGO_MAX_PX — para que
    el PDF pese poco sin importar el tamaño del archivo que se haya
    subido en Configuración."""
    from PIL import Image as PILImage

    con_imagen = BytesIO()
    with PILImage.open(ruta_archivo) as img:
        img = img.convert('RGBA') if img.mode in ('P', 'LA') else img
        img.thumbnail(_LOGO_MAX_PX, PILImage.LANCZOS)
        img.save(con_imagen, format='PNG', optimize=True)
    con_imagen.seek(0)
    return con_imagen


def _encabezado_hotel(elementos, styles, config):
    """Nombre/dirección/teléfono/RUC del hotel (+ logo si hay uno cargado
    en Configuración) y una línea divisoria — igual en factura y comprobante."""
    from reportlab.platypus import HRFlowable, Image, Paragraph, Spacer, Table, TableStyle

    datos_hotel = [Paragraph(config.nombre_hotel, styles['titulo_hotel'])]
    for dato in (config.direccion, config.telefono, config.ruc and f'RUC: {config.ruc}'):
        if dato:
            datos_hotel.append(Paragraph(dato, styles['dato_hotel']))

    logo = None
    if config.logo:
        try:
            logo = Image(_logo_redimensionado(config.logo.path), width=70, height=42, kind='proportional')
        except Exception:
            logger.exception('No se pudo procesar el logo del hotel para el PDF')
            logo = None

    fila = Table(
        [[logo, datos_hotel]] if logo else [[datos_hotel]],
        colWidths=[80, None] if logo else [None],
    )
    fila.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    elementos.append(fila)
    elementos.append(Spacer(1, 8))
    elementos.append(HRFlowable(width='100%', color=_colores()['indigo'], thickness=1.4))
    elementos.append(Spacer(1, 10))


def _dato_par(etiqueta, valor, styles):
    from reportlab.platypus import Paragraph
    return Paragraph(f'<b>{etiqueta}:</b> {valor}', styles['texto'])


def _documento_base(titulo_archivo):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=titulo_archivo,
    )
    return doc, buffer


def pdf_factura_bytes(factura):
    """Factura completa (sección 20): datos del hotel, del huésped, de la
    habitación, servicios consumidos, pagos y saldo — en A4, lista para
    imprimir (sección 31: formato de papel usado en Ecuador)."""
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    reserva = factura.reserva
    config = ConfiguracionHotel.actual()
    styles = _estilos()
    c = _colores()
    doc, buffer = _documento_base(f'Factura #{factura.id}')
    elementos = []

    _encabezado_hotel(elementos, styles, config)

    saldo = reserva.saldo_pendiente()
    estado = 'PENDIENTE' if saldo > 0 else 'PAGADA'
    color_estado = c['rojo'] if saldo > 0 else c['verde']
    elementos.append(Paragraph('FACTURA', styles['titulo_doc']))
    elementos.append(Paragraph(
        f'N.º FA-{factura.id:06d} &nbsp;·&nbsp; {factura.fecha:%d/%m/%Y} &nbsp;·&nbsp; '
        f'<font color="{color_estado.hexval()}"><b>{estado}</b></font>',
        styles['subtitulo_doc'],
    ))
    elementos.append(Spacer(1, 14))

    # Huésped / Estancia, dos columnas
    info_huesped = [
        Paragraph('HUÉSPED', styles['seccion']),
        _dato_par('Nombre', reserva.cliente.nombre_completo(), styles),
        _dato_par('Documento', f'{reserva.cliente.get_tipo_documento_display()} {reserva.cliente.cedula}', styles),
        _dato_par('Teléfono', reserva.cliente.telefono, styles),
        _dato_par('Email', reserva.cliente.correo, styles),
    ]
    info_estancia = [
        Paragraph('ESTANCIA', styles['seccion']),
        _dato_par('Reserva', f'#{reserva.id}', styles),
        _dato_par('Habitación', f'{reserva.habitacion.numero} — {reserva.habitacion.tipo.nombre}', styles),
        _dato_par('Ingreso', f'{reserva.fecha_ingreso:%d/%m/%Y}', styles),
        _dato_par('Salida', f'{reserva.fecha_salida:%d/%m/%Y}', styles),
    ]
    tabla_info = Table([[info_huesped, info_estancia]], colWidths=[(doc.width / 2) - 6, (doc.width / 2) - 6])
    tabla_info.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elementos.append(tabla_info)
    elementos.append(Spacer(1, 16))

    # Detalle: alojamiento + consumos
    descripcion_alojamiento = f'Alojamiento — {reserva.habitacion.tipo.nombre} ({reserva.duracion()} noche(s))'
    temporada = reserva.temporada_especial()
    if temporada and temporada.porcentaje_ajuste:
        descripcion_alojamiento += f'<br/><font size="8" color="{c["gris"].hexval()}">★ {temporada.nombre} ({temporada.porcentaje_ajuste}%)</font>'

    filas = [[
        Paragraph('Descripción', styles['celda_header']),
        Paragraph('Cant.', styles['celda_header']),
        Paragraph('P. unitario', styles['celda_header']),
        Paragraph('Total', styles['celda_header']),
    ], [
        Paragraph(descripcion_alojamiento, styles['celda']),
        Paragraph('1', styles['celda']),
        Paragraph(f'${reserva.habitacion.precio}', styles['celda']),
        Paragraph(f'${reserva.costo()}', styles['celda']),
    ]]
    for consumo in reserva.consumos.select_related('servicio'):
        filas.append([
            Paragraph(consumo.servicio.nombre, styles['celda']),
            Paragraph(str(consumo.cantidad), styles['celda']),
            Paragraph(f'${consumo.precio_unitario}', styles['celda']),
            Paragraph(f'${consumo.subtotal}', styles['celda']),
        ])

    from reportlab.lib.units import cm as _cm
    col_cant, col_precio, col_total = 1.4 * _cm, 2.4 * _cm, 2.2 * _cm
    ancho_desc = doc.width - col_cant - col_precio - col_total
    tabla_detalle = Table(
        filas, colWidths=[ancho_desc, col_cant, col_precio, col_total], repeatRows=1,
    )
    tabla_detalle.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), c['indigo']),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, c['borde']),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [c['blanco'], c['gris_claro']]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elementos.append(tabla_detalle)
    elementos.append(Spacer(1, 14))

    # Pagos (si hay) + totales, lado a lado
    pagos = list(reserva.pagos.all())
    bloque_pagos = None
    if pagos:
        filas_pago = [[
            Paragraph('Método', styles['celda_header']),
            Paragraph('Referencia', styles['celda_header']),
            Paragraph('Fecha', styles['celda_header']),
            Paragraph('Monto', styles['celda_header']),
        ]]
        for pago in pagos:
            filas_pago.append([
                Paragraph(pago.get_metodo_display(), styles['celda']),
                Paragraph(pago.referencia or '—', styles['celda']),
                Paragraph(f'{pago.fecha:%d/%m/%y}', styles['celda']),
                Paragraph(f'${pago.monto}', styles['celda']),
            ])
        ancho_pagos = doc.width * 0.58
        tabla_pagos = Table(
            filas_pago,
            colWidths=[ancho_pagos * .24, ancho_pagos * .28, ancho_pagos * .22, ancho_pagos * .26],
        )
        tabla_pagos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), c['indigo']),
            ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.4, c['borde']),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [c['blanco'], c['gris_claro']]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        bloque_pagos = Table([[Paragraph('PAGOS REGISTRADOS', styles['seccion'])], [tabla_pagos]])
        bloque_pagos.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0)]))

    total_filas = [
        [Paragraph('Subtotal', styles['texto']), Paragraph(f'${factura.subtotal}', styles['texto_bold'])],
        [Paragraph(f'IVA ({factura.porcentaje_iva()}%)', styles['texto']), Paragraph(f'${factura.iva_monto}', styles['texto_bold'])],
        [Paragraph('Total', styles['texto']), Paragraph(f'${factura.total}', styles['texto_bold'])],
        [Paragraph('Pagado', styles['texto']), Paragraph(f'${reserva.total_pagado()}', styles['texto_bold'])],
        [
            Paragraph('Saldo', styles['total_label']),
            Paragraph(f'<font color="{color_estado.hexval()}">${saldo if saldo > 0 else 0}</font>', styles['total_valor']),
        ],
    ]
    tabla_totales = Table(total_filas, colWidths=[doc.width * 0.30 - 40, 90])
    tabla_totales.setStyle(TableStyle([
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, c['texto']),
        ('TOPPADDING', (0, -1), (-1, -1), 6),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
    ]))

    if bloque_pagos:
        fila_inferior = Table([[bloque_pagos, tabla_totales]], colWidths=[doc.width * 0.58, doc.width * 0.42])
        fila_inferior.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        elementos.append(fila_inferior)
    else:
        elementos.append(tabla_totales)

    elementos.append(Spacer(1, 24))
    elementos.append(Paragraph('Gracias por su preferencia.', styles['pie']))

    doc.build(elementos)
    return buffer.getvalue()


def pdf_comprobante_pago_bytes(pago):
    """Comprobante de UN pago puntual — a diferencia de la factura, existe
    desde el momento en que se registra el pago, no recién al check-out
    (útil para depósitos/señas antes de que termine la estadía)."""
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    reserva = pago.reserva
    config = ConfiguracionHotel.actual()
    styles = _estilos()
    c = _colores()
    doc, buffer = _documento_base(f'Comprobante de pago #{pago.id}')
    elementos = []
    _encabezado_hotel(elementos, styles, config)

    elementos.append(Paragraph('COMPROBANTE DE PAGO', styles['titulo_doc']))
    elementos.append(Paragraph(f'N.º {pago.id:06d} &nbsp;·&nbsp; {pago.fecha:%d/%m/%Y %H:%M}', styles['subtitulo_doc']))
    elementos.append(Spacer(1, 16))

    filas = [
        [_dato_par('Huésped', reserva.cliente.nombre_completo(), styles)],
        [_dato_par('Documento', reserva.cliente.cedula, styles)],
        [_dato_par('Reserva', f'#{reserva.id} — Hab. {reserva.habitacion.numero}', styles)],
        [_dato_par('Método', pago.get_metodo_display(), styles)],
    ]
    if pago.referencia:
        filas.append([_dato_par('N.º de comprobante/referencia', pago.referencia, styles)])
    if pago.observacion:
        filas.append([_dato_par('Observación', pago.observacion, styles)])
    tabla = Table(filas, colWidths=[doc.width])
    tabla.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    elementos.append(tabla)
    elementos.append(Spacer(1, 10))

    saldo = reserva.saldo_pendiente()
    color_estado = c['rojo'] if saldo > 0 else c['verde']
    monto_tabla = Table(
        [[Paragraph('MONTO PAGADO', styles['seccion']), Paragraph(f'${pago.monto}', styles['total_valor'])]],
        colWidths=[doc.width - 100, 100],
    )
    monto_tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), c['indigo_claro']),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elementos.append(monto_tabla)
    elementos.append(Spacer(1, 14))

    resumen = Table([
        [Paragraph('Subtotal', styles['texto']), Paragraph(f'${reserva.total_estadia()}', styles['texto_bold'])],
        [Paragraph(f'IVA ({config.iva_porcentaje}%)', styles['texto']), Paragraph(f'${reserva.iva()}', styles['texto_bold'])],
        [Paragraph('Total de la estadía', styles['texto']), Paragraph(f'${reserva.total_con_iva()}', styles['texto_bold'])],
        [Paragraph('Pagado a la fecha', styles['texto']), Paragraph(f'${reserva.total_pagado()}', styles['texto_bold'])],
        [
            Paragraph('Saldo pendiente' if saldo > 0 else 'Saldo', styles['total_label']),
            Paragraph(f'<font color="{color_estado.hexval()}">${saldo if saldo > 0 else 0}</font>', styles['total_valor']),
        ],
    ], colWidths=[doc.width - 100, 100])
    resumen.setStyle(TableStyle([('LINEABOVE', (0, -1), (-1, -1), 0.8, c['texto']), ('TOPPADDING', (0, -1), (-1, -1), 6)]))
    elementos.append(resumen)

    elementos.append(Spacer(1, 24))
    elementos.append(Paragraph('Gracias por su pago.', styles['pie']))

    doc.build(elementos)
    return buffer.getvalue()


def _enviar_pdf_por_correo(destinatario, asunto, cuerpo, nombre_archivo, contenido_pdf):
    """Devuelve True si se pudo enviar (o al menos entregar al backend de
    mail configurado), False si no había a dónde mandarlo o falló el envío.
    Nunca deja que un error de correo tire abajo la operación que lo
    disparó (el pago/checkout ya se guardó igual)."""
    if not destinatario:
        return False
    try:
        email = EmailMessage(subject=asunto, body=cuerpo, to=[destinatario])
        email.attach(nombre_archivo, contenido_pdf, 'application/pdf')
        email.send(fail_silently=False)
        return True
    except Exception:
        logger.exception('No se pudo enviar el correo a %s', destinatario)
        return False


def enviar_factura_por_correo(factura):
    nombre_hotel = ConfiguracionHotel.actual().nombre_hotel
    reserva = factura.reserva
    cuerpo = (
        f'Hola {reserva.cliente.nombre},\n\n'
        f'Adjuntamos la factura #{factura.id} de tu estadía en {nombre_hotel} '
        f'por un total de ${factura.total}.\n\nGracias por tu visita.'
    )
    return _enviar_pdf_por_correo(
        reserva.cliente.correo, f'{nombre_hotel} — Factura #{factura.id}',
        cuerpo, f'factura_{factura.id}.pdf', pdf_factura_bytes(factura),
    )


def enviar_comprobante_pago_por_correo(pago):
    nombre_hotel = ConfiguracionHotel.actual().nombre_hotel
    reserva = pago.reserva
    cuerpo = (
        f'Hola {reserva.cliente.nombre},\n\n'
        f'Registramos tu pago de ${pago.monto} ({pago.get_metodo_display()}) '
        f'para la reserva #{reserva.id}. Adjuntamos el comprobante.\n\nGracias.'
    )
    return _enviar_pdf_por_correo(
        reserva.cliente.correo, f'{nombre_hotel} — Comprobante de pago #{pago.id}',
        cuerpo, f'comprobante_pago_{pago.id}.pdf', pdf_comprobante_pago_bytes(pago),
    )
