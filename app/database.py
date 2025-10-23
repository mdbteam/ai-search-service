# app/database.py
import pyodbc
import os
from fastapi import HTTPException, status
from dotenv import load_dotenv # Importar dotenv aquí también

# Cargar .env AQUI para asegurar que se lee antes de definir la variable global
load_dotenv(override=True)

CONNECTION_STRING = os.environ.get("DATABASE_CONNECTION_STRING")
# Puedes dejar esta línea de debug si quieres verificar al iniciar
print(f"DEBUG DB (Global): Usando Connection String: {CONNECTION_STRING}")

def get_db_connection():
    # Leer la variable DENTRO por si acaso, aunque ya debería estar cargada
    conn_str = os.environ.get("DATABASE_CONNECTION_STRING")
    # print(f"DEBUG DB (get_connection): Intentando conectar a SERVER={conn_str.split('SERVER=')[1].split(';')[0] if conn_str else 'N/A'}...") # Debug opcional

    if not conn_str:
        raise HTTPException(status_code=500, detail="Cadena de conexión no configurada.")

    conn = None
    try:
        conn = pyodbc.connect(conn_str, autocommit=False)
        # print(f"DEBUG DB (get_connection): Conexión exitosa a SERVER={conn.getinfo(pyodbc.SQL_SERVER_NAME)};DATABASE={conn.getinfo(pyodbc.SQL_DATABASE_NAME)}") # Debug opcional
        yield conn
    except pyodbc.Error as e:
        # print(f"DEBUG DB (get_connection): ERROR al conectar - {e}") # Debug opcional
        raise HTTPException(status_code=503, detail=f"No se pudo conectar a la base de datos: {e}")
    finally:
        if conn:
            conn.close()