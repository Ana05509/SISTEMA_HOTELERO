import calendar
import csv
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from . import auditoria, comprobantes, services
from .decorators import login_required_api, permission_required_api
from .forms import (
    BusquedaDisponibilidadForm, CambiarPasswordForm, ConfiguracionHotelForm, ConsumoForm, FechaEspecialForm,
    IncidenciaForm, PagoForm, ReservaForm, UsuarioCrearForm, UsuarioEditarForm,
)
from .models import (
    AuditLog, Cliente, ConfiguracionHotel, Consumo, ESTADOS_HABITACION, Factura, FechaEspecial, Habitacion,
    Incidencia, Pago, Reserva, TareaLimpieza, TipoHabitacion,
)

# Módulos del plan que todavía no existen: se muestran en el menú como
# "próximamente" en vez de enlazar a algo que no funciona (ver base.html).
PROXIMOS_MODULOS = []


def superuser_required(view):
    """Para pantallas reservadas al rol "Administrador" real (cuenta
    is_superuser, ver setup_roles.py) — hoy, gestión de usuarios: asignar
    roles es sensible como para dejarlo detrás de un permiso delegable."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


class LoginView(auth_views.LoginView):
    """Login estándar de Django + la casilla "Recordarme": sin marcarla, la
    sesión expira al cerrar el navegador; marcada, dura 30 días en vez de
    las 2 semanas por defecto."""
    template_name = 'registration/login.html'

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if self.request.POST.get('recordarme'):
            self.request.session.set_expiry(60 * 60 * 24 * 30)
        else:
            self.request.session.set_expiry(0)
        return respuesta


@login_required
def dashboard(request):
    hoy = timezone.localdate()
    contexto = {'seccion': 'dashboard', 'proximos_modulos': PROXIMOS_MODULOS}

    if request.user.has_perm('reservas.view_habitacion'):
        habitaciones = Habitacion.objects.all()
        contexto.update({
            'habitaciones_total': habitaciones.count(),
            'habitaciones_disponibles': habitaciones.filter(estado='Disponible').count(),
            'habitaciones_ocupadas': habitaciones.filter(estado='Ocupada').count(),
            'habitaciones_mantenimiento': habitaciones.filter(estado='Mantenimiento').count(),
        })

    if request.user.has_perm('reservas.view_reserva'):
        reservas_qs = Reserva.objects.select_related('cliente', 'habitacion').filter(cancelada_en__isnull=True)
        contexto.update({
            'checkins_hoy': reservas_qs.filter(fecha_ingreso=hoy).count(),
            'checkouts_hoy': reservas_qs.filter(fecha_salida=hoy).count(),
            'proximas_reservas': reservas_qs.filter(fecha_ingreso__gt=hoy).order_by('fecha_ingreso')[:5],
        })

        # Reservas creadas por día, últimos 7 días — separadas en
        # confirmadas/canceladas para el gráfico de barras del dashboard.
        dias_grafico = [hoy - timedelta(days=i) for i in range(6, -1, -1)]
        creadas_periodo = Reserva.objects.filter(
            creada_en__date__gte=dias_grafico[0], creada_en__date__lte=hoy,
        )
        confirmadas_por_dia, canceladas_por_dia = [], []
        for dia in dias_grafico:
            del_dia = creadas_periodo.filter(creada_en__date=dia)
            confirmadas_por_dia.append(del_dia.filter(cancelada_en__isnull=True).count())
            canceladas_por_dia.append(del_dia.filter(cancelada_en__isnull=False).count())
        contexto.update({
            'grafico_dias': [d.strftime('%d/%m') for d in dias_grafico],
            'grafico_confirmadas': confirmadas_por_dia,
            'grafico_canceladas': canceladas_por_dia,
        })

    if request.user.has_perm('reservas.view_factura'):
        contexto.update({
            'ingresos_dia': Factura.objects.filter(fecha=hoy).aggregate(t=Sum('total'))['t'] or 0,
            'ingresos_mes': Factura.objects.filter(
                fecha__year=hoy.year, fecha__month=hoy.month
            ).aggregate(t=Sum('total'))['t'] or 0,
        })

    return render(request, 'reservas/dashboard.html', contexto)


def _decimal_precio(valor):
    """Convierte un precio recibido por POST a Decimal positivo, o None si
    no es válido — para no reventar con un ValueError feo si alguien manda
    basura en el campo."""
    try:
        numero = Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return numero if numero > 0 else None


@permission_required('reservas.view_habitacion', raise_exception=True)
def precios_habitaciones(request):
    """Editor de precios (sección 11/31): precio base por tipo de
    habitación y precio individual de cada habitación (para casos como
    "esta suite tiene vista al mar, cobra más que las demás del mismo
    tipo"). Separado del admin de Django para que el personal autorizado
    no necesite entrar ahí solo para esto."""
    puede_editar = (
        request.user.has_perm('reservas.change_tipohabitacion')
        and request.user.has_perm('reservas.change_habitacion')
    )
    tipos = TipoHabitacion.objects.all().order_by('nombre')
    habitaciones = Habitacion.objects.select_related('tipo').order_by('numero')

    if request.method == 'POST':
        if not puede_editar:
            raise PermissionDenied
        errores = []
        cambios = 0
        with transaction.atomic():
            for tipo in tipos:
                crudo = request.POST.get(f'tipo_precio_{tipo.id}')
                if crudo is None:
                    continue
                nuevo = _decimal_precio(crudo)
                if nuevo is None:
                    errores.append(f'Precio inválido para el tipo "{tipo.nombre}".')
                elif nuevo != tipo.precio_base:
                    tipo.precio_base = nuevo
                    tipo.save(update_fields=['precio_base'])
                    auditoria.registrar_desde_request(
                        request, 'modificar', 'habitaciones', objeto=tipo,
                        descripcion=f'Precio base de "{tipo.nombre}" actualizado a ${nuevo}.',
                    )
                    cambios += 1

            for habitacion in habitaciones:
                crudo = request.POST.get(f'habitacion_precio_{habitacion.codigo}')
                if crudo is None:
                    continue
                nuevo = _decimal_precio(crudo)
                if nuevo is None:
                    errores.append(f'Precio inválido para la habitación {habitacion.numero}.')
                elif nuevo != habitacion.precio:
                    habitacion.precio = nuevo
                    habitacion.save(update_fields=['precio'])
                    auditoria.registrar_desde_request(
                        request, 'modificar', 'habitaciones', objeto=habitacion,
                        descripcion=f'Precio de la habitación {habitacion.numero} actualizado a ${nuevo}.',
                    )
                    cambios += 1

        if errores:
            messages.error(request, ' '.join(errores))
        elif cambios:
            messages.success(request, f'{cambios} precio(s) actualizado(s).')
        else:
            messages.info(request, 'No había cambios para guardar.')
        return redirect('precios_habitaciones')

    return render(request, 'reservas/precios_habitaciones.html', {
        'seccion': 'habitaciones',
        'tipos': tipos,
        'habitaciones': habitaciones,
        'puede_editar': puede_editar,
    })


@permission_required('reservas.view_fechaespecial', raise_exception=True)
def fechas_especiales_lista(request):
    """Temporadas especiales (sección 32): rango de fechas con descuento/
    recargo automático sobre el precio y, opcionalmente, un tema visual
    que recolorea el sistema mientras están vigentes."""
    fechas = FechaEspecial.objects.all()
    return render(request, 'reservas/fechas_especiales_lista.html', {
        'seccion': 'fechas_especiales', 'fechas': fechas,
    })


@permission_required('reservas.add_fechaespecial', raise_exception=True)
def fecha_especial_nueva(request):
    if request.method == 'POST':
        form = FechaEspecialForm(request.POST)
        if form.is_valid():
            fecha_especial = form.save()
            auditoria.registrar_desde_request(
                request, 'crear', 'fechas_especiales', objeto=fecha_especial,
                descripcion=f'Fecha especial "{fecha_especial.nombre}" creada '
                            f'({fecha_especial.fecha_inicio} a {fecha_especial.fecha_fin}).',
            )
            messages.success(request, f'"{fecha_especial.nombre}" creada.')
            return redirect('fechas_especiales_lista')
        messages.error(request, 'Revisá los datos de la fecha especial.')
    else:
        form = FechaEspecialForm()
    return render(request, 'reservas/fecha_especial_form.html', {
        'seccion': 'fechas_especiales', 'form': form, 'modo': 'crear',
    })


@permission_required('reservas.change_fechaespecial', raise_exception=True)
def fecha_especial_editar(request, fecha_especial_id):
    fecha_especial = get_object_or_404(FechaEspecial, pk=fecha_especial_id)
    if request.method == 'POST':
        form = FechaEspecialForm(request.POST, instance=fecha_especial)
        if form.is_valid():
            form.save()
            auditoria.registrar_desde_request(
                request, 'modificar', 'fechas_especiales', objeto=fecha_especial,
                descripcion=f'Fecha especial "{fecha_especial.nombre}" editada.',
            )
            messages.success(request, f'"{fecha_especial.nombre}" actualizada.')
            return redirect('fechas_especiales_lista')
        messages.error(request, 'Revisá los datos de la fecha especial.')
    else:
        form = FechaEspecialForm(instance=fecha_especial)
    return render(request, 'reservas/fecha_especial_form.html', {
        'seccion': 'fechas_especiales', 'form': form, 'modo': 'editar', 'fecha_especial': fecha_especial,
    })


@require_http_methods(['POST'])
@permission_required('reservas.delete_fechaespecial', raise_exception=True)
def fecha_especial_eliminar(request, fecha_especial_id):
    fecha_especial = get_object_or_404(FechaEspecial, pk=fecha_especial_id)
    nombre = fecha_especial.nombre
    fecha_especial.delete()
    auditoria.registrar_desde_request(
        request, 'eliminar', 'fechas_especiales', descripcion=f'Fecha especial "{nombre}" eliminada.',
    )
    messages.success(request, f'"{nombre}" eliminada.')
    return redirect('fechas_especiales_lista')


@permission_required('reservas.view_habitacion', raise_exception=True)
def habitaciones_grid(request):
    """Vista visual de todas las habitaciones agrupadas por piso, con su
    estado a simple vista (sección 10 del plan)."""
    habitaciones = Habitacion.objects.select_related('tipo').all()

    pisos = {}
    for h in habitaciones:
        pisos.setdefault(h.piso or 'Sin piso asignado', []).append(h)

    contexto = {
        'seccion': 'habitaciones',
        'pisos': sorted(pisos.items(), key=lambda kv: kv[0]),
    }
    return render(request, 'reservas/habitaciones_grid.html', contexto)


@permission_required('reservas.view_habitacion', raise_exception=True)
def habitacion_detalle(request, codigo):
    habitacion = get_object_or_404(Habitacion.objects.select_related('tipo'), pk=codigo)
    contexto = {
        'seccion': 'habitaciones',
        'habitacion': habitacion,
        'reserva_actual': habitacion.reserva_actual() if request.user.has_perm('reservas.view_reserva') else None,
    }
    if request.user.has_perm('reservas.view_incidencia'):
        contexto['incidencias_abiertas'] = habitacion.incidencias.exclude(estado='Cerrado')
    return render(request, 'reservas/habitacion_detalle.html', contexto)


@permission_required('reservas.view_cliente', raise_exception=True)
def huespedes_lista(request):
    q = request.GET.get('q', '').strip()
    huespedes = Cliente.objects.all()
    if q:
        huespedes = huespedes.filter(
            Q(cedula__icontains=q) | Q(nombre__icontains=q) | Q(apellido__icontains=q)
        )

    pagina = Paginator(huespedes, 20).get_page(request.GET.get('page'))
    return render(request, 'reservas/huespedes_lista.html', {
        'seccion': 'huespedes',
        'pagina': pagina,
        'q': q,
    })


@permission_required('reservas.view_cliente', raise_exception=True)
def huesped_detalle(request, cedula):
    huesped = get_object_or_404(Cliente, pk=cedula)
    reservas_qs = []
    if request.user.has_perm('reservas.view_reserva'):
        reservas_qs = huesped.reserva_set.select_related('habitacion').order_by('-fecha_ingreso')

    return render(request, 'reservas/huesped_detalle.html', {
        'seccion': 'huespedes',
        'huesped': huesped,
        'reservas': reservas_qs,
    })


@permission_required('reservas.view_reserva', raise_exception=True)
def reservas_lista(request):
    hoy = timezone.localdate()
    filtro = request.GET.get('filtro', 'proximas')
    reservas_qs = Reserva.objects.select_related('cliente', 'habitacion')

    if filtro == 'canceladas':
        reservas_qs = reservas_qs.filter(cancelada_en__isnull=False).order_by('-cancelada_en')
    elif filtro == 'completadas':
        reservas_qs = reservas_qs.filter(check_out_at__isnull=False).order_by('-check_out_at')
    elif filtro == 'no_show':
        reservas_qs = reservas_qs.filter(
            cancelada_en__isnull=True, check_in_at__isnull=True, fecha_salida__lt=hoy,
        ).order_by('-fecha_salida')
    elif filtro == 'pasadas':
        reservas_qs = reservas_qs.filter(
            fecha_salida__lt=hoy, cancelada_en__isnull=True,
        ).order_by('-fecha_salida')
    elif filtro == 'hoy':
        reservas_qs = reservas_qs.filter(
            fecha_ingreso__lte=hoy, fecha_salida__gt=hoy, cancelada_en__isnull=True,
        ).order_by('fecha_salida')
    else:
        filtro = 'proximas'
        reservas_qs = reservas_qs.filter(
            fecha_salida__gte=hoy, cancelada_en__isnull=True,
        ).order_by('fecha_ingreso')

    pagina = Paginator(reservas_qs, 20).get_page(request.GET.get('page'))
    return render(request, 'reservas/reservas_lista.html', {
        'seccion': 'reservas',
        'pagina': pagina,
        'filtro': filtro,
        'hoy': hoy,
    })


@permission_required('reservas.view_pago', raise_exception=True)
def pagos_lista(request):
    """Ledger de todos los pagos registrados (sección 19), más reciente
    primero. Cada fila muestra también qué consumió el huésped en esa
    reserva — un pago no dice mucho por sí solo si no se ve contra qué."""
    pagos_qs = Pago.objects.select_related(
        'reserva__cliente', 'reserva__habitacion', 'usuario',
    ).prefetch_related('reserva__consumos__servicio')
    pagina = Paginator(pagos_qs, 25).get_page(request.GET.get('page'))
    total_periodo = pagos_qs.aggregate(t=Sum('monto'))['t'] or 0

    for pago in pagina:
        consumos = list(pago.reserva.consumos.all())
        pago.resumen_consumos = ', '.join(
            f'{c.cantidad}× {c.servicio.nombre}' for c in consumos
        )

    return render(request, 'reservas/pagos_lista.html', {
        'seccion': 'pagos',
        'pagina': pagina,
        'total': total_periodo,
    })


@permission_required('reservas.view_auditlog', raise_exception=True)
def auditoria_lista(request):
    """Sección 25: solo lectura. Filtrable por módulo/acción/usuario."""
    registros = AuditLog.objects.select_related('usuario')

    modulo = request.GET.get('modulo', '')
    if modulo:
        registros = registros.filter(modulo=modulo)

    accion = request.GET.get('accion', '')
    if accion:
        registros = registros.filter(accion=accion)

    modulos = AuditLog.objects.values_list('modulo', flat=True).distinct().order_by('modulo')
    acciones = AuditLog.objects.values_list('accion', flat=True).distinct().order_by('accion')

    pagina = Paginator(registros, 50).get_page(request.GET.get('page'))
    return render(request, 'reservas/auditoria_lista.html', {
        'seccion': 'auditoria', 'pagina': pagina,
        'modulos': modulos, 'acciones': acciones,
        'modulo_actual': modulo, 'accion_actual': accion,
    })


@permission_required('reservas.add_reserva', raise_exception=True)
def reserva_nueva(request):
    """Paso 1: elegir fechas (+ huéspedes) y ver qué habitaciones están
    realmente disponibles para ese rango (sección 13)."""
    disponibles = None
    form = BusquedaDisponibilidadForm(request.GET or None)
    if request.GET and form.is_valid():
        disponibles = services.habitaciones_disponibles(
            form.cleaned_data['fecha_ingreso'],
            form.cleaned_data['fecha_salida'],
            capacidad_minima=form.cleaned_data.get('huespedes'),
        )

    return render(request, 'reservas/reserva_nueva.html', {
        'seccion': 'reservas',
        'form': form,
        'buscado': bool(request.GET),
        'disponibles': disponibles,
    })


@permission_required('reservas.add_reserva', raise_exception=True)
def reserva_crear(request):
    """Paso 2: confirmar huésped + habitación ya elegidos y crear la reserva."""
    inicial = {
        'habitacion': request.GET.get('habitacion'),
        'fecha_ingreso': request.GET.get('fecha_ingreso'),
        'fecha_salida': request.GET.get('fecha_salida'),
    }

    if request.method == 'POST':
        form = ReservaForm(request.POST)
        if form.is_valid():
            try:
                reserva = services.crear_reserva(
                    cliente=form.cleaned_data['cliente'],
                    habitacion=form.cleaned_data['habitacion'],
                    fecha_ingreso=form.cleaned_data['fecha_ingreso'],
                    fecha_salida=form.cleaned_data['fecha_salida'],
                )
            except ValidationError as exc:
                if hasattr(exc, 'message_dict'):
                    for campo, mensajes in exc.message_dict.items():
                        for mensaje in mensajes:
                            form.add_error(campo if campo in form.fields else None, mensaje)
                else:
                    for mensaje in exc.messages:
                        form.add_error(None, mensaje)
            else:
                auditoria.registrar_desde_request(
                    request, 'crear_reserva', 'reservas', objeto=reserva,
                    descripcion=f'Reserva #{reserva.id}: {reserva.cliente.nombre_completo()}, '
                                f'hab. {reserva.habitacion.numero}, {reserva.fecha_ingreso} a {reserva.fecha_salida}.',
                )
                messages.success(request, f'Reserva #{reserva.id} creada para {reserva.cliente.nombre_completo()}.')
                return redirect('reservas_lista')
    else:
        form = ReservaForm(initial=inicial)

    habitacion = None
    if inicial['habitacion']:
        habitacion = Habitacion.objects.select_related('tipo').filter(pk=inicial['habitacion']).first()

    return render(request, 'reservas/reserva_crear.html', {
        'seccion': 'reservas',
        'form': form,
        'habitacion': habitacion,
    })


@require_http_methods(['POST'])
@permission_required('reservas.delete_reserva', raise_exception=True)
def reserva_cancelar(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id)
    try:
        services.cancelar_reserva(reserva)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        auditoria.registrar_desde_request(
            request, 'cancelar_reserva', 'reservas', objeto=reserva,
            descripcion=f'Reserva #{reserva.id} cancelada.',
        )
        messages.success(request, f'Reserva #{reserva_id} cancelada.')

    # 'volver' lo manda el propio formulario para volver a la página con el
    # filtro/página que tenía el usuario, pero es un valor de POST — no hay
    # que confiar en él a ciegas (open redirect). Se valida que sea una URL
    # relativa al propio sitio antes de usarla.
    volver = request.POST.get('volver')
    if volver and url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        return redirect(volver)
    return redirect('reservas_lista')


@permission_required('reservas.view_reserva', raise_exception=True)
def reserva_detalle(request, reserva_id):
    """Hub de una reserva: alojamiento + consumos (sección 18) + pagos y
    saldo (sección 19)."""
    reserva = get_object_or_404(
        Reserva.objects.select_related('cliente', 'habitacion', 'habitacion__tipo'),
        pk=reserva_id,
    )
    consumos = reserva.consumos.select_related('servicio') if request.user.has_perm('reservas.view_consumo') else []
    pagos = reserva.pagos.all() if request.user.has_perm('reservas.view_pago') else []

    consumo_form = None
    if reserva.esta_alojado() and request.user.has_perm('reservas.add_consumo'):
        consumo_form = ConsumoForm()

    pago_form = None
    if reserva.saldo_pendiente() > 0 and request.user.has_perm('reservas.add_pago'):
        pago_form = PagoForm()

    return render(request, 'reservas/reserva_detalle.html', {
        'seccion': 'reservas',
        'reserva': reserva,
        'consumos': consumos,
        'pagos': pagos,
        'form': consumo_form,
        'pago_form': pago_form,
    })


@require_http_methods(['POST'])
@permission_required('reservas.add_consumo', raise_exception=True)
def consumo_agregar(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id)
    form = ConsumoForm(request.POST)
    if form.is_valid():
        try:
            services.registrar_consumo(
                reserva=reserva,
                servicio=form.cleaned_data['servicio'],
                cantidad=form.cleaned_data['cantidad'],
                usuario=request.user,
            )
        except ValidationError as exc:
            messages.error(request, _mensaje_validacion(exc))
        else:
            messages.success(request, 'Consumo agregado.')
    else:
        messages.error(request, 'Revisá los datos del consumo.')
    return redirect('reserva_detalle', reserva_id=reserva_id)


@require_http_methods(['POST'])
@permission_required('reservas.add_pago', raise_exception=True)
def pago_agregar(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id)
    form = PagoForm(request.POST)
    if form.is_valid():
        try:
            pago = services.registrar_pago(
                reserva=reserva,
                monto=form.cleaned_data['monto'],
                metodo=form.cleaned_data['metodo'],
                usuario=request.user,
                observacion=form.cleaned_data['observacion'],
                referencia=form.cleaned_data['referencia'],
            )
        except ValidationError as exc:
            messages.error(request, _mensaje_validacion(exc))
        else:
            auditoria.registrar_desde_request(
                request, 'registrar_pago', 'pagos', objeto=reserva,
                descripcion=f'Pago de ${form.cleaned_data["monto"]} ({form.cleaned_data["metodo"]}) '
                            f'contra la reserva #{reserva.id}.',
            )
            enviado = comprobantes.enviar_comprobante_pago_por_correo(pago)
            if enviado and comprobantes.correo_es_real():
                messages.success(request, f'Pago registrado. Comprobante enviado a {reserva.cliente.correo}.')
            elif enviado:
                messages.success(
                    request,
                    f'Pago registrado. No hay un servidor de correo real configurado — '
                    f'el comprobante para {reserva.cliente.correo} quedó en el log del '
                    f'servidor en vez de salir de verdad (ver .env.example).',
                )
            else:
                messages.success(
                    request,
                    'Pago registrado. No se pudo enviar el comprobante por correo '
                    '(revisá la configuración de correo o el mail del huésped) — '
                    'lo podés descargar igual desde el detalle de la reserva.',
                )
    else:
        messages.error(request, 'Revisá los datos del pago.')
    return redirect('reserva_detalle', reserva_id=reserva_id)


@permission_required('reservas.view_reserva', raise_exception=True)
def calendario(request):
    """Calendario mensual: filas = habitaciones, columnas = días del mes
    (sección 14). Navegable con ?anio=YYYY&mes=M."""
    hoy = timezone.localdate()
    anio = int(request.GET.get('anio', hoy.year))
    mes = int(request.GET.get('mes', hoy.month))
    primer_dia = date(anio, mes, 1)
    ultimo_dia = date(anio, mes, calendar.monthrange(anio, mes)[1])
    dias = [primer_dia + timedelta(days=i) for i in range((ultimo_dia - primer_dia).days + 1)]

    habitaciones = Habitacion.objects.select_related('tipo').order_by('numero')
    reservas_mes = Reserva.objects.select_related('cliente').filter(
        fecha_ingreso__lte=ultimo_dia, fecha_salida__gt=primer_dia, cancelada_en__isnull=True,
    )
    por_habitacion = {}
    for r in reservas_mes:
        por_habitacion.setdefault(r.habitacion_id, []).append(r)

    filas = []
    for h in habitaciones:
        celdas = []
        for d in dias:
            reserva_del_dia = next(
                (r for r in por_habitacion.get(h.codigo, []) if r.fecha_ingreso <= d < r.fecha_salida),
                None,
            )
            celdas.append((d, reserva_del_dia))
        filas.append((h, celdas))

    anterior = (primer_dia - timedelta(days=1))
    siguiente = (ultimo_dia + timedelta(days=1))

    return render(request, 'reservas/calendario.html', {
        'seccion': 'calendario',
        'dias': dias,
        'filas': filas,
        'mes_actual': primer_dia,
        'hoy': hoy,
        'es_mes_actual': (anio, mes) == (hoy.year, hoy.month),
        'anio_anterior': anterior.year, 'mes_anterior': anterior.month,
        'anio_siguiente': siguiente.year, 'mes_siguiente': siguiente.month,
    })


@permission_required('reservas.change_reserva', raise_exception=True)
def checkin_lista(request):
    """Reservas cuya fecha de ingreso ya llegó (o pasó) y todavía no
    tuvieron check-in (sección 16)."""
    hoy = timezone.localdate()
    pendientes = Reserva.objects.select_related('cliente', 'habitacion').filter(
        fecha_ingreso__lte=hoy, check_in_at__isnull=True, check_out_at__isnull=True,
        cancelada_en__isnull=True,
    ).order_by('fecha_ingreso')
    return render(request, 'reservas/checkin_lista.html', {
        'seccion': 'checkin', 'reservas': pendientes,
    })


@require_http_methods(['POST'])
@permission_required('reservas.change_reserva', raise_exception=True)
def checkin_confirmar(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id)
    try:
        services.hacer_checkin(reserva)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        auditoria.registrar_desde_request(
            request, 'check_in', 'reservas', objeto=reserva,
            descripcion=f'Ingreso registrado de {reserva.cliente.nombre_completo()} en hab. {reserva.habitacion.numero}.',
        )
        messages.success(request, f'Ingreso registrado: {reserva.cliente.nombre_completo()} — Hab. {reserva.habitacion.numero}.')
    return redirect('checkin_lista')


@permission_required('reservas.change_reserva', raise_exception=True)
def checkout_lista(request):
    """Huéspedes actualmente alojados (con check-in hecho y sin check-out
    todavía), sección 17."""
    alojados = Reserva.objects.select_related('cliente', 'habitacion').filter(
        check_in_at__isnull=False, check_out_at__isnull=True,
    ).order_by('fecha_salida')
    return render(request, 'reservas/checkout_lista.html', {
        'seccion': 'checkout', 'reservas': alojados,
    })


@require_http_methods(['POST'])
@permission_required('reservas.change_reserva', raise_exception=True)
def checkout_confirmar(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id)
    try:
        factura = services.hacer_checkout(reserva)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
        return redirect('checkout_lista')

    auditoria.registrar_desde_request(
        request, 'check_out', 'reservas', objeto=reserva,
        descripcion=f'Salida registrada de {reserva.cliente.nombre_completo()} en hab. {reserva.habitacion.numero}.',
    )
    auditoria.registrar_desde_request(
        request, 'generar_factura', 'facturacion', objeto=factura,
        descripcion=f'Factura #{factura.id} generada por ${factura.total} (reserva #{reserva.id}).',
    )

    enviado = comprobantes.enviar_factura_por_correo(factura)
    if enviado and comprobantes.correo_es_real():
        aviso_correo = f' Factura enviada a {reserva.cliente.correo}.'
    elif enviado:
        aviso_correo = ' (correo simulado — sin SMTP configurado, ver .env.example)'
    else:
        aviso_correo = ' No se pudo enviar la factura por correo.'

    messages.success(
        request,
        f'Salida registrada: {reserva.cliente.nombre_completo()} — Hab. {reserva.habitacion.numero}. '
        f'Factura #{factura.id} por ${factura.total}. La habitación pasó a Limpieza.{aviso_correo}',
    )
    return redirect('checkout_lista')


@permission_required('reservas.change_habitacion', raise_exception=True)
@require_http_methods(['POST'])
def habitacion_finalizar_limpieza(request, codigo):
    habitacion = get_object_or_404(Habitacion, pk=codigo)
    try:
        services.finalizar_limpieza(habitacion)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Habitación {habitacion.numero} disponible de nuevo.')
    return redirect('habitacion_detalle', codigo=codigo)


@permission_required('reservas.view_tarealimpieza', raise_exception=True)
def limpieza_lista(request):
    """Sección 21: tareas pendientes o en curso, para el rol Limpieza."""
    tareas = TareaLimpieza.objects.select_related('habitacion', 'responsable').filter(
        estado__in=['Pendiente', 'En limpieza'],
    )
    return render(request, 'reservas/limpieza_lista.html', {
        'seccion': 'limpieza', 'tareas': tareas,
    })


@require_http_methods(['POST'])
@permission_required('reservas.change_tarealimpieza', raise_exception=True)
def limpieza_iniciar(request, tarea_id):
    tarea = get_object_or_404(TareaLimpieza, pk=tarea_id)
    try:
        services.iniciar_limpieza(tarea, responsable=request.user)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Limpieza de la habitación {tarea.habitacion.numero} iniciada.')
    return redirect('limpieza_lista')


@require_http_methods(['POST'])
@permission_required('reservas.change_tarealimpieza', raise_exception=True)
def limpieza_completar(request, tarea_id):
    tarea = get_object_or_404(TareaLimpieza, pk=tarea_id)
    try:
        services.completar_tarea_limpieza(tarea, observaciones=request.POST.get('observaciones', ''))
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Habitación {tarea.habitacion.numero} lista y disponible.')
    return redirect('limpieza_lista')


@permission_required('reservas.view_incidencia', raise_exception=True)
def mantenimiento_lista(request):
    """Sección 22: incidencias abiertas (todo lo que no esté Cerrado)."""
    incidencias = Incidencia.objects.select_related('habitacion', 'responsable', 'reportado_por').exclude(
        estado='Cerrado',
    )
    form = IncidenciaForm() if request.user.has_perm('reservas.add_incidencia') else None
    return render(request, 'reservas/mantenimiento_lista.html', {
        'seccion': 'mantenimiento', 'incidencias': incidencias, 'form': form,
    })


@require_http_methods(['POST'])
@permission_required('reservas.add_incidencia', raise_exception=True)
def mantenimiento_reportar(request):
    form = IncidenciaForm(request.POST)
    if form.is_valid():
        services.reportar_incidencia(
            habitacion=form.cleaned_data['habitacion'],
            descripcion=form.cleaned_data['descripcion'],
            prioridad=form.cleaned_data['prioridad'],
            reportado_por=request.user,
        )
        messages.success(request, 'Problema reportado.')
    else:
        messages.error(request, 'Revisá los datos del reporte.')
    return redirect('mantenimiento_lista')


@require_http_methods(['POST'])
@permission_required('reservas.change_incidencia', raise_exception=True)
def mantenimiento_asignar(request, incidencia_id):
    incidencia = get_object_or_404(Incidencia, pk=incidencia_id)
    try:
        services.asignar_responsable_incidencia(incidencia, responsable=request.user)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Incidencia #{incidencia.id} asignada a vos.')
    return redirect('mantenimiento_lista')


@require_http_methods(['POST'])
@permission_required('reservas.change_incidencia', raise_exception=True)
def mantenimiento_iniciar(request, incidencia_id):
    incidencia = get_object_or_404(Incidencia, pk=incidencia_id)
    try:
        services.iniciar_reparacion(incidencia)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Reparación de la incidencia #{incidencia.id} iniciada.')
    return redirect('mantenimiento_lista')


@require_http_methods(['POST'])
@permission_required('reservas.change_incidencia', raise_exception=True)
def mantenimiento_resolver(request, incidencia_id):
    incidencia = get_object_or_404(Incidencia, pk=incidencia_id)
    try:
        services.resolver_incidencia(incidencia, solucion=request.POST.get('solucion', ''))
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Incidencia #{incidencia.id} marcada como resuelta.')
    return redirect('mantenimiento_lista')


@require_http_methods(['POST'])
@permission_required('reservas.change_incidencia', raise_exception=True)
def mantenimiento_cerrar(request, incidencia_id):
    incidencia = get_object_or_404(Incidencia, pk=incidencia_id)
    try:
        services.cerrar_incidencia(incidencia)
    except ValidationError as exc:
        messages.error(request, _mensaje_validacion(exc))
    else:
        messages.success(request, f'Incidencia #{incidencia.id} cerrada.')
    return redirect('mantenimiento_lista')


@permission_required('reservas.view_factura', raise_exception=True)
def facturas_lista(request):
    """Listado de todas las facturas emitidas (sección 20) — hasta ahora
    solo se llegaba a una factura desde su reserva; esto da una vista
    general con búsqueda y filtro por estado de pago."""
    facturas_qs = Factura.objects.select_related(
        'reserva__cliente', 'reserva__habitacion',
    ).order_by('-fecha', '-id')

    q = request.GET.get('q', '').strip()
    if q:
        facturas_qs = facturas_qs.filter(
            Q(reserva__cliente__nombre__icontains=q) | Q(reserva__cliente__apellido__icontains=q)
            | Q(reserva__cliente__cedula__icontains=q) | Q(id__icontains=q)
        )

    total_facturado = facturas_qs.aggregate(t=Sum('total'))['t'] or 0

    estado = request.GET.get('estado', '')
    facturas = list(facturas_qs)
    if estado == 'pendiente':
        facturas = [f for f in facturas if f.reserva.saldo_pendiente() > 0]
    elif estado == 'pagada':
        facturas = [f for f in facturas if f.reserva.saldo_pendiente() <= 0]

    paginator = Paginator(facturas, 25)
    pagina = paginator.get_page(request.GET.get('page'))

    return render(request, 'reservas/facturas_lista.html', {
        'seccion': 'facturas', 'pagina': pagina, 'q': q, 'estado': estado,
        'total_facturado': total_facturado,
    })


@permission_required('reservas.view_factura', raise_exception=True)
def factura_detalle(request, factura_id):
    """Pantalla de la factura en el navegador (sección 20): mismo contenido
    que el PDF pero navegable/imprimible desde acá; el PDF real (para
    descargar o adjuntar al correo) sigue existiendo aparte en factura_pdf."""
    factura = get_object_or_404(
        Factura.objects.select_related('reserva__cliente', 'reserva__habitacion__tipo'),
        pk=factura_id,
    )
    reserva = factura.reserva
    return render(request, 'reservas/factura_detalle.html', {
        'seccion': 'reservas',
        'factura': factura,
        'reserva': reserva,
        'consumos': reserva.consumos.select_related('servicio'),
        'pagos': reserva.pagos.all(),
        'config_hotel': ConfiguracionHotel.actual(),
    })


@require_http_methods(['POST'])
@permission_required('reservas.view_factura', raise_exception=True)
def factura_reenviar_correo(request, factura_id):
    """Reenvía la factura al correo del huésped a pedido (ej. si el envío
    automático del check-out falló o cambió el email del cliente)."""
    factura = get_object_or_404(Factura.objects.select_related('reserva__cliente'), pk=factura_id)
    enviado = comprobantes.enviar_factura_por_correo(factura)
    if enviado and comprobantes.correo_es_real():
        auditoria.registrar_desde_request(
            request, 'reenviar_factura', 'facturacion', objeto=factura,
            descripcion=f'Factura #{factura.id} reenviada a {factura.reserva.cliente.correo}.',
        )
        messages.success(request, f'Factura reenviada a {factura.reserva.cliente.correo}.')
    elif enviado:
        messages.success(
            request,
            'No hay un servidor de correo real configurado — el reenvío quedó '
            'simulado en el log del servidor (ver .env.example).',
        )
    else:
        messages.error(request, 'No se pudo reenviar la factura por correo.')
    return redirect('factura_detalle', factura_id=factura_id)


@permission_required('reservas.view_factura', raise_exception=True)
def factura_pdf(request, factura_id):
    """PDF de la factura (sección 20): datos del huésped, habitación,
    noches, servicios consumidos, pagos y saldo."""
    factura = get_object_or_404(
        Factura.objects.select_related('reserva__cliente', 'reserva__habitacion__tipo'),
        pk=factura_id,
    )
    contenido = comprobantes.pdf_factura_bytes(factura)
    return HttpResponse(contenido, content_type='application/pdf', headers={
        'Content-Disposition': f'inline; filename="factura_{factura.id}.pdf"',
    })


@permission_required('reservas.view_pago', raise_exception=True)
def comprobante_pago_pdf(request, pago_id):
    """PDF de UN pago puntual (sección 20/28) — existe desde el momento en
    que se registra el pago, no recién al check-out."""
    pago = get_object_or_404(
        Pago.objects.select_related('reserva__cliente', 'reserva__habitacion'), pk=pago_id,
    )
    contenido = comprobantes.pdf_comprobante_pago_bytes(pago)
    return HttpResponse(contenido, content_type='application/pdf', headers={
        'Content-Disposition': f'inline; filename="comprobante_pago_{pago.id}.pdf"',
    })


# Reportes (sección 26). Los de tabla ofrecen exportar a CSV — se abre nativo
# en Excel/Sheets sin agregar una dependencia nueva solo para generar .xlsx;
# el de ocupación exporta a PDF porque es un resumen de una sola tabla, no
# una lista larga.

def _rango_de_fechas(request, dias_por_defecto=30):
    hoy = timezone.localdate()
    desde = parse_date(request.GET.get('desde', '')) or (hoy - timedelta(days=dias_por_defecto))
    hasta = parse_date(request.GET.get('hasta', '')) or hoy
    return desde, hasta


def _csv_response(filename, encabezados, filas):
    respuesta = HttpResponse(content_type='text/csv')
    respuesta['Content-Disposition'] = f'attachment; filename="{filename}"'
    escritor = csv.writer(respuesta)
    escritor.writerow(encabezados)
    escritor.writerows(filas)
    return respuesta


@login_required
def reportes_lista(request):
    """Hub de reportes: cada tarjeta solo aparece si el usuario tiene
    permiso para ver esos datos."""
    return render(request, 'reservas/reportes_lista.html', {'seccion': 'reportes'})


@permission_required('reservas.view_habitacion', raise_exception=True)
def reporte_ocupacion(request):
    """Sección 26: disponibles/ocupadas/reservadas/mantenimiento y % de
    ocupación. Es una foto de AHORA (Habitacion.estado no es histórico)."""
    habitaciones = Habitacion.objects.all()
    total = habitaciones.count()
    por_estado = [
        (etiqueta, habitaciones.filter(estado=valor).count())
        for valor, etiqueta in ESTADOS_HABITACION
    ]
    ocupadas = habitaciones.filter(estado='Ocupada').count()
    porcentaje = round(ocupadas / total * 100, 1) if total else 0

    if request.GET.get('formato') == 'pdf':
        return _pdf_reporte_ocupacion(total, por_estado, porcentaje)

    return render(request, 'reservas/reporte_ocupacion.html', {
        'seccion': 'reportes', 'total': total, 'por_estado': por_estado, 'porcentaje': porcentaje,
    })


def _pdf_reporte_ocupacion(total, por_estado, porcentaje):
    from io import BytesIO
    from reportlab.pdfgen import canvas as pdf_canvas

    buffer = BytesIO()
    c = pdf_canvas.Canvas(buffer)
    y = 800

    def linea(texto, salto=22, negrita=False, tamano=12):
        nonlocal y
        c.setFont('Helvetica-Bold' if negrita else 'Helvetica', tamano)
        c.drawString(100, y, texto)
        y -= salto

    linea('Reporte de ocupación', tamano=16, negrita=True)
    linea(f'Generado: {timezone.localdate()}')
    linea('')
    linea(f'Habitaciones totales: {total}', negrita=True)
    for etiqueta, cantidad in por_estado:
        linea(f'  {etiqueta}: {cantidad}')
    linea('')
    linea(f'Porcentaje de ocupación: {porcentaje}%', negrita=True)
    c.showPage()
    c.save()

    return HttpResponse(buffer.getvalue(), content_type='application/pdf', headers={
        'Content-Disposition': 'inline; filename="reporte_ocupacion.pdf"',
    })


@permission_required('reservas.view_factura', raise_exception=True)
def reporte_ingresos(request):
    """Sección 26: ingresos por día en un rango — de ahí se arma el
    diario/semanal/mensual/anual eligiendo el rango de fechas."""
    desde, hasta = _rango_de_fechas(request)
    facturas = Factura.objects.filter(fecha__gte=desde, fecha__lte=hasta)
    por_dia = facturas.values('fecha').annotate(total=Sum('total')).order_by('fecha')
    total_periodo = facturas.aggregate(t=Sum('total'))['t'] or 0

    if request.GET.get('formato') == 'csv':
        filas = [(fila['fecha'], fila['total']) for fila in por_dia]
        return _csv_response('ingresos.csv', ['Fecha', 'Total'], filas)

    return render(request, 'reservas/reporte_ingresos.html', {
        'seccion': 'reportes', 'desde': desde, 'hasta': hasta,
        'por_dia': por_dia, 'total_periodo': total_periodo,
    })


@permission_required('reservas.view_reserva', raise_exception=True)
def reporte_reservas(request):
    """Sección 26: confirmadas / canceladas / completadas / no-show, por
    fecha de ingreso dentro del rango."""
    hoy = timezone.localdate()
    desde, hasta = _rango_de_fechas(request)
    periodo = Reserva.objects.filter(fecha_ingreso__gte=desde, fecha_ingreso__lte=hasta)

    datos = {
        'total': periodo.count(),
        'canceladas': periodo.filter(cancelada_en__isnull=False).count(),
        'completadas': periodo.filter(check_out_at__isnull=False).count(),
        'no_show': periodo.filter(
            cancelada_en__isnull=True, check_in_at__isnull=True, fecha_salida__lt=hoy,
        ).count(),
        'confirmadas': periodo.filter(
            cancelada_en__isnull=True, check_in_at__isnull=True, fecha_salida__gte=hoy,
        ).count(),
    }

    return render(request, 'reservas/reporte_reservas.html', {
        'seccion': 'reportes', 'desde': desde, 'hasta': hasta, **datos,
    })


@permission_required('reservas.view_cliente', raise_exception=True)
def reporte_huespedes(request):
    """Sección 26: huéspedes con más reservas y más gasto en el rango
    (por fecha de ingreso)."""
    desde, hasta = _rango_de_fechas(request, dias_por_defecto=365)
    clientes = Cliente.objects.filter(
        reserva__fecha_ingreso__gte=desde, reserva__fecha_ingreso__lte=hasta,
    ).distinct()

    filas = []
    for cliente in clientes:
        reservas_cliente = cliente.reserva_set.filter(fecha_ingreso__gte=desde, fecha_ingreso__lte=hasta)
        gastado = sum(r.total_con_iva() for r in reservas_cliente if not r.esta_cancelada())
        filas.append({
            'cliente': cliente,
            'num_reservas': reservas_cliente.count(),
            'canceladas': reservas_cliente.filter(cancelada_en__isnull=False).count(),
            'gastado': gastado,
        })
    filas.sort(key=lambda f: f['gastado'], reverse=True)

    if request.GET.get('formato') == 'csv':
        encabezados = ['Cédula', 'Nombre', 'Reservas', 'Canceladas', 'Gastado']
        datos = [(f['cliente'].cedula, f['cliente'].nombre_completo(), f['num_reservas'], f['canceladas'], f['gastado']) for f in filas]
        return _csv_response('huespedes.csv', encabezados, datos)

    return render(request, 'reservas/reporte_huespedes.html', {
        'seccion': 'reportes', 'desde': desde, 'hasta': hasta, 'filas': filas[:50],
    })


@permission_required('reservas.view_consumo', raise_exception=True)
def reporte_servicios(request):
    """Sección 26: consumo por servicio en el rango (por fecha del consumo)."""
    desde, hasta = _rango_de_fechas(request)
    consumos = Consumo.objects.filter(fecha__date__gte=desde, fecha__date__lte=hasta)
    por_servicio = (
        consumos.values('servicio__nombre', 'servicio__categoria')
        .annotate(cantidad_total=Sum('cantidad'), total=Sum('subtotal'))
        .order_by('-total')
    )
    total_periodo = consumos.aggregate(t=Sum('subtotal'))['t'] or 0

    if request.GET.get('formato') == 'csv':
        filas = [(f['servicio__categoria'], f['servicio__nombre'], f['cantidad_total'], f['total']) for f in por_servicio]
        return _csv_response('servicios.csv', ['Categoría', 'Servicio', 'Cantidad', 'Total'], filas)

    return render(request, 'reservas/reporte_servicios.html', {
        'seccion': 'reportes', 'desde': desde, 'hasta': hasta,
        'por_servicio': por_servicio, 'total_periodo': total_periodo,
    })


@permission_required('reservas.view_pago', raise_exception=True)
def reporte_pagos(request):
    """Sección 26: pagos agrupados por método en el rango."""
    desde, hasta = _rango_de_fechas(request)
    pagos = Pago.objects.filter(fecha__date__gte=desde, fecha__date__lte=hasta)
    por_metodo = pagos.values('metodo').annotate(total=Sum('monto'), cantidad=Count('id')).order_by('-total')
    total_periodo = pagos.aggregate(t=Sum('monto'))['t'] or 0

    if request.GET.get('formato') == 'csv':
        filas = [(f['metodo'], f['cantidad'], f['total']) for f in por_metodo]
        return _csv_response('pagos.csv', ['Método', 'Cantidad', 'Total'], filas)

    return render(request, 'reservas/reporte_pagos.html', {
        'seccion': 'reportes', 'desde': desde, 'hasta': hasta,
        'por_metodo': por_metodo, 'total_periodo': total_periodo,
    })


# API JSON simple, pensada para que la consuma la interfaz de escritorio (Tkinter)
# vía `requests`/`urllib`. No usa Django REST Framework a propósito: mientras el
# proyecto sea de este tamaño, vistas + JsonResponse alcanzan y evitan una
# dependencia extra.
#
# Autenticación: sesión de Django (la misma que usa /admin/). Todos los
# endpoints exigen login; las acciones de escritura además exigen el permiso
# concreto del modelo (ver reservas/management/commands/setup_roles.py para
# cómo se reparten esos permisos entre roles). Al requerir sesión real,
# CSRF vuelve a aplicar en los POST — ya no están @csrf_exempt.


def _body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body)
    except json.JSONDecodeError:
        raise ValueError('El cuerpo de la petición no es JSON válido.')


def cliente_a_dict(c: Cliente) -> dict:
    return {
        'cedula': c.cedula,
        'nombre': c.nombre,
        'apellido': c.apellido,
        'telefono': c.telefono,
        'correo': c.correo,
        'direccion': c.direccion,
    }


def habitacion_a_dict(h: Habitacion) -> dict:
    return {
        'codigo': h.codigo,
        'numero': h.numero,
        'tipo': h.tipo.nombre,
        'precio': str(h.precio),
        'estado': h.estado,
    }


def reserva_a_dict(r: Reserva) -> dict:
    return {
        'id': r.id,
        'cliente': r.cliente_id,
        'habitacion': r.habitacion_id,
        'fecha_ingreso': r.fecha_ingreso.isoformat(),
        'fecha_salida': r.fecha_salida.isoformat(),
        'duracion_noches': r.duracion(),
        'costo': str(r.costo()),
    }


def factura_a_dict(f: Factura) -> dict:
    return {
        'id': f.id,
        'reserva': f.reserva_id,
        'fecha': f.fecha.isoformat(),
        'total': str(f.total),
    }


@require_http_methods(['GET', 'POST'])
@login_required_api
def clientes(request):
    if request.method == 'GET':
        if not request.user.has_perm('reservas.view_cliente'):
            return JsonResponse({'error': 'No tenés permiso para ver clientes.'}, status=403)
        return JsonResponse(
            {'resultados': [cliente_a_dict(c) for c in Cliente.objects.all()]}
        )

    if not request.user.has_perm('reservas.add_cliente'):
        return JsonResponse({'error': 'No tenés permiso para crear clientes.'}, status=403)

    try:
        datos = _body(request)
        cliente = Cliente(
            cedula=datos.get('cedula', ''),
            nombre=datos.get('nombre', ''),
            apellido=datos.get('apellido', ''),
            telefono=datos.get('telefono', ''),
            correo=datos.get('correo', ''),
            direccion=datos.get('direccion') or None,
        )
        cliente.full_clean()
        cliente.save()
    except (ValueError, ValidationError) as exc:
        return JsonResponse({'error': _errores(exc)}, status=400)

    return JsonResponse(cliente_a_dict(cliente), status=201)


@require_http_methods(['GET'])
@permission_required_api('reservas.view_habitacion')
def habitaciones(request):
    queryset = Habitacion.objects.select_related('tipo')
    disponible = request.GET.get('disponible')
    if disponible is not None:
        queryset = queryset.filter(estado='Disponible')
    return JsonResponse(
        {'resultados': [habitacion_a_dict(h) for h in queryset]}
    )


@require_http_methods(['GET', 'POST'])
@login_required_api
def reservas(request):
    if request.method == 'GET':
        if not request.user.has_perm('reservas.view_reserva'):
            return JsonResponse({'error': 'No tenés permiso para ver reservas.'}, status=403)
        return JsonResponse(
            {'resultados': [reserva_a_dict(r) for r in Reserva.objects.select_related('cliente', 'habitacion')]}
        )

    if not request.user.has_perm('reservas.add_reserva'):
        return JsonResponse({'error': 'No tenés permiso para crear reservas.'}, status=403)

    try:
        datos = _body(request)
        cliente = get_object_or_404(Cliente, pk=datos.get('cliente'))
        habitacion = get_object_or_404(Habitacion, pk=datos.get('habitacion'))
        # Vía el servicio compartido: valida (fechas, solapamiento, bajo
        # bloqueo de fila) y deja Habitacion.estado consistente, igual que
        # la web y que interfaz.py.
        reserva = services.crear_reserva(
            cliente=cliente,
            habitacion=habitacion,
            fecha_ingreso=datos.get('fecha_ingreso'),
            fecha_salida=datos.get('fecha_salida'),
        )
    except (ValueError, ValidationError) as exc:
        return JsonResponse({'error': _errores(exc)}, status=400)

    return JsonResponse(reserva_a_dict(reserva), status=201)


@require_http_methods(['POST'])
@permission_required_api('reservas.add_factura')
def crear_factura(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id)
    if hasattr(reserva, 'factura'):
        return JsonResponse(
            {'error': 'Esta reserva ya tiene una factura generada.'}, status=409
        )
    factura = Factura(reserva=reserva)
    factura.save()
    return JsonResponse(factura_a_dict(factura), status=201)


def _errores(exc):
    if isinstance(exc, ValidationError):
        return exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
    return str(exc)


def _mensaje_validacion(exc: ValidationError) -> str:
    """Texto legible de un ValidationError armado con un solo mensaje de
    texto (no un dict por campo) — para mostrar en messages.error()."""
    if hasattr(exc, 'message'):
        return str(exc.message)
    return '; '.join(exc.messages)


# Configuración del hotel (sección 29).

@permission_required('reservas.view_configuracionhotel', raise_exception=True)
def configuracion_ver(request):
    config = ConfiguracionHotel.actual()
    puede_editar = request.user.has_perm('reservas.change_configuracionhotel')

    if request.method == 'POST':
        if not puede_editar:
            raise PermissionDenied
        form = ConfiguracionHotelForm(request.POST, request.FILES, instance=config)
        if form.is_valid():
            form.save()
            auditoria.registrar_desde_request(
                request, 'modificar', 'configuracion', objeto=config,
                descripcion='Configuración del hotel actualizada.',
            )
            messages.success(request, 'Configuración actualizada.')
            return redirect('configuracion')
        messages.error(request, 'Revisá los datos de configuración.')
    else:
        form = ConfiguracionHotelForm(instance=config)

    return render(request, 'reservas/configuracion.html', {
        'seccion': 'configuracion', 'form': form, 'config': config, 'puede_editar': puede_editar,
    })


# Gestión de usuarios (sección 29) — reservada a superusuarios.

@superuser_required
def usuarios_lista(request):
    usuarios = User.objects.all().prefetch_related('groups').order_by('username')
    return render(request, 'reservas/usuarios_lista.html', {'seccion': 'usuarios', 'usuarios': usuarios})


@superuser_required
def usuario_nuevo(request):
    if request.method == 'POST':
        form = UsuarioCrearForm(request.POST)
        if form.is_valid():
            usuario = User.objects.create_user(
                username=form.cleaned_data['username'],
                password=form.cleaned_data['password1'],
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                email=form.cleaned_data['email'],
                is_staff=True,
            )
            rol = form.cleaned_data['rol']
            usuario.groups.add(rol)
            auditoria.registrar_desde_request(
                request, 'crear', 'usuarios', objeto=usuario,
                descripcion=f'Usuario "{usuario.username}" creado con rol {rol.name}.',
            )
            messages.success(request, f'Usuario "{usuario.username}" creado.')
            return redirect('usuarios_lista')
        messages.error(request, 'Revisá los datos del nuevo usuario.')
    else:
        form = UsuarioCrearForm()
    return render(request, 'reservas/usuario_form.html', {'seccion': 'usuarios', 'form': form, 'modo': 'crear'})


@superuser_required
def usuario_editar(request, usuario_id):
    usuario_editado = get_object_or_404(User, pk=usuario_id)

    if request.method == 'POST':
        form = UsuarioEditarForm(request.POST)
        if form.is_valid():
            if usuario_editado == request.user and not form.cleaned_data['activo']:
                messages.error(request, 'No podés desactivar tu propia cuenta.')
                return redirect('usuario_editar', usuario_id=usuario_editado.id)

            usuario_editado.first_name = form.cleaned_data['first_name']
            usuario_editado.last_name = form.cleaned_data['last_name']
            usuario_editado.email = form.cleaned_data['email']
            usuario_editado.is_active = form.cleaned_data['activo']
            usuario_editado.save()

            if not usuario_editado.is_superuser:
                usuario_editado.groups.clear()
                if form.cleaned_data['rol']:
                    usuario_editado.groups.add(form.cleaned_data['rol'])

            auditoria.registrar_desde_request(
                request, 'modificar', 'usuarios', objeto=usuario_editado,
                descripcion=f'Usuario "{usuario_editado.username}" editado.',
            )
            messages.success(request, f'Usuario "{usuario_editado.username}" actualizado.')
            return redirect('usuarios_lista')
        messages.error(request, 'Revisá los datos del usuario.')
    else:
        form = UsuarioEditarForm(initial={
            'first_name': usuario_editado.first_name,
            'last_name': usuario_editado.last_name,
            'email': usuario_editado.email,
            'rol': usuario_editado.groups.first(),
            'activo': usuario_editado.is_active,
        })

    return render(request, 'reservas/usuario_form.html', {
        'seccion': 'usuarios', 'form': form, 'modo': 'editar', 'usuario_editado': usuario_editado,
        'password_form': CambiarPasswordForm(),
    })


@require_http_methods(['POST'])
@superuser_required
def usuario_cambiar_password(request, usuario_id):
    usuario_editado = get_object_or_404(User, pk=usuario_id)
    form = CambiarPasswordForm(request.POST)
    if form.is_valid():
        usuario_editado.set_password(form.cleaned_data['password1'])
        usuario_editado.save()
        auditoria.registrar_desde_request(
            request, 'modificar', 'usuarios', objeto=usuario_editado,
            descripcion=f'Contraseña de "{usuario_editado.username}" restablecida por un administrador.',
        )
        messages.success(request, f'Contraseña de "{usuario_editado.username}" actualizada.')
    else:
        messages.error(request, 'Revisá la nueva contraseña: ' + '; '.join(
            f'{error}' for errores in form.errors.values() for error in errores
        ))
    return redirect('usuario_editar', usuario_id=usuario_id)
