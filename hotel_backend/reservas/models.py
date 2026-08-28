from datetime import date, time
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

from .validadores_ecuador import cedula_valida, ruc_valido, validar_ruc, validar_telefono_ecuador

TIPOS_DOCUMENTO = [
    ('Cedula', 'Cédula'),
    ('Pasaporte', 'Pasaporte'),
    ('RUC', 'RUC'),
    ('Otro', 'Otro'),
]


# Modelo de Cliente
class Cliente(models.Model):
    # 13 (no 10) para que entre un RUC completo cuando tipo_documento='RUC'
    # (sección 31: el sistema está pensado para Ecuador — cédula/RUC del
    # Registro Civil/SRI, ver validadores_ecuador.py).
    cedula = models.CharField(
        max_length=13, primary_key=True, verbose_name='N.º de documento',
        help_text='Cédula (10 dígitos) o RUC (13 dígitos) según el tipo elegido abajo.',
    )
    tipo_documento = models.CharField(max_length=20, choices=TIPOS_DOCUMENTO, default='Cedula')
    nombre = models.CharField(max_length=50)
    apellido = models.CharField(max_length=50)
    telefono = models.CharField(
        max_length=10, validators=[validar_telefono_ecuador],
        help_text='10 dígitos, empieza con 0 (ej. 0991234567).',
    )
    correo = models.EmailField()
    direccion = models.CharField(max_length=100, null=True, blank=True)
    nacionalidad = models.CharField(max_length=50, null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['apellido', 'nombre']

    def __str__(self):
        return f'{self.cedula} - {self.nombre} {self.apellido}'

    def nombre_completo(self):
        return f'{self.nombre} {self.apellido}'

    def clean(self):
        super().clean()
        # El formato exigido depende de tipo_documento: cédula y RUC tienen
        # un algoritmo verificable (Registro Civil/SRI); pasaporte/otro no
        # tienen un formato único en Ecuador, así que ahí no se valida más
        # que "no vacío" (ya lo exige el propio campo).
        if self.tipo_documento == 'Cedula' and self.cedula and not cedula_valida(self.cedula):
            raise ValidationError({'cedula': 'Cédula ecuatoriana inválida — revisá el número (10 dígitos).'})
        if self.tipo_documento == 'RUC' and self.cedula and not ruc_valido(self.cedula):
            raise ValidationError({'cedula': 'RUC ecuatoriano inválido — revisá el número (13 dígitos).'})


# Catálogo configurable de tipos de habitación (sección 11 del plan). El
# administrador puede agregar tipos nuevos desde el admin; viene con los
# típicos de hotel precargados vía migración de datos.
class TipoHabitacion(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True)
    capacidad = models.PositiveSmallIntegerField(default=1, help_text='Huéspedes máximos')
    precio_base = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Precio de referencia por noche, IVA incluido (sección 31).',
    )
    camas = models.PositiveSmallIntegerField(default=1)
    servicios_incluidos = models.TextField(
        blank=True,
        help_text='Lista libre por ahora (ej: "wifi, desayuno"); se formaliza en el módulo de Servicios.',
    )

    class Meta:
        ordering = ['precio_base']

    def __str__(self):
        return self.nombre


ESTADOS_HABITACION = [
    ('Disponible', 'Disponible'),
    ('Reservada', 'Reservada'),
    ('Ocupada', 'Ocupada'),
    ('Limpieza', 'Limpieza'),
    ('Mantenimiento', 'Mantenimiento'),
    ('Fuera de servicio', 'Fuera de servicio'),
]


