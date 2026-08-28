"""Rutas del frontend web (HTML), separadas de la API JSON (`urls.py`)."""
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('habitaciones/', views.habitaciones_grid, name='habitaciones_grid'),
    # Antes que <str:codigo>/ a propósito: si no, "precios" se interpretaría
    # como un código de habitación y nunca llegaría a esta vista.
    path('habitaciones/precios/', views.precios_habitaciones, name='precios_habitaciones'),
    path('habitaciones/<str:codigo>/', views.habitacion_detalle, name='habitacion_detalle'),
    path('habitaciones/<str:codigo>/finalizar-limpieza/', views.habitacion_finalizar_limpieza, name='habitacion_finalizar_limpieza'),

    path('huespedes/', views.huespedes_lista, name='huespedes_lista'),
    path('huespedes/<str:cedula>/', views.huesped_detalle, name='huesped_detalle'),

    path('reservas/', views.reservas_lista, name='reservas_lista'),
    path('reservas/nueva/', views.reserva_nueva, name='reserva_nueva'),
    path('reservas/nueva/crear/', views.reserva_crear, name='reserva_crear'),
    path('reservas/<int:reserva_id>/cancelar/', views.reserva_cancelar, name='reserva_cancelar'),
    path('reservas/<int:reserva_id>/', views.reserva_detalle, name='reserva_detalle'),
    path('reservas/<int:reserva_id>/consumos/', views.consumo_agregar, name='consumo_agregar'),
    path('reservas/<int:reserva_id>/pagos/', views.pago_agregar, name='pago_agregar'),

    path('pagos/', views.pagos_lista, name='pagos_lista'),
    path('auditoria/', views.auditoria_lista, name='auditoria_lista'),

    path('calendario/', views.calendario, name='calendario'),

    path('checkin/', views.checkin_lista, name='checkin_lista'),
    path('checkin/<int:reserva_id>/', views.checkin_confirmar, name='checkin_confirmar'),
    path('checkout/', views.checkout_lista, name='checkout_lista'),
    path('checkout/<int:reserva_id>/', views.checkout_confirmar, name='checkout_confirmar'),

    path('facturas/', views.facturas_lista, name='facturas_lista'),
    path('facturas/<int:factura_id>/', views.factura_detalle, name='factura_detalle'),
    path('facturas/<int:factura_id>/pdf/', views.factura_pdf, name='factura_pdf'),
    path('facturas/<int:factura_id>/reenviar/', views.factura_reenviar_correo, name='factura_reenviar_correo'),
    path('pagos/<int:pago_id>/comprobante/', views.comprobante_pago_pdf, name='comprobante_pago_pdf'),

    path('limpieza/', views.limpieza_lista, name='limpieza_lista'),
    path('limpieza/<int:tarea_id>/iniciar/', views.limpieza_iniciar, name='limpieza_iniciar'),
    path('limpieza/<int:tarea_id>/completar/', views.limpieza_completar, name='limpieza_completar'),

    path('mantenimiento/', views.mantenimiento_lista, name='mantenimiento_lista'),
    path('mantenimiento/reportar/', views.mantenimiento_reportar, name='mantenimiento_reportar'),
    path('mantenimiento/<int:incidencia_id>/asignar/', views.mantenimiento_asignar, name='mantenimiento_asignar'),
    path('mantenimiento/<int:incidencia_id>/iniciar/', views.mantenimiento_iniciar, name='mantenimiento_iniciar'),
    path('mantenimiento/<int:incidencia_id>/resolver/', views.mantenimiento_resolver, name='mantenimiento_resolver'),
    path('mantenimiento/<int:incidencia_id>/cerrar/', views.mantenimiento_cerrar, name='mantenimiento_cerrar'),

    path('reportes/', views.reportes_lista, name='reportes_lista'),
    path('reportes/ocupacion/', views.reporte_ocupacion, name='reporte_ocupacion'),
    path('reportes/ingresos/', views.reporte_ingresos, name='reporte_ingresos'),
    path('reportes/reservas/', views.reporte_reservas, name='reporte_reservas'),
    path('reportes/huespedes/', views.reporte_huespedes, name='reporte_huespedes'),
    path('reportes/servicios/', views.reporte_servicios, name='reporte_servicios'),
    path('reportes/pagos/', views.reporte_pagos, name='reporte_pagos'),

    path('configuracion/', views.configuracion_ver, name='configuracion'),

    path('usuarios/', views.usuarios_lista, name='usuarios_lista'),
    path('usuarios/nuevo/', views.usuario_nuevo, name='usuario_nuevo'),
    path('usuarios/<int:usuario_id>/editar/', views.usuario_editar, name='usuario_editar'),
    path('usuarios/<int:usuario_id>/password/', views.usuario_cambiar_password, name='usuario_cambiar_password'),
]
