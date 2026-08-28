"""Decoradores de autenticación/autorización para la API JSON de reservas.

No usamos `django.contrib.auth.decorators.login_required` tal cual porque
ese redirige a la página de login (pensado para vistas HTML); una API JSON
debe responder 401/403 en JSON en vez de redirigir.
"""
from functools import wraps

from django.http import JsonResponse


def login_required_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {'error': 'Debes iniciar sesión para usar esta API.'}, status=401
            )
        return view(request, *args, **kwargs)
    return wrapper


def permission_required_api(perm):
    """Exige login Y el permiso Django `perm` (ej: 'reservas.add_reserva').

    Los superusuarios siempre lo cumplen (comportamiento estándar de
    `User.has_perm`).
    """
    def decorador(view):
        @wraps(view)
        @login_required_api
        def wrapper(request, *args, **kwargs):
            if not request.user.has_perm(perm):
                return JsonResponse(
                    {'error': 'No tenés permiso para realizar esta acción.'},
                    status=403,
                )
            return view(request, *args, **kwargs)
        return wrapper
    return decorador
