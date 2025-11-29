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

# --- Google AI Configuration ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY: raise RuntimeError("GOOGLE_API_KEY no está configurada.")
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('models/gemini-flash-latest')

# --- PROMPT MAESTRO (Final) ---
SYSTEM_INSTRUCTIONS = """
Eres el Asistente Chambee. Tu misión es ayudar a los usuarios a encontrar el profesional ideal.
Tu respuesta DEBE ser un único bloque de CÓDIGO JSON válido usando siempre comillas dobles (").
**NO** incluyas ninguna explicación, preámbulo o texto en Markdown fuera del bloque JSON. Solo el objeto JSON.
El JSON debe tener tres claves: "respuesta_texto" (string), "intent" (string, ej: 'buscar_prestador', 'aclarar_duda', 'emergencia', 'rechazo') y "data" (un objeto JSON con los filtros extraídos o {{}}).

FILTROS DISPONIBLES: 'oficio' (Gasfitería, Electricidad, Carpintería, Pintura, Jardinería, Limpieza), 'genero' (hombre/mujer), 'puntuacion_minima', 'min_trabajos_realizados', 'edad_minima', 'edad_maxima', 'nombre', 'apellido'.

REGLAS:
1.  **DESCOMPOSICIÓN:** Si el usuario da un nombre completo, DEBE separarlo en 'nombre' y 'apellido'.
2.  **BÚSQUEDA INCOMPLETA:** Si SOLO proporciona 'nombre'/'apellido' y NO 'oficio', asigna 'intent': 'aclarar_duda'.
3.  **Emergencias/Ilegal/Inapropiado:** Responde apropiadamente, asigna el 'intent' (ej: 'emergencia') y devuelve "data" como {{}}.
4.  **Extracción/Combinación:** Si la solicitud es clara (o usa el historial), combina filtros. Asigna 'intent': 'buscar_prestador' y pon los filtros en "data". Confirma la búsqueda en "respuesta_texto".
5.  **Recomendaciones:** Si pide "el mejor", añade 'puntuacion_minima': 4.

Tu respuesta JSON (¡RECUERDA USAR SOLO COMILLAS DOBLES!"):
"""


# --- FIN INSTRUCCIONES ---

@app.get("/", tags=["Status"])
def root():
    return {"message": "AI Search Service funcionando 🚀"}


