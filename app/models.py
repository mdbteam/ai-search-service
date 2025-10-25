# ai-search-service/app/models.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import date

# --- MODELOS PARA EL CHATBOT (Req 4.1) ---
class ChatMessage(BaseModel):
    role: str
    parts: List[Dict[str, str]]

class ChatbotQuery(BaseModel):
    session_id: Optional[str] = None
    mensaje: str # Renombrado de query
    history: Optional[List[ChatMessage]] = None

class SearchFilters(BaseModel): # Los filtros extraídos
    oficio: Optional[str] = None
    genero: Optional[str] = None
    puntuacion_minima: Optional[int] = None
    min_trabajos_realizados: Optional[int] = None
    edad_minima: Optional[int] = None
    edad_maxima: Optional[int] = None

class ChatbotResponse(BaseModel):
    respuesta_texto: str
    intent: Optional[str] = None
    data: Optional[SearchFilters] = None # 'data' contendrá los filtros
    # También incluimos los resultados de la búsqueda,
    # para que el frontend no tenga que hacer 2 llamadas
    resultados: List[dict] = []
    history: List[ChatMessage]

# --- MODELO INTERNO ---
class UserInDB(BaseModel):
    id_usuario: int
    nombres: str
    primer_apellido: str
    correo: str
    id_rol: int
    estado: str
    # ... (campos completos si se usa /users/me)