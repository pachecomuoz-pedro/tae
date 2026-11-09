#!/bin/bash
# Respaldo diario de MySQL. Conserva 14 días en el servidor.
# Programarlo y copiar los respaldos FUERA del servidor: docs/DESPLIEGUE.md, paso 12.
set -euo pipefail

RAIZ=/opt/tae
cd "$RAIZ"
set -a; source .env; set +a

DESTINO="$RAIZ/respaldos"
mkdir -p "$DESTINO"
ARCHIVO="$DESTINO/tae_$(date +%F_%H%M).sql.gz"

# --single-transaction: respaldo consistente sin bloquear las ventas (InnoDB).
docker compose -f deploy/docker-compose.prod.yml --env-file .env exec -T db \
  mysqldump -u root -p"$DB_ROOT_PASSWORD" --single-transaction --routines "$DB_NAME" \
  | gzip > "$ARCHIVO"

find "$DESTINO" -name "tae_*.sql.gz" -mtime +14 -delete
echo "$(date '+%F %T') respaldo listo: $ARCHIVO"