# --- ENDPOINT CHATBOT (Público, solo requiere DB conn) ---
@app.post("/chatbot/query", response_model=ChatbotResponse, tags=["Chatbot"])
def chatbot_query(
        query_data: ChatbotQuery,
        conn: pyodbc.Connection = Depends(get_db_connection)
):
    respuesta_asistente = "Lo siento, hubo un problema."
    filtros_dict = {}
    filtros = SearchFilters()
    resultados_finales = []
    intent_detectado = "desconocido"

    # 🚨 Definición del mapeo de género
    GENERO_MAP = {
        'mujer': 'Femenino',
        'femenino': 'Femenino',
        'hombre': 'Masculino',
        'masculino': 'Masculino'
    }

    # 1. Construir contexto
    gemini_contents = []

    # Se añade el System Prompt para establecer las reglas del JSON
    gemini_contents.append({'role': 'user', 'parts': [{'text': SYSTEM_INSTRUCTIONS}]})

    # LÓGICA DE CONTEXTO: Si el historial está vacío, le damos un EJEMPLO de RESPUESTA JSON
    if not query_data.history:
        initial_json = json.dumps({
            "respuesta_texto": "Soy el Asistente Chambee. ¿En qué te ayudo? (ej: 'Busco un electricista').",
            "intent": "aclarar_duda",
            "data": {}
        })
        gemini_contents.append({'role': 'model', 'parts': [{'text': initial_json}]})

    # AÑADIR HISTORIAL EXISTENTE (si lo hay)
    if query_data.history:
        for msg in query_data.history:
            parts_formatted = [{'text': part.get('text', '')} for part in msg.parts if part.get('text')]
            if parts_formatted: gemini_contents.append({'role': msg.role, 'parts': parts_formatted})

    # Añadir el mensaje actual del usuario al contexto (último turno)
    gemini_contents.append({'role': 'user', 'parts': [{'text': query_data.mensaje}]})

    # 2. Llamar a Gemini
    try:
        response = model.generate_content(gemini_contents)
        raw_json_response = response.text.strip().replace('```json', '').replace('```', '')
        try:
            ai_response = json.loads(raw_json_response)
            respuesta_asistente = ai_response.get("respuesta_texto", "No pude procesar eso...")
            filtros_dict = ai_response.get("data", {})
            intent_detectado = ai_response.get("intent", "buscar_prestador")
            filtros = SearchFilters(**filtros_dict)

            # 🚨 PUNTO DE NORMALIZACIÓN DE FILTROS 🚨
            if filtros.oficio:
                filtros.oficio = filtros.oficio.lower().replace('á', 'a').replace('é', 'e').replace('í', 'i').replace(
                    'ó', 'o').replace('ú', 'u')

            if filtros.genero:
                filtros.genero = GENERO_MAP.get(filtros.genero.lower(), filtros.genero)

            if hasattr(filtros, 'nombre') and filtros.nombre:
                filtros.nombre = filtros.nombre.strip().lower().replace('á', 'a').replace('é', 'e').replace('í',
                                                                                                            'i').replace(
                    'ó', 'o').replace('ú', 'u')

            if hasattr(filtros, 'apellido') and filtros.apellido:
                filtros.apellido = filtros.apellido.strip().lower().replace('á', 'a').replace('é', 'e').replace('í',
                                                                                                                'i').replace(
                    'ó', 'o').replace('ú', 'u')

            # 🚨 FIN NORMALIZACIÓN 🚨

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
            if parts_formatted: final_history.append(ChatMessage(role=msg.role, parts=parts_dict))
    final_history.append(ChatMessage(role="user", parts=[{'text': query_data.mensaje}]))
    final_history.append(ChatMessage(role="model", parts=[{'text': respuesta_asistente}]))

    # 4. Si no es para buscar, devolvemos ahora
    # 🚨 LÓGICA CORREGIDA PARA LAS PRUEBAS 🚨
    is_search_intent = (intent_detectado == 'buscar_prestador')
    has_valid_oficio = (filtros.oficio is not None)

    if not is_search_intent or not has_valid_oficio:

        # Si el intent era buscar_prestador pero falló la extracción, mejoramos el mensaje
        if is_search_intent:
            respuesta_asistente = "Detecté que quieres buscar, pero necesito el **oficio o categoría principal** (ej: Gasfitería). ¿Puedes especificar?"

        return ChatbotResponse(
            respuesta_texto=respuesta_asistente,
            intent=intent_detectado,
            data=filtros,
            history=final_history,
            resultados=[]
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

    # Usamos los filtros normalizados
    if filtros.oficio: sql_query += " AND ofi.nombre_oficio LIKE ?"; params.append(f"%{filtros.oficio}%")
    if filtros.genero: sql_query += " AND u.genero = ?"; params.append(filtros.genero)

    # Búsqueda de nombre/apellido con COLLATE CI_AI para ignorar tildes/mayúsculas
    if hasattr(filtros, 'nombre') and filtros.nombre:
        sql_query += " AND u.nombres COLLATE SQL_Latin1_General_CP1_CI_AI LIKE ?";
        params.append(f"%{filtros.nombre}%")

    if hasattr(filtros, 'apellido') and filtros.apellido:
        sql_query += " AND u.primer_apellido COLLATE SQL_Latin1_General_CP1_CI_AI LIKE ?";
        params.append(f"%{filtros.apellido}%")

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
            # Lógica de filtrado excesivo
            if filtros.oficio and len([f for f in filtros_dict.values() if f is not None]) == 1:
                respuesta_asistente = f"Entendido, necesitas '{filtros.oficio}'. De momento no encuentro a nadie, pero puedes explorar la categoría."
            else:
                respuesta_asistente = "No encontré prestadores con esos criterios. ¿Probamos algo menos restrictivo?"

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
        resultados=resultados_finales,
        history=final_history
    )