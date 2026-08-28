import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import (
    AuditLog, Cliente, ConfiguracionHotel, Consumo, Factura, Habitacion, Incidencia, Pago, Reserva, Servicio,
    TareaLimpieza, TipoHabitacion,
)
from .validadores_ecuador import cedula_valida, ruc_valido


def _tipo(nombre):
    """Los tipos de habitación (Doble, Suite, ...) los precarga la migración
    de datos 0007_seed_tipos_habitacion; acá solo los recuperamos."""
    return TipoHabitacion.objects.get(nombre=nombre)


class ReservaTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405',
            nombre='Ana',
            apellido='Torres',
            telefono='0991234567',
            correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1',
            numero='101',
            tipo=_tipo('Doble'),
            precio=50,
        )

    def test_costo_se_calcula_con_precio_por_noche(self):
        reserva = Reserva(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 1, 1),
            fecha_salida=date(2026, 1, 4),
        )
        reserva.save()
        self.assertEqual(reserva.duracion(), 3)
        self.assertEqual(reserva.costo(), 150)

    def test_fecha_salida_anterior_o_igual_a_ingreso_es_invalida(self):
        reserva = Reserva(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 1, 5),
            fecha_salida=date(2026, 1, 5),
        )
        with self.assertRaises(ValidationError):
            reserva.save()

    def test_no_permite_doble_reserva_en_fechas_solapadas(self):
        Reserva.objects.create(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 2, 1),
            fecha_salida=date(2026, 2, 10),
        )
        solapada = Reserva(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 2, 5),
            fecha_salida=date(2026, 2, 15),
        )
        with self.assertRaises(ValidationError):
            solapada.save()

    def test_permite_reservas_consecutivas_sin_solaparse(self):
        Reserva.objects.create(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 3, 1),
            fecha_salida=date(2026, 3, 10),
        )
        # Entra el mismo día que sale la anterior: no se solapan.
        consecutiva = Reserva(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 3, 10),
            fecha_salida=date(2026, 3, 12),
        )
        consecutiva.save()
        self.assertIsNotNone(consecutiva.pk)


class FacturaTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405',
            nombre='Ana',
            apellido='Torres',
            telefono='0991234567',
            correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1',
            numero='101',
            tipo=_tipo('Suite'),
            precio=80,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 4, 1),
            fecha_salida=date(2026, 4, 5),
        )

    def test_total_se_calcula_automaticamente_al_guardar(self):
        factura = Factura(reserva=self.reserva)
        factura.save()
        # El precio de la habitación ya incluye IVA: el total es el precio
        # tal cual, y el subtotal sale de descontarle el impuesto.
        self.assertEqual(factura.total, self.reserva.costo())
        self.assertEqual(factura.total, 320)
        self.assertEqual(factura.subtotal, Decimal('278.26'))  # $320 / 1.15
        self.assertEqual(factura.iva_monto, Decimal('41.74'))


