import networkx as nx
import numpy as np
import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "resiliencia_chile",
    "user": "postgres",
    "password": "postgrespassword",
}

ITERACIONES_MC = 5000
SEED = 42


def cargar_red(cur):
    cur.execute("SELECT id, source, target, prob_falla FROM public.red_fibra;")
    enlaces = cur.fetchall()
    cur.execute("SELECT id, nombre, vertex_id FROM public.nodos_clave ORDER BY id;")
    nodos_clave = cur.fetchall()
    return enlaces, nodos_clave


def calcular_attr_operacion(enlaces, nodos_clave):
    G = nx.Graph()
    for _, u, v, _ in enlaces:
        G.add_edge(u, v)

    kv = [(nid, nombre, vid) for nid, nombre, vid in nodos_clave]
    k = len(kv)
    total_pares = k * (k - 1) // 2

    comp_map = {}
    for idx, comp in enumerate(nx.connected_components(G)):
        for node in comp:
            comp_map[node] = idx

    conectados = 0
    for i in range(k):
        for j in range(i + 1, k):
            c_i = comp_map.get(kv[i][2])
            c_j = comp_map.get(kv[j][2])
            if c_i is not None and c_i == c_j:
                conectados += 1

    return conectados / total_pares


def calcular_attr_montecarlo(enlaces, nodos_clave, m=ITERACIONES_MC, seed=SEED):
    rng = np.random.default_rng(seed)
    kv = [(nid, nombre, vid) for nid, nombre, vid in nodos_clave]
    k = len(kv)
    total_pares = k * (k - 1) // 2

    u_arr = np.array([e[1] for e in enlaces], dtype=np.int64)
    v_arr = np.array([e[2] for e in enlaces], dtype=np.int64)
    p_arr = np.array([e[3] for e in enlaces], dtype=np.float64)
    num_enlaces = len(enlaces)

    attr_muestras = np.zeros(m, dtype=np.float64)
    pares_conectados_por_nodo = np.zeros(k, dtype=np.float64)

    for it in range(m):
        activos = rng.random(num_enlaces) >= p_arr
        G = nx.Graph()
        G.add_edges_from(zip(u_arr[activos].tolist(), v_arr[activos].tolist()))

        comp_map = {}
        for idx, comp in enumerate(nx.connected_components(G)):
            for node in comp:
                comp_map[node] = idx

        conectados_it = 0
        for i in range(k):
            c_i = comp_map.get(kv[i][2])
            if c_i is None:
                continue
            for j in range(i + 1, k):
                if c_i == comp_map.get(kv[j][2]):
                    conectados_it += 1
                    pares_conectados_por_nodo[i] += 1
                    pares_conectados_por_nodo[j] += 1

        attr_muestras[it] = conectados_it / total_pares

    media = float(np.mean(attr_muestras))
    std = float(np.std(attr_muestras, ddof=1))
    ic95 = 1.96 * std / np.sqrt(m)

    conectividad_nodos = [
        (kv[i][1], float(pares_conectados_por_nodo[i] / (m * (k - 1))))
        for i in range(k)
    ]
    return media, std, ic95, conectividad_nodos


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # 1. Asegurar escenario en operación (prob_falla = 0)
    cur.execute("UPDATE public.red_fibra SET prob_falla = 0.0;")
    conn.commit()
    enlaces_op, nodos_clave = cargar_red(cur)
    attr_antes = calcular_attr_operacion(enlaces_op, nodos_clave)

    print("=== ATTR EN OPERACIÓN (prob_falla = 0) ===")
    print(f"Nodos clave (|K|): {len(nodos_clave)} | Pares evaluados: {len(nodos_clave)*(len(nodos_clave)-1)//2}")
    print(f"ATTR antes del terremoto: {attr_antes:.4f}")

    # 2. Aplicar modelo de falla sísmica 27F (Youngs 1997 + HAZUS-MH)
    with open("sql/02_probabilidades.sql", "r", encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()

    # Resumen de probabilidades (Criterio 4.2)
    cur.execute("""
        SELECT
            MIN(prob_falla),
            AVG(prob_falla),
            MAX(prob_falla),
            COUNT(*) FILTER (WHERE prob_falla >= 0.5) AS alta_falla,
            COUNT(*) FILTER (WHERE prob_falla < 0.01) AS baja_falla
        FROM public.red_fibra;
    """)
    min_p, avg_p, max_p, alta, baja = cur.fetchone()
    print("\n=== PROBABILIDADES DE FALLA (SISMO 8.8 27F) ===")
    print(f"Min: {min_p:.6f} | Promedio: {avg_p:.4f} | Max: {max_p:.4f}")
    print(f"Enlaces con prob >= 0.50: {alta} | Enlaces con prob < 0.01: {baja}")

    print("\nTop 5 enlaces MÁS afectados:")
    cur.execute("""
        SELECT id, ROUND((dist_epicentro_m/1000.0)::numeric, 2) AS dist_km,
               ROUND((longitud_m/1000.0)::numeric, 2) AS largo_km,
               ROUND(pga_g::numeric, 4) AS pga,
               ROUND(prob_falla::numeric, 4) AS prob,
               es_sintetico
        FROM public.red_fibra
        ORDER BY prob_falla DESC
        LIMIT 5;
    """)
    for row in cur.fetchall():
        print(f"  ID {row[0]:<5} | Dist: {row[1]:>6} km | Largo: {row[2]:>6} km | PGA: {row[3]} g | P(falla): {row[4]} | Sintético: {row[5]}")

    print("\nTop 5 enlaces MENOS afectados:")
    cur.execute("""
        SELECT id, ROUND((dist_epicentro_m/1000.0)::numeric, 2) AS dist_km,
               ROUND((longitud_m/1000.0)::numeric, 2) AS largo_km,
               ROUND(pga_g::numeric, 6) AS pga,
               ROUND(prob_falla::numeric, 6) AS prob,
               es_sintetico
        FROM public.red_fibra
        ORDER BY prob_falla ASC
        LIMIT 5;
    """)
    for row in cur.fetchall():
        print(f"  ID {row[0]:<5} | Dist: {row[1]:>7} km | Largo: {row[2]:>6} km | PGA: {row[3]} g | P(falla): {row[4]} | Sintético: {row[5]}")

    # 3. Calcular ATTR post-terremoto (Monte Carlo)
    enlaces_post, nodos_clave = cargar_red(cur)
    attr_despues, std_post, ic95, conect_nodos = calcular_attr_montecarlo(enlaces_post, nodos_clave)

    diff = attr_despues - attr_antes
    pct = (diff / attr_antes * 100.0) if attr_antes > 0 else 0.0

    print(f"\n=== ATTR POST-TERREMOTO 27F (Monte Carlo M={ITERACIONES_MC}, seed={SEED}) ===")
    print(f"ATTR después del terremoto: {attr_despues:.4f} ± {ic95:.4f} (IC 95%, std={std_post:.4f})")
    print(f"Diferencia absoluta: {diff:+.4f} ({pct:+.2f}%)")

    print("\nConectividad promedio post-27F por nodo clave (ordenado de más a menos afectado):")
    for nombre, val in sorted(conect_nodos, key=lambda x: x[1]):
        print(f"  {nombre:<28} -> {val*100:5.2f}%")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()