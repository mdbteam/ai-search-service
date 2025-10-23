# app/auth_utils.py
import os
from typing import Optional
from fastapi import Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import pyodbc
from app.database import get_db_connection
from app.models import UserInDB

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY no está configurada.")

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def get_current_user_from_token(token: str = Depends(oauth2_scheme), conn: pyodbc.Connection = Depends(get_db_connection)) -> Optional[UserInDB]:
    if token is None: return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None: return None
    except JWTError: return None
    cursor = conn.cursor()
    cursor.execute("SELECT id_usuario, nombres, primer_apellido, correo, id_rol, estado FROM Usuarios WHERE id_usuario = ?", int(user_id))
    user_record = cursor.fetchone()
    cursor.close()
    if user_record is None: return None
    return UserInDB(**dict(zip([column[0] for column in user_record.cursor_description], user_record)))

async def get_current_user_from_cookie_or_token(
    token_from_header: str = Depends(oauth2_scheme),
    token_from_query: str = Query(None, alias="token"),
    conn: pyodbc.Connection = Depends(get_db_connection)
) -> UserInDB:
    token = token_from_header or token_from_query
    if not token: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    user = await get_current_user_from_token(token, conn)
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado")
    return user