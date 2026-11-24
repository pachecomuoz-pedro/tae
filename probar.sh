#!/bin/sh
# Corre las pruebas del proyecto dentro de Docker.
#   sh probar.sh
set -e
echo "1/3 Base de datos con la configuración actual..."
docker compose up -d db
echo "2/3 Permiso para la base de pruebas..."
docker compose exec -T db sh /docker-entrypoint-initdb.d/01-permisos-pruebas.sh 2>&1 | grep -v "Using a password" || true
echo "3/3 Pruebas (tarda unos minutos)..."
docker compose exec -T api python manage.py test apps
