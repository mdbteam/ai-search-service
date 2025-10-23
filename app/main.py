# app/main.py
import os
import json
from fastapi import FastAPI, Depends, HTTPException, status
from typing import List, Optional, Dict, Any  # Añadir Dict, Any
import pyodbc
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import date

load_dotenv(override=True)

from app.database import get_db_connection
# Asegúrate que el modelo ChatMessage esté definido así en models.py:
# class ChatMessage(BaseModel):
#    role: str
#    parts: List[Dict[str, str]] # Lista de diccionarios {'text': 'mensaje'}
from app.models import SearchQuery, SearchResponse, SearchFilters, UserInDB, ChatMessage
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
model = genai.GenerativeModel('models/gemini-flash-latest')  # Modelo confirmado

# --- INSTRUCCIONES INICIALES (Separadas del prompt principal) ---
SYSTEM_INSTRUCTIONS = """
Eres el Asistente Chambee. Tu misión es ayudar a los usuarios a encontrar el profesional ideal en nuestra red de forma conversacional. Eres amable, servicial y profesional.
Las categorías de servicio (oficios) disponibles son: Gasfitería, Electricidad, Carpintería, Pintura, Jardinería, Limpieza.
Los filtros que puedes extraer son: 'oficio', 'genero' (hombre/mujer), 'puntuacion_minima' (1 a 5), 'min_trabajos_realizados', 'edad_minima', 'edad_maxima'.

Tu respuesta DEBE ser un único bloque de CÓDIGO JSON válido. Utiliza **SIEMPRE comillas dobles (")** para TODAS las claves y TODOS los valores de tipo string dentro del JSON. NO uses comillas simples ('). El JSON debe tener exactamente dos claves: "respuesta_asistente" (string) y "filtros" (objeto JSON o {{}}).

EJEMPLO DE RESPUESTA JSON VÁLIDA:
{
  "respuesta_asistente": "¡Entendido! Buscando carpinteros con más de 4 estrellas...",
  "filtros": {
    "oficio": "Carpintería",
    "puntuacion_minima": 4
  }
}

REGLAS IMPORTANTES:
1.  **Emergencias/Ilegal/Inapropiado:** Si aplica, RECHAZA amablemente, explica por qué y devuelve "filtros" como {{}}.
2.  **Consultas Comunes:** Si es un problema común ("se cortó la luz", etc.), haz preguntas de diagnóstico ANTES de sugerir un oficio. Tu 'respuesta_asistente' debe ser la pregunta y "filtros" debe ser {{}}.
3.  **Claridad:** Si es vago, PIDE ACLARACIÓN y devuelve "filtros" como {{}}.
4.  **Extracción/Combinación:** Utiliza el historial para combinar filtros. Si el usuario añade un filtro a uno ya existente, combina ambos. Si cambia un filtro, usa el nuevo. Confirma la búsqueda con los filtros aplicados.
5.  **Recomendaciones:** Si pide "el mejor", "recomendación", o "más de X estrellas", añade "puntuacion_minima" correspondiente.
6.  **Contexto:** Utiliza el historial de la conversación si está disponible para entender mejor la solicitud actual.

Tu respuesta JSON (¡RECUERDA USAR SOLO COMILLAS DOBLES!"):
"""


# --- FIN INSTRUCCIONES ---

@app.get("/", tags=["Status"])
def root():
    return {"message": "AI Search Service funcionando 🚀"}


