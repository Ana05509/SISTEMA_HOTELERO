from django import forms
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    METODOS_CON_REFERENCIA, METODOS_PAGO, PRIORIDADES_INCIDENCIA, Cliente, ConfiguracionHotel, FechaEspecial,
    Habitacion, Servicio,
)

_INPUT = 'form-control form-control-sm'
_SELECT = 'form-select form-select-sm'


class BusquedaDisponibilidadForm(forms.Form):
    fecha_ingreso = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': _INPUT}))
    fecha_salida = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': _INPUT}))
    huespedes = forms.IntegerField(
        min_value=1, initial=1, required=False, label='N.º de huéspedes',
        widget=forms.NumberInput(attrs={'class': _INPUT, 'style': 'width: 6rem;'}),
    )

    def clean(self):
        limpio = super().clean()
        ingreso, salida = limpio.get('fecha_ingreso'), limpio.get('fecha_salida')
        if ingreso and salida and salida <= ingreso:
            raise forms.ValidationError('La fecha de salida debe ser posterior a la de ingreso.')
        return limpio


class ReservaForm(forms.Form):
    cliente = forms.ModelChoiceField(
        queryset=Cliente.objects.all(), label='Huésped',
        help_text='¿No está en la lista? Creálo primero desde "Huéspedes".',
        widget=forms.Select(attrs={'class': _SELECT}),
    )
    habitacion = forms.ModelChoiceField(queryset=Habitacion.objects.all(), widget=forms.HiddenInput)
    fecha_ingreso = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': _INPUT}))
    fecha_salida = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': _INPUT}))


class ConsumoForm(forms.Form):
    servicio = forms.ModelChoiceField(
        queryset=Servicio.objects.filter(activo=True),
        widget=forms.Select(attrs={'class': _SELECT}),
    )
    cantidad = forms.IntegerField(
        min_value=1, initial=1,
        widget=forms.NumberInput(attrs={'class': _INPUT, 'style': 'width: 6rem;'}),
    )


class PagoForm(forms.Form):
    monto = forms.DecimalField(
        min_value=0.01, max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': _INPUT, 'step': '0.01', 'style': 'width: 8rem;'}),
    )
    metodo = forms.ChoiceField(
        choices=METODOS_PAGO,
        widget=forms.Select(attrs={'class': _SELECT, 'id': 'id_pago_metodo'}),
    )
    referencia = forms.CharField(
        required=False, label='N.º de comprobante / referencia',
        widget=forms.TextInput(attrs={
            'class': _INPUT, 'placeholder': 'Solo transferencia/tarjeta', 'id': 'id_pago_referencia',
        }),
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': _INPUT, 'placeholder': 'Opcional'}),
    )

    def clean(self):
        limpio = super().clean()
        metodo, referencia = limpio.get('metodo'), limpio.get('referencia')
        if metodo in METODOS_CON_REFERENCIA and not referencia:
            self.add_error('referencia', f'Obligatorio para pagos por {metodo}.')
        return limpio


class IncidenciaForm(forms.Form):
    habitacion = forms.ModelChoiceField(
        queryset=Habitacion.objects.all(),
        widget=forms.Select(attrs={'class': _SELECT}),
    )
    descripcion = forms.CharField(
        widget=forms.Textarea(attrs={'class': _INPUT, 'rows': 3}),
    )
    prioridad = forms.ChoiceField(
        choices=PRIORIDADES_INCIDENCIA, initial='Media',
        widget=forms.Select(attrs={'class': _SELECT}),
    )


class ConfiguracionHotelForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionHotel
        fields = [
            'nombre_hotel', 'direccion', 'telefono', 'ruc', 'logo', 'moneda', 'iva_porcentaje',
            'hora_checkin_default', 'hora_checkout_default', 'politica_cancelacion',
        ]
        widgets = {
            'nombre_hotel': forms.TextInput(attrs={'class': _INPUT}),
            'direccion': forms.TextInput(attrs={'class': _INPUT}),
            'telefono': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '0991234567'}),
            'ruc': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '13 dígitos, ej. 1792060346001'}),
            'logo': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
            'moneda': forms.TextInput(attrs={'class': _INPUT, 'style': 'width: 6rem;'}),
            'iva_porcentaje': forms.NumberInput(attrs={'class': _INPUT, 'step': '0.01', 'min': '0', 'style': 'width: 7rem;'}),
            'hora_checkin_default': forms.TimeInput(attrs={'class': _INPUT, 'type': 'time'}),
            'hora_checkout_default': forms.TimeInput(attrs={'class': _INPUT, 'type': 'time'}),
            'politica_cancelacion': forms.Textarea(attrs={'class': _INPUT, 'rows': 3}),
        }


