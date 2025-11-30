# Importaciones necesarias de Flask y utilidades
from flask import Flask, render_template, request, redirect, url_for, session, g
import pyodbc
from datetime import date, datetime, time, timedelta
import string
import random

app = Flask(__name__)
# Es CRÍTICO que esta clave sea estable para que las sesiones funcionen
app.secret_key = 'clave_secreta_rossy_2025'

# -------------------------------------------------------------------
# --- MAPPING: ASOCIACIÓN DE ROL CON ENDPOINT (FUNCIÓN) CORRECTO ---
# -------------------------------------------------------------------

ROLE_ENDPOINT_MAP = {
    'Administradora': 'dashboard_admin',
    'Dueña': 'dashboard_admin',
    'Recepcionista': 'agenda_recepcion',
    'Estilista': 'vista_estilista',
    'Cliente': 'perfil_cliente'
}


# -------------------------------------------------------------------
# --- INYECCIÓN GLOBAL DE VARIABLES ---
# -------------------------------------------------------------------

@app.context_processor
def inject_global_vars():
    """Hace la variable 'app' accesible en todos los templates de Jinja2."""
    return dict(app=app)


# -------------------------------------------------------------------
# --- 1. CONFIGURACIÓN DE CONEXIÓN A SQL SERVER ---
# -------------------------------------------------------------------

conn_str = (
    r'DRIVER={ODBC Driver 17 for SQL Server};'
    r'SERVER=RIKILAP\SQLEXPRESS;'  # <--- TU SERVIDOR
    r'DATABASE=ROSSY_SALON;'  # <--- TU BD
    r'Trusted_Connection=yes;'
)


def obtener_conexion():
    """Establece la conexión a la base de datos, reutilizando la existente en 'g'."""
    try:
        # Usar getattr/setattr para almacenar la conexión en g y reutilizarla
        conn = getattr(g, '_database', None)
        if conn is None:
            conn = g._database = pyodbc.connect(conn_str)
        return conn
    except Exception as e:
        print(f"ERROR DE CONEXIÓN CON SQL SERVER: {e}")
        return None


# Cierra la conexión al finalizar la solicitud
@app.teardown_appcontext
def close_connection(exception):
    """Cierra la conexión a la base de datos al finalizar la solicitud."""
    conn = getattr(g, '_database', None)
    if conn is not None:
        conn.close()


def row_to_list(rows):
    """
    Convierte una lista de objetos pyodbc.Row a una lista de listas.
    Esto es CRÍTICO para la serialización en la sesión y para el manejo en Jinja2.
    """
    return [list(row) for row in rows]


# -------------------------------------------------------------------
# --- 3. FUNCIONES DE UTILIDAD DE SEGURIDAD ---
# -------------------------------------------------------------------

def generar_contrasena(longitud=8):
    """Genera una contraseña alfanumérica segura para la columna 'contraseña'."""
    caracteres = string.ascii_letters + string.digits  # No incluimos puntuación para simplificar
    return ''.join(random.choice(caracteres) for i in range(longitud))


# -------------------------------------------------------------------
# --- 4. RUTAS DE AUTENTICACIÓN Y REGISTRO PÚBLICO ---
# -------------------------------------------------------------------

@app.route('/')
def index():
    """Muestra el login al inicio o redirige si hay sesión activa."""
    if 'rol' in session:
        rol = session["rol"]
        endpoint = ROLE_ENDPOINT_MAP.get(rol)
        if endpoint:
            return redirect(url_for(endpoint))

        session.clear()
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/register')
def vista_registro():
    """Muestra la interfaz de registro (register.html)."""
    return render_template('register.html')