# Modelo de Habitación
class Habitacion(models.Model):
    codigo = models.CharField(max_length=100, primary_key=True)
    numero = models.CharField(max_length=10, unique=True)
    tipo = models.ForeignKey(TipoHabitacion, on_delete=models.PROTECT, related_name='habitaciones')
    precio = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text=(
            'Precio por noche que paga el huésped, IVA incluido (sección 31). '
            'Si se deja en blanco se usa el del tipo.'
        ),
    )
    estado = models.CharField(max_length=20, choices=ESTADOS_HABITACION, default='Disponible')
    piso = models.CharField(max_length=10, null=True, blank=True)
    descripcion = models.TextField(null=True, blank=True)
    caracteristicas = models.TextField(
        null=True, blank=True,
        help_text='Ej: vista al mar, balcón, aire acondicionado (texto libre por ahora).',
    )
    imagen = models.ImageField(upload_to='habitaciones/', null=True, blank=True)

    # NOTA: 'codigo' y 'numero' son dos identificadores distintos para lo mismo
    # (codigo es la PK interna, numero es único de todas formas). Lo ideal sería
    # eliminar 'codigo' y usar 'numero' como PK, pero cambiar la primary key de
    # una tabla que ya tiene la FK de Reserva apuntándole es una migración
    # riesgosa de aplicar sin poder probarla contra la base real. Se deja
    # pendiente para hacerla con cuidado (y con respaldo de datos) aparte.

    class Meta:
        ordering = ['numero']

    def __str__(self):
        return f'Habitación {self.numero} - {self.tipo}'

    def es_disponible(self):
        return self.estado == 'Disponible'

    def reserva_actual(self):
        """La reserva activa 'ahora' (hoy cae dentro de su rango), si existe."""
        hoy = date.today()
        return self.reserva_set.filter(
            fecha_ingreso__lte=hoy, fecha_salida__gt=hoy, cancelada_en__isnull=True,
        ).first()


# Modelo de Reserva
class Reserva(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT)
    habitacion = models.ForeignKey(Habitacion, on_delete=models.CASCADE)
    fecha_ingreso = models.DateField()
    fecha_salida = models.DateField()
    # Cuándo se CREÓ la reserva en el sistema (distinto de fecha_ingreso, que
    # es la fecha planeada de llegada) — lo usan las notificaciones de
    # "reserva nueva" (sección 28).
    creada_en = models.DateTimeField(auto_now_add=True, null=True)
    # Fecha/hora REAL en que ocurrió el check-in/check-out (sección 16-17),
    # distinta de fecha_ingreso/fecha_salida que son la fecha PLANEADA de la
    # reserva. Nulas mientras no haya ocurrido ese evento todavía.
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    # Cancelación "suave": no se borra la fila (sección 26 necesita poder
    # reportar reservas canceladas, y borrarla se llevaría en CASCADE
    # cualquier pago/depósito ya registrado — ver services.cancelar_reserva).
    cancelada_en = models.DateTimeField(null=True, blank=True)
    motivo_cancelacion = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_ingreso']
        constraints = [
            models.CheckConstraint(
                check=Q(fecha_salida__gt=models.F('fecha_ingreso')),
                name='reserva_fecha_salida_posterior_a_ingreso',
            ),
        ]

    def __str__(self):
        return f'Reserva de {self.cliente.apellido} en habitación {self.habitacion.numero}'

    def duracion(self):
        return (self.fecha_salida - self.fecha_ingreso).days

    def costo(self):
        """Solo el costo del alojamiento (noches × precio). No incluye
        consumos — para eso está total_con_iva()."""
        return self.habitacion.precio * self.duracion()

    def total_consumos(self):
        return self.consumos.aggregate(t=Sum('subtotal'))['t'] or 0

    def total_con_iva(self):
        """Lo que el huésped paga en total: alojamiento + consumos, tal
        como están cargados los precios en Habitaciones/Precios — que YA
        incluyen el IVA (sección 31: así es como cotiza la mayoría de los
        hoteles; el precio de la habitación no cambia según el % de IVA)."""
        return self.costo() + self.total_consumos()

    def total_estadia(self):
        """El SUBTOTAL sin impuesto — se obtiene descontando el IVA del
        total, no sumándolo aparte, porque el precio cargado ya lo trae
        incluido. Para lo que el huésped paga de verdad, ver
        total_con_iva()."""
        porcentaje = ConfiguracionHotel.actual().iva_porcentaje
        divisor = Decimal('1') + (porcentaje / Decimal('100'))
        return (self.total_con_iva() / divisor).quantize(Decimal('0.01'))

    def iva(self):
        """Impuesto ya incluido en total_con_iva() — sale por diferencia
        con el subtotal (no se recalcula aparte) para que subtotal + IVA
        cierre exacto con el total, centavo a centavo."""
        return self.total_con_iva() - self.total_estadia()

    def total_pagado(self):
        return self.pagos.aggregate(t=Sum('monto'))['t'] or 0

    def saldo_pendiente(self):
        # Con factura ya emitida, el saldo se mide contra su total FIJO
        # (no contra total_con_iva() en vivo) — si el % de IVA cambia
        # después en Configuración, una factura ya emitida no debe
        # "deberle" de golpe una diferencia que nunca se le cobró.
        total = self.factura.total if hasattr(self, 'factura') else self.total_con_iva()
        return total - self.total_pagado()

    def puede_hacer_checkin(self):
        return self.check_in_at is None

    def puede_hacer_checkout(self):
        return self.check_in_at is not None and self.check_out_at is None

    def esta_alojado(self):
        return self.check_in_at is not None and self.check_out_at is None

    def esta_cancelada(self):
        return self.cancelada_en is not None

    def es_no_show(self):
        """No se presentó: la fecha de salida ya pasó, nunca hizo check-in,
        y no fue cancelada formalmente (sección 26: reporte de reservas)."""
        return (
            not self.esta_cancelada()
            and self.check_in_at is None
            and self.fecha_salida < date.today()
        )

    def estado_display(self):
        if self.esta_cancelada():
            return 'Cancelada'
        if self.check_out_at:
            return 'Completada'
        if self.check_in_at:
            return 'Alojado'
        if self.es_no_show():
            return 'No-show'
        return 'Confirmada'

    def clean(self):
        super().clean()
        errores = {}

        if self.fecha_ingreso and self.fecha_salida and self.fecha_salida <= self.fecha_ingreso:
            errores['fecha_salida'] = 'La fecha de salida debe ser posterior a la fecha de ingreso.'

        if self.habitacion_id and self.fecha_ingreso and self.fecha_salida:
            solapadas = Reserva.objects.filter(
                habitacion=self.habitacion,
                fecha_ingreso__lt=self.fecha_salida,
                fecha_salida__gt=self.fecha_ingreso,
                cancelada_en__isnull=True,
            ).exclude(pk=self.pk)
            if solapadas.exists():
                errores['habitacion'] = (
                    'La habitación ya está reservada en ese rango de fechas.'
                )

        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# Modelo de Factura
