"""Registro de auditoría (sección 25).

Deliberadamente NO vive en services.py: necesita "quién" (usuario/IP), que
es un concepto de request HTTP, no de regla de negocio. `interfaz.py`
(Tkinter) no pasa por una sesión de Django, así que sus acciones no quedan
auditadas — es una limitación conocida y documentada, no un olvido.
"""
from .models import AuditLog


def registrar(usuario, accion, modulo, objeto=None, descripcion='', ip=None):
    AuditLog.objects.create(
        usuario=usuario if (usuario is not None and usuario.is_authenticated) else None,
        accion=accion,
        modulo=modulo,
        objeto_repr=str(objeto) if objeto is not None else '',
        descripcion=descripcion,
        direccion_ip=ip,
    )


def registrar_desde_request(request, accion, modulo, objeto=None, descripcion=''):
    """Atajo para el caso común: dentro de una vista, con `request` a mano."""
    registrar(
        usuario=getattr(request, 'user', None),
        accion=accion,
        modulo=modulo,
        objeto=objeto,
        descripcion=descripcion,
        ip=request.META.get('REMOTE_ADDR') if request else None,
    )