@app.route('/register', methods=['POST'])
def register():
    """Procesa el formulario, registra un nuevo CLIENTE y asigna una contraseña automática."""
    conn = obtener_conexion()
    if conn is None: return render_template('register.html', error="Error: No se pudo conectar con la BD.")

    try:
        cursor = conn.cursor()

        nombre_completo = request.form.get('nombre') + " " + request.form.get('apellido')
        telefono = request.form.get('telefono')
        correo = request.form.get('correo')

        # 1. GENERAR CONTRASEÑA SEGURA
        nueva_contrasena = generar_contrasena()

        # 2. INSERTAR EL NUEVO CLIENTE (Incluyendo el nuevo campo 'contraseña')
        insert_query = """
        INSERT INTO CLIENTE (Nombre, Telefono, Correo, FechaRegistro, contraseña) 
        VALUES (?, ?, ?, ?, ?)
        """
        # IMPORTANTE: Estamos asumiendo que el nombre de tu columna es 'contraseña'
        cursor.execute(insert_query, nombre_completo, telefono, correo, date.today(), nueva_contrasena)

        # 3. OBTENER EL ID DEL CLIENTE RECIÉN CREADO (CRÍTICO para agendar_cita)
        select_id_query = "SELECT IDCliente FROM CLIENTE WHERE Correo = ? AND Telefono = ?"
        cursor.execute(select_id_query, correo, telefono)
        id_cliente = cursor.fetchone()[0]

        conn.commit()

        # 4. ESTABLECER SESIÓN COMPLETA
        session['rol'] = 'Cliente'
        session['nombre'] = nombre_completo
        session['id_usuario'] = id_cliente

        # 5. REDIRIGIR, MOSTRANDO LA CONTRASEÑA GENERADA
        return redirect(url_for('perfil_cliente',
                                success=f"Registro exitoso. Tu clave de acceso es: {nueva_contrasena}. Por favor, anótala."))

    except pyodbc.IntegrityError:
        return render_template('register.html', error="El correo o teléfono ya están registrados.")
    except Exception as e:
        return render_template('register.html', error=f"Error al registrar: {e}")


@app.route('/login', methods=['POST'])
def login():
    """
    Procesa el login, guardando el ID de usuario y redirigiendo según el Rol.
    Ahora, para el Cliente, usa la columna 'contraseña' para la verificación.
    """
    correo = request.form.get('correo')
    password = request.form.get('password')  # Clave ingresada por el usuario (cliente, estilista, etc.)
    conn = obtener_conexion()
    if conn is None: return render_template('login.html', error="Error de BD.")

    try:
        cursor = conn.cursor()
        nombre_db = None
        rol_db = None
        id_db = None
        usuario = None

        # 1. CHECK SYSTEM ROLES (Administradora/Recepcionista)
        if correo == 'admin@rossysalon.com' and password == '12345':
            rol_db = "Administradora"
            nombre_db = "Dueña Rossy"
            id_db = 0
        elif correo == 'recepcion@rossysalon.com' and password == '123':
            rol_db = "Recepcionista"
            nombre_db = "Recepción"
            id_db = 0

        # 2. IF NOT A SYSTEM ROLE, CHECK DATABASE USERS
        if not rol_db:

            # A. Check Cliente (usando Correo y la columna 'contraseña')
            query_cliente = "SELECT IDCliente, Nombre, 'Cliente' AS Rol FROM CLIENTE WHERE Correo = ? AND contraseña = ?"
            cursor.execute(query_cliente, (correo, password))
            usuario = cursor.fetchone()

            if usuario is not None:
                id_db, nombre_db, rol_db = usuario
            else:
                # B. Check Estilista (asumiendo que sigue usando Correo y Telefono/Contraseña de Estilista)
                # Si los Estilistas también usan la nueva columna 'contraseña', adapta esta consulta:
                # query_estilista = "SELECT IDEstilista, Nombre, 'Estilista' AS Rol FROM ESTILISTA WHERE Correo = ? AND contraseña = ?"
                query_estilista = "SELECT IDEstilista, Nombre, 'Estilista' AS Rol FROM ESTILISTA WHERE Correo = ? AND Telefono = ?"
                cursor.execute(query_estilista, (correo, password))
                usuario = cursor.fetchone()

                if usuario is not None:
                    id_db, nombre_db, rol_db = usuario

        # 3. FINAL VERIFICATION AND SESSION START
        if rol_db is None:
            return render_template('login.html', error="Credenciales incorrectas o usuario no registrado.")

        # Establecer la sesión
        session['rol'] = rol_db
        session['nombre'] = nombre_db
        if id_db is not None:
            session['id_usuario'] = id_db

        # 4. REDIRECTION
        endpoint = ROLE_ENDPOINT_MAP.get(rol_db)
        if endpoint:
            return redirect(url_for(endpoint))
        else:
            return render_template('login.html', error="Rol de usuario no reconocido en el sistema.")

    except pyodbc.Error as ex:
        return render_template('login.html', error=f"Error en la BD: {ex}")


