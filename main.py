import json
import os
import webbrowser
from threading import Timer

import networkx as nx
import numpy as np
import psycopg2
from flask import Flask, jsonify, render_template

DB_HOST = os.getenv("PGHOST", "localhost")
DB_PORT = int(os.getenv("PGPORT", "5432"))
DB_USER = os.getenv("PGUSER", "postgres")
DB_PASSWORD = os.getenv("PGPASSWORD", "postgrespassword")
DB_NAME = os.getenv("PGDATABASE", "resiliencia_chile")

ITERACIONES_MC = 5000
SEED = 42

app = Flask(__name__)
RESULTADOS_CACHE = {}


def get_conn(dbname=DB_NAME):
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=dbname,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def inicializar_bd_desde_main_sql():
    print("[1/5] Creando/reiniciando base de datos y cargando main.sql...")
    conn_admin = get_conn("postgres")
    conn_admin.autocommit = True
    cur_admin = conn_admin.cursor()

    # Cerrar conexiones previas si existen
    cur_admin.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid();
        """,
        (DB_NAME,),
    )
    cur_admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}";')
    cur_admin.execute(f'CREATE DATABASE "{DB_NAME}";')
    cur_admin.close()
    conn_admin.close()

    conn = get_conn(DB_NAME)
    cur = conn.cursor()
    with open("main.sql", "r", encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()
    cur.close()
    conn.close()
    print("      Base de datos cargada desde main.sql sin errores.")


def calcular_attr_operacion(enlaces, nodos_clave):
    G = nx.Graph()
    for _, u, v, _ in enlaces:
        G.add_edge(u, v)

    k = len(nodos_clave)
    total_pares = k * (k - 1) // 2
    comp_map = {
        node: idx
        for idx, comp in enumerate(nx.connected_components(G))
        for node in comp
    }

    conectados = 0
    for i in range(k):
        c_i = comp_map.get(nodos_clave[i][2])
        if c_i is None:
            continue
        for j in range(i + 1, k):
            if c_i == comp_map.get(nodos_clave[j][2]):
                conectados += 1
    return conectados / total_pares


def calcular_attr_montecarlo(enlaces, nodos_clave, m=ITERACIONES_MC, seed=SEED):
    rng = np.random.default_rng(seed)
    k = len(nodos_clave)
    total_pares = k * (k - 1) // 2

    u_arr = np.array([e[1] for e in enlaces], dtype=np.int64)
    v_arr = np.array([e[2] for e in enlaces], dtype=np.int64)
    p_arr = np.array([e[3] for e in enlaces], dtype=np.float64)
    num_enlaces = len(enlaces)

    attr_muestras = np.zeros(m, dtype=np.float64)
    pares_por_nodo = np.zeros(k, dtype=np.float64)

    for it in range(m):
        activos = rng.random(num_enlaces) >= p_arr
        G = nx.Graph()
        G.add_edges_from(zip(u_arr[activos].tolist(), v_arr[activos].tolist()))

        comp_map = {
            node: idx
            for idx, comp in enumerate(nx.connected_components(G))
            for node in comp
        }

        conectados_it = 0
        for i in range(k):
            c_i = comp_map.get(nodos_clave[i][2])
            if c_i is None:
                continue
            for j in range(i + 1, k):
                if c_i == comp_map.get(nodos_clave[j][2]):
                    conectados_it += 1
                    pares_por_nodo[i] += 1
                    pares_por_nodo[j] += 1

        attr_muestras[it] = conectados_it / total_pares

    media = float(np.mean(attr_muestras))
    std = float(np.std(attr_muestras, ddof=1))
    ic95 = float(1.96 * std / np.sqrt(m))
    conect_nodos = {
        nodos_clave[i][0]: float(pares_por_nodo[i] / (m * (k - 1)))
        for i in range(k)
    }
    return media, std, ic95, conect_nodos


def ejecutar_flujo_completo():
    inicializar_bd_desde_main_sql()

    conn = get_conn(DB_NAME)
    cur = conn.cursor()

    # 2. Verificar topología y calcular ATTR en operación (prob_falla = 0)
    print("[2/5] Calculando ATTR en operación (prob_falla = 0)...")
    cur.execute("UPDATE public.red_fibra SET prob_falla = 0.0;")
    conn.commit()


    cur.execute("SELECT id, source, target, prob_falla FROM public.red_fibra ORDER BY id;")
    enlaces_op = cur.fetchall()
    cur.execute("SELECT id, nombre, vertex_id FROM public.nodos_clave ORDER BY id;")
    nodos_clave = cur.fetchall()

    attr_antes = calcular_attr_operacion(enlaces_op, nodos_clave)
    print(f"      ATTR antes del terremoto: {attr_antes:.4f}")

    # 3. Calcular probabilidades de falla del 27F (Youngs 1997 + HAZUS-MH)
    print("[3/5] Calculando probabilidades de falla (sismo 8.8 27F)...")
    with open("sql/02_probabilidades.sql", "r", encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()

    # 4. Calcular ATTR post-terremoto
    print(f"[4/5] Calculando ATTR post-terremoto (Monte Carlo M={ITERACIONES_MC}, seed={SEED})...")
    cur.execute("SELECT id, source, target, prob_falla FROM public.red_fibra;")
    enlaces_post = cur.fetchall()
    attr_despues, std_post, ic95, conect_nodos = calcular_attr_montecarlo(enlaces_post, nodos_clave)

    diff = attr_despues - attr_antes
    pct = (diff / attr_antes * 100.0) if attr_antes > 0 else 0.0

    print(f"      ATTR después del terremoto: {attr_despues:.4f} ± {ic95:.4f} (IC 95%, std={std_post:.4f})")
    print(f"      Variación: {diff:+.4f} ({pct:+.2f}%)")

    RESULTADOS_CACHE["metricas"] = {
        "attr_antes": attr_antes,
        "attr_despues": attr_despues,
        "std": std_post,
        "ic95": ic95,
        "diff": diff,
        "pct": pct,
        "iteraciones": ITERACIONES_MC,
        "seed": SEED,
    }
    RESULTADOS_CACHE["conect_nodos"] = conect_nodos

    cur.close()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/datos")
def api_datos():
    conn = get_conn(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            ROUND(prob_falla::numeric, 6),
            es_sintetico,
            ROUND((dist_epicentro_m / 1000.0)::numeric, 2),
            ROUND((longitud_m / 1000.0)::numeric, 2),
            ROUND(pga_g::numeric, 4),
            ST_AsGeoJSON(geom)
        FROM public.red_fibra;
    """)
    enlaces = [
        {
            "id": r[0],
            "prob_falla": float(r[1]),
            "es_sintetico": bool(r[2]),
            "dist_km": float(r[3]),
            "largo_km": float(r[4]),
            "pga_g": float(r[5]),
            "geom": json.loads(r[6]),
        }
        for r in cur.fetchall()
    ]

    conect_map = RESULTADOS_CACHE.get("conect_nodos", {})
    cur.execute("""
        SELECT id, nombre, ubicacion, tipo, rol, ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM public.nodos_clave
        ORDER BY id;
    """)
    nodos = [
        {
            "id": r[0],
            "nombre": r[1],
            "ubicacion": r[2],
            "tipo": r[3],
            "rol": r[4],
            "lat": float(r[5]),
            "lon": float(r[6]),
            "conectividad_post": conect_map.get(r[0], 0.0),
        }
        for r in cur.fetchall()
    ]

    cur.execute("""
        SELECT event_id, magnitud, lugar, profundidad_km, ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM public.sismo_27f
        LIMIT 1;
    """)
    s = cur.fetchone()
    sismo = {
        "event_id": s[0],
        "magnitud": float(s[1]),
        "lugar": s[2],
        "profundidad_km": float(s[3]),
        "lat": float(s[4]),
        "lon": float(s[5]),
    }

    cur.close()
    conn.close()

    return jsonify({
        "enlaces": enlaces,
        "nodos": nodos,
        "sismo": sismo,
        "metricas": RESULTADOS_CACHE["metricas"],
    })


if __name__ == "__main__":
    ejecutar_flujo_completo()
    print("\n[5/5] Levantando mapa interactivo Leaflet en http://127.0.0.1:5000 ...")
    Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False)