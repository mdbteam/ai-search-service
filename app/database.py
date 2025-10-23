# app/database.py
import pyodbc
import os
from fastapi import HTTPException, status
from dotenv import load_dotenv

# Cargar .env AQUI para asegurar que se lee antes de definir la variable global
load_dotenv(override=True)

CONNECTION_STRING = os.environ.get("DATABASE_CONNECTION_STRING")
# Ya no imprimimos la conexión aquí

def get_db_connection():
    # Leemos la variable DENTRO por si acaso
    conn_str = os.environ.get("DATABASE_CONNECTION_STRING")
    # Ya no imprimimos la conexión aquí tampoco

    if not conn_str:
        raise HTTPException(status_code=500, detail="Cadena de conexión no configurada.")

    conn = None
    try:
        conn = pyodbc.connect(conn_str, autocommit=False)
        yield conn
    except pyodbc.Error as e:
        # Mantenemos el log de error, pero sin exponer la conexión
        print(f"DEBUG DB (get_connection): ERROR al conectar - {e}")
        raise HTTPException(status_code=503, detail=f"No se pudo conectar a la base de datos.") # Mensaje genérico
    finally:
        if conn:
            conn.close()