@app.route('/agendar_cita', methods=['POST'])
def agendar_cita():
    """Valida la disponibilidad de horario y registra la cita (desde el Cliente)."""
    # 1. Verificar sesión de cliente
    if session.get('rol') != 'Cliente' or not session.get('id_usuario'):
        return redirect(url_for('index'))

    # 2. Obtener datos del formulario y el ID de sesión
    id_cliente = session.get('id_usuario')
    id_servicio = request.form.get('id_servicio')
    id_estilista = request.form.get('id_estilista')
    fecha = request.form.get('fecha')
    hora = request.form.get('hora')

    # Validación: El estilista no puede ser "Cualquiera" (valor "")
    if id_estilista == "":
        return redirect(url_for('perfil_cliente', error="Por favor, selecciona un estilista específico para agendar."))

    if not all([id_cliente, id_servicio, id_estilista, fecha, hora]):
        return redirect(url_for('perfil_cliente', error="Faltan datos de la cita."))

    conn = obtener_conexion()
    if conn is None:
        return redirect(url_for('perfil_cliente', error="Error de conexión con la BD."))

    try:
        cursor = conn.cursor()

        # 3. LÓGICA DE VERIFICACIÓN DE CONFLICTO (CÓDIGO ANTI-CHOQUE)
        conflict_query = """
        SELECT COUNT(*) 
        FROM CITA 
        WHERE IDEstilista = ? 
          AND Fecha = ? 
          AND Hora = ?
          AND Estado IN ('Pendiente', 'Realizada') 
        """
        cursor.execute(conflict_query, int(id_estilista), fecha, hora)

        if cursor.fetchone()[0] > 0:
            return redirect(url_for('perfil_cliente', error="El estilista ya tiene una cita agendada a esa hora."))

        # 4. REGISTRAR CITA
        insert_query = """
        INSERT INTO CITA (IDCliente, IDEstilista, IDServicio, Fecha, Hora, Estado)
        VALUES (?, ?, ?, ?, ?, 'Pendiente')
        """
        cursor.execute(insert_query, id_cliente, int(id_estilista), int(id_servicio), fecha, hora)
        conn.commit()

        # 5. Éxito
        return redirect(url_for('perfil_cliente', success="Cita agendada con éxito."))

    except Exception as e:
        return redirect(url_for('perfil_cliente', error=f"Error al agendar: {e}"))


# -------------------------------------------------------------------
# --- 5. RUTAS DE RECEPCIONISTA ---
# -------------------------------------------------------------------

@app.route('/recepcion')
def agenda_recepcion():
    """Ruta para la Recepcionista: Muestra la agenda del día, servicios y estilistas."""
    if session.get('rol') == 'Recepcionista':
        conn = obtener_conexion()
        citas_hoy = []
        servicios = []
        estilistas = []
        fecha_hoy = date.today().strftime('%Y-%m-%d')
        horas_disponibles = app.config.get('HOURS')

        if conn:
            try:
                cursor = conn.cursor()

                # 1. Agenda del Día
                agenda_query = """
                SELECT C.IDCita, C.Hora, C.Estado, CL.Nombre AS Cliente, S.NombreServicio, E.Nombre AS Estilista
                FROM CITA C
                JOIN CLIENTE CL ON C.IDCliente = CL.IDCliente
                JOIN SERVICIO S ON C.IDServicio = S.IDServicio
                JOIN ESTILISTA E ON C.IDEstilista = E.IDEstilista
                WHERE C.Fecha = ?
                ORDER BY C.Hora
                """
                cursor.execute(agenda_query, fecha_hoy)
                citas_hoy = row_to_list(cursor.fetchall())

                # 2. Obtener Servicios (para el formulario de agendamiento)
                query_servicios = "SELECT IDServicio, NombreServicio FROM SERVICIO ORDER BY NombreServicio"
                cursor.execute(query_servicios)
                servicios = row_to_list(cursor.fetchall())

                # 3. Obtener Estilistas (para el formulario de agendamiento)
                # CORRECCIÓN: Se revierte la consulta para solo obtener 2 valores
                # (IDEstilista, Nombre) para que coincida con el bucle Jinja de 2 variables.
                query_estilistas = "SELECT IDEstilista, Nombre FROM ESTILISTA WHERE Estado = 'Activo' ORDER BY Nombre"
                cursor.execute(query_estilistas)
                estilistas = row_to_list(cursor.fetchall())

            except Exception as e:
                print(f"Error al cargar la agenda o catálogos: {e}")

        # Se eliminan los clientes encontrados de la sesión después de cargarlos
        clientes_encontrados = session.pop('clientes_encontrados', None)
        id_cliente_seleccionado = session.pop('id_cliente_seleccionado', None)
        nombre_cliente_seleccionado = session.pop('nombre_cliente_seleccionado', None)

        return render_template('recepcionista.html',
                               citas_hoy=citas_hoy,
                               fecha_hoy=fecha_hoy,
                               servicios=servicios,
                               estilistas=estilistas,
                               clientes_encontrados=clientes_encontrados,
                               id_cliente_seleccionado=id_cliente_seleccionado,
                               nombre_cliente_seleccionado=nombre_cliente_seleccionado,
                               horas=app.config.get('HOURS'),
                               error=request.args.get('error'),
                               success=request.args.get('success'))
    return redirect(url_for('index'))


