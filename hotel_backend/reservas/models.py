from django.db import models
from datetime import date


# Modelo de Cliente
class Cliente(models.Model):
    cedula = models.CharField(max_length=10, primary_key=True)
    nombre = models.CharField(max_length=50)
    apellido = models.CharField(max_length=50)
    telefono = models.CharField(max_length=10)
    correo = models.EmailField()
    direccion = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f'{self.cedula} - {self.nombre} {self.apellido}'


# Modelo de Habitación
class Habitacion(models.Model):
    codigo = models.CharField(max_length=100, primary_key=True)
    numero = models.CharField(max_length=10, unique=True)
    tipo = models.CharField(
        max_length=50,
        choices=[('Sencilla', 'Sencilla'), ('Doble', 'Doble'), ('Suite', 'Suite')],
        default='Sencilla'
    )
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(
        max_length=20,
        choices=[('Disponible', 'Disponible'), ('Ocupada', 'Ocupada'), ('Mantenimiento', 'Mantenimiento')],
        default='Disponible'
    )

    def __str__(self):
        return f'Habitación {self.numero} - {self.tipo}'

    def es_disponible(self):
        return self.estado == 'Disponible'
# Modelo de Reserva
class Reserva(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT)
    habitacion = models.ForeignKey(Habitacion, on_delete=models.CASCADE)
    fecha_ingreso = models.DateField()
    fecha_salida = models.DateField()

    def __str__(self):
        return f'Reserva de {self.cliente.apellido} en habitación {self.habitacion.numero}'

    def duracion(self):
        return (self.fecha_salida - self.fecha_ingreso).days

    def costo(self):
        return self.habitacion.precio * self.duracion()
    


# Modelo de Factura
class Factura(models.Model):
    reserva = models.OneToOneField(Reserva, on_delete=models.CASCADE)
    fecha = models.DateField(default=date.today)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f'Factura de {self.reserva.cliente.apellido} por {self.total}'

    def calcular_total(self):
        return self.reserva.costo()