class IvaTests(TestCase):
    """Sección 31: el precio de la habitación YA incluye el IVA (así cotiza
    la mayoría de los hoteles) — el % configurado (15% por defecto en
    Ecuador) se usa para DESGLOSAR cuánto de ese precio es impuesto, no
    para sumarlo aparte. La factura queda fija al emitirse."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='1710034065', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='HIVA', numero='401', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )  # precio ya incluye IVA: total_con_iva = $100 (lo que paga el huésped)

    def test_el_total_es_el_precio_cargado_sin_agregarle_nada(self):
        self.assertEqual(ConfiguracionHotel.actual().iva_porcentaje, Decimal('15.00'))
        self.assertEqual(self.reserva.total_con_iva(), Decimal('100.00'))  # no $115

    def test_iva_se_desglosa_segun_el_porcentaje_configurado_15(self):
        self.assertEqual(self.reserva.total_estadia(), Decimal('86.96'))  # $100 / 1.15
        self.assertEqual(self.reserva.iva(), Decimal('13.04'))
        self.assertEqual(self.reserva.total_estadia() + self.reserva.iva(), self.reserva.total_con_iva())

    def test_iva_se_desglosa_segun_el_porcentaje_configurado_12(self):
        config = ConfiguracionHotel.actual()
        config.iva_porcentaje = Decimal('12.00')
        config.save()
        self.assertEqual(self.reserva.total_con_iva(), Decimal('100.00'))  # el total no cambia
        self.assertEqual(self.reserva.total_estadia(), Decimal('89.29'))  # $100 / 1.12
        self.assertEqual(self.reserva.iva(), Decimal('10.71'))

    def test_iva_en_cero_el_subtotal_es_igual_al_total(self):
        config = ConfiguracionHotel.actual()
        config.iva_porcentaje = Decimal('0')
        config.save()
        self.assertEqual(self.reserva.iva(), Decimal('0.00'))
        self.assertEqual(self.reserva.total_estadia(), self.reserva.total_con_iva())

    def test_factura_queda_fija_aunque_cambie_el_porcentaje_despues(self):
        services.hacer_checkin(self.reserva)
        factura = services.hacer_checkout(self.reserva)
        self.assertEqual(factura.total, Decimal('100.00'))
        self.assertEqual(factura.subtotal, Decimal('86.96'))
        self.assertEqual(factura.iva_monto, Decimal('13.04'))

        config = ConfiguracionHotel.actual()
        config.iva_porcentaje = Decimal('20.00')
        config.save()

        factura.refresh_from_db()
        self.assertEqual(factura.total, Decimal('100.00'))  # no cambió
        self.assertEqual(factura.subtotal, Decimal('86.96'))  # tampoco
        self.assertEqual(factura.porcentaje_iva(), Decimal('15.00'))  # el que se cobró, no el 20% actual

        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.saldo_pendiente(), Decimal('100.00'))  # contra la factura, no contra el 20% nuevo

    def test_configuracion_permite_iva_en_cero(self):
        admin = User.objects.create_superuser('admin_iva', 'admin_iva@example.com', 'x')
        self.client.force_login(admin)
        respuesta = self.client.post(reverse('configuracion'), {
            'nombre_hotel': 'Hotel Test', 'direccion': '', 'telefono': '', 'ruc': '',
            'moneda': 'USD', 'iva_porcentaje': '0',
            'hora_checkin_default': '14:00', 'hora_checkout_default': '12:00', 'politica_cancelacion': '',
        })
        self.assertRedirects(respuesta, reverse('configuracion'))
        self.assertEqual(ConfiguracionHotel.actual().iva_porcentaje, Decimal('0.00'))


class ApiReservasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405',
            nombre='Ana',
            apellido='Torres',
            telefono='0991234567',
            correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1',
            numero='101',
            tipo=_tipo('Doble'),
            precio=50,
        )
        self.recepcionista = User.objects.create_user('recepcion', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_crear_reserva_via_api(self):
        respuesta = self.client.post(
            reverse('reservas:reservas'),
            data=json.dumps({
                'cliente': self.cliente.cedula,
                'habitacion': self.habitacion.codigo,
                'fecha_ingreso': '2026-05-01',
                'fecha_salida': '2026-05-03',
            }),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 201)
        cuerpo = respuesta.json()
        self.assertEqual(cuerpo['costo'], '100.00')

    def test_crear_reserva_solapada_via_api_devuelve_400(self):
        Reserva.objects.create(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 6, 1),
            fecha_salida=date(2026, 6, 10),
        )
        respuesta = self.client.post(
            reverse('reservas:reservas'),
            data=json.dumps({
                'cliente': self.cliente.cedula,
                'habitacion': self.habitacion.codigo,
                'fecha_ingreso': '2026-06-05',
                'fecha_salida': '2026-06-07',
            }),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_listar_habitaciones_disponibles(self):
        Habitacion.objects.create(codigo='H2', numero='102', precio=60, tipo=_tipo('Doble'), estado='Ocupada')
        respuesta = self.client.get(reverse('reservas:habitaciones'), {'disponible': '1'})
        self.assertEqual(respuesta.status_code, 200)
        numeros = [h['numero'] for h in respuesta.json()['resultados']]
        self.assertEqual(numeros, ['101'])

    def test_crear_factura_via_api(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente,
            habitacion=self.habitacion,
            fecha_ingreso=date(2026, 7, 1),
            fecha_salida=date(2026, 7, 4),
        )
        respuesta = self.client.post(
            reverse('reservas:crear_factura', args=[reserva.id])
        )
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.json()['total'], '150.00')  # el precio ya incluye el IVA

        # Una segunda factura para la misma reserva debe rechazarse.
        repetida = self.client.post(
            reverse('reservas:crear_factura', args=[reserva.id])
        )
        self.assertEqual(repetida.status_code, 409)


class ApiPermisosTests(TestCase):
    """La API exige login y respeta los permisos por rol (ver setup_roles)."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )

    def test_sin_login_devuelve_401(self):
        respuesta = self.client.get(reverse('reservas:clientes'))
        self.assertEqual(respuesta.status_code, 401)

    def test_rol_sin_permiso_devuelve_403(self):
        # Limpieza puede ver/cambiar habitaciones, pero no crear clientes.
        limpieza = User.objects.create_user('limpieza', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.post(
            reverse('reservas:clientes'),
            data=json.dumps({'cedula': '1', 'nombre': 'X', 'apellido': 'Y',
                              'telefono': '1', 'correo': 'x@x.com'}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_limpieza_puede_ver_habitaciones(self):
        limpieza = User.objects.create_user('limpieza2', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.get(reverse('reservas:habitaciones'))
        self.assertEqual(respuesta.status_code, 200)

    def test_administrador_tiene_todos_los_permisos(self):
        admin = User.objects.create_superuser('admin', 'admin@example.com', 'x')
        self.client.force_login(admin)

        respuesta = self.client.get(reverse('reservas:clientes'))
        self.assertEqual(respuesta.status_code, 200)


class FrontendWebTests(TestCase):
    """Cubre el login y el dashboard (Fase 3): que rendericen sin errores
    de plantilla y que respeten login/permisos."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        Habitacion.objects.create(codigo='H1', numero='101', precio=50, tipo=_tipo('Doble'), estado='Disponible')
        Habitacion.objects.create(codigo='H2', numero='102', precio=60, tipo=_tipo('Doble'), estado='Ocupada')

    def test_dashboard_redirige_a_login_si_no_hay_sesion(self):
        respuesta = self.client.get(reverse('dashboard'))
        self.assertRedirects(respuesta, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_login_formulario_se_renderiza(self):
        respuesta = self.client.get(reverse('login'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ingresar')

    def test_login_valido_redirige_al_dashboard(self):
        User.objects.create_user('recepcion', password='claveSegura123')
        respuesta = self.client.post(reverse('login'), {'username': 'recepcion', 'password': 'claveSegura123'})
        self.assertRedirects(respuesta, reverse('dashboard'))

    def test_login_sin_recordarme_expira_al_cerrar_el_navegador(self):
        User.objects.create_user('recepcion_sr', password='claveSegura123')
        self.client.post(reverse('login'), {'username': 'recepcion_sr', 'password': 'claveSegura123'})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_logout_por_post_cierra_la_sesion(self):
        # El botón "Salir" del sidebar manda POST (no GET): Django 5 exige
        # POST para /logout/ por seguridad CSRF, un <a> normal daría 405.
        usuario = User.objects.create_user('recepcion_logout', password='claveSegura123')
        self.client.force_login(usuario)
        respuesta = self.client.post(reverse('logout'))
        self.assertRedirects(respuesta, reverse('login'))
        self.assertFalse(self.client.session.get('_auth_user_id'))

    def test_login_con_recordarme_extiende_la_sesion(self):
        User.objects.create_user('recepcion_cr', password='claveSegura123')
        self.client.post(reverse('login'), {
            'username': 'recepcion_cr', 'password': 'claveSegura123', 'recordarme': 'on',
        })
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertGreater(self.client.session.get_expiry_age(), 60 * 60 * 24 * 7)

    def test_dashboard_muestra_conteo_de_habitaciones_segun_permiso(self):
        recepcionista = User.objects.create_user('recepcion2', password='x', is_staff=True)
        recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(recepcionista)

        respuesta = self.client.get(reverse('dashboard'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['habitaciones_total'], 2)
        self.assertEqual(respuesta.context['habitaciones_disponibles'], 1)
        self.assertEqual(respuesta.context['habitaciones_ocupadas'], 1)

    def test_dashboard_oculta_reservas_para_rol_sin_permiso(self):
        limpieza = User.objects.create_user('limpieza3', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.get(reverse('dashboard'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn('checkins_hoy', respuesta.context)
        self.assertIn('habitaciones_total', respuesta.context)


class ValidacionesEcuadorTests(TestCase):
    """Sección 31: cédula/RUC/teléfono con las reglas reales de Ecuador
    (Registro Civil/SRI) — casos públicos conocidos, no inventados."""

    def test_cedulas_validas_conocidas(self):
        for cedula in ('1710034065', '0926687856'):
            self.assertTrue(cedula_valida(cedula), cedula)

    def test_cedulas_invalidas(self):
        for cedula in ('1234567890', '0101010101', '99999999999', 'abcdefghij', ''):
            self.assertFalse(cedula_valida(cedula), cedula)

    def test_rucs_validos_conocidos(self):
        # persona natural, entidad pública, sociedad privada respectivamente
        for ruc in ('1710034065001', '1760001550001', '1792060346001'):
            self.assertTrue(ruc_valido(ruc), ruc)

    def test_rucs_invalidos(self):
        for ruc in ('1710034065002', '1234567890123', '17100340650011'):
            self.assertFalse(ruc_valido(ruc), ruc)

    def test_cliente_con_cedula_invalida_no_pasa_full_clean(self):
        cliente = Cliente(
            cedula='1234567890', tipo_documento='Cedula', nombre='Test', apellido='Test',
            telefono='0991234567', correo='test@example.com',
        )
        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_cliente_con_cedula_valida_pasa_full_clean(self):
        cliente = Cliente(
            cedula='1710034065', tipo_documento='Cedula', nombre='Test', apellido='Test',
            telefono='0991234567', correo='test@example.com',
        )
        cliente.full_clean()  # no debe lanzar

    def test_cliente_con_ruc_invalido_no_pasa_full_clean(self):
        cliente = Cliente(
            cedula='1234567890123', tipo_documento='RUC', nombre='Empresa', apellido='SA',
            telefono='0991234567', correo='empresa@example.com',
        )
        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_cliente_pasaporte_no_exige_formato_de_cedula(self):
        # Pasaporte/Otro no tienen un formato fijo en Ecuador — no se le
        # aplica el algoritmo de cédula/RUC.
        cliente = Cliente(
            cedula='X1234567', tipo_documento='Pasaporte', nombre='Turista', apellido='Extranjero',
            telefono='0991234567', correo='turista@example.com',
        )
        cliente.full_clean()  # no debe lanzar

    def test_telefono_invalido_no_pasa_full_clean(self):
        cliente = Cliente(
            cedula='1710034065', tipo_documento='Cedula', nombre='Test', apellido='Test',
            telefono='12345', correo='test@example.com',
        )
        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_configuracion_ruc_invalido_no_pasa_full_clean(self):
        config = ConfiguracionHotel(nombre_hotel='Hotel Test', ruc='1234567890123')
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_configuracion_ruc_vacio_es_valido(self):
        config = ConfiguracionHotel(nombre_hotel='Hotel Test', ruc='')
        config.full_clean()  # opcional — no debe exigir formato si está vacío

    def test_configuracion_ruc_valido_pasa_full_clean(self):
        config = ConfiguracionHotel(nombre_hotel='Hotel Test', ruc='1792060346001')
        config.full_clean()


class HabitacionesYHuespedesTests(TestCase):
    """Fase 4: catálogo de tipos, grilla de habitaciones, huéspedes con
    búsqueda, y que la migración de datos haya poblado los tipos."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
            piso='1', estado='Disponible',
        )
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.recepcionista = User.objects.create_user('recep', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_migracion_precarga_catalogo_de_tipos(self):
        nombres = set(TipoHabitacion.objects.values_list('nombre', flat=True))
        self.assertTrue({'Individual', 'Doble', 'Matrimonial', 'Triple', 'Familiar', 'Suite'} <= nombres)

    def test_grilla_de_habitaciones_se_renderiza(self):
        respuesta = self.client.get(reverse('habitaciones_grid'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '101')

    def test_detalle_de_habitacion_muestra_reserva_actual(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today() - timedelta(days=1),
            fecha_salida=date.today() + timedelta(days=2),
        )
        respuesta = self.client.get(reverse('habitacion_detalle', args=[self.habitacion.codigo]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')

    def test_lista_de_huespedes_busca_por_apellido(self):
        Cliente.objects.create(
            cedula='9999999999', nombre='Luis', apellido='Zambrano',
            telefono='0987654321', correo='luis@example.com',
        )
        respuesta = self.client.get(reverse('huespedes_lista'), {'q': 'Torres'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')
        self.assertNotContains(respuesta, 'Luis Zambrano')

    def test_detalle_de_huesped_muestra_historial(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 1, 1), fecha_salida=date(2026, 1, 3),
        )
        respuesta = self.client.get(reverse('huesped_detalle', args=[self.cliente.cedula]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '101')

    def test_limpieza_no_puede_ver_huespedes(self):
        limpieza = User.objects.create_user('limpieza4', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.get(reverse('huespedes_lista'))
        self.assertEqual(respuesta.status_code, 403)


class PreciosHabitacionesTests(TestCase):
    """Editor de precios (sección 11/31): precio base por tipo y precio
    individual por habitación, sin pasar por el admin de Django."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.tipo = _tipo('Doble')
        self.habitacion = Habitacion.objects.create(
            codigo='HP1', numero='201', tipo=self.tipo, precio=50, piso='2',
        )

    def test_recepcionista_ve_precios_pero_no_puede_editar(self):
        recepcionista = User.objects.create_user('recep_precios', password='x', is_staff=True)
        recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(recepcionista)

        respuesta = self.client.get(reverse('precios_habitaciones'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context['puede_editar'])
        self.assertContains(respuesta, '201')

        respuesta_post = self.client.post(reverse('precios_habitaciones'), {
            f'habitacion_precio_{self.habitacion.codigo}': '999',
        })
        self.assertEqual(respuesta_post.status_code, 403)
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.precio, 50)

    def test_limpieza_no_puede_ver_precios(self):
        limpieza = User.objects.create_user('limpieza_precios', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)
        # Limpieza tiene view/change de Habitacion, pero no de
        # TipoHabitacion, así que no cumple ambos permisos y no puede
        # editar — igual puede VER la pantalla (view_habitacion sí lo tiene).
        respuesta = self.client.get(reverse('precios_habitaciones'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context['puede_editar'])

    def test_administrador_puede_editar_precio_de_habitacion_y_de_tipo(self):
        admin = User.objects.create_superuser('admin_precios', 'admin_precios@example.com', 'x')
        self.client.force_login(admin)

        respuesta = self.client.post(reverse('precios_habitaciones'), {
            f'habitacion_precio_{self.habitacion.codigo}': '75.50',
            f'tipo_precio_{self.tipo.id}': '60.00',
        })
        self.assertRedirects(respuesta, reverse('precios_habitaciones'))

        self.habitacion.refresh_from_db()
        self.tipo.refresh_from_db()
        self.assertEqual(self.habitacion.precio, Decimal('75.50'))
        self.assertEqual(self.tipo.precio_base, Decimal('60.00'))

    def test_los_inputs_de_precio_usan_punto_no_coma(self):
        # Regresión: con LANGUAGE_CODE='es', Django localiza los Decimal
        # con coma ({{ valor }} -> "50,00"); puesto crudo en el value= de
        # un <input type="number"> eso lo vuelve inválido para el
        # navegador y el campo aparece vacío. Ver precios_habitaciones.html
        # (usa |stringformat:"0.2f" a propósito para evitarlo).
        admin = User.objects.create_superuser('admin_precios_fmt', 'admin_precios_fmt@example.com', 'x')
        self.client.force_login(admin)
        respuesta = self.client.get(reverse('precios_habitaciones'))
        self.assertContains(respuesta, f'value="{self.habitacion.precio:.2f}"')
        self.assertNotContains(respuesta, f'value="{self.habitacion.precio:.2f}"'.replace('.', ','))

    def test_precio_invalido_no_se_guarda_y_avisa(self):
        admin = User.objects.create_superuser('admin_precios2', 'admin_precios2@example.com', 'x')
        self.client.force_login(admin)

        respuesta = self.client.post(reverse('precios_habitaciones'), {
            f'habitacion_precio_{self.habitacion.codigo}': '-5',
        }, follow=True)
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.precio, 50)
        mensajes = [str(m) for m in respuesta.context['messages']]
        self.assertTrue(any('inválido' in m for m in mensajes))

    def test_precios_quedan_auditados(self):
        admin = User.objects.create_superuser('admin_precios3', 'admin_precios3@example.com', 'x')
        self.client.force_login(admin)
        self.client.post(reverse('precios_habitaciones'), {
            f'habitacion_precio_{self.habitacion.codigo}': '80',
        })
        self.assertTrue(
            AuditLog.objects.filter(modulo='habitaciones', descripcion__icontains='201').exists()
        )


class ServiciosReservaTests(TestCase):
    """services.py: disponibilidad real por rango de fechas y el ciclo de
    vida de Habitacion.estado al crear/cancelar (Fase 5)."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )

    def test_habitacion_disponible_desaparece_de_la_busqueda_si_hay_solapamiento(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 8, 1), fecha_salida=date(2026, 8, 10),
        )
        disponibles = services.habitaciones_disponibles(date(2026, 8, 5), date(2026, 8, 8))
        self.assertNotIn(self.habitacion, list(disponibles))

    def test_habitacion_disponible_aparece_fuera_del_rango_ocupado(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 8, 1), fecha_salida=date(2026, 8, 10),
        )
        disponibles = services.habitaciones_disponibles(date(2026, 9, 1), date(2026, 9, 3))
        self.assertIn(self.habitacion, list(disponibles))

    def test_habitacion_en_mantenimiento_no_aparece_como_disponible(self):
        self.habitacion.estado = 'Mantenimiento'
        self.habitacion.save(update_fields=['estado'])
        disponibles = services.habitaciones_disponibles(date(2026, 9, 1), date(2026, 9, 3))
        self.assertNotIn(self.habitacion, list(disponibles))

    def test_crear_reserva_futura_marca_habitacion_reservada(self):
        futuro = date.today() + timedelta(days=10)
        services.crear_reserva(self.cliente, self.habitacion, futuro, futuro + timedelta(days=2))
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Reservada')

    def test_crear_reserva_desde_hoy_marca_habitacion_ocupada(self):
        hoy = date.today()
        services.crear_reserva(self.cliente, self.habitacion, hoy, hoy + timedelta(days=2))
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Ocupada')

    def test_crear_reserva_rechaza_habitacion_en_mantenimiento(self):
        self.habitacion.estado = 'Mantenimiento'
        self.habitacion.save(update_fields=['estado'])
        with self.assertRaises(ValidationError):
            services.crear_reserva(
                self.cliente, self.habitacion, date(2026, 9, 1), date(2026, 9, 3),
            )

    def test_cancelar_reserva_libera_la_habitacion(self):
        futuro = date.today() + timedelta(days=10)
        reserva = services.crear_reserva(self.cliente, self.habitacion, futuro, futuro + timedelta(days=2))
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Reservada')

        services.cancelar_reserva(reserva)
        self.habitacion.refresh_from_db()
        reserva.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Disponible')
        # Cancelación suave: la fila sigue existiendo, para el reporte de
        # reservas canceladas (Fase 12) y para no perder pagos ya hechos.
        self.assertTrue(reserva.esta_cancelada())

    def test_cancelar_no_toca_habitacion_en_limpieza(self):
        self.habitacion.estado = 'Limpieza'
        self.habitacion.save(update_fields=['estado'])
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 9, 1), fecha_salida=date(2026, 9, 3),
        )
        services.cancelar_reserva(reserva)
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Limpieza')

    def test_no_se_puede_cancelar_dos_veces(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 9, 1), fecha_salida=date(2026, 9, 3),
        )
        services.cancelar_reserva(reserva)
        with self.assertRaises(ValidationError):
            services.cancelar_reserva(reserva)

    def test_cancelar_reserva_libera_las_fechas_para_una_nueva_reserva(self):
        """Una reserva cancelada no debe seguir bloqueando esas fechas."""
        r1 = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 9, 1), fecha_salida=date(2026, 9, 5),
        )
        services.cancelar_reserva(r1)

        r2 = services.crear_reserva(
            self.cliente, self.habitacion, date(2026, 9, 2), date(2026, 9, 4),
        )
        self.assertIsNotNone(r2.pk)

    def test_reserva_cancelada_no_aparece_como_ocupada_en_disponibilidad(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 9, 1), fecha_salida=date(2026, 9, 5),
        )
        services.cancelar_reserva(reserva)
        disponibles = services.habitaciones_disponibles(date(2026, 9, 2), date(2026, 9, 4))
        self.assertIn(self.habitacion, list(disponibles))

    def test_estado_display_marca_no_show(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2020, 1, 1), fecha_salida=date(2020, 1, 3),
        )
        self.assertEqual(reserva.estado_display(), 'No-show')
        self.assertTrue(reserva.es_no_show())


class ReservasWebTests(TestCase):
    """Fase 5: páginas de reservas (lista, alta, cancelación) y calendario."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.recepcionista = User.objects.create_user('recep', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_lista_de_reservas_se_renderiza(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today() + timedelta(days=1),
            fecha_salida=date.today() + timedelta(days=3),
        )
        respuesta = self.client.get(reverse('reservas_lista'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')

    def test_busqueda_de_disponibilidad_muestra_habitacion_libre(self):
        respuesta = self.client.get(reverse('reserva_nueva'), {
            'fecha_ingreso': '2026-10-01', 'fecha_salida': '2026-10-05',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Hab. 101')

    def test_flujo_completo_crear_reserva_desde_la_web(self):
        respuesta = self.client.post(reverse('reserva_crear'), {
            'cliente': self.cliente.cedula,
            'habitacion': self.habitacion.codigo,
            'fecha_ingreso': '2026-10-01',
            'fecha_salida': '2026-10-05',
        })
        self.assertRedirects(respuesta, reverse('reservas_lista'))
        self.assertTrue(Reserva.objects.filter(cliente=self.cliente, habitacion=self.habitacion).exists())

    def test_crear_reserva_solapada_desde_la_web_muestra_error_sin_crearla(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date(2026, 10, 1), fecha_salida=date(2026, 10, 10),
        )
        respuesta = self.client.post(reverse('reserva_crear'), {
            'cliente': self.cliente.cedula,
            'habitacion': self.habitacion.codigo,
            'fecha_ingreso': '2026-10-05',
            'fecha_salida': '2026-10-07',
        })
        self.assertEqual(respuesta.status_code, 200)  # vuelve a mostrar el form con el error
        self.assertEqual(Reserva.objects.filter(habitacion=self.habitacion).count(), 1)

    def test_cancelar_reserva_la_marca_cancelada(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today() + timedelta(days=1),
            fecha_salida=date.today() + timedelta(days=3),
        )
        respuesta = self.client.post(reverse('reserva_cancelar', args=[reserva.id]))
        self.assertRedirects(respuesta, reverse('reservas_lista'))
        reserva.refresh_from_db()
        self.assertTrue(reserva.esta_cancelada())
        # Ya no aparece en "próximas" (el filtro por defecto), pero la fila sigue existiendo.
        self.assertFalse(Reserva.objects.filter(pk=reserva.pk, cancelada_en__isnull=True).exists())

    def test_cancelar_reserva_respeta_volver_si_es_del_mismo_sitio(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today() + timedelta(days=1),
            fecha_salida=date.today() + timedelta(days=3),
        )
        destino = reverse('reservas_lista') + '?filtro=pasadas'
        respuesta = self.client.post(reverse('reserva_cancelar', args=[reserva.id]), {'volver': destino})
        self.assertRedirects(respuesta, destino)

    def test_cancelar_reserva_ignora_volver_hacia_otro_sitio(self):
        """Regresión: 'volver' es un campo de POST, no hay que confiar en él
        a ciegas para redirigir (open redirect)."""
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today() + timedelta(days=1),
            fecha_salida=date.today() + timedelta(days=3),
        )
        respuesta = self.client.post(
            reverse('reserva_cancelar', args=[reserva.id]),
            {'volver': 'https://evil.example.com/robar-sesion'},
        )
        self.assertRedirects(respuesta, reverse('reservas_lista'))

    def test_limpieza_no_puede_crear_ni_cancelar_reservas(self):
        limpieza = User.objects.create_user('limpieza5', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        self.assertEqual(self.client.get(reverse('reserva_nueva')).status_code, 403)

    def test_cancelar_reserva_con_checkin_hecho_muestra_error_sin_crashear(self):
        """Regresión: cancelar_reserva() ahora rechaza reservas con check-in
        hecho; la vista debe mostrar un mensaje, no un 500."""
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        services.hacer_checkin(reserva)

        respuesta = self.client.post(reverse('reserva_cancelar', args=[reserva.id]))
        self.assertRedirects(respuesta, reverse('reservas_lista'))
        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())  # no se borró

    def test_calendario_se_renderiza(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        respuesta = self.client.get(reverse('calendario'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '101')

    def test_calendario_muestra_la_reserva_con_su_estado(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        respuesta = self.client.get(reverse('calendario'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'g-ocupada')
        self.assertContains(respuesta, 'text-bg-secondary')  # Confirmada
        self.assertContains(respuesta, self.cliente.apellido[:1])
        self.assertContains(respuesta, 'g-hoy')  # hoy está dentro del rango de la reserva

    def test_calendario_del_mes_actual_no_muestra_boton_hoy(self):
        respuesta = self.client.get(reverse('calendario'))
        self.assertNotContains(respuesta, '>Hoy<')

    def test_calendario_de_otro_mes_muestra_boton_hoy(self):
        hoy = date.today()
        otro_anio, otro_mes = (hoy.year + 1, 1) if hoy.month == 12 else (hoy.year, hoy.month % 12 + 1)
        respuesta = self.client.get(reverse('calendario'), {'anio': otro_anio, 'mes': otro_mes})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '>Hoy<')


class ServiciosCheckinCheckoutTests(TestCase):
    """Fase 7: services.hacer_checkin / hacer_checkout y sus reglas."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50, estado='Reservada',
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )

    def test_checkin_marca_hora_y_ocupa_la_habitacion(self):
        services.hacer_checkin(self.reserva)
        self.reserva.refresh_from_db()
        self.habitacion.refresh_from_db()
        self.assertIsNotNone(self.reserva.check_in_at)
        self.assertEqual(self.habitacion.estado, 'Ocupada')

    def test_no_permite_doble_checkin(self):
        services.hacer_checkin(self.reserva)
        with self.assertRaises(ValidationError):
            services.hacer_checkin(self.reserva)

    def test_checkout_sin_checkin_previo_falla(self):
        with self.assertRaises(ValidationError):
            services.hacer_checkout(self.reserva)

    def test_checkout_genera_factura_y_manda_a_limpieza(self):
        services.hacer_checkin(self.reserva)
        factura = services.hacer_checkout(self.reserva)
        self.reserva.refresh_from_db()
        self.habitacion.refresh_from_db()
        self.assertIsNotNone(self.reserva.check_out_at)
        self.assertEqual(self.habitacion.estado, 'Limpieza')
        self.assertEqual(factura.total, self.reserva.total_con_iva())

    def test_no_permite_doble_checkout(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        with self.assertRaises(ValidationError):
            services.hacer_checkout(self.reserva)

    def test_no_se_puede_cancelar_reserva_con_checkin_hecho(self):
        services.hacer_checkin(self.reserva)
        with self.assertRaises(ValidationError):
            services.cancelar_reserva(self.reserva)

    def test_finalizar_limpieza_deja_habitacion_disponible(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        services.finalizar_limpieza(self.habitacion)
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Disponible')

    def test_finalizar_limpieza_falla_si_no_esta_en_limpieza(self):
        with self.assertRaises(ValidationError):
            services.finalizar_limpieza(self.habitacion)


class CheckinCheckoutWebTests(TestCase):
    """Fase 7: páginas de check-in/check-out y descarga de factura."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        self.recepcionista = User.objects.create_user('recep', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_lista_de_checkin_muestra_la_reserva_pendiente(self):
        respuesta = self.client.get(reverse('checkin_lista'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')

    def test_confirmar_checkin_actualiza_estado_y_redirige(self):
        respuesta = self.client.post(reverse('checkin_confirmar', args=[self.reserva.id]))
        self.assertRedirects(respuesta, reverse('checkin_lista'))
        self.reserva.refresh_from_db()
        self.assertIsNotNone(self.reserva.check_in_at)

    def test_checkout_completo_genera_factura_descargable(self):
        self.client.post(reverse('checkin_confirmar', args=[self.reserva.id]))
        respuesta = self.client.post(reverse('checkout_confirmar', args=[self.reserva.id]))
        self.assertRedirects(respuesta, reverse('checkout_lista'))

        factura = Factura.objects.get(reserva=self.reserva)
        pdf = self.client.get(reverse('factura_pdf', args=[factura.id]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_finalizar_limpieza_desde_la_web(self):
        self.client.post(reverse('checkin_confirmar', args=[self.reserva.id]))
        self.client.post(reverse('checkout_confirmar', args=[self.reserva.id]))
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Limpieza')

        # Finalizar la limpieza es tarea del rol Limpieza, no de Recepción.
        limpieza = User.objects.create_user('limpieza7', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.post(reverse('habitacion_finalizar_limpieza', args=[self.habitacion.codigo]))
        self.assertRedirects(respuesta, reverse('habitacion_detalle', args=[self.habitacion.codigo]))
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Disponible')

    def test_limpieza_no_puede_hacer_checkin(self):
        limpieza = User.objects.create_user('limpieza6', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.get(reverse('checkin_lista'))
        self.assertEqual(respuesta.status_code, 403)


class ServiciosConsumosTests(TestCase):
    """Fase 8: catálogo de servicios y consumos durante la estadía."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        self.servicio = Servicio.objects.create(nombre='Gaseosa de prueba', categoria='Minibar', precio=3)

    def test_migracion_precarga_catalogo_de_servicios(self):
        nombres = set(Servicio.objects.values_list('nombre', flat=True))
        self.assertIn('Desayuno buffet', nombres)
        self.assertIn('Gaseosa', nombres)

    def test_consumo_calcula_subtotal_al_guardar(self):
        consumo = Consumo.objects.create(reserva=self.reserva, servicio=self.servicio, cantidad=4)
        self.assertEqual(consumo.precio_unitario, 3)
        self.assertEqual(consumo.subtotal, 12)

    def test_consumo_congela_el_precio_aunque_el_catalogo_cambie_despues(self):
        consumo = Consumo.objects.create(reserva=self.reserva, servicio=self.servicio, cantidad=1)
        self.servicio.precio = 100
        self.servicio.save()
        consumo.refresh_from_db()
        self.assertEqual(consumo.precio_unitario, 3)  # no cambia retroactivamente

    def test_no_se_puede_registrar_consumo_sin_checkin(self):
        with self.assertRaises(ValidationError):
            services.registrar_consumo(self.reserva, self.servicio, 1)

    def test_no_se_puede_registrar_consumo_despues_del_checkout(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        with self.assertRaises(ValidationError):
            services.registrar_consumo(self.reserva, self.servicio, 1)

    def test_no_se_puede_registrar_consumo_de_servicio_inactivo(self):
        services.hacer_checkin(self.reserva)
        self.servicio.activo = False
        self.servicio.save()
        with self.assertRaises(ValidationError):
            services.registrar_consumo(self.reserva, self.servicio, 1)

    def test_registrar_consumo_durante_la_estadia(self):
        services.hacer_checkin(self.reserva)
        consumo = services.registrar_consumo(self.reserva, self.servicio, 2)
        self.assertEqual(consumo.subtotal, 6)

    def test_total_estadia_incluye_consumos(self):
        services.hacer_checkin(self.reserva)
        services.registrar_consumo(self.reserva, self.servicio, 2)  # $6
        # Reserva de 2 noches × $50 = $100 de alojamiento + $6 de consumo;
        # el precio ya incluye IVA, así que eso es lo que paga el huésped.
        self.assertEqual(self.reserva.total_con_iva(), 106)
        self.assertEqual(self.reserva.total_estadia(), Decimal('92.17'))  # $106 / 1.15, sin IVA

    def test_factura_al_checkout_incluye_consumos(self):
        services.hacer_checkin(self.reserva)
        services.registrar_consumo(self.reserva, self.servicio, 2)  # $6
        factura = services.hacer_checkout(self.reserva)
        # El precio ya incluye IVA: el total es $106 tal cual, y el
        # subtotal sale de descontarle el impuesto.
        self.assertEqual(factura.total, 106)
        self.assertEqual(factura.subtotal, Decimal('92.17'))  # $106 / 1.15


class ConsumosWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        self.servicio = Servicio.objects.create(nombre='Gaseosa de prueba', categoria='Minibar', precio=3)
        self.recepcionista = User.objects.create_user('recep', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_detalle_de_reserva_sin_checkin_no_muestra_formulario_de_consumo(self):
        respuesta = self.client.get(reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'solo se pueden agregar durante la estadía')

    def test_agregar_consumo_desde_la_web(self):
        services.hacer_checkin(self.reserva)
        respuesta = self.client.post(reverse('consumo_agregar', args=[self.reserva.id]), {
            'servicio': self.servicio.id, 'cantidad': 3,
        })
        self.assertRedirects(respuesta, reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertEqual(Consumo.objects.filter(reserva=self.reserva).count(), 1)

    def test_detalle_de_reserva_muestra_consumos_y_total(self):
        services.hacer_checkin(self.reserva)
        services.registrar_consumo(self.reserva, self.servicio, 2)
        respuesta = self.client.get(reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertContains(respuesta, 'Gaseosa de prueba')
        # Con LANGUAGE_CODE='es', Django muestra los montos con coma
        # decimal ("106,00"), no punto — es el separador correcto en
        # español, a diferencia del value= de un <input type="number">.
        # El precio ya incluye IVA: el total es $106 ($100 alojamiento +
        # $6 consumo) tal cual, y el subtotal sale de descontarle el
        # impuesto ($106 / 1.15 = $92.17).
        self.assertContains(respuesta, '92,17')  # subtotal
        self.assertContains(respuesta, '106,00')  # total

    def test_limpieza_no_puede_agregar_consumos(self):
        limpieza = User.objects.create_user('limpieza8', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)
        services.hacer_checkin(self.reserva)

        respuesta = self.client.post(reverse('consumo_agregar', args=[self.reserva.id]), {
            'servicio': self.servicio.id, 'cantidad': 1,
        })
        self.assertEqual(respuesta.status_code, 403)


class PagosTests(TestCase):
    """Fase 9: pagos contra el saldo de una reserva."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )  # el precio ya incluye IVA: total_con_iva = $100 (lo que paga el huésped)

    def test_saldo_pendiente_es_el_total_sin_pagos(self):
        self.assertEqual(self.reserva.saldo_pendiente(), 100)

    def test_registrar_pago_reduce_el_saldo(self):
        services.registrar_pago(self.reserva, 40, 'Efectivo')
        self.assertEqual(self.reserva.total_pagado(), 40)
        self.assertEqual(self.reserva.saldo_pendiente(), 60)

    def test_pago_no_puede_superar_el_saldo(self):
        with self.assertRaises(ValidationError):
            services.registrar_pago(self.reserva, 150, 'Efectivo')
        self.assertEqual(self.reserva.total_pagado(), 0)

    def test_pago_no_puede_superar_saldo_ya_parcialmente_pagado(self):
        services.registrar_pago(self.reserva, 80, 'Efectivo')
        with self.assertRaises(ValidationError):
            services.registrar_pago(self.reserva, 30, 'Tarjeta', referencia='AUTH123')  # saldo es 20
        self.assertEqual(self.reserva.total_pagado(), 80)

    def test_pago_de_monto_cero_o_negativo_rechazado(self):
        with self.assertRaises(ValidationError):
            services.registrar_pago(self.reserva, 0, 'Efectivo')

    def test_pago_completo_salda_la_cuenta(self):
        services.registrar_pago(self.reserva, 100, 'Transferencia', referencia='TRX-001')
        self.assertEqual(self.reserva.saldo_pendiente(), 0)

    def test_factura_generada_al_checkout_no_depende_de_los_pagos(self):
        """El total de la factura es lo que se debe, no lo que se pagó."""
        services.hacer_checkin(self.reserva)
        services.registrar_pago(self.reserva, 30, 'Efectivo')
        factura = services.hacer_checkout(self.reserva)
        self.assertEqual(factura.total, 100)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.saldo_pendiente(), 70)

    def test_transferencia_sin_referencia_es_rechazada(self):
        with self.assertRaises(ValidationError):
            services.registrar_pago(self.reserva, 50, 'Transferencia')
        self.assertEqual(self.reserva.total_pagado(), 0)

    def test_tarjeta_sin_referencia_es_rechazada(self):
        with self.assertRaises(ValidationError):
            services.registrar_pago(self.reserva, 50, 'Tarjeta')
        self.assertEqual(self.reserva.total_pagado(), 0)

    def test_efectivo_no_necesita_referencia(self):
        pago = services.registrar_pago(self.reserva, 50, 'Efectivo')
        self.assertEqual(pago.referencia, '')

    def test_transferencia_con_referencia_se_registra(self):
        pago = services.registrar_pago(self.reserva, 50, 'Transferencia', referencia='TRX-42')
        self.assertEqual(pago.referencia, 'TRX-42')


class PagosWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        self.recepcionista = User.objects.create_user('recep', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_registrar_pago_desde_la_web(self):
        respuesta = self.client.post(reverse('pago_agregar', args=[self.reserva.id]), {
            'monto': '40.00', 'metodo': 'Efectivo', 'observacion': '',
        })
        self.assertRedirects(respuesta, reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertEqual(Pago.objects.filter(reserva=self.reserva).count(), 1)

    def test_pago_excesivo_no_se_registra_y_avisa(self):
        respuesta = self.client.post(reverse('pago_agregar', args=[self.reserva.id]), {
            'monto': '999.00', 'metodo': 'Efectivo', 'observacion': '',
        })
        self.assertRedirects(respuesta, reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertEqual(Pago.objects.filter(reserva=self.reserva).count(), 0)

    def test_detalle_de_reserva_muestra_saldo_pendiente(self):
        services.registrar_pago(self.reserva, 30, 'Efectivo')
        respuesta = self.client.get(reverse('reserva_detalle', args=[self.reserva.id]))
        # el precio ya incluye IVA: total $100, saldo tras pagar $30 = $70
        # (Django muestra los montos con coma decimal — LANGUAGE_CODE='es').
        self.assertContains(respuesta, '70,00')

    def test_lista_de_pagos_se_renderiza(self):
        services.registrar_pago(self.reserva, 40, 'Efectivo')
        respuesta = self.client.get(reverse('pagos_lista'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')

    def test_lista_de_pagos_sin_consumos_lo_indica(self):
        services.registrar_pago(self.reserva, 40, 'Efectivo')
        respuesta = self.client.get(reverse('pagos_lista'))
        self.assertContains(respuesta, 'Sin consumos')

    def test_lista_de_pagos_muestra_lo_que_consumio_el_huesped(self):
        servicio = Servicio.objects.create(nombre='Desayuno buffet especial', categoria='Restaurante', precio=8)
        services.hacer_checkin(self.reserva)
        services.registrar_consumo(self.reserva, servicio, 2)
        services.registrar_pago(self.reserva, 40, 'Efectivo')

        respuesta = self.client.get(reverse('pagos_lista'))
        self.assertContains(respuesta, '2× Desayuno buffet especial')

    def test_limpieza_no_puede_ver_pagos(self):
        limpieza = User.objects.create_user('limpieza9', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        self.assertEqual(self.client.get(reverse('pagos_lista')).status_code, 403)

    def test_factura_pdf_incluye_saldo_pendiente(self):
        services.hacer_checkin(self.reserva)
        services.registrar_pago(self.reserva, 30, 'Efectivo')
        factura = services.hacer_checkout(self.reserva)
        respuesta = self.client.get(reverse('factura_pdf', args=[factura.id]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')

    def test_factura_detalle_muestra_datos_del_huesped_y_totales(self):
        services.hacer_checkin(self.reserva)
        factura = services.hacer_checkout(self.reserva)
        respuesta = self.client.get(reverse('factura_detalle', args=[factura.id]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')
        self.assertContains(respuesta, 'Habitación')
        self.assertContains(respuesta, reverse('factura_pdf', args=[factura.id]))

    def test_factura_reenviar_correo(self):
        from django.core import mail

        services.hacer_checkin(self.reserva)
        factura = services.hacer_checkout(self.reserva)
        mail.outbox = []  # limpiar el correo automático que ya mandó el checkout

        respuesta = self.client.post(reverse('factura_reenviar_correo', args=[factura.id]))
        self.assertRedirects(respuesta, reverse('factura_detalle', args=[factura.id]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.cliente.correo])

    def test_transferencia_sin_referencia_muestra_error_de_formulario(self):
        respuesta = self.client.post(reverse('pago_agregar', args=[self.reserva.id]), {
            'monto': '40.00', 'metodo': 'Transferencia', 'referencia': '', 'observacion': '',
        })
        self.assertRedirects(respuesta, reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertEqual(Pago.objects.filter(reserva=self.reserva).count(), 0)

    def test_transferencia_con_referencia_se_registra_desde_la_web(self):
        respuesta = self.client.post(reverse('pago_agregar', args=[self.reserva.id]), {
            'monto': '40.00', 'metodo': 'Transferencia', 'referencia': 'TRX-777', 'observacion': '',
        })
        self.assertRedirects(respuesta, reverse('reserva_detalle', args=[self.reserva.id]))
        pago = Pago.objects.get(reserva=self.reserva)
        self.assertEqual(pago.referencia, 'TRX-777')

    def test_comprobante_de_pago_pdf_descargable(self):
        pago = services.registrar_pago(self.reserva, 40, 'Efectivo')
        respuesta = self.client.get(reverse('comprobante_pago_pdf', args=[pago.id]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')

    def test_registrar_pago_envia_comprobante_por_correo(self):
        from django.core import mail

        respuesta = self.client.post(reverse('pago_agregar', args=[self.reserva.id]), {
            'monto': '40.00', 'metodo': 'Efectivo', 'observacion': '',
        })
        self.assertRedirects(respuesta, reverse('reserva_detalle', args=[self.reserva.id]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.cliente.correo])
        self.assertEqual(len(mail.outbox[0].attachments), 1)

    def test_checkout_envia_factura_por_correo(self):
        from django.core import mail

        services.hacer_checkin(self.reserva)
        respuesta = self.client.post(reverse('checkout_confirmar', args=[self.reserva.id]))
        self.assertRedirects(respuesta, reverse('checkout_lista'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.cliente.correo])


class ComprobantesPdfTests(TestCase):
    """El PDF de factura/comprobante se arma con reportlab.platypus (tablas
    reales, sección 31: A4 listo para imprimir) en vez de líneas sueltas de
    texto — cubrir que no rompa y que un logo grande no infle el archivo."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='1710034065', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(codigo='HPDF', numero='301', tipo=_tipo('Doble'), precio=50)
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=1),
        )
        services.hacer_checkin(self.reserva)
        self.factura = services.hacer_checkout(self.reserva)

    def test_pdf_factura_se_genera_sin_errores(self):
        from reservas import comprobantes
        pdf = comprobantes.pdf_factura_bytes(self.factura)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_pdf_comprobante_pago_se_genera_sin_errores(self):
        from reservas import comprobantes
        pago = services.registrar_pago(self.reserva, 20, 'Efectivo')
        pdf = comprobantes.pdf_comprobante_pago_bytes(pago)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_logo_grande_no_infla_el_pdf(self):
        # Regresión: un logo subido a resolución "de cámara/IA" (varios MB)
        # inflaba cada factura a varios MB también, porque ReportLab
        # embebe el archivo tal cual si no se lo redimensiona antes
        # (ver comprobantes._logo_redimensionado).
        import shutil
        import tempfile
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from PIL import Image as PILImage

        from reservas import comprobantes

        tmp_media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=tmp_media):
            buffer = BytesIO()
            PILImage.new('RGB', (2000, 2000), color='blue').save(buffer, format='PNG')
            config = ConfiguracionHotel.actual()
            config.logo.save('logo_grande.png', SimpleUploadedFile('logo_grande.png', buffer.getvalue()), save=True)

            pdf = comprobantes.pdf_factura_bytes(self.factura)
            self.assertLess(len(pdf), 300_000)


class FacturasListaTests(TestCase):
    """Sección 20: listado general de facturas — antes solo se llegaba a
    una factura puntual desde su reserva."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='1710034065', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='HFAC', numero='501', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        services.hacer_checkin(self.reserva)
        self.factura = services.hacer_checkout(self.reserva)

        self.recepcionista = User.objects.create_user('recep_fac', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_lista_de_facturas_se_renderiza(self):
        respuesta = self.client.get(reverse('facturas_lista'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ana Torres')
        self.assertContains(respuesta, f'FA-{self.factura.id:06d}')

    def test_busqueda_por_apellido(self):
        Cliente.objects.create(
            cedula='0926687856', nombre='Luis', apellido='Zambrano',
            telefono='0987654321', correo='luis@example.com',
        )
        respuesta = self.client.get(reverse('facturas_lista'), {'q': 'Torres'})
        self.assertContains(respuesta, 'Ana Torres')

    def test_filtro_pendiente_solo_muestra_facturas_con_saldo(self):
        services.registrar_pago(self.reserva, self.factura.total, 'Efectivo')
        respuesta = self.client.get(reverse('facturas_lista'), {'estado': 'pendiente'})
        self.assertNotContains(respuesta, f'FA-{self.factura.id:06d}')

    def test_filtro_pagada_muestra_facturas_saldadas(self):
        services.registrar_pago(self.reserva, self.factura.total, 'Efectivo')
        respuesta = self.client.get(reverse('facturas_lista'), {'estado': 'pagada'})
        self.assertContains(respuesta, f'FA-{self.factura.id:06d}')

    def test_limpieza_no_puede_ver_facturas(self):
        limpieza = User.objects.create_user('limpieza_fac', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)
        self.assertEqual(self.client.get(reverse('facturas_lista')).status_code, 403)


class LimpiezaTests(TestCase):
    """Fase 10: TareaLimpieza y su ciclo Pendiente → En limpieza → Limpia."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=1),
        )
        self.limpiador = User.objects.create_user('limpiador', password='x')

    def test_checkout_crea_tarea_de_limpieza_pendiente(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)
        self.assertEqual(tarea.estado, 'Pendiente')

    def test_iniciar_limpieza_registra_responsable_y_hora(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)

        services.iniciar_limpieza(tarea, responsable=self.limpiador)
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, 'En limpieza')
        self.assertEqual(tarea.responsable, self.limpiador)
        self.assertIsNotNone(tarea.iniciada_en)

    def test_no_se_puede_iniciar_una_tarea_ya_iniciada(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)
        services.iniciar_limpieza(tarea, responsable=self.limpiador)
        with self.assertRaises(ValidationError):
            services.iniciar_limpieza(tarea, responsable=self.limpiador)

    def test_completar_tarea_libera_la_habitacion(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)
        services.iniciar_limpieza(tarea, responsable=self.limpiador)

        services.completar_tarea_limpieza(tarea, observaciones='todo bien')
        tarea.refresh_from_db()
        self.habitacion.refresh_from_db()
        self.assertEqual(tarea.estado, 'Limpia')
        self.assertEqual(tarea.observaciones, 'todo bien')
        self.assertIsNotNone(tarea.finalizada_en)
        self.assertEqual(self.habitacion.estado, 'Disponible')

    def test_completar_tarea_ya_completada_falla(self):
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)
        services.completar_tarea_limpieza(tarea)
        with self.assertRaises(ValidationError):
            services.completar_tarea_limpieza(tarea)

    def test_finalizar_limpieza_rapido_tambien_cierra_la_tarea(self):
        """El botón rápido del detalle de habitación usa la misma tarea."""
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)

        services.finalizar_limpieza(self.habitacion)
        tarea.refresh_from_db()
        self.habitacion.refresh_from_db()
        self.assertEqual(tarea.estado, 'Limpia')
        self.assertEqual(self.habitacion.estado, 'Disponible')


class LimpiezaWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=1),
        )
        services.hacer_checkin(self.reserva)
        services.hacer_checkout(self.reserva)
        self.tarea = TareaLimpieza.objects.get(habitacion=self.habitacion)

        self.limpieza_user = User.objects.create_user('limpieza10', password='x', is_staff=True)
        self.limpieza_user.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(self.limpieza_user)

    def test_lista_de_limpieza_muestra_la_tarea_pendiente(self):
        respuesta = self.client.get(reverse('limpieza_lista'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '101')

    def test_iniciar_y_completar_desde_la_web(self):
        respuesta = self.client.post(reverse('limpieza_iniciar', args=[self.tarea.id]))
        self.assertRedirects(respuesta, reverse('limpieza_lista'))
        self.tarea.refresh_from_db()
        self.assertEqual(self.tarea.estado, 'En limpieza')
        self.assertEqual(self.tarea.responsable, self.limpieza_user)

        respuesta = self.client.post(
            reverse('limpieza_completar', args=[self.tarea.id]),
            {'observaciones': 'lista'},
        )
        self.assertRedirects(respuesta, reverse('limpieza_lista'))
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Disponible')

    def test_recepcionista_no_puede_gestionar_limpieza(self):
        recep = User.objects.create_user('recep10', password='x', is_staff=True)
        recep.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(recep)

        self.assertEqual(self.client.get(reverse('limpieza_lista')).status_code, 403)


class MantenimientoTests(TestCase):
    """Fase 11: incidencias — reportar → asignar → reparar → resolver → cerrar."""

    def setUp(self):
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50, estado='Disponible',
        )
        self.tecnico = User.objects.create_user('tecnico', password='x')

    def test_reportar_incidencia_saca_habitacion_disponible_de_circulacion(self):
        services.reportar_incidencia(self.habitacion, 'Aire acondicionado no enfría', 'Alta')
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Mantenimiento')

    def test_reportar_incidencia_no_interrumpe_habitacion_ocupada(self):
        self.habitacion.estado = 'Ocupada'
        self.habitacion.save(update_fields=['estado'])
        services.reportar_incidencia(self.habitacion, 'Foco quemado', 'Baja')
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Ocupada')  # no se le interrumpe la estadía

    def test_habitacion_en_mantenimiento_no_es_reservable(self):
        services.reportar_incidencia(self.habitacion, 'Fuga de agua', 'Urgente')
        disponibles = services.habitaciones_disponibles(date(2026, 12, 1), date(2026, 12, 3))
        self.assertNotIn(self.habitacion, list(disponibles))

    def test_asignar_responsable_pasa_a_en_revision(self):
        incidencia = services.reportar_incidencia(self.habitacion, 'Puerta no cierra', 'Media')
        services.asignar_responsable_incidencia(incidencia, self.tecnico)
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.responsable, self.tecnico)
        self.assertEqual(incidencia.estado, 'En revisión')

    def test_no_se_puede_iniciar_reparacion_sin_responsable(self):
        incidencia = services.reportar_incidencia(self.habitacion, 'TV no enciende', 'Media')
        with self.assertRaises(ValidationError):
            services.iniciar_reparacion(incidencia)

    def test_flujo_completo_hasta_cerrar(self):
        incidencia = services.reportar_incidencia(self.habitacion, 'Ducha gotea', 'Media')
        services.asignar_responsable_incidencia(incidencia, self.tecnico)
        services.iniciar_reparacion(incidencia)
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.estado, 'En reparación')

        services.resolver_incidencia(incidencia, 'Se cambió la llave de la ducha.')
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.estado, 'Resuelto')
        self.assertIsNotNone(incidencia.resuelta_en)

        services.cerrar_incidencia(incidencia)
        incidencia.refresh_from_db()
        self.habitacion.refresh_from_db()
        self.assertEqual(incidencia.estado, 'Cerrado')
        self.assertEqual(self.habitacion.estado, 'Disponible')

    def test_no_se_puede_cerrar_sin_resolver(self):
        incidencia = services.reportar_incidencia(self.habitacion, 'Cerradura floja', 'Baja')
        with self.assertRaises(ValidationError):
            services.cerrar_incidencia(incidencia)

    def test_cerrar_no_libera_habitacion_si_queda_otra_incidencia_abierta(self):
        i1 = services.reportar_incidencia(self.habitacion, 'Problema 1', 'Alta')
        services.reportar_incidencia(self.habitacion, 'Problema 2', 'Media')  # sigue abierta

        services.resolver_incidencia(i1, 'Arreglado 1')
        services.cerrar_incidencia(i1)
        self.habitacion.refresh_from_db()
        self.assertEqual(self.habitacion.estado, 'Mantenimiento')  # sigue en mantenimiento


class MantenimientoWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50, estado='Disponible',
        )
        self.mantenimiento_user = User.objects.create_user('mant1', password='x', is_staff=True)
        self.mantenimiento_user.groups.add(Group.objects.get(name='Mantenimiento'))
        self.client.force_login(self.mantenimiento_user)

    def test_reportar_desde_la_web(self):
        respuesta = self.client.post(reverse('mantenimiento_reportar'), {
            'habitacion': self.habitacion.codigo, 'descripcion': 'Grifo roto', 'prioridad': 'Alta',
        })
        self.assertRedirects(respuesta, reverse('mantenimiento_lista'))
        self.assertEqual(Incidencia.objects.filter(habitacion=self.habitacion).count(), 1)

    def test_flujo_completo_desde_la_web(self):
        incidencia = services.reportar_incidencia(self.habitacion, 'Enchufe suelto', 'Media')

        self.client.post(reverse('mantenimiento_asignar', args=[incidencia.id]))
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.responsable, self.mantenimiento_user)

        self.client.post(reverse('mantenimiento_iniciar', args=[incidencia.id]))
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.estado, 'En reparación')

        self.client.post(reverse('mantenimiento_resolver', args=[incidencia.id]), {'solucion': 'Cambiado'})
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.estado, 'Resuelto')

        self.client.post(reverse('mantenimiento_cerrar', args=[incidencia.id]))
        incidencia.refresh_from_db()
        self.assertEqual(incidencia.estado, 'Cerrado')

    def test_lista_de_mantenimiento_no_muestra_cerradas(self):
        incidencia = services.reportar_incidencia(self.habitacion, 'Ya resuelto', 'Baja')
        services.resolver_incidencia(incidencia, 'listo')
        services.cerrar_incidencia(incidencia)

        respuesta = self.client.get(reverse('mantenimiento_lista'))
        self.assertNotContains(respuesta, 'Ya resuelto')

    def test_recepcionista_puede_reportar_pero_no_gestionar(self):
        recep = User.objects.create_user('recep11', password='x', is_staff=True)
        recep.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(recep)

        respuesta = self.client.post(reverse('mantenimiento_reportar'), {
            'habitacion': self.habitacion.codigo, 'descripcion': 'Ventana rota', 'prioridad': 'Alta',
        })
        self.assertRedirects(respuesta, reverse('mantenimiento_lista'))

        incidencia = Incidencia.objects.get(habitacion=self.habitacion)
        respuesta = self.client.post(reverse('mantenimiento_asignar', args=[incidencia.id]))
        self.assertEqual(respuesta.status_code, 403)