@app.route('/registrar_cliente_recepcion', methods=['POST'])
def registrar_cliente_recepcion():
    """Registra un nuevo cliente desde la vista de Recepción y lo selecciona para agendar cita.
       Ahora genera una contraseña."""
    if session.get('rol') != 'Recepcionista':
        return redirect(url_for('index'))

    conn = obtener_conexion()
    if conn is None:
        return redirect(url_for('agenda_recepcion', error="Error: No se pudo conectar con la BD."))

    try:
        cursor = conn.cursor()

        nombre_completo = request.form.get('nombre_registro')
        telefono = request.form.get('telefono_registro')
        correo = request.form.get('correo_registro')

        # 1. GENERAR CONTRASEÑA SEGURA
        nueva_contrasena = generar_contrasena()

        # 2. INSERTAR EL NUEVO CLIENTE (Incluyendo el nuevo campo 'contraseña')
        insert_query = """
        INSERT INTO CLIENTE (Nombre, Telefono, Correo, FechaRegistro, contraseña) 
        VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(insert_query, nombre_completo, telefono, correo, date.today(), nueva_contrasena)

        # 3. OBTENER EL ID Y NOMBRE DEL CLIENTE RECIÉN CREADO
        select_id_query = "SELECT IDCliente, Nombre FROM CLIENTE WHERE Correo = ? AND Teléfono = ?"
        cursor.execute(select_id_query, correo, telefono)
        id_cliente, nombre_cliente = cursor.fetchone()

        conn.commit()

        # 4. SELECCIONAR AUTOMÁTICAMENTE EL CLIENTE PARA AGENDAR LA CITA
        session['id_cliente_seleccionado'] = id_cliente
        session['nombre_cliente_seleccionado'] = nombre_cliente

        return redirect(
            url_for('agenda_recepcion',
                    success=f"Cliente '{nombre_cliente}' registrado y seleccionado. Clave de acceso generada: {nueva_contrasena}. Por favor, anótala."))

    except pyodbc.IntegrityError:
        return redirect(
            url_for('agenda_recepcion', error="Error de registro: El correo o teléfono ya están registrados."))
    except Exception as e:
        return redirect(url_for('agenda_recepcion', error=f"Error al registrar cliente: {e}"))


@app.route('/buscar', methods=['POST'])
def buscar_cliente():
    """
    Busca clientes por nombre o teléfono y guarda los resultados en sesión.
    CRÍTICO: Convierte explícitamente el objeto date/datetime a cadena (string)
             para evitar el error de serialización y el problema de .strftime() en Jinja.
    """
    termino = request.form.get('termino_busqueda')
    conn = obtener_conexion()

    if not termino:
        session['clientes_encontrados'] = []
        return redirect(url_for('agenda_recepcion'))

    if conn:
        try:
            cursor = conn.cursor()
            # Búsqueda por Nombre o Teléfono
            # Columnas: 0=IDCliente, 1=Nombre, 2=Telefono, 3=Correo, 4=FechaRegistro, 5=contraseña
            query = """
            SELECT IDCliente, Nombre, Telefono, Correo, FechaRegistro
            FROM CLIENTE 
            WHERE Nombre LIKE ? OR Teléfono LIKE ?
            """
            termino_busqueda = f'%{termino}%'
            cursor.execute(query, termino_busqueda, termino_busqueda)

            raw_clientes = cursor.fetchall()

            # --- CORRECCIÓN CRÍTICA: SERIALIZACIÓN MANUAL DE LA FECHA ---
            clientes_serializables = []
            for cliente_row in raw_clientes:
                cliente_list = list(cliente_row)

                # Indice 4 es FechaRegistro
                fecha_registro = cliente_list[4]

                # Si es un objeto de fecha (date o datetime), lo convertimos a string.
                if isinstance(fecha_registro, (date, datetime)):
                    cliente_list[4] = fecha_registro.strftime('%Y-%m-%d')

                # Si ya es None o str, se mantiene

                clientes_serializables.append(cliente_list)

            session['clientes_encontrados'] = clientes_serializables
            return redirect(url_for('agenda_recepcion', success=f"{len(clientes_serializables)} clientes encontrados."))

        except Exception as e:
            return redirect(url_for('agenda_recepcion', error=f"Error al buscar clientes: {e}"))

    return redirect(url_for('agenda_recepcion', error="Error de conexión con la BD."))


@app.route('/seleccionar_cliente/<int:id_cliente>/<nombre_cliente>')
def seleccionar_cliente(id_cliente, nombre_cliente):
    """Guarda el cliente seleccionado en sesión para agendar la cita."""
    if session.get('rol') != 'Recepcionista':
        return redirect(url_for('index'))

    session['id_cliente_seleccionado'] = id_cliente
    session['nombre_cliente_seleccionado'] = nombre_cliente
    return redirect(url_for('agenda_recepcion', success=f"Cliente {nombre_cliente} seleccionado para agendar cita."))


@app.route('/agendar_cita_recepcion', methods=['POST'])
def agendar_cita_recepcion():
    """Agrega una cita usando la información del cliente guardada en sesión."""
    if session.get('rol') != 'Recepcionista':
        return redirect(url_for('index'))

    # Se usa .pop() para asegurar que la ID no se quede en sesión si el agendamiento falla
    id_cliente = session.pop('id_cliente_seleccionado', None)

    id_servicio = request.form.get('id_servicio')
    id_estilista = request.form.get('id_estilista')
    fecha = request.form.get('fecha')
    hora = request.form.get('hora')

    if not id_cliente:
        return redirect(url_for('agenda_recepcion', error="Debe seleccionar un cliente primero."))

    if not all([id_servicio, id_estilista, fecha, hora]):
        # Si faltan datos, devolvemos el ID del cliente a la sesión para que no se pierda la selección
        session['id_cliente_seleccionado'] = id_cliente
        return redirect(url_for('agenda_recepcion', error="Faltan datos de la cita."))

    conn = obtener_conexion()
    if conn is None:
        # Si falla la conexión, devolvemos el ID del cliente a la sesión
        session['id_cliente_seleccionado'] = id_cliente
        return redirect(url_for('agenda_recepcion', error="Error de conexión con la BD."))

    try:
        cursor = conn.cursor()

        # 1. VERIFICACIÓN DE CONFLICTO
        conflict_query = """
        SELECT COUNT(*) 
        FROM CITA 
        WHERE IDEstilista = ? 
          AND Fecha = ? 
          AND Hora = ?
          AND Estado IN ('Pendiente', 'Realizada') 
        """
        cursor.execute(conflict_query, id_estilista, fecha, hora)

        if cursor.fetchone()[0] > 0:
            # Si hay conflicto, devolvemos el ID del cliente a la sesión
            session['id_cliente_seleccionado'] = id_cliente
            return redirect(url_for('agenda_recepcion', error="El estilista ya tiene una cita agendada a esa hora."))

        # 2. REGISTRAR CITA
        insert_query = """
        INSERT INTO CITA (IDCliente, IDEstilista, IDServicio, Fecha, Hora, Estado)
        VALUES (?, ?, ?, ?, ?, 'Pendiente')
        """
        cursor.execute(insert_query, id_cliente, id_estilista, id_servicio, fecha, hora)
        conn.commit()

        # Como fue exitoso, no devolvemos el ID a la sesión (se "consume" la selección)
        return redirect(url_for('agenda_recepcion', success="Cita agendada con éxito para el cliente."))

    except Exception as e:
        # En caso de error, devolvemos el ID del cliente a la sesión
        session['id_cliente_seleccionado'] = id_cliente
        return redirect(url_for('agenda_recepcion', error=f"Error al agendar: {e}"))


# -------------------------------------------------------------------
# --- 6. RUTAS DE INTERFAZ POR ROL (OTRAS) ---
# -------------------------------------------------------------------

@app.route('/admin')
def dashboard_admin():
    """Ruta para la Administradora/Dueña."""
    if session.get('rol') in ['Dueña', 'Administradora']:
        return render_template('administradora.html')
    return redirect(url_for('index'))


@app.route('/estilista')
def vista_estilista():
    """Ruta para el Estilista: Muestra su agenda del día."""
    id_estilista = None
    fecha_hoy = date.today().strftime('%Y-%m-%d')
    agenda_hoy = []

    if session.get('rol') == 'Estilista':
        id_estilista_raw = session.get('id_usuario')
        nombre_estilista = session.get('nombre')
        conn = obtener_conexion()

        try:
            id_estilista = int(id_estilista_raw)
        except (ValueError, TypeError):
            print("ERROR FATAL: El ID de estilista en sesión no es un entero válido.")
            return render_template('estilista.html',
                                   error="Error interno: El ID de tu sesión no es válido.",
                                   nombre_estilista="Estilista Desconocido",
                                   fecha_hoy=fecha_hoy,
                                   agenda_hoy=[])

        if conn and id_estilista:
            try:
                cursor = conn.cursor()

                query_agenda = """
                SELECT 
                    C.IDCita, 
                    C.Hora, 
                    CL.Nombre AS Cliente, 
                    S.NombreServicio, 
                    C.Estado
                FROM CITA C
                JOIN CLIENTE CL ON C.IDCliente = CL.IDCliente
                JOIN SERVICIO S ON C.IDServicio = S.IDServicio
                JOIN ESTILISTA E ON C.IDEstilista = E.IDEstilista
                WHERE C.IDEstilista = ? AND C.Fecha = ? 
                ORDER BY C.Hora
                """

                # Ejecutar la consulta
                cursor.execute(query_agenda, id_estilista, fecha_hoy)
                agenda_hoy = row_to_list(cursor.fetchall())

            except Exception as e:
                print(f"Error al cargar la agenda del estilista: {e}")
                return render_template('estilista.html',
                                       error=f"Error en la BD al cargar agenda: {e}",
                                       nombre_estilista=nombre_estilista,
                                       fecha_hoy=fecha_hoy,
                                       agenda_hoy=[])

        return render_template('estilista.html',
                               nombre_estilista=nombre_estilista,
                               fecha_hoy=fecha_hoy,
                               agenda_hoy=agenda_hoy,
                               error=request.args.get('error'),
                               success=request.args.get('success'))
    return redirect(url_for('index'))


@app.route('/actualizar_cita_estilista', methods=['POST'])
def actualizar_cita_estilista():
    """Permite al estilista cambiar el estado de una cita (Realizada/Cancelada)."""
    if session.get('rol') != 'Estilista':
        return redirect(url_for('index'))

    id_cita = request.form.get('id_cita')
    nuevo_estado = request.form.get('nuevo_estado')
    id_estilista_sesion_raw = session.get('id_usuario')

    try:
        id_estilista_sesion = int(id_estilista_sesion_raw)
    except (ValueError, TypeError):
        return redirect(url_for('vista_estilista', error="ID de estilista no válido."))

    if not all([id_cita, nuevo_estado]) or nuevo_estado not in ['Realizada', 'Cancelada']:
        return redirect(url_for('vista_estilista', error="Datos inválidos para actualizar la cita."))

    conn = obtener_conexion()
    if conn is None:
        return redirect(url_for('vista_estilista', error="Error de conexión con la BD."))

    try:
        cursor = conn.cursor()

        # 1. VERIFICAR QUE LA CITA PERTENEZCA AL ESTILISTA (Seguridad)
        check_query = "SELECT IDEstilista FROM CITA WHERE IDCita = ?"
        cursor.execute(check_query, id_cita)
        cita_data = cursor.fetchone()

        if cita_data is None:
            return redirect(url_for('vista_estilista', error="Cita no encontrada."))

        id_estilista_cita = cita_data[0]

        if id_estilista_cita != id_estilista_sesion:
            return redirect(url_for('vista_estilista', error="Acceso denegado. No puedes modificar esta cita."))

        # 2. ACTUALIZAR EL ESTADO
        update_query = "UPDATE CITA SET Estado = ? WHERE IDCita = ?"
        cursor.execute(update_query, nuevo_estado, id_cita)
        conn.commit()

        mensaje = f"Cita {id_cita} actualizada a '{nuevo_estado}' con éxito."
        return redirect(url_for('vista_estilista', success=mensaje))

    except Exception as e:
        return redirect(url_for('vista_estilista', error=f"Error en la BD al actualizar cita: {e}"))


@app.route('/cliente')
def perfil_cliente():
    """Ruta para el Cliente: Muestra sus datos, servicios y estilistas disponibles.
       También carga el historial de citas (citas_cliente)."""
    if session.get('rol') == 'Cliente':
        # 1. Obtener la fecha de hoy para el input date
        fecha_hoy = date.today().strftime('%Y-%m-%d')

        # OBTENEMOS LAS HORAS DISPONIBLES
        horas_disponibles = app.config.get('HOURS')

        servicios = []
        estilistas = []
        citas_cliente = []  # Se inicializa para pasarla al template
        conn = obtener_conexion()
        id_cliente = session.get('id_usuario')

        if conn and id_cliente:
            try:
                cursor = conn.cursor()

                # A. Consulta Citas del Cliente (HISTORIAL)
                query_citas = """
                SELECT C.Fecha, C.Hora, C.Estado, S.NombreServicio, E.Nombre AS Estilista
                FROM CITA C
                JOIN SERVICIO S ON C.IDServicio = S.IDServicio
                JOIN ESTILISTA E ON C.IDEstilista = E.IDEstilista
                WHERE C.IDCliente = ?
                ORDER BY C.Fecha DESC, C.Hora DESC
                """
                cursor.execute(query_citas, id_cliente)
                citas_cliente = row_to_list(cursor.fetchall())
                # NOTA: citas_cliente ya contiene el historial que solicitaste.

                # B. Consulta Servicios
                query_servicios = "SELECT IDServicio, NombreServicio, Precio FROM SERVICIO ORDER BY NombreServicio"
                cursor.execute(query_servicios)
                servicios = row_to_list(cursor.fetchall())

                # C. Consulta Estilistas (Solo los activos)
                # CORRECCIÓN: Se revierte la consulta para solo obtener 2 valores
                # (IDEstilista, Nombre) para que coincida con el bucle Jinja de 2 variables.
                query_estilistas = "SELECT IDEstilista, Nombre FROM ESTILISTA WHERE Estado = 'Activo' ORDER BY Nombre"
                cursor.execute(query_estilistas)
                estilistas = row_to_list(cursor.fetchall())

            except Exception as e:
                print(f"Error al cargar datos del cliente: {e}")
                return render_template('cliente.html',
                                       error=f"Error al cargar datos: {e}",
                                       servicios=[], estilistas=[], citas_cliente=[], fecha_hoy=fecha_hoy)

        return render_template('cliente.html',
                               servicios=servicios,
                               estilistas=estilistas,
                               citas_cliente=citas_cliente,
                               fecha_hoy=fecha_hoy,
                               horas=horas_disponibles,
                               error=request.args.get('error'),
                               success=request.args.get('success'))

    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Cierra la sesión y redirige al index/login."""
    session.clear()
    return redirect(url_for('index'))


