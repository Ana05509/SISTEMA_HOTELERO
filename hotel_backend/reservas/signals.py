"""Auditoría de login/logout (sección 25) vía las señales nativas de
django.contrib.auth — así queda cubierto también el login que usa
/admin/login/, no solo nuestro formulario propio."""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from . import auditoria


@receiver(user_logged_in)
def _log_login(sender, request, user, **kwargs):
    # Ojo: usar el 'user' de la señal, NO request.user — Django solo
    # reasigna request.user si el atributo ya existía en el request (lo
    # pone AuthenticationMiddleware); un request "pelado" como el que arma
    # Client.login() en los tests no lo tiene, y quedaría auditado como
    # usuario=None a pesar de que sabemos exactamente quién inició sesión.
    auditoria.registrar(
        usuario=user, accion='login', modulo='auth', objeto=user,
        descripcion=f'Inicio de sesión de "{user.get_username()}".',
        ip=request.META.get('REMOTE_ADDR') if request else None,
    )


@receiver(user_logged_out)
def _log_logout(sender, request, user, **kwargs):
    if user is None:
        return  # sesión anónima expirando; no hay a quién auditar
    auditoria.registrar(
        usuario=user, accion='logout', modulo='auth', objeto=user,
        descripcion=f'Cierre de sesión de "{user.get_username()}".',
        ip=request.META.get('REMOTE_ADDR') if request else None,
    )


@receiver(user_login_failed)
def _log_login_failed(sender, credentials, request=None, **kwargs):
    intento = credentials.get('username', '?')
    auditoria.registrar(
        usuario=None, accion='login_fallido', modulo='auth',
        descripcion=f'Intento de inicio de sesión fallido con usuario "{intento}".',
        ip=request.META.get('REMOTE_ADDR') if request else None,
    )