@app.post("/ai-search", response_model=SearchResponse, tags=["Búsqueda IA"])
def ai_search(  # Síncrono
        search_query: SearchQuery,
        conn: pyodbc.Connection = Depends(get_db_connection)
):
    respuesta_asistente = "Lo siento, hubo un problema al procesar tu solicitud."
    filtros_dict = {}
    filtros = SearchFilters()
    resultados_finales = []

    # --- CONSTRUCCIÓN DE CONTEXTO ORDENADO ---
    gemini_contents = []
    # 1. Si NO hay historial, enviamos las instrucciones iniciales
    if not search_query.history:
        gemini_contents.append({'role': 'user', 'parts': [{'text': SYSTEM_INSTRUCTIONS}]})
        gemini_contents.append({'role': 'model', 'parts': [{'text': "¡Entendido! ¿En qué puedo ayudarte hoy?"}]})
    else:
        # 2. Si SÍ hay historial, lo añadimos
        for msg in search_query.history:
            # Aseguramos formato correcto parts: [{'text': ...}]
            parts_formatted = [{'text': part.get('text', '')} for part in msg.parts if part.get('text')]
            if parts_formatted:
                gemini_contents.append({'role': msg.role, 'parts': parts_formatted})

    # 3. Añadimos SIEMPRE la nueva consulta del usuario al final
    gemini_contents.append({'role': 'user', 'parts': [{'text': search_query.query}]})
    # --- FIN CONSTRUCCIÓN DE CONTEXTO ---

    try:
        # 4. Llamada SÍNCRONA a Gemini con la secuencia completa
        response = model.generate_content(gemini_contents)

        raw_json_response = response.text.strip().replace('```json', '').replace('```', '')
        try:
            ai_response = json.loads(raw_json_response)
            respuesta_asistente = ai_response.get("respuesta_asistente", "No pude procesar eso...")
            filtros_dict = ai_response.get("filtros", {})
            filtros = SearchFilters(**filtros_dict)
        except Exception as json_err:
            print(f"Error parseando JSON: {json_err} - Respuesta: {raw_json_response}")
            respuesta_asistente = "Tuve problemas interpretando la respuesta..."
            filtros_dict = {}
            filtros = SearchFilters()

    except Exception as e:
        print(f"Error llamando a Gemini API: {e}")
        respuesta_asistente = "No pude contactar al asistente..."
        filtros_dict = {}
        filtros = SearchFilters()
        # Construimos historial de error para devolver
        final_history_error = []
        if search_query.history:
            for msg in search_query.history:
                parts_dict = [{'text': part.get('text', '')} for part in msg.parts]
                if parts_dict: final_history_error.append(ChatMessage(role=msg.role, parts=parts_dict))
        final_history_error.append(ChatMessage(role="user", parts=[{'text': search_query.query}]))
        final_history_error.append(ChatMessage(role="model", parts=[{'text': respuesta_asistente}]))
        return SearchResponse(respuesta_asistente=respuesta_asistente, filtros_aplicados=filtros, resultados=[],
                              history=final_history_error)

    # Construimos el historial final para devolver al frontend
    final_history = []
    # Empezamos con el historial que recibimos (si existe)
    if search_query.history:
        for msg in search_query.history:
            # Aseguramos formato correcto parts: [{'text': ...}]
            parts_dict = [{'text': part.get('text', '')} for part in msg.parts if part.get('text')]
            if parts_dict: final_history.append(ChatMessage(role=msg.role, parts=parts_dict))
    # Añadimos la última interacción
    final_history.append(ChatMessage(role="user", parts=[{'text': search_query.query}]))
    final_history.append(ChatMessage(role="model", parts=[{'text': respuesta_asistente}]))

    # Si no hay filtros (error, aclaración, rechazo), devolvemos solo respuesta e historial actualizado
    if not filtros_dict:
        return SearchResponse(
            respuesta_asistente=respuesta_asistente,
            filtros_aplicados=filtros,
            resultados=[],
            history=final_history
        )

    # 2. Búsqueda en Base de Datos
    # ... (El código de la búsqueda SQL es el mismo que funcionaba con la CTE) ...
    sql_query = """
        WITH AvgValoraciones AS (SELECT id_evaluado, AVG(CAST(puntaje AS FLOAT)) AS puntuacion_promedio FROM Valoraciones WHERE rol_autor = 'cliente' GROUP BY id_evaluado)
        SELECT DISTINCT u.id_usuario, u.nombres, u.primer_apellido, u.foto_url, p.resumen_profesional,
            (SELECT STRING_AGG(o.nombre_oficio, ', ') FROM Oficio o WHERE o.id_usuario = u.id_usuario) AS oficios,
            ISNULL(avg_v.puntuacion_promedio, 0) AS puntuacion_promedio
        FROM Usuarios u
        LEFT JOIN Perfil p ON u.id_usuario = p.id_usuario
        LEFT JOIN Oficio ofi ON u.id_usuario = ofi.id_usuario
        LEFT JOIN AvgValoraciones avg_v ON u.id_usuario = avg_v.id_evaluado
        WHERE u.id_rol IN (2, 3) AND u.estado = 'activo'
    """
    params = []
    if filtros.oficio: sql_query += " AND ofi.nombre_oficio LIKE ?"; params.append(f"%{filtros.oficio}%")
    if filtros.genero: sql_query += " AND u.genero = ?"; params.append(filtros.genero)
    if filtros.min_trabajos_realizados: sql_query += " AND u.trabajos_realizados >= ?"; params.append(
        filtros.min_trabajos_realizados)
    if filtros.edad_minima or filtros.edad_maxima:
        sql_query += " AND u.fecha_nacimiento IS NOT NULL"
        if filtros.edad_minima: sql_query += " AND DATEDIFF(YEAR, u.fecha_nacimiento, GETDATE()) >= ?"; params.append(
            filtros.edad_minima)
        if filtros.edad_maxima: sql_query += " AND DATEDIFF(YEAR, u.fecha_nacimiento, GETDATE()) <= ?"; params.append(
            filtros.edad_maxima)
    if filtros.puntuacion_minima: sql_query += " AND ISNULL(avg_v.puntuacion_promedio, 0) >= ?"; params.append(
        filtros.puntuacion_minima)
    sql_query += " ORDER BY puntuacion_promedio DESC;"

    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql_query, tuple(params))
        results_db = cursor.fetchall()
        for row in results_db:
            resultados_finales.append(
                {"id": str(row.id_usuario), "nombres": row.nombres, "primer_apellido": row.primer_apellido,
                 "foto_url": row.foto_url, "oficios": row.oficios.split(', ') if row.oficios else [],
                 "resumen": row.resumen_profesional, "puntuacion": round(row.puntuacion_promedio, 1)})
        if not resultados_finales and filtros_dict:
            if filtros.oficio and len(filtros_dict) == 1:
                respuesta_asistente = f"Entendido, necesitas '{filtros.oficio}'. De momento no encuentro a nadie, pero puedes explorar la categoría."
            else:
                respuesta_asistente = "No encontré prestadores con esos criterios. ¿Probamos algo más general?"
            final_history[-1] = ChatMessage(role="model",
                                            parts=[{'text': respuesta_asistente}])  # Actualiza el último mensaje

    except pyodbc.Error as e:
        print(f"Database Error during search: {e}")
        respuesta_asistente = "Ups, tuve un problema técnico buscando en la base de datos."
        resultados_finales = []
        final_history[-1] = ChatMessage(role="model",
                                        parts=[{'text': respuesta_asistente}])  # Actualiza el último mensaje
    finally:
        if cursor: cursor.close()

    return SearchResponse(
        respuesta_asistente=respuesta_asistente,
        filtros_aplicados=filtros,
        resultados=resultados_finales,
        history=final_history  # Devolvemos el historial actualizado
    )