# Función de inicialización que crea la lista de horas con intervalos de 1:30
def initialize_hours():
    """
    Genera la lista de horas disponibles en intervalos de 1 hora y 30 minutos (1:30).
    Salón abierto de 9:00 AM a 10:00 PM (22:00). Última cita inicia a las 19:30.
    """
    HOURS = []
    # Usaremos datetime.strptime para crear un objeto datetime inicial a las 9:00 AM
    start_time = datetime.strptime('09:00', '%H:%M')
    # Definimos la hora límite: el último slot es 19:30 que termina a las 21:00.
    end_time_limit = datetime.strptime('21:00', '%H:%M')
    # Definimos el intervalo de 90 minutos (1:30)
    interval = timedelta(minutes=90)

    current_time = start_time
    # La condición de parada asegura que el último slot generado sea 19:30.
    while current_time < end_time_limit:
        # Asegura el formato de 24 horas para la BD (ej. 09:00:00)
        hora_24 = current_time.strftime('%H:%M:%S')
        # Formato AM/PM para mostrar en el select (ej. 9:00 a.m.)
        hora_ampm = current_time.strftime('%I:%M %p').lstrip('0').replace('AM', 'a.m.').replace('PM', 'p.m.')
        HOURS.append((hora_24, hora_ampm))

        current_time += interval

    return HOURS


# Inicializar la lista de horas para que esté disponible en app.config.get('HOURS')
app.config['HOURS'] = initialize_hours()

if __name__ == '__main__':
    app.run(debug=True)