class Factura(models.Model):
    reserva = models.OneToOneField(Reserva, on_delete=models.CASCADE)
    fecha = models.DateField(default=date.today)
    # subtotal/iva_monto/total se recalculan automáticamente a partir de la
    # reserva en save(), así que no deberían editarse a mano (quedan de
    # solo lectura en el admin). Se guardan los tres — no solo el total —
    # para que la factura quede como un comprobante fijo: si más adelante
    # cambia el % de IVA en Configuración, las facturas ya emitidas no
    # deben cambiar de valor retroactivamente.
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    iva_monto = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0, verbose_name='IVA')
    total = models.DecimalField(max_digits=10, decimal_places=2, editable=False)

    def __str__(self):
        return f'Factura de {self.reserva.cliente.apellido} por {self.total}'

    def porcentaje_iva(self):
        """% de IVA efectivamente aplicado en ESTA factura, calculado a
        partir de lo ya guardado (no del % actual en Configuración, que
        puede haber cambiado desde que se emitió)."""
        if not self.subtotal:
            return Decimal('0')
        return (self.iva_monto / self.subtotal * 100).quantize(Decimal('0.01'))

    def save(self, *args, **kwargs):
        self.subtotal = self.reserva.total_estadia()
        self.iva_monto = self.reserva.iva()
        self.total = self.subtotal + self.iva_monto
        super().save(*args, **kwargs)


CATEGORIAS_SERVICIO = [
    ('Restaurante', 'Restaurante'),
    ('Minibar', 'Minibar'),
    ('Lavanderia', 'Lavandería'),
    ('Parqueadero', 'Parqueadero'),
    ('Transporte', 'Transporte'),
    ('Room service', 'Room service'),
    ('Otros', 'Otros'),
]


