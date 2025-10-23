FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    curl gnupg lsb-release unixodbc unixodbc-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" \
    > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y msodbcsql18

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./app /app/app
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT