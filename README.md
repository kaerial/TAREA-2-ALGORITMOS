# TI2 — Resiliencia de la Infraestructura de Internet Chilena (Terremoto 27F 8.8)

Análisis de resiliencia topológica de la red de fibra óptica chilena (datos SAIP / Estudio Yafun, NIC Labs 2018) ante el terremoto Mw 8.8 del 27 de febrero de 2010 (evento USGS `official20100227063411530_30`), utilizando PostgreSQL + PostGIS + pgRouting, cálculo de ATTR mediante simulación de Monte Carlo y visualización interactiva en Leaflet.

## Requisitos previos

- **Docker Desktop** (para levantar PostgreSQL + PostGIS + pgRouting).
- **Python 3.10+**

## Ejecución rápida (desde un clon limpio)

1. **Levantar la base de datos espacial con Docker:**

```bash
docker compose up -d
```

*(Esperar ~5 segundos a que PostgreSQL termine de inicializar.)*

2. **Crear entorno virtual e instalar dependencias:**

```bash
python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

3. **Ejecutar todo el flujo con un solo comando:**

```bash
python main.py
```

Este comando ejecuta automáticamente todo el pipeline de extremo a extremo:
1. Crea/reinicia la base de datos `resiliencia_chile` y carga íntegramente `main.sql` (infraestructura, topología `pgr_createTopology`, nodos clave y epicentro del sismo 27F).
2. Calcula el **ATTR en operación** (`prob_falla = 0` → `1.0000`).
3. Calcula las **probabilidades de falla** por enlace mediante `sql/02_probabilidades.sql` (distancias proyectadas en metros `EPSG:32719`, ecuación de atenuación Youngs et al. 1997 + curvas de fragilidad HAZUS-MH/FEMA + proceso de Poisson por longitud del enlace).
4. Calcula el **ATTR post-terremoto** vía Monte Carlo ($M = 5000$, `seed = 42` → `0.1401 ± 0.0015`, variación `-0.8599` / `-85.99%`).
5. Levanta el servidor local Flask con el mapa interactivo en **Leaflet** en `http://127.0.0.1:5000`.

## Explicación de dependencias (`requirements.txt`)

- **`psycopg2-binary`**: Conector Python–PostgreSQL utilizado para crear la base de datos, cargar `main.sql`, ejecutar los scripts SQL y consultar las geometrías en formato GeoJSON.
- **`networkx`**: Construcción del grafo de red en memoria para el cálculo rápido de componentes conexas durante la simulación de Monte Carlo del ATTR.
- **`numpy`**: Generación vectorizada y determinista (`seed = 42`) de muestras aleatorias para las 5.000 iteraciones de Monte Carlo y el cálculo del intervalo de confianza al 95%.
- **`flask`**: Servidor web ligero para servir la visualización Leaflet (`templates/index.html`) y exponer el endpoint `/api/datos`.
- **`geopandas`**: Utilizada en el script de ingesta inicial (`load_data.py`) para leer el shapefile SAIP (`fibra_optica_detectada.shp`) y normalizar su sistema de referencia a `EPSG:4326`.
- **`requests`**: Utilidad para consultas HTTP (descarga del archivo GeoJSON oficial desde la API de USGS).

## Estructura del repositorio

- `main.py`: Orquestador principal de un solo comando y servidor web Flask/Leaflet.
- `main.sql`: Dump autocontenido de la base de datos con la infraestructura, topología, nodos clave, sismo 27F y probabilidades de falla (carga en una BD vacía sin errores).
- `docker-compose.yml`: Definición del contenedor PostgreSQL + PostGIS + pgRouting.
- `requirements.txt`: Dependencias de Python declaradas.
- `sql/01_topologia.sql`: Construcción de la topología con `pgr_createTopology` y definición de los 12 nodos clave.
- `sql/02_probabilidades.sql`: Cálculo espacial en metros (`EPSG:32719`) de PGA, PGV, tasa de falla y probabilidad de falla por enlace.
- `templates/index.html`: Mapa interactivo en Leaflet (nodos clave, enlaces coloreados por probabilidad de falla, enlaces sintéticos distinguidos con línea punteada, epicentro y radios de daño del 27F).
- `load_data.py`, `setup_topology.py`, `calcular_modelo.py`, `generar_main_sql.py`: Scripts modulares del pipeline de construcción, auditoría y generación de `main.sql`.