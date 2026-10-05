# Imagen de producción del Consultor de Procesos.
# Etapa 1: construye el paquete (wheel). Etapa 2: solo lo necesario para ejecutarlo.

FROM python:3.13-slim AS construccion
WORKDIR /fuente
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /ruedas .

FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/JuanLesmes/consultor-procesos-judiciales" \
      org.opencontainers.image.description="Consultor de Procesos: vigilancia cortés de procesos judiciales (Rama Judicial de Colombia)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Bogota \
    CONSULTOR_CONFIG=/datos/consultor_procesos.toml

# El horario de verificación (07:00 a 18:00, lunes a viernes) es hora de Colombia: hace falta tzdata.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=construccion /ruedas /ruedas
RUN pip install --no-cache-dir --no-index --find-links /ruedas consultor-procesos && rm -rf /ruedas

# Usuario sin privilegios (uid 1000): la carpeta ./datos del servidor debe pertenecerle.
RUN useradd --create-home --uid 1000 consultor && mkdir -p /datos && chown consultor:consultor /datos
USER consultor
# Base de datos, documentos descargados, novedades.jsonl y respaldos viven en /datos (volumen).
WORKDIR /datos

# El commit va al final para no invalidar la caché de las capas anteriores en cada versión.
ARG COMMIT=""
ENV CONSULTOR_COMMIT=${COMMIT}

EXPOSE 8770
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --start-interval=5s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/api/salud', timeout=4).status == 200 else 1)"]

CMD ["consultor-procesos", "web", "--host", "0.0.0.0", "--puerto", "8770", "--proxy"]