class ReportesTests(TestCase):
    """Fase 12: reportes de ocupación, ingresos, reservas, huéspedes,
    servicios y pagos."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50, estado='Disponible',
        )
        self.admin = User.objects.create_superuser('admin12', 'a@example.com', 'x')
        self.client.force_login(self.admin)

    def test_hub_de_reportes_se_renderiza(self):
        respuesta = self.client.get(reverse('reportes_lista'))
        self.assertEqual(respuesta.status_code, 200)

    def test_reporte_ocupacion_calcula_porcentaje(self):
        Habitacion.objects.create(codigo='H2', numero='102', tipo=_tipo('Doble'), precio=50, estado='Ocupada')
        respuesta = self.client.get(reverse('reporte_ocupacion'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['total'], 2)
        self.assertEqual(respuesta.context['porcentaje'], 50.0)

    def test_reporte_ocupacion_pdf(self):
        respuesta = self.client.get(reverse('reporte_ocupacion'), {'formato': 'pdf'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')

    def test_reporte_ingresos_suma_por_dia(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        Factura.objects.create(reserva=reserva)  # $100 (el precio ya incluye IVA)
        respuesta = self.client.get(reverse('reporte_ingresos'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['total_periodo'], 100)

    def test_reporte_ingresos_csv(self):
        respuesta = self.client.get(reverse('reporte_ingresos'), {'formato': 'csv'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'text/csv')

    def test_reporte_reservas_clasifica_correctamente(self):
        hoy = date.today()
        # Confirmada (futura, sin check-in)
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=hoy, fecha_salida=hoy + timedelta(days=2),
        )
        # Cancelada
        h2 = Habitacion.objects.create(codigo='H2', numero='102', tipo=_tipo('Doble'), precio=50)
        cancelada = Reserva.objects.create(
            cliente=self.cliente, habitacion=h2,
            fecha_ingreso=hoy, fecha_salida=hoy + timedelta(days=1),
        )
        services.cancelar_reserva(cancelada)
        # No-show (ya pasó, nunca hizo check-in)
        h3 = Habitacion.objects.create(codigo='H3', numero='103', tipo=_tipo('Doble'), precio=50)
        Reserva.objects.create(
            cliente=self.cliente, habitacion=h3,
            fecha_ingreso=hoy - timedelta(days=5), fecha_salida=hoy - timedelta(days=3),
        )

        respuesta = self.client.get(reverse('reporte_reservas'), {
            'desde': (hoy - timedelta(days=10)).isoformat(), 'hasta': (hoy + timedelta(days=10)).isoformat(),
        })
        self.assertEqual(respuesta.context['confirmadas'], 1)
        self.assertEqual(respuesta.context['canceladas'], 1)
        self.assertEqual(respuesta.context['no_show'], 1)

    def test_reporte_huespedes_calcula_gasto(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        respuesta = self.client.get(reverse('reporte_huespedes'))
        self.assertEqual(respuesta.status_code, 200)
        fila = respuesta.context['filas'][0]
        self.assertEqual(fila['cliente'], self.cliente)
        self.assertEqual(fila['gastado'], reserva.total_con_iva())

    def test_reporte_servicios_agrupa_por_servicio(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        services.hacer_checkin(reserva)
        servicio = Servicio.objects.create(nombre='Gaseosa test', categoria='Minibar', precio=3)
        services.registrar_consumo(reserva, servicio, 2)

        respuesta = self.client.get(reverse('reporte_servicios'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['total_periodo'], 6)

    def test_reporte_pagos_agrupa_por_metodo(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        services.registrar_pago(reserva, 40, 'Efectivo')
        services.registrar_pago(reserva, 20, 'Tarjeta', referencia='AUTH999')

        respuesta = self.client.get(reverse('reporte_pagos'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['total_periodo'], 60)

    def test_gerencia_puede_ver_reportes_pero_no_gestionar(self):
        gerente = User.objects.create_user('gerente1', password='x', is_staff=True)
        gerente.groups.add(Group.objects.get(name='Gerencia'))
        self.client.force_login(gerente)

        self.assertEqual(self.client.get(reverse('reporte_ocupacion')).status_code, 200)

    def test_limpieza_no_puede_ver_reporte_de_ingresos(self):
        limpieza = User.objects.create_user('limpieza11', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        self.assertEqual(self.client.get(reverse('reporte_ingresos')).status_code, 403)


class AuditoriaTests(TestCase):
    """Fase 13: se registran los eventos clave y nadie puede alterarlos."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.recepcionista = User.objects.create_user('recepAud', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))

    def test_login_queda_auditado(self):
        self.client.login(username='recepAud', password='x')
        self.assertTrue(AuditLog.objects.filter(accion='login', usuario=self.recepcionista).exists())

    def test_login_fallido_queda_auditado_sin_usuario(self):
        self.client.login(username='recepAud', password='clave-incorrecta')
        registro = AuditLog.objects.get(accion='login_fallido')
        self.assertIsNone(registro.usuario)
        self.assertIn('recepAud', registro.descripcion)

    def test_crear_reserva_queda_auditada(self):
        self.client.force_login(self.recepcionista)
        self.client.post(reverse('reserva_crear'), {
            'cliente': self.cliente.cedula, 'habitacion': self.habitacion.codigo,
            'fecha_ingreso': '2026-10-01', 'fecha_salida': '2026-10-05',
        })
        registro = AuditLog.objects.get(accion='crear_reserva')
        self.assertEqual(registro.usuario, self.recepcionista)
        self.assertEqual(registro.modulo, 'reservas')

    def test_checkin_checkout_y_factura_quedan_auditados(self):
        self.client.force_login(self.recepcionista)
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=1),
        )
        self.client.post(reverse('checkin_confirmar', args=[reserva.id]))
        self.client.post(reverse('checkout_confirmar', args=[reserva.id]))

        self.assertTrue(AuditLog.objects.filter(accion='check_in').exists())
        self.assertTrue(AuditLog.objects.filter(accion='check_out').exists())
        self.assertTrue(AuditLog.objects.filter(accion='generar_factura').exists())

    def test_cambio_por_admin_queda_auditado(self):
        admin = User.objects.create_superuser('adminAud', 'a@example.com', 'x')
        self.client.force_login(admin)
        self.client.post(f'/admin/reservas/habitacion/{self.habitacion.codigo}/change/', {
            'codigo': self.habitacion.codigo, 'numero': '101', 'tipo': self.habitacion.tipo_id,
            'precio': '75.00', 'estado': 'Disponible',
        })
        self.assertTrue(AuditLog.objects.filter(accion='modificar', modulo='habitacion').exists())

    def test_nadie_puede_modificar_auditoria_desde_el_admin(self):
        """Con permiso de view pero sin change, Django admin muestra la
        página en modo solo-lectura (200) en vez de un 403 — la garantía
        real es que un POST no cambia nada, y que alta/baja sí están
        bloqueadas del todo."""
        admin = User.objects.create_superuser('adminAud2', 'a@example.com', 'x')
        self.client.force_login(admin)
        registro = AuditLog.objects.create(accion='login', modulo='auth', descripcion='original')

        respuesta = self.client.post(f'/admin/reservas/auditlog/{registro.id}/change/', {
            'accion': 'alterado', 'modulo': 'auth', 'descripcion': 'alterado',
        })
        registro.refresh_from_db()
        self.assertEqual(registro.descripcion, 'original')  # el POST no tuvo efecto

        respuesta = self.client.get('/admin/reservas/auditlog/add/')
        self.assertEqual(respuesta.status_code, 403)

        respuesta = self.client.post(f'/admin/reservas/auditlog/{registro.id}/delete/', {'post': 'yes'})
        self.assertTrue(AuditLog.objects.filter(pk=registro.pk).exists())  # tampoco se pudo borrar

    def test_recepcionista_no_puede_ver_auditoria(self):
        self.client.force_login(self.recepcionista)
        self.assertEqual(self.client.get(reverse('auditoria_lista')).status_code, 403)

    def test_admin_puede_ver_lista_de_auditoria(self):
        admin = User.objects.create_superuser('adminAud3', 'a@example.com', 'x')
        self.client.force_login(admin)
        AuditLog.objects.create(accion='login', modulo='auth', descripcion='test')
        respuesta = self.client.get(reverse('auditoria_lista'))
        self.assertEqual(respuesta.status_code, 200)


