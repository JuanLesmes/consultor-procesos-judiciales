#!/usr/bin/env bash
# Despliega una imagen del Consultor en este servidor y comprueba que arrancó con el commit esperado.
# Si la versión nueva no responde, vuelve sola a la anterior. Lo ejecuta el flujo CD de GitHub por SSH,
# pero también se puede correr a mano desde la carpeta del despliegue (por defecto /opt/consultor):
#
#   ./despliegue/desplegar.sh ghcr.io/juanlesmes/consultor-procesos-judiciales:sha-0123456789ab 0123456789ab...
#
# Pasos: respaldo de la base -> descarga de la imagen -> arranque -> espera a /api/salud con el commit
# nuevo -> si falla, registros y vuelta a la imagen anterior.
set -euo pipefail

IMAGEN="${1:?Indique la imagen, por ejemplo ghcr.io/usuario/consultor-procesos-judiciales:sha-...}"
COMMIT="${2:?Indique el commit esperado}"
cd "$(dirname "$0")/.."

PUERTO="${CONSULTOR_PUERTO_LOCAL:-8770}"
ESPERA_MAXIMA="${ESPERA_MAXIMA:-120}"

if [ ! -f .env ]; then
  echo "Falta el archivo .env en $(pwd). Cópielo de .env.ejemplo y complételo (ver docs/DESPLIEGUE.md)." >&2
  exit 1
fi
mkdir -p datos

leer_imagen_actual() { grep -E '^CONSULTOR_IMAGEN=' .env | tail -n 1 | cut -d= -f2- || true; }

fijar_imagen() {
  if grep -qE '^CONSULTOR_IMAGEN=' .env; then
    sed -i "s|^CONSULTOR_IMAGEN=.*|CONSULTOR_IMAGEN=$1|" .env
  else
    printf '\nCONSULTOR_IMAGEN=%s\n' "$1" >> .env
  fi
}

esperar_salud() {
  local esperado="$1" limite=$((SECONDS + ESPERA_MAXIMA)) respuesta=""
  while [ "$SECONDS" -lt "$limite" ]; do
    respuesta="$(curl -fsS --max-time 5 "http://127.0.0.1:${PUERTO}/api/salud" 2>/dev/null || true)"
    if printf '%s' "$respuesta" | grep -q "\"commit\": \"${esperado}\""; then
      echo "Salud: $respuesta"
      return 0
    fi
    sleep 3
  done
  echo "No respondió con el commit ${esperado} en ${ESPERA_MAXIMA} s. Última respuesta: ${respuesta:-ninguna}" >&2
  return 1
}

ANTERIOR="$(leer_imagen_actual)"
echo "== Versión actual: ${ANTERIOR:-ninguna}"
echo "== Versión nueva:  $IMAGEN ($COMMIT)"

if docker compose ps --status running --services 2>/dev/null | grep -qx consultor; then
  echo "== Respaldo de la base de datos antes de actualizar"
  docker compose exec -T consultor consultor-procesos respaldar --destino /datos/respaldos --conservar 30 \
    || echo "(no se pudo respaldar; se continúa con el despliegue)"
fi

fijar_imagen "$IMAGEN"
echo "== Descargando la imagen"
docker compose pull consultor
echo "== Arrancando"
docker compose up -d --no-build --remove-orphans

if esperar_salud "$COMMIT"; then
  docker compose ps
  # Imágenes sin uso de hace más de 30 días; las recientes quedan para volver atrás sin descargar.
  docker image prune -af --filter "until=720h" >/dev/null || true
  echo "== Despliegue completado"
  exit 0
fi

echo "== La versión nueva no arrancó bien. Últimos registros:" >&2
docker compose logs --tail 80 consultor >&2 || true
if [ -n "$ANTERIOR" ] && [ "$ANTERIOR" != "$IMAGEN" ]; then
  echo "== Volviendo a $ANTERIOR" >&2
  fijar_imagen "$ANTERIOR"
  docker compose up -d --no-build --remove-orphans
fi
exit 1
