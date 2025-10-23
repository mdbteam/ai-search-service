# app/models.py
from pydantic import BaseModel
from typing import List, Optional

# Modelo para la consulta del usuario
class SearchQuery(BaseModel):
    query: str

# Modelo para los filtros extraídos por la IA
class SearchFilters(BaseModel):
    oficio: Optional[str] = None
    genero: Optional[str] = None
    puntuacion_minima: Optional[int] = None
    min_trabajos_realizados: Optional[int] = None
    edad_minima: Optional[int] = None
    edad_maxima: Optional[int] = None

# Modelo para la respuesta al frontend
class SearchResponse(BaseModel):
    respuesta_asistente: str
    filtros_aplicados: SearchFilters
    resultados: List[dict]

# Modelo interno para el usuario autenticado
class UserInDB(BaseModel):
    id_usuario: int
    nombres: str
    primer_apellido: str
    correo: str
    id_rol: int
    estado: str