# --- Gestión de usuarios (sección 29): alta/edición de cuentas de staff y
# su rol, sin pasar por el admin de Django. Reservado a superusuarios (ver
# `superuser_required` en views.py) — asignar roles es sensible. ---

class UsuarioCrearForm(forms.Form):
    username = forms.CharField(max_length=150, label='Usuario', widget=forms.TextInput(attrs={'class': _INPUT}))
    first_name = forms.CharField(max_length=150, required=False, label='Nombre', widget=forms.TextInput(attrs={'class': _INPUT}))
    last_name = forms.CharField(max_length=150, required=False, label='Apellido', widget=forms.TextInput(attrs={'class': _INPUT}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': _INPUT}))
    rol = forms.ModelChoiceField(
        queryset=Group.objects.all().order_by('name'), label='Rol',
        widget=forms.Select(attrs={'class': _SELECT}),
    )
    password1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput(attrs={'class': _INPUT}))
    password2 = forms.CharField(label='Confirmar contraseña', widget=forms.PasswordInput(attrs={'class': _INPUT}))

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Ya existe un usuario con ese nombre.')
        return username

    def clean(self):
        limpio = super().clean()
        p1, p2 = limpio.get('password1'), limpio.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Las contraseñas no coinciden.')
        elif p1:
            try:
                validate_password(p1)
            except DjangoValidationError as exc:
                self.add_error('password1', exc)
        return limpio


class UsuarioEditarForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label='Nombre', widget=forms.TextInput(attrs={'class': _INPUT}))
    last_name = forms.CharField(max_length=150, required=False, label='Apellido', widget=forms.TextInput(attrs={'class': _INPUT}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': _INPUT}))
    rol = forms.ModelChoiceField(
        queryset=Group.objects.all().order_by('name'), required=False, label='Rol',
        widget=forms.Select(attrs={'class': _SELECT}),
    )
    activo = forms.BooleanField(
        required=False, label='Usuario activo',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )


class CambiarPasswordForm(forms.Form):
    password1 = forms.CharField(label='Nueva contraseña', widget=forms.PasswordInput(attrs={'class': _INPUT}))
    password2 = forms.CharField(label='Confirmar nueva contraseña', widget=forms.PasswordInput(attrs={'class': _INPUT}))

    def clean(self):
        limpio = super().clean()
        p1, p2 = limpio.get('password1'), limpio.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Las contraseñas no coinciden.')
        elif p1:
            try:
                validate_password(p1)
            except DjangoValidationError as exc:
                self.add_error('password1', exc)
        return limpio


class FechaEspecialForm(forms.ModelForm):
    class Meta:
        model = FechaEspecial
        fields = ['nombre', 'fecha_inicio', 'fecha_fin', 'porcentaje_ajuste', 'tema', 'activo', 'descripcion']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': _INPUT, 'placeholder': 'Ej. Navidad'}),
            'fecha_inicio': forms.DateInput(attrs={'class': _INPUT, 'type': 'date'}),
            'fecha_fin': forms.DateInput(attrs={'class': _INPUT, 'type': 'date'}),
            'porcentaje_ajuste': forms.NumberInput(attrs={
                'class': _INPUT, 'step': '0.01', 'placeholder': '-20 = 20% off',
            }),
            'tema': forms.Select(attrs={'class': _SELECT}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'descripcion': forms.Textarea(attrs={'class': _INPUT, 'rows': 2}),
        }

    def clean(self):
        limpio = super().clean()
        inicio, fin = limpio.get('fecha_inicio'), limpio.get('fecha_fin')
        if inicio and fin and fin < inicio:
            self.add_error('fecha_fin', 'La fecha de fin no puede ser anterior a la de inicio.')
        return limpio