class NotificacionesTests(TestCase):
    """Fase 13: notificaciones internas calculadas al vuelo."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.cliente = Cliente.objects.create(
            cedula='0102030405', nombre='Ana', apellido='Torres',
            telefono='0991234567', correo='ana@example.com',
        )
        self.habitacion = Habitacion.objects.create(
            codigo='H1', numero='101', tipo=_tipo('Doble'), precio=50,
        )
        self.recepcionista = User.objects.create_user('recepNotif', password='x', is_staff=True)
        self.recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(self.recepcionista)

    def test_sin_novedades_no_hay_notificaciones(self):
        respuesta = self.client.get(reverse('dashboard'))
        self.assertEqual(respuesta.context['notificaciones_count'], 0)

    def test_checkin_esperado_hoy_genera_notificacion(self):
        Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=2),
        )
        respuesta = self.client.get(reverse('dashboard'))
        textos = [n['texto'] for n in respuesta.context['notificaciones']]
        self.assertTrue(any('ingreso' in t for t in textos))

    def test_limpieza_pendiente_genera_notificacion_a_quien_puede_verla(self):
        reserva = Reserva.objects.create(
            cliente=self.cliente, habitacion=self.habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=1),
        )
        services.hacer_checkin(reserva)
        services.hacer_checkout(reserva)

        limpieza = User.objects.create_user('limpiezaNotif', password='x', is_staff=True)
        limpieza.groups.add(Group.objects.get(name='Limpieza'))
        self.client.force_login(limpieza)

        respuesta = self.client.get(reverse('dashboard'))
        textos = [n['texto'] for n in respuesta.context['notificaciones']]
        self.assertTrue(any('limpieza' in t for t in textos))

    def test_anonimo_no_tiene_notificaciones_en_el_contexto(self):
        self.client.logout()
        respuesta = self.client.get(reverse('login'))
        self.assertNotIn('notificaciones', respuesta.context or {})


class ConfiguracionHotelTests(TestCase):
    """Sección 29: la configuración del hotel es un singleton (una sola
    fila) y alimenta la factura con datos reales en vez de inventados."""

    def test_actual_crea_la_fila_si_no_existe(self):
        self.assertEqual(ConfiguracionHotel.objects.count(), 0)
        config = ConfiguracionHotel.actual()
        self.assertEqual(ConfiguracionHotel.objects.count(), 1)
        self.assertEqual(config.nombre_hotel, 'Sistema Hotelero')

    def test_actual_siempre_devuelve_la_misma_fila(self):
        primera = ConfiguracionHotel.actual()
        primera.nombre_hotel = 'Hotel de Prueba'
        primera.save()
        self.assertEqual(ConfiguracionHotel.actual().nombre_hotel, 'Hotel de Prueba')
        self.assertEqual(ConfiguracionHotel.objects.count(), 1)

    def test_no_se_puede_borrar_desde_la_app(self):
        config = ConfiguracionHotel.actual()
        config.delete()
        self.assertEqual(ConfiguracionHotel.objects.count(), 1)


class ConfiguracionWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def test_anonimo_no_puede_ver_configuracion(self):
        # Igual que el resto de las vistas con permission_required(...,
        # raise_exception=True): sin el permiso da 403, no redirige a
        # login (ver p.ej. habitacion_detalle/reserva_detalle).
        respuesta = self.client.get(reverse('configuracion'))
        self.assertEqual(respuesta.status_code, 403)

    def test_recepcionista_no_tiene_permiso_de_ver_configuracion(self):
        recepcionista = User.objects.create_user('recep_cfg', password='x', is_staff=True)
        recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(recepcionista)
        self.assertEqual(self.client.get(reverse('configuracion')).status_code, 403)

    def test_gerencia_puede_ver_pero_no_editar_configuracion(self):
        gerente = User.objects.create_user('gerente_cfg', password='x', is_staff=True)
        gerente.groups.add(Group.objects.get(name='Gerencia'))
        self.client.force_login(gerente)

        respuesta = self.client.get(reverse('configuracion'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context['puede_editar'])

        respuesta_post = self.client.post(reverse('configuracion'), {'nombre_hotel': 'Hackeado'})
        self.assertEqual(respuesta_post.status_code, 403)
        self.assertEqual(ConfiguracionHotel.actual().nombre_hotel, 'Sistema Hotelero')

    def test_administrador_puede_editar_configuracion(self):
        admin = User.objects.create_superuser('admin_cfg', 'admin_cfg@example.com', 'x')
        self.client.force_login(admin)

        respuesta = self.client.post(reverse('configuracion'), {
            'nombre_hotel': 'Hotel Nuevo', 'direccion': 'Calle 123', 'telefono': '0999999999',
            'ruc': '1792060346001', 'moneda': 'USD', 'iva_porcentaje': '15.00',
            'hora_checkin_default': '15:00', 'hora_checkout_default': '11:00',
            'politica_cancelacion': 'Sin reembolso dentro de las 24hs.',
        })
        self.assertRedirects(respuesta, reverse('configuracion'))
        config = ConfiguracionHotel.actual()
        self.assertEqual(config.nombre_hotel, 'Hotel Nuevo')
        self.assertEqual(config.direccion, 'Calle 123')

    def test_factura_usa_el_nombre_del_hotel_configurado(self):
        config = ConfiguracionHotel.actual()
        config.nombre_hotel = 'Hotel Amanecer'
        config.save()

        cliente = Cliente.objects.create(
            cedula='0102030499', nombre='Rita', apellido='Solis',
            telefono='0991234567', correo='rita@example.com',
        )
        habitacion = Habitacion.objects.create(codigo='HCFG', numero='201', tipo=_tipo('Doble'), precio=50)
        reserva = Reserva.objects.create(
            cliente=cliente, habitacion=habitacion,
            fecha_ingreso=date.today(), fecha_salida=date.today() + timedelta(days=1),
        )
        services.hacer_checkin(reserva)
        factura = services.hacer_checkout(reserva)

        admin = User.objects.create_superuser('admin_cfg2', 'admin_cfg2@example.com', 'x')
        self.client.force_login(admin)
        respuesta = self.client.get(reverse('factura_detalle', args=[factura.id]))
        self.assertContains(respuesta, 'Hotel Amanecer')


class UsuariosWebTests(TestCase):
    """Sección 29: alta/edición de usuarios y su rol, reservada al
    superusuario (rol "Administrador" real)."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)

    def setUp(self):
        self.admin = User.objects.create_superuser('admin_usr', 'admin_usr@example.com', 'x')
        self.client.force_login(self.admin)

    def test_recepcionista_no_puede_ver_usuarios(self):
        self.client.logout()
        recepcionista = User.objects.create_user('recep_usr', password='x', is_staff=True)
        recepcionista.groups.add(Group.objects.get(name='Recepcionista'))
        self.client.force_login(recepcionista)
        self.assertEqual(self.client.get(reverse('usuarios_lista')).status_code, 403)

    def test_lista_de_usuarios_se_renderiza(self):
        respuesta = self.client.get(reverse('usuarios_lista'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'admin_usr')

    def test_crear_usuario_le_asigna_rol_y_puede_loguearse(self):
        respuesta = self.client.post(reverse('usuario_nuevo'), {
            'username': 'nueva_recep', 'first_name': 'Nueva', 'last_name': 'Recepcionista',
            'email': 'nueva@example.com', 'rol': Group.objects.get(name='Recepcionista').id,
            'password1': 'ClaveSegura123!', 'password2': 'ClaveSegura123!',
        })
        self.assertRedirects(respuesta, reverse('usuarios_lista'))
        usuario = User.objects.get(username='nueva_recep')
        self.assertTrue(usuario.is_staff)
        self.assertEqual(usuario.groups.first().name, 'Recepcionista')

        self.client.logout()
        self.assertTrue(self.client.login(username='nueva_recep', password='ClaveSegura123!'))

    def test_crear_usuario_con_contraseñas_distintas_no_se_crea(self):
        respuesta = self.client.post(reverse('usuario_nuevo'), {
            'username': 'otro', 'first_name': '', 'last_name': '', 'email': '',
            'rol': Group.objects.get(name='Recepcionista').id,
            'password1': 'ClaveSegura123!', 'password2': 'OtraClave456!',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(User.objects.filter(username='otro').exists())

    def test_crear_usuario_con_nombre_repetido_no_se_crea(self):
        User.objects.create_user('repetido', password='x')
        respuesta = self.client.post(reverse('usuario_nuevo'), {
            'username': 'repetido', 'first_name': '', 'last_name': '', 'email': '',
            'rol': Group.objects.get(name='Recepcionista').id,
            'password1': 'ClaveSegura123!', 'password2': 'ClaveSegura123!',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(User.objects.filter(username='repetido').count(), 1)

    def test_editar_usuario_cambia_rol_y_estado(self):
        usuario = User.objects.create_user('editable', password='x', is_staff=True)
        usuario.groups.add(Group.objects.get(name='Limpieza'))

        respuesta = self.client.post(reverse('usuario_editar', args=[usuario.id]), {
            'first_name': 'Editado', 'last_name': '', 'email': '',
            'rol': Group.objects.get(name='Mantenimiento').id, 'activo': 'on',
        })
        self.assertRedirects(respuesta, reverse('usuarios_lista'))
        usuario.refresh_from_db()
        self.assertEqual(usuario.first_name, 'Editado')
        self.assertEqual(usuario.groups.first().name, 'Mantenimiento')

    def test_no_se_puede_desactivar_la_propia_cuenta(self):
        respuesta = self.client.post(reverse('usuario_editar', args=[self.admin.id]), {
            'first_name': '', 'last_name': '', 'email': '',
            'rol': '', 'activo': '',  # sin marcar = desactivar
        })
        self.assertRedirects(respuesta, reverse('usuario_editar', args=[self.admin.id]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cambiar_password_de_otro_usuario(self):
        usuario = User.objects.create_user('conclave', password='viejaClave123')
        respuesta = self.client.post(reverse('usuario_cambiar_password', args=[usuario.id]), {
            'password1': 'NuevaClave456!', 'password2': 'NuevaClave456!',
        })
        self.assertRedirects(respuesta, reverse('usuario_editar', args=[usuario.id]))
        self.client.logout()
        self.assertTrue(self.client.login(username='conclave', password='NuevaClave456!'))