# Catálogo de servicios/consumos (sección 18). El administrador puede
# agregar más; viene con uno de ejemplo por categoría precargado.
class Servicio(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    categoria = models.CharField(max_length=20, choices=CATEGORIAS_SERVICIO, default='Otros')
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True, help_text='Si está desactivado, no aparece para agregar consumos nuevos.')

    class Meta:
        ordering = ['categoria', 'nombre']

    def __str__(self):
        return f'{self.nombre} (${self.precio})'


# Un consumo de un servicio durante una estadía — se suma a la cuenta de
# la reserva (Reserva.total_estadia()).
class Consumo(models.Model):
    reserva = models.ForeignKey(Reserva, on_delete=models.CASCADE, related_name='consumos')
    servicio = models.ForeignKey(Servicio, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(default=1)
    # Se copia el precio del servicio al momento del consumo: si el precio
    # del catálogo cambia después, no debe alterar consumos ya registrados.
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Quién lo registró.',
    )

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f'{self.cantidad} x {self.servicio.nombre} — reserva #{self.reserva_id}'

    def save(self, *args, **kwargs):
        if self.precio_unitario is None:
            self.precio_unitario = self.servicio.precio
        self.subtotal = self.precio_unitario * self.cantidad
        super().save(*args, **kwargs)


METODOS_PAGO = [
    ('Efectivo', 'Efectivo'),
    ('Tarjeta', 'Tarjeta'),
    ('Transferencia', 'Transferencia'),
    ('Otro', 'Otro'),
]


# Un pago contra la cuenta de una reserva (sección 19). No se permite
# registrar un pago que supere el saldo pendiente — ver
# services.registrar_pago().
METODOS_CON_REFERENCIA = ('Transferencia', 'Tarjeta')


class Pago(models.Model):
    reserva = models.ForeignKey(Reserva, on_delete=models.CASCADE, related_name='pagos')
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS_PAGO, default='Efectivo')
    # Nº de comprobante de la transferencia o de autorización de la tarjeta
    # — sirve para conciliar contra el banco/pasarela después. Obligatorio
    # para Transferencia/Tarjeta, sin sentido para Efectivo (ver
    # services.registrar_pago, que es quien realmente exige esto).
    referencia = models.CharField(
        max_length=100, blank=True, verbose_name='N.º de comprobante / referencia',
        help_text='Obligatorio para Transferencia y Tarjeta.',
    )
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Quién lo registró.',
    )
    observacion = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f'Pago de ${self.monto} ({self.metodo}) — reserva #{self.reserva_id}'


ESTADOS_TAREA_LIMPIEZA = [
    ('Pendiente', 'Pendiente'),
    ('En limpieza', 'En limpieza'),
    ('Limpia', 'Limpia'),
]


# Tarea de limpieza de una habitación (sección 21). Se crea automáticamente
# al hacer check-out (ver services.hacer_checkout); también puede crearse a
# mano desde el módulo de Limpieza para una habitación que no pasó por un
# check-out (ej. limpieza extra pedida por un huésped).
class TareaLimpieza(models.Model):
    habitacion = models.ForeignKey(Habitacion, on_delete=models.CASCADE, related_name='tareas_limpieza')
    estado = models.CharField(max_length=20, choices=ESTADOS_TAREA_LIMPIEZA, default='Pendiente')
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    iniciada_en = models.DateTimeField(null=True, blank=True)
    finalizada_en = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        ordering = ['-creada_en']

    def __str__(self):
        return f'Limpieza {self.habitacion.numero} — {self.estado}'


PRIORIDADES_INCIDENCIA = [
    ('Baja', 'Baja'),
    ('Media', 'Media'),
    ('Alta', 'Alta'),
    ('Urgente', 'Urgente'),
]

ESTADOS_INCIDENCIA = [
    ('Reportado', 'Reportado'),
    ('En revisión', 'En revisión'),
    ('En reparación', 'En reparación'),
    ('Resuelto', 'Resuelto'),
    ('Cerrado', 'Cerrado'),
]


