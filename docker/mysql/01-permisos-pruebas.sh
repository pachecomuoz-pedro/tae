#!/bin/sh
# Django crea la base test_<nombre> al correr las pruebas; el usuario de la
# app necesita permiso sobre ella. MySQL ejecuta este script solo la primera
# vez que se crea el volumen; para un volumen que ya existe, ver README
# ("Problemas frecuentes").
mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "GRANT ALL PRIVILEGES ON \`test_${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%'; FLUSH PRIVILEGES;"
