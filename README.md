# SISTEMA_HOTELERO

Sistema web de gestión hotelera: backend en Django (`hotel_backend/`), base
de datos SQLite (un solo archivo, sin servidor que instalar), y una interfaz
de escritorio en Tkinter (`hotel_backend/interfaz.py`) como alternativa a la
web.

## Instalación

```bash
cd hotel_backend
python -m venv ../Entorno          # o el venv que prefieras
../Entorno/Scripts/activate        # Windows
pip install -r requirements.txt

cp .env.example .env               # completar SECRET_KEY (ver más abajo)
python manage.py migrate           # crea hotel_backend/db.sqlite3
python manage.py setup_roles       # crea los grupos de roles del hotel
python manage.py createsuperuser   # cuenta de Administrador (acceso total)
```

No hace falta instalar ni configurar ningún motor de base de datos aparte:
`migrate` crea el archivo `db.sqlite3` en `hotel_backend/` la primera vez
que se corre (queda fuera de git, cada quien tiene el suyo).

## Roles y permisos

La API (`/api/...`), el admin (`/admin/`) y el dashboard web usan el sistema
de autenticación de Django: todo requiere haber iniciado sesión, y las
acciones de escritura además requieren el permiso del modelo
correspondiente. `setup_roles` crea estos grupos (podés reasignarlos desde
`/admin/auth/user/`, sección Groups):

| Rol | Puede |
|---|---|
| Administrador | todo (usar una cuenta `createsuperuser`) |
| Recepcionista | crear/editar clientes y reservas, generar facturas, ver habitaciones |
| Limpieza | ver y cambiar el estado de habitaciones |
| Mantenimiento | ver y cambiar el estado de habitaciones |
| Gerencia | solo lectura de todo (para reportes) |

## Uso

```bash
python manage.py runserver
```

- `http://127.0.0.1:8000/` — dashboard (pide login).
- `http://127.0.0.1:8000/admin/` — panel de administración.
- `http://127.0.0.1:8000/api/...` — API JSON (requiere sesión iniciada).

```bash
# Interfaz de escritorio alternativa (usa el ORM directo, no pasa por la API)
python interfaz.py
```

## Tests

```bash
python manage.py test
```

Con el motor SQLite, Django usa automáticamente una base en memoria para
los tests — no toca `db.sqlite3`.
