# Imagen del backend (Django). La misma para desarrollo y producción:
# en desarrollo docker-compose.yml la arranca con runserver; en producción
# deploy/docker-compose.prod.yml la arranca con gunicorn.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Librerías de sistema para compilar mysqlclient.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential default-libmysqlclient-dev pkg-config \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "90"]
