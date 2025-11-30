import pyodbc

def conectar():
    try:
        conexion = pyodbc.connect(
            'DRIVER={SQL Server};'
            'SERVER=RIKILAP\\SQLEXPRESS;'  # 👈 tu instancia
            'DATABASE=Salon;'
            'Trusted_Connection=yes;'
        )
        print("✅ Conectado a SQL Server (Windows Authentication)")
        return conexion
    except Exception as e:
        print("❌ Error al conectar a SQL Server:", e)
        return None
