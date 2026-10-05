# Despliegue en un servidor (VPS)

Esta guía deja el Consultor funcionando en `https://consultor.sufirma.com` con usuarios y contraseña,
certificado HTTPS automático, despliegue automático desde GitHub y un monitor que avisa si deja de
vigilar bien. La primera vez toma unos 45 minutos; después, cada cambio que llega a `main` se
despliega solo.

```text
push a main ──> CI: pruebas (Python 3.11 y 3.13) + imagen Docker + prueba de humo
                 └─> CD: imagen a GHCR ──SSH──> VPS: respaldo -> actualizar -> /api/salud con el commit nuevo
                                                       └─ si no arranca: vuelve sola a la versión anterior
                      └─> revisión desde internet (HTTPS, acceso, CSRF, /api/monitor)
Monitor: 07:40, 10:40, 13:40 y 17:40 de lunes a viernes ──> abre un issue si algo falla y lo cierra al resolverse
```

## 1. Lo que necesita

| Elemento | Detalle |
| --- | --- |
| VPS con Ubuntu 22.04 o 24.04 | 1 GB de RAM basta. Puede ser el mismo del Administrador de Procesos (ver 3B). |
| Un subdominio | Por ejemplo `consultor.sufirma.com`, con un registro **A** que apunte a la IP del VPS. |
| Este repositorio en GitHub | Ya existe: `JuanLesmes/consultor-procesos-judiciales` (privado). |
| Un correo de contacto | Con él se identifica el programa ante la Rama Judicial. |

## 2. Preparar el servidor (una sola vez)

Conéctese por SSH como `root` (o un usuario con `sudo`).

1. Si el VPS aún no tiene Docker (el del Administrador ya lo tiene):

```bash
curl -fsSL https://get.docker.com | sh
```

2. Firewall: solo SSH, HTTP y HTTPS.

