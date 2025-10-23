# app/models.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import date, datetime

# Modelo para un mensaje en el historial del chat
class ChatMessage(BaseModel):
    role: str # "user" o "model"
    parts: List[Dict[str, str]] # Gemini espera una lista de diccionarios {'text': 'mensaje'}

# Modelo para la consulta del usuario, ahora con historial
class SearchQuery(BaseModel):
    query: str
    history: Optional[List[ChatMessage]] = None # Historial opcional

# Modelo para los filtros extraídos por la IA
class SearchFilters(BaseModel):
    oficio: Optional[str] = None
    genero: Optional[str] = None
    puntuacion_minima: Optional[int] = None
    min_trabajos_realizados: Optional[int] = None
    edad_minima: Optional[int] = None
    edad_maxima: Optional[int] = None

# Modelo para la respuesta al frontend, ahora con historial
class SearchResponse(BaseModel):
    respuesta_asistente: str
    filtros_aplicados: SearchFilters
    resultados: List[dict] # Usamos dict por simplicidad
    history: List[ChatMessage] # Historial actualizado

# Modelo interno para el usuario autenticado (si se requiere login)
class UserInDB(BaseModel):
    id_usuario: int
    nombres: str
    primer_apellido: str
    correo: str
    id_rol: int
    estado: str