# Incidencia de mantenimiento de una habitación (sección 22): reportar →
# asignar responsable → establecer prioridad → reparar → resolver → cerrar.
class Incidencia(models.Model):
    habitacion = models.ForeignKey(Habitacion, on_delete=models.CASCADE, related_name='incidencias')
    descripcion = models.TextField()
    prioridad = models.CharField(max_length=10, choices=PRIORIDADES_INCIDENCIA, default='Media')
    estado = models.CharField(max_length=20, choices=ESTADOS_INCIDENCIA, default='Reportado')
    reportado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='incidencias_reportadas',
    )
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='incidencias_asignadas',
    )
    solucion = models.TextField(blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    resuelta_en = models.DateTimeField(null=True, blank=True)
    cerrada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name_plural = 'incidencias'

    def __str__(self):
        return f'Incidencia #{self.pk} — {self.habitacion.numero} ({self.estado})'

    def esta_abierta(self):
        return self.estado != 'Cerrado'

    def puede_asignarse(self):
        return self.estado in ('Reportado', 'En revisión')

    def puede_iniciar_reparacion(self):
        return self.responsable_id is not None and self.estado in ('Reportado', 'En revisión')

    def puede_resolverse(self):
        return self.estado == 'En reparación'

    def puede_cerrarse(self):
        return self.estado == 'Resuelto'


# Registro de auditoría (sección 25): quién hizo qué, cuándo y sobre qué
# objeto. Es de solo lectura por diseño — ver AuditLogAdmin en admin.py y
# setup_roles.py (ningún rol tiene add/change/delete sobre este modelo,
# ni siquiera Administrador; se llena únicamente vía código, nunca a mano).
class AuditLog(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Nulo si el evento no tiene un usuario autenticado (ej. login fallido).',
    )
    fecha = models.DateTimeField(auto_now_add=True)
    accion = models.CharField(max_length=50)
    modulo = models.CharField(max_length=50)
    objeto_repr = models.CharField(max_length=200, blank=True)
    descripcion = models.TextField(blank=True)
    direccion_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'registro de auditoría'
        verbose_name_plural = 'registros de auditoría'

    def __str__(self):
        quien = self.usuario.get_username() if self.usuario else 'sistema'
        return f'{self.fecha:%Y-%m-%d %H:%M} — {quien} — {self.accion}'


# Configuración general del hotel (sección 29): nombre, dirección, logo,
# políticas por defecto. Es un singleton — siempre hay una única fila
# (pk=1) — porque no tiene sentido "más de una configuración" para un
# solo hotel; se usa en la factura y queda disponible para lo que se
# agregue más adelante (recibos, reportes con membrete, etc.).
class ConfiguracionHotel(models.Model):
    nombre_hotel = models.CharField(max_length=100, default='Sistema Hotelero')
    direccion = models.CharField(max_length=150, blank=True)
    telefono = models.CharField(max_length=10, blank=True, validators=[validar_telefono_ecuador])
    ruc = models.CharField(max_length=13, blank=True, verbose_name='RUC', validators=[validar_ruc])
    logo = models.ImageField(upload_to='config/', blank=True, null=True)
    moneda = models.CharField(max_length=3, default='USD', verbose_name='Moneda (código)')
    # 15% = tasa de IVA vigente en Ecuador. Los precios de habitaciones ya
    # lo incluyen (sección 31: así cotiza la mayoría de los hoteles) — este
    # % se usa para DESGLOSAR cuánto de ese precio es IVA en la factura,
    # no para sumarlo aparte. Un hotel que no deba cobrarlo pone 0 acá.
    iva_porcentaje = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('15.00'), verbose_name='IVA (%)',
        help_text=(
            'Los precios de las habitaciones ya incluyen IVA — este % solo se usa '
            'para desglosarlo en la factura. Poné 0 si no corresponde cobrarlo.'
        ),
    )
    hora_checkin_default = models.TimeField(default=time(14, 0))
    hora_checkout_default = models.TimeField(default=time(12, 0))
    politica_cancelacion = models.TextField(
        blank=True,
        help_text='Texto informativo — todavía no bloquea cancelaciones automáticamente.',
    )

    class Meta:
        verbose_name = 'configuración del hotel'
        verbose_name_plural = 'configuración del hotel'

    def __str__(self):
        return self.nombre_hotel

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Singleton: nunca se borra desde la app (no tendría sentido un
        hotel sin configuración)."""

    @classmethod
    def actual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
