"""Interfaz de escritorio (Tkinter) del sistema de reservas.

Es un script standalone que usa el ORM de Django directamente (requiere que
las dependencias de hotel_backend/requirements.txt estén instaladas y que
hotel_backend/.env exista con credenciales válidas de la base de datos).
"""
import tkinter as tk
from tkinter import messagebox
from datetime import date, timedelta
from reportlab.pdfgen import canvas
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hotel_backend.settings")
django.setup()

# Importar los modelos
from django.core.exceptions import ValidationError
from reservas import services
from reservas.models import Cliente, Habitacion, Reserva, Factura

# Funciones de la interfaz
def verificar_disponibilidad():
    habitacion_numero = entry_habitacion.get()
    try:
        habitacion = Habitacion.objects.get(numero=habitacion_numero)
        if habitacion.es_disponible():
            messagebox.showinfo("Disponible", f"La habitación {habitacion_numero} está disponible.")
        else:
            messagebox.showwarning("Ocupada", f"La habitación {habitacion_numero} no está disponible (Estado: {habitacion.estado}).")
    except Habitacion.DoesNotExist:
        messagebox.showerror("Error", "Número de habitación no válido.")

def generar_factura_pdf(factura):
    """Generar un archivo PDF de la factura."""
    file_name = f"factura_{factura.id}.pdf"
    c = canvas.Canvas(file_name)
    
    c.setFont("Helvetica", 12)
    c.drawString(100, 800, f"Factura #{factura.id}")
    c.drawString(100, 780, f"Cliente: {factura.reserva.cliente.nombre}")
    c.drawString(100, 760, f"Hab. Número: {factura.reserva.habitacion.numero}")
    c.drawString(100, 740, f"Fecha Ingreso: {factura.reserva.fecha_ingreso}")
    c.drawString(100, 720, f"Fecha Salida: {factura.reserva.fecha_salida}")
    c.drawString(100, 700, f"Total: ${factura.total}")
    
    c.save()

def realizar_reserva():
    cedula_cliente = entry_cedula.get()
    habitacion_numero = entry_habitacion.get()
    dias = entry_dias.get()
    
    if not dias.isdigit() or int(dias) <= 0:
        messagebox.showerror("Error", "El número de días debe ser un valor positivo.")
        return
    
    try:
        cliente = Cliente.objects.get(cedula=cedula_cliente)
        habitacion = Habitacion.objects.get(numero=habitacion_numero)
        if not habitacion.es_disponible():
            messagebox.showwarning("Error", f"La habitación {habitacion_numero} no está disponible.")
            return

        fecha_ingreso = date.today()
        fecha_salida = fecha_ingreso + timedelta(days=int(dias))

        # Vía el servicio compartido (reservas/services.py): valida fechas y
        # solapamiento, y deja Habitacion.estado consistente — lo mismo que
        # usan la web y la API.
        reserva = services.crear_reserva(
            cliente=cliente,
            habitacion=habitacion,
            fecha_ingreso=fecha_ingreso,
            fecha_salida=fecha_salida,
        )

        # Crear factura (el total se calcula solo a partir de la reserva)
        factura = Factura.objects.create(reserva=reserva)

        # Generar el PDF de la factura
        generar_factura_pdf(factura)

        messagebox.showinfo("Reserva Exitosa", f"Reserva creada para {cliente.nombre}. Total: ${factura.total}")
    except Cliente.DoesNotExist:
        messagebox.showerror("Error", "Cliente no encontrado.")
    except Habitacion.DoesNotExist:
        messagebox.showerror("Error", "Habitación no encontrada.")
    except ValidationError as exc:
        mensajes = exc.message_dict if hasattr(exc, "message_dict") else {"Error": exc.messages}
        texto = "\n".join(f"{campo}: {', '.join(msgs)}" for campo, msgs in mensajes.items())
        messagebox.showerror("Reserva no válida", texto)

# Configuración de la ventana principal
ventana = tk.Tk()
ventana.title("Sistema de Reservas")
ventana.geometry("450x500")
ventana.configure(bg="#f2f2f2")  # Fondo claro

# Etiqueta de título
tk.Label(
    ventana,
    text="Sistema de Reservas",
    font=("Arial", 20, "bold"),
    bg="#f2f2f2",
    fg="#333"
).pack(pady=10)

# Función para agregar campos de entrada
def agregar_campo(titulo):
    label = tk.Label(ventana, text=titulo, font=("Arial", 12), bg="#f2f2f2", fg="#333")
    label.pack(pady=5)
    entry = tk.Entry(ventana, font=("Arial", 12), bd=2, relief="solid")
    entry.pack(pady=5, ipadx=10, ipady=5)
    return entry

# Entradas para datos
entry_cedula = agregar_campo("Cédula del Cliente:")
entry_habitacion = agregar_campo("Número de Habitación:")
entry_dias = agregar_campo("Número de Días:")

# Función para crear botones estilizados
def crear_boton(titulo, comando, color="#4CAF50", hover="#45a049"):
    def on_enter(event):
        boton.configure(bg=hover)

    def on_leave(event):
        boton.configure(bg=color)

    boton = tk.Button(
        ventana,
        text=titulo,
        command=comando,
        font=("Arial", 12, "bold"),
        bg=color,
        fg="white",
        relief="flat",
        bd=0,
        padx=20,
        pady=10
    )
    boton.pack(pady=10)
    boton.bind("<Enter>", on_enter)
    boton.bind("<Leave>", on_leave)
    return boton

# Botones de acciones
crear_boton("Verificar Disponibilidad", verificar_disponibilidad)
crear_boton("Reservar Habitación", realizar_reserva, color="#007BFF", hover="#0056b3")

# Pie de página
tk.Label(
    ventana,
    text="Hotel Gestión © 2025",
    font=("Arial", 10, "italic"),
    bg="#f2f2f2",
    fg="#666"
).pack(side="bottom", pady=10)

# Ejecutar la ventana principal
ventana.mainloop()




