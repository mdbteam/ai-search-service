# app/main.py
import os
import json
from fastapi import FastAPI, Depends, HTTPException, status
from typing import List
import pyodbc
from dotenv import load_dotenv # Mover al principio
load_dotenv(override=True) # Cargar antes de todo
import google.generativeai as genai
from datetime import date

from app.database import get_db_connection
from app.models import SearchQuery, SearchResponse, SearchFilters, UserInDB
from app.auth_utils import get_current_user_from_cookie_or_token

app = FastAPI(
    title="Servicio de Búsqueda IA - Chambee",
    description="Procesa búsquedas en lenguaje natural usando Google Gemini.",
    version="1.0.0"
)

# --- Google AI Configuration ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY: raise RuntimeError("GOOGLE_API_KEY no está configurada.")
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('models/gemini-flash-latest') # Modelo confirmado

# --- PROMPT MAESTRO ---
PROMPT_MAESTRO = """
Eres el Asistente Chambee. Tu misión es ayudar a los usuarios a encontrar el profesional ideal en nuestra red de forma conversacional. Eres amable, servicial y profesional.
Las categorías de servicio (oficios) disponibles son: Gasfitería, Electricidad, Carpintería, Pintura, Jardinería, Limpieza.
Los filtros que puedes extraer son: 'oficio', 'genero' (hombre/mujer), 'puntuacion_minima' (1 a 5), 'min_trabajos_realizados', 'edad_minima', 'edad_maxima'.

Analiza la consulta del usuario. Tu respuesta DEBE ser un único bloque JSON válido con 'respuesta_asistente' (string) y 'filtros' (objeto JSON o {{}}).

REGLAS IMPORTANTES:
1.  **Emergencias/Ilegal/Inapropiado:** Si aplica, RECHAZA amablemente, explica por qué y devuelve 'filtros' como {{}}.
2.  **Consultas Comunes:** Si es un problema común ("se cortó la luz", "se tapó el baño", "olor a gas"), haz preguntas de diagnóstico ANTES de sugerir un oficio. Tu 'respuesta_asistente' debe ser la pregunta y 'filtros' debe ser {{}}.
3.  **Claridad:** Si es vago y no aplica a lo anterior, PIDE ACLARACIÓN y devuelve 'filtros' como {{}}.
4.  **Extracción Simple (Solo Oficio):** Si la solicitud es clara y solo puedes identificar el 'oficio', tu 'respuesta_asistente' debe ser simple, confirmando el oficio. Ejemplo: "Entendido, parece que necesitas un Carpintero." Extrae el filtro 'oficio'.
5.  **Extracción Compleja:** Si identificas el 'oficio' Y otros filtros, genera una 'respuesta_asistente' confirmando la búsqueda específica (ej: "¡Claro! Buscando electricistas hombres mayores de 40...").
6.  **Recomendaciones:** Si pide "el mejor" o "recomendación", añade 'puntuacion_minima': 4 a los filtros.

Consulta del usuario: "{user_query}"

Tu respuesta JSON:
"""
# --- FIN PROMPT MAESTRO ---

@app.get("/", tags=["Status"])
def root():
    return {"message": "AI Search Service funcionando 🚀"}

