"""Validaciones de formato específicas de Ecuador (sección 29/31): el
sistema está pensado para un hotel operando en Ecuador — cédula y RUC del
Registro Civil/SRI, teléfonos de 10 dígitos, moneda USD (ya default en
ConfiguracionHotel).

Los algoritmos de cédula y RUC son públicos (los mismos que usa cualquier
validador de este tipo en Ecuador); no dependen de ningún servicio externo
ni credenciales — son puro cálculo sobre los dígitos.
"""
from django.core.exceptions import ValidationError

_COEFICIENTES_CEDULA = [2, 1, 2, 1, 2, 1, 2, 1, 2]
_COEFICIENTES_RUC_PUBLICO = [3, 2, 7, 6, 5, 4, 3, 2]
_COEFICIENTES_RUC_PRIVADO = [4, 3, 2, 7, 6, 5, 4, 3, 2]


def _verificador_modulo10(digitos, coeficientes):
    total = 0
    for digito, coef in zip(digitos, coeficientes):
        producto = digito * coef
        if producto > 9:
            producto -= 9
        total += producto
    residuo = total % 10
    return 0 if residuo == 0 else 10 - residuo


def _verificador_modulo11(digitos, coeficientes):
    total = sum(d * c for d, c in zip(digitos, coeficientes))
    residuo = total % 11
    return 0 if residuo == 0 else 11 - residuo


def cedula_valida(cedula):
    """Cédula ecuatoriana: 10 dígitos, provincia 01-24, tercer dígito 0-6
    (persona natural), dígito verificador módulo 10."""
    if not cedula or not cedula.isdigit() or len(cedula) != 10:
        return False
    digitos = [int(d) for d in cedula]
    provincia = int(cedula[:2])
    if provincia < 1 or provincia > 24:
        return False
    if digitos[2] > 6:
        return False
    return _verificador_modulo10(digitos[:9], _COEFICIENTES_CEDULA) == digitos[9]


def ruc_valido(ruc):
    """RUC ecuatoriano: 13 dígitos.
    - Persona natural (3.er dígito 0-6): cédula válida + '001'.
    - Entidad pública (3.er dígito 6... en realidad el dígito exacto es 6):
      módulo 11 propio, termina en '0001'.
    - Sociedad privada (3.er dígito 9): módulo 11 propio, terminación
      variable (código de establecimiento, ej. '001', '002')."""
    if not ruc or not ruc.isdigit() or len(ruc) != 13:
        return False
    digitos = [int(d) for d in ruc]
    tercer_digito = digitos[2]

    if tercer_digito < 6:
        return cedula_valida(ruc[:10]) and ruc[10:] == '001'
    if tercer_digito == 6:
        return ruc[9:] == '0001' and _verificador_modulo11(
            digitos[:8], _COEFICIENTES_RUC_PUBLICO,
        ) == digitos[8]
    if tercer_digito == 9:
        return _verificador_modulo11(digitos[:9], _COEFICIENTES_RUC_PRIVADO) == digitos[9]
    return False


def validar_cedula(value):
    if not cedula_valida(value):
        raise ValidationError('Cédula ecuatoriana inválida — revisá el número (10 dígitos).')


def validar_ruc(value):
    if not ruc_valido(value):
        raise ValidationError('RUC ecuatoriano inválido — revisá el número (13 dígitos).')


def validar_telefono_ecuador(value):
    """10 dígitos, empieza en 0 — cubre celular (09XXXXXXXX) y convencional
    (0X XXXXXXX según el código de provincia)."""
    if not value.isdigit() or len(value) != 10 or not value.startswith('0'):
        raise ValidationError('El teléfono debe tener 10 dígitos y empezar con 0 (ej. 0991234567).')