```bash
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

3. Un usuario solo para los despliegues, con permiso para usar Docker:

```bash
adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy
```

4. La carpeta del Consultor. La subcarpeta `datos` debe pertenecer al usuario 1000, que es con el
   que corre el programa dentro del contenedor:

```bash
mkdir -p /opt/consultor/datos && chown -R deploy:deploy /opt/consultor && chown 1000:1000 /opt/consultor/datos
```

5. La llave con la que GitHub entrará al servidor. Genérela **en su computador** (no en el servidor):

```bash
ssh-keygen -t ed25519 -f consultor_despliegue -N "" -C "github-actions-consultor"
```

   Copie el contenido de `consultor_despliegue.pub` al servidor:

```bash
mkdir -p /home/deploy/.ssh && nano /home/deploy/.ssh/authorized_keys
```

```bash
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
```

   El archivo `consultor_despliegue` (sin `.pub`) es secreto: va a GitHub en el paso 5 y después
   puede borrarlo de su computador.

## 3. Elegir cómo se publica el HTTPS

### 3A. Servidor solo para el Consultor (lo más simple)

El Consultor trae su propio Caddy, que pide y renueva el certificado. En el `.env` del paso 4 deje
`COMPOSE_PROFILES=proxy`. No hay nada más que hacer aquí.

### 3B. Mismo servidor que el Administrador de Procesos

Dos Caddy no pueden usar los puertos 80 y 443 a la vez, así que el Caddy del Administrador atiende
los dos dominios y el Consultor se une a su red de Docker.

1. En `/opt/procesos/Caddyfile` (el del Administrador) agregue al final este bloque:

```text
{$DOMINIO_CONSULTOR} {
	encode zstd gzip
	request_body {
		max_size 1MB
	}
	header Strict-Transport-Security "max-age=31536000"
	reverse_proxy consultor:8770
}
```

2. En `/opt/procesos/.env` agregue `DOMINIO_CONSULTOR=consultor.sufirma.com`.
3. Mire el nombre de la red del Administrador (normalmente `procesos_default`):

```bash
docker network ls
```

4. En el `.env` del Consultor (paso 4) deje `COMPOSE_PROFILES=` vacío y active:

```text
COMPOSE_FILE=docker-compose.yml:docker-compose.compartido.yml
RED_PROXY=procesos_default
```

5. Después del primer despliegue del Consultor (paso 6), recargue el Caddy del Administrador:

```bash
cd /opt/procesos && docker compose up -d caddy && docker compose restart caddy
```

Conviene guardar también ese cambio del Caddyfile en el repositorio del Administrador.

## 4. El archivo `.env` del servidor

Primero genere los usuarios. En su computador, dentro de la carpeta del proyecto (con el entorno
`.venv` instalado), una vez por persona y una más para el monitor:

```bash
.venv\Scripts\consultor-procesos crear-usuario juan
```

```bash
.venv\Scripts\consultor-procesos crear-usuario monitor
```

Cada uno pide la contraseña (mínimo 10 caracteres) e imprime una línea `nombre:scrypt:...`. La
contraseña no queda guardada en ningún lado: anote la del usuario `monitor`, que va a GitHub.

Luego, en el servidor, como `deploy`:

```bash
cd /opt/consultor && nano .env
```

Con este contenido (las líneas de usuario van separadas por comas, sin espacios):

```text
DOMINIO=consultor.sufirma.com
CONSULTOR_USUARIOS=juan:scrypt:16384:8:1:...,monitor:scrypt:16384:8:1:...
CONSULTOR_CONTACTO=sistemas@sufirma.com
COMPOSE_PROFILES=proxy
TZ=America/Bogota
```

El ejemplo completo, con todas las opciones, es [.env.ejemplo](../.env.ejemplo). La línea
`CONSULTOR_IMAGEN=` la escribe el despliegue automático: no la edite a mano.

Opcional: ajustes finos (horario, palabras clave, aviso por correo) en
`/opt/consultor/datos/consultor_procesos.toml`, con la forma de
[consultor_procesos.ejemplo.toml](../consultor_procesos.ejemplo.toml). Sin ese archivo se usan los
valores por defecto, que son los recomendados.

## 5. Configurar GitHub

En el repositorio: **Settings → Secrets and variables → Actions**.

Pestaña **Variables** (no son secretas):

| Variable | Valor |
| --- | --- |
| `VPS_HOST` | IP del servidor. Mientras no exista, el CD no hace nada. |
| `VPS_USUARIO` | `deploy` |
| `CONSULTOR_URL` | `https://consultor.sufirma.com`. Mientras no exista, el monitor no hace nada. |
| `VPS_PUERTO`, `VPS_CARPETA` | Opcionales: `22` y `/opt/consultor` por defecto. |

Pestaña **Secrets**:

| Secreto | Valor |
| --- | --- |
| `VPS_SSH_KEY` | Todo el contenido del archivo `consultor_despliegue` (la llave privada del paso 2.5). |
| `VPS_KNOWN_HOSTS` | La salida de `ssh-keyscan -H IP_DEL_SERVIDOR`, ejecutado en su computador. |
| `CONSULTOR_MONITOR_USUARIO` | `monitor` |
| `CONSULTOR_MONITOR_CLAVE` | La contraseña que le puso al usuario `monitor`. |

Si quiere aprobar cada despliegue a mano: **Settings → Environments → produccion → Required
reviewers** (el ambiente se crea solo en el primer despliegue).

## 6. Primer despliegue

En GitHub: **Actions → CD → Run workflow** (rama `main`). El flujo:

1. Construye la imagen y la publica en GHCR (`ghcr.io/juanlesmes/consultor-procesos-judiciales`).
2. Copia al servidor `docker-compose.yml`, `Caddyfile` y el script `despliegue/desplegar.sh`.
3. En el servidor respalda la base, descarga la imagen, arranca y espera a que `/api/salud`
   responda con el commit nuevo. Si no lo logra en dos minutos, muestra los registros y vuelve a la
   versión anterior.
4. Revisa desde internet con `scripts/vigilar.py`: HTTPS y redirección, usuario obligatorio,
   protección CSRF, cabeceras de seguridad y el resumen de `/api/monitor`.

Desde entonces, cada push a `main` con la CI en verde repite el proceso solo.

Entre a `https://consultor.sufirma.com` con su usuario, agregue los radicados y espere a que
termine la línea base de cada uno.

## 7. El monitor

