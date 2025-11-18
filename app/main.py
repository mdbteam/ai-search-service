# ai-search-service/app/main.py
import os
import json
from fastapi import FastAPI, Depends, HTTPException, status, APIRouter
from typing import List, Optional, Dict, Any
import pyodbc
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import date

load_dotenv(override=True)

from app.database import get_db_connection
from app.models import ChatbotQuery, ChatbotResponse, SearchFilters, UserInDB, ChatMessage
from app.auth_utils import get_current_user_from_cookie_or_token

app = FastAPI(
    title="Servicio de Búsqueda IA - Chambee",
    description="Procesa búsquedas en lenguaje natural usando Google Gemini.",
    version="1.0.0"
)

# --- CONFIGURACIÓN CORS ---
origins = [
    "http://localhost",
    "http://localhost:8081",
    "https://auth-service-1-8301.onrender.com",
    "*",  # solo para desarrollo
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,             # Permite enviar credenciales (cookies, auth headers)
    allow_methods=["*"],                # Permite todos los métodos HTTP
    allow_headers=["*"],                # Permite todas las cabeceras
)
# --- CONFIGURACIÓN CORS ---


# Creamos un router con el prefijo /api
router = APIRouter(prefix="/api")

# --- Google AI Configuration ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY: raise RuntimeError("GOOGLE_API_KEY no está configurada.")
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('models/gemini-flash-latest')

# --- PROMPT MAESTRO (ACTUALIZADO PARA EL NUEVO FORMATO) ---
SYSTEM_INSTRUCTIONS = """
Eres el Asistente Chambee. Tu misión es ayudar a los usuarios a encontrar el profesional ideal.
Tu respuesta DEBE ser un único bloque de CÓDIGO JSON válido usando siempre comillas dobles (").
El JSON debe tener tres claves: "respuesta_texto" (string), "intent" (string, ej: 'buscar_prestador', 'aclarar_duda', 'emergencia', 'rechazo') y "data" (un objeto JSON con los filtros extraídos o {{}}).

FILTROS DISPONIBLES: 'oficio' (Gasfitería, Electricidad, Carpintería, Pintura, Jardinería, Limpieza), 'genero' (hombre/mujer), 'puntuacion_minima', 'min_trabajos_realizados', 'edad_minima', 'edad_maxima'.

REGLAS:
1.  **Emergencias/Ilegal/Inapropiado:** Responde apropiadamente, asigna el 'intent' (ej: 'emergencia') y devuelve "data" como {{}}.
2.  **Consultas Comunes (Diagnóstico):** Si es un problema común, haz preguntas de diagnóstico. Asigna 'intent': 'aclarar_duda' y "data" como {{}}.
3.  **Claridad:** Si es vago, PIDE ACLARACIÓN, asigna 'intent': 'aclarar_duda' y "data" como {{}}.
4.  **Extracción/Combinación:** Si la solicitud es clara (o usa el historial), combina filtros. Asigna 'intent': 'buscar_prestador' y pon los filtros en "data". Confirma la búsqueda en "respuesta_texto".
5.  **Recomendaciones:** Si pide "el mejor", añade 'puntuacion_minima': 4.

Tu respuesta JSON (¡RECUERDA USAR SOLO COMILLAS DOBLES!"):
"""


# --- FIN INSTRUCCIONES ---

@app.get("/", tags=["Status"])
def root():
    return {"message": "AI Search Service funcionando 🚀"}


# --- ENDPOINT RENOMBRADO Y ACTUALIZADO (Req 4.1) ---
@router.post("/chatbot/query", response_model=ChatbotResponse, tags=["Chatbot"])
def chatbot_query(
        query_data: ChatbotQuery,
        conn: pyodbc.Connection = Depends(get_db_connection)
):
    respuesta_asistente = "Lo siento, hubo un problema."
    filtros_dict = {}
    filtros = SearchFilters()
    resultados_finales = []
    intent_detectado = "desconocido"

    # 1. Construir contexto
    gemini_contents = []
    if not query_data.history:
        gemini_contents.append({'role': 'user', 'parts': [{'text': SYSTEM_INSTRUCTIONS}]})
        gemini_contents.append({'role': 'model', 'parts': [{'text': "¡Entendido! ¿En qué puedo ayudarte hoy?"}]})
    else:
        for msg in query_data.history:
            parts_formatted = [{'text': part.get('text', '')} for part in msg.parts if part.get('text')]
            if parts_formatted: gemini_contents.append({'role': msg.role, 'parts': parts_formatted})
    gemini_contents.append({'role': 'user', 'parts': [{'text': query_data.mensaje}]})

    # 2. Llamar a Gemini
    try:
        response = model.generate_content(gemini_contents)
        raw_json_response = response.text.strip().replace('```json', '').replace('```', '')
        try:
            ai_response = json.loads(raw_json_response)
            respuesta_asistente = ai_response.get("respuesta_texto", "No pude procesar eso...")
            filtros_dict = ai_response.get("data", {})  # Leemos desde 'data'
            intent_detectado = ai_response.get("intent", "buscar_prestador")  # Leemos 'intent'
            filtros = SearchFilters(**filtros_dict)
        except Exception as json_err:
            print(f"Error parseando JSON: {json_err} - Respuesta: {raw_json_response}");
            respuesta_asistente = "Tuve problemas interpretando la respuesta..."
            filtros = SearchFilters();
            filtros_dict = {};
            intent_detectado = "error_parseo"

    except Exception as e:
        print(f"Error llamando a Gemini API: {e}");
        respuesta_asistente = "No pude contactar al asistente..."
        filtros = SearchFilters();
        filtros_dict = {};
        intent_detectado = "error_api"

    # 3. Construir historial final
    final_history = []
    if query_data.history:
        for msg in query_data.history:
            parts_dict = [{'text': part.get('text', '')} for part in msg.parts]
            if parts_dict: final_history.append(ChatMessage(role=msg.role, parts=parts_dict))
    final_history.append(ChatMessage(role="user", parts=[{'text': query_data.mensaje}]))
    final_history.append(ChatMessage(role="model", parts=[{'text': respuesta_asistente}]))

    # 4. Si no es para buscar, devolvemos ahora
    if intent_detectado != 'buscar_prestador' or not filtros_dict:
        return ChatbotResponse(
            respuesta_texto=respuesta_asistente,
            intent=intent_detectado,
            data=filtros,
            history=final_history,
            resultados=[]  # Devolvemos resultados vacíos
        )

    # 5. Búsqueda en BBDD (si el intent es 'buscar_prestador')
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

        # Ajustamos la respuesta si no hubo resultados
        if not resultados_finales and filtros_dict:
            if filtros.oficio and len(filtros_dict) == 1:
                respuesta_asistente = f"Entendido, necesitas '{filtros.oficio}'. De momento no encuentro a nadie, pero puedes explorar la categoría."
            else:
                respuesta_asistente = "No encontré prestadores con esos criterios. ¿Probamos algo más general?"
            final_history[-1] = ChatMessage(role="model", parts=[{'text': respuesta_asistente}])

    except pyodbc.Error as e:
        print(f"Database Error during search: {e}");
        respuesta_asistente = "Ups, tuve un problema técnico buscando en la base de datos."
        resultados_finales = []
        final_history[-1] = ChatMessage(role="model", parts=[{'text': respuesta_asistente}])
    finally:
        if cursor: cursor.close()

    return ChatbotResponse(
        respuesta_texto=respuesta_asistente,
        intent=intent_detectado,
        data=filtros,
        resultados=resultados_finales,  # Devolvemos los resultados aquí
        history=final_history
    )


# Incluimos el router en la app
app.include_router(router)