@app.post("/ai-search", response_model=SearchResponse, tags=["Búsqueda IA"])
def ai_search( # Síncrono
    search_query: SearchQuery,
    # current_user: UserInDB = Depends(get_current_user_from_cookie_or_token),
    conn: pyodbc.Connection = Depends(get_db_connection)
):
    respuesta_asistente = "Lo siento, hubo un problema al procesar tu solicitud."
    filtros_dict = {}
    filtros = SearchFilters()
    resultados_finales = []

    try:
        # 1. Llamada a Gemini
        prompt_completo = PROMPT_MAESTRO.format(user_query=search_query.query)
        response = model.generate_content(prompt_completo)
        raw_json_response = response.text.strip().replace('```json', '').replace('```', '')
        try:
            ai_response = json.loads(raw_json_response)
            respuesta_asistente = ai_response.get("respuesta_asistente", respuesta_asistente)
            filtros_dict = ai_response.get("filtros", {})
            filtros = SearchFilters(**filtros_dict)
        except Exception as json_err:
             print(f"Error parseando JSON: {json_err} - Respuesta: {raw_json_response}")
             respuesta_asistente = "Tuve problemas interpretando la respuesta. ¿Puedes reformular?"
             filtros_dict = {}
             filtros = SearchFilters()
             return SearchResponse(respuesta_asistente=respuesta_asistente, filtros_aplicados=filtros, resultados=[])

    except Exception as e:
        print(f"Error llamando a Gemini API: {e}")
        respuesta_asistente = "No pude contactar al asistente en este momento. Intenta más tarde."
        return SearchResponse(respuesta_asistente=respuesta_asistente, filtros_aplicados=filtros, resultados=[])

    if not filtros_dict:
        return SearchResponse(respuesta_asistente=respuesta_asistente, filtros_aplicados=filtros, resultados=[])

    # 2. Búsqueda en Base de Datos (Consulta Completa Restaurada)
    # --- CONSULTA SQL COMPLETA ---
    sql_query = """
        SELECT DISTINCT
            u.id_usuario, u.nombres, u.primer_apellido, u.foto_url,
            p.resumen_profesional,
            (SELECT STRING_AGG(o.nombre_oficio, ', ') FROM Oficio o WHERE o.id_usuario = u.id_usuario) AS oficios,
            ISNULL(AVG(CAST(v.puntaje AS FLOAT)), 0) AS puntuacion_promedio
        FROM Usuarios u
        LEFT JOIN Perfil p ON u.id_usuario = p.id_usuario
        LEFT JOIN Oficio ofi ON u.id_usuario = ofi.id_usuario
        LEFT JOIN Valoraciones v ON u.id_evaluado = u.id_usuario AND v.rol_autor = 'cliente' -- id_evaluado
        WHERE u.id_rol IN (2, 3) AND u.estado = 'activo'
    """
    params = []
    # Aplicamos filtros dinámicamente
    if filtros.oficio: sql_query += " AND ofi.nombre_oficio LIKE ?"; params.append(f"%{filtros.oficio}%")
    if filtros.genero: sql_query += " AND u.genero = ?"; params.append(filtros.genero)
    if filtros.min_trabajos_realizados: sql_query += " AND u.trabajos_realizados >= ?"; params.append(filtros.min_trabajos_realizados)
    if filtros.edad_minima or filtros.edad_maxima:
        sql_query += " AND u.fecha_nacimiento IS NOT NULL"
        if filtros.edad_minima: sql_query += " AND DATEDIFF(YEAR, u.fecha_nacimiento, GETDATE()) >= ?"; params.append(filtros.edad_minima)
        if filtros.edad_maxima: sql_query += " AND DATEDIFF(YEAR, u.fecha_nacimiento, GETDATE()) <= ?"; params.append(filtros.edad_maxima)

    sql_query += " GROUP BY u.id_usuario, u.nombres, u.primer_apellido, u.foto_url, p.resumen_profesional"
    if filtros.puntuacion_minima: sql_query += " HAVING ISNULL(AVG(CAST(v.puntaje AS FLOAT)), 0) >= ?"; params.append(filtros.puntuacion_minima)
    sql_query += " ORDER BY puntuacion_promedio DESC;"
    # --- FIN CONSULTA SQL COMPLETA ---

    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql_query, tuple(params))
        results_db = cursor.fetchall()
        for row in results_db:
            resultados_finales.append({
                "id": str(row.id_usuario),
                "nombres": row.nombres,
                "primer_apellido": row.primer_apellido,
                "foto_url": row.foto_url,
                "oficios": row.oficios.split(', ') if row.oficios else [],
                "resumen": row.resumen_profesional,
                "puntuacion": round(row.puntuacion_promedio, 1) # Puntuación real
            })

        if not resultados_finales and filtros_dict:
            if filtros.oficio and len(filtros_dict) == 1:
                 respuesta_asistente = f"Entendido, necesitas '{filtros.oficio}'. De momento no encuentro a nadie con ese perfil exacto, pero puedes explorar la categoría."
            else:
                 respuesta_asistente = "No encontré prestadores que coincidan con todos tus criterios. ¿Probamos con una búsqueda más general?"

    except pyodbc.Error as e:
        print(f"Database Error during search: {e}")
        respuesta_asistente = "Ups, tuve un problema técnico buscando en nuestra base de datos. Intenta de nuevo."
        resultados_finales = []

    finally:
        if cursor:
            cursor.close()

    return SearchResponse(
        respuesta_asistente=respuesta_asistente,
        filtros_aplicados=filtros,
        resultados=resultados_finales
    )
