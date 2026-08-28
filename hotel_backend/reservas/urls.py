from django.urls import path

from . import views

app_name = 'reservas'

urlpatterns = [
    path('clientes/', views.clientes, name='clientes'),
    path('habitaciones/', views.habitaciones, name='habitaciones'),
    path('reservas/', views.reservas, name='reservas'),
    path('reservas/<int:reserva_id>/factura/', views.crear_factura, name='crear_factura'),
]