`Actions → Monitor` corre 40 minutos después de cada verificación programada (07:40, 10:40, 13:40 y
17:40, lunes a viernes) y una vez al día los fines de semana. Falla, y abre un issue con la etiqueta
`monitor`, si:

- el servidor no responde, no exige usuario o no tiene HTTPS;
- la vigilancia automática está detenida (alguien la pausó o el hilo murió);
- una verificación programada no se ejecutó;
- algún proceso terminó en `ERROR`, por ejemplo porque la Rama Judicial cambió el formato de su API.

Los avisos que no requieren acción inmediata (la Rama Judicial no responde, se agotó el presupuesto
del día, falta el correo de contacto) se muestran en el informe sin abrir un issue. Cuando la
revisión vuelve a estar en orden, el issue se cierra solo. Para recibir las alertas por correo o en
el celular, active las notificaciones del repositorio (**Watch → All activity**, o al menos Issues)
y la app de GitHub.

Para probarlo en cualquier momento: **Actions → Monitor → Run workflow**, o desde su computador:

```bash
python scripts/vigilar.py https://consultor.sufirma.com --usuario monitor --clave "..."
```

## 8. Operación diaria

Todos los comandos, en el servidor, dentro de `/opt/consultor`:

| Tarea | Comando |
| --- | --- |
| Ver si está corriendo | `docker compose ps` |
| Ver los registros | `docker compose logs -f consultor` |
| Respaldar ahora | `docker compose exec consultor consultor-procesos respaldar --destino /datos/respaldos` |
| Verificar ahora desde la consola | `docker compose exec consultor consultor-procesos verificar` |
| Reiniciar | `docker compose restart consultor` |
| Volver a una versión anterior | ver más abajo |

**Respaldos.** Cada despliegue respalda la base antes de actualizar (se conservan 30 en
`datos/respaldos/`). Para uno diario, agregue con `crontab -e` (usuario `deploy`):

```text
30 23 * * * cd /opt/consultor && docker compose exec -T consultor consultor-procesos respaldar --destino /datos/respaldos --conservar 30
```

Conviene copiar de vez en cuando `datos/respaldos/` fuera del servidor.

**Restaurar un respaldo.** Detenga el servicio, reemplace la base y arranque:

```bash
docker compose stop consultor && cp datos/respaldos/consultor-AAAAMMDD-HHMMSS.sqlite datos/consultor_procesos.sqlite && docker compose start consultor
```

**Volver a una versión anterior.** Las imágenes de los últimos 30 días quedan en el servidor:

```bash
docker images ghcr.io/juanlesmes/consultor-procesos-judiciales
```

Ponga la etiqueta elegida en la línea `CONSULTOR_IMAGEN=` del `.env` y ejecute `docker compose up -d`.
Lo normal, sin embargo, es revertir el commit en GitHub y dejar que el CD despliegue.

## 9. Seguridad incluida y pendiente

Incluido:

- HTTPS obligatorio con certificado automático y HSTS (Caddy).
- Usuario y contraseña en toda la interfaz y la API; contraseñas con scrypt y sal, nunca en claro.
- Bloqueo de 15 minutos por dirección IP tras 10 intentos fallidos.
- Protección CSRF (cabecera obligatoria en toda petición que modifica) y rechazo de peticiones a la
  API originadas en otros sitios.
- Cabeceras de seguridad (CSP, nosniff, sin marcos externos) y límite de tamaño de las peticiones.
- El contenedor corre sin privilegios (usuario 1000) y el puerto 8770 solo escucha en la interfaz
  local del servidor; desde internet solo se entra por Caddy.
- El token con el que el servidor descarga la imagen vale solo durante el despliegue y se borra al terminar.

Pendiente o fuera de alcance:

- Doble factor de autenticación y cierre de sesión (la autenticación es HTTP Basic: para salir hay
  que cerrar el navegador).
- Cifrado del disco del servidor (algunos proveedores lo ofrecen al crear el VPS).
- Copia de los respaldos fuera del servidor.

## 10. Costos de GitHub Actions

El repositorio es privado: el plan gratuito incluye 2.000 minutos al mes. Cada push consume unos 5
(pruebas e imagen), cada despliegue unos 3 y el monitor unos 120 al mes. Con un uso normal sobra.
