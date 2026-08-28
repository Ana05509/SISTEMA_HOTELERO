from django.contrib import admin

from . import auditoria
from .models import (
    AuditLog, Cliente, Consumo, Factura, Habitacion, Incidencia, Pago, Reserva, Servicio, TareaLimpieza,
    TipoHabitacion,
)

# El admin sigue siendo el "puente" para gestionar catálogos (Servicios,
# Tipos de habitación) que todavía no tienen pantalla propia — al menos que
# diga "Sistema Hotelero" en vez del genérico "Django administration".
admin.site.site_header = 'Sistema Hotelero — Administración'
admin.site.site_title = 'Sistema Hotelero'
admin.site.index_title = 'Panel de administración'


class AuditoriaAdminMixin:
    """Registra en AuditLog cualquier alta/edición/baja hecha desde el admin
    (sección 25: "modificación de habitación", "cambios administrativos").
    Se suma a la auditoría explícita de views.py para las acciones que
    pasan por ahí (reservas, check-in/out, pagos)."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        accion = 'modificar' if change else 'crear'
        auditoria.registrar_desde_request(
            request, accion, obj._meta.model_name, objeto=obj,
            descripcion=f'{obj._meta.verbose_name} {"modificado" if change else "creado"} vía admin.',
        )

    def delete_model(self, request, obj):
        auditoria.registrar_desde_request(
            request, 'eliminar', obj._meta.model_name, objeto=obj,
            descripcion=f'{obj._meta.verbose_name} eliminado vía admin.',
        )
        super().delete_model(request, obj)


@admin.register(Cliente)
class ClienteAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('cedula', 'nombre', 'apellido', 'telefono', 'correo', 'tipo_documento')
    search_fields = ('cedula', 'nombre', 'apellido', 'correo')
    list_filter = ('tipo_documento', 'nacionalidad')


@admin.register(TipoHabitacion)
class TipoHabitacionAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('nombre', 'capacidad', 'camas', 'precio_base')
    search_fields = ('nombre',)


@admin.register(Habitacion)
class HabitacionAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('numero', 'tipo', 'piso', 'precio', 'estado')
    list_filter = ('estado', 'tipo', 'piso')
    search_fields = ('numero', 'codigo')


@admin.register(Reserva)
class ReservaAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'cliente', 'habitacion', 'fecha_ingreso', 'fecha_salida')
    list_filter = ('habitacion',)
    search_fields = ('cliente__cedula', 'cliente__nombre', 'cliente__apellido', 'habitacion__numero')
    autocomplete_fields = ('cliente', 'habitacion')


@admin.register(Factura)
class FacturaAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'reserva', 'fecha', 'subtotal', 'iva_monto', 'total')
    search_fields = ('reserva__cliente__cedula', 'reserva__cliente__apellido')


@admin.register(Servicio)
class ServicioAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'precio', 'activo')
    list_filter = ('categoria', 'activo')
    search_fields = ('nombre',)


@admin.register(Consumo)
class ConsumoAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'reserva', 'servicio', 'cantidad', 'subtotal', 'fecha', 'usuario')
    list_filter = ('servicio__categoria',)
    search_fields = ('reserva__cliente__cedula', 'reserva__cliente__apellido')
    autocomplete_fields = ('reserva', 'servicio')


@admin.register(Pago)
class PagoAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'reserva', 'monto', 'metodo', 'referencia', 'fecha', 'usuario')
    list_filter = ('metodo',)
    search_fields = ('reserva__cliente__cedula', 'reserva__cliente__apellido', 'referencia')
    autocomplete_fields = ('reserva',)


@admin.register(TareaLimpieza)
class TareaLimpiezaAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'habitacion', 'estado', 'responsable', 'creada_en', 'finalizada_en')
    list_filter = ('estado',)
    search_fields = ('habitacion__numero',)
    autocomplete_fields = ('habitacion',)


@admin.register(Incidencia)
class IncidenciaAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'habitacion', 'prioridad', 'estado', 'responsable', 'creada_en')
    list_filter = ('estado', 'prioridad')
    search_fields = ('habitacion__numero', 'descripcion')
    autocomplete_fields = ('habitacion',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """De solo lectura para TODOS, incluido un superusuario — un log que se
    puede editar o borrar desde la misma pantalla no sirve como auditoría
    (sección 25: "los usuarios normales no deben poder modificar
    auditoría"; acá directamente nadie puede, ni por error)."""
    list_display = ('fecha', 'usuario', 'accion', 'modulo', 'objeto_repr', 'direccion_ip')
    list_filter = ('modulo', 'accion')
    search_fields = ('usuario__username', 'objeto_repr', 'descripcion')
    date_hierarchy = 'fecha'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
