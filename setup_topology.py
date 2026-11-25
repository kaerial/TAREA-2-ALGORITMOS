import numpy as np
import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "resiliencia_chile",
    "user": "postgres",
    "password": "postgrespassword",
}


def auditar_red(cur, titulo):
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE NOT es_sintetico) AS reales,
            COUNT(*) FILTER (WHERE es_sintetico) AS sinteticos,
            ROUND((SUM(longitud_m) FILTER (WHERE NOT es_sintetico) / 1000.0)::numeric, 2) AS km_reales,
            ROUND((COALESCE(SUM(longitud_m) FILTER (WHERE es_sintetico), 0) / 1000.0)::numeric, 2) AS km_sinteticos,
            MIN(prob_falla), MAX(prob_falla)
        FROM public.red_fibra;
    """)
    reales, sint, km_r, km_s, min_p, max_p = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM public.red_fibra_vertices_pgr;")
    vertices = cur.fetchone()[0]

    cur.execute("""
        WITH cc AS (
            SELECT component, node
            FROM pgr_connectedComponents(
                'SELECT id, source, target, longitud_m AS cost, longitud_m AS reverse_cost FROM public.red_fibra'
            )
        )
        SELECT COUNT(DISTINCT component), MAX(cnt)
        FROM (SELECT component, COUNT(*) AS cnt FROM cc GROUP BY component) t;
    """)
    total_cc, mayor_cc = cur.fetchone()

    print(f"\n=== {titulo} ===")
    print(f"Enlaces reales: {reales} ({km_r} km) | Enlaces sintéticos: {sint} ({km_s} km)")
    print(f"Vértices: {vertices} | Componentes conexas: {total_cc} | Mayor componente: {mayor_cc} vértices")
    print(f"Rango prob_falla: [{min_p}, {max_p}]")

    cur.execute("""
        WITH cc AS (
            SELECT component, node
            FROM pgr_connectedComponents(
                'SELECT id, source, target, longitud_m AS cost, longitud_m AS reverse_cost FROM public.red_fibra'
            )
        )
        SELECT nc.id, nc.nombre, nc.vertex_id, ROUND(nc.dist_a_red_m::numeric, 1), cc.component
        FROM public.nodos_clave nc
        LEFT JOIN cc ON cc.node = nc.vertex_id
        ORDER BY nc.id;
    """)
    rows = cur.fetchall()
    print(f"{'ID':<3} {'Nombre':<28} {'Vertex':<8} {'Dist(m)':<10} {'Componente':<10}")
    print("-" * 65)
    for r in rows:
        print(f"{r[0]:<3} {r[1]:<28} {r[2]:<8} {str(r[3]):<10} {str(r[4]):<10}")


def agregar_enlaces_sinteticos_mst(cur):
    # Obtener vértices con su componente y coordenadas proyectadas en metros (UTM 19S)
    cur.execute("""
        WITH cc AS (
            SELECT component, node
            FROM pgr_connectedComponents(
                'SELECT id, source, target, longitud_m AS cost, longitud_m AS reverse_cost FROM public.red_fibra'
            )
        )
        SELECT
            v.id,
            cc.component,
            ST_X(ST_Transform(v.the_geom, 32719)) AS x_m,
            ST_Y(ST_Transform(v.the_geom, 32719)) AS y_m,
            ST_X(v.the_geom) AS lon,
            ST_Y(v.the_geom) AS lat
        FROM public.red_fibra_vertices_pgr v
        JOIN cc ON cc.node = v.id
        ORDER BY v.id;
    """)
    vertices = cur.fetchall()
    n = len(vertices)
    coords = np.array([[v[2], v[3]] for v in vertices], dtype=np.float64)
    comps = np.array([v[1] for v in vertices])

    # Matriz de distancias euclidianas en metros (UTM 19S)
    diff = coords[:, None, :] - coords[None, :, :]
    dist_mat = np.hypot(diff[:, :, 0], diff[:, :, 1])

    unique_comps = set(comps.tolist())
    in_tree_comp = {int(comps[0])}
    in_tree_mask = (comps == comps[0])

    tree_indices = np.where(in_tree_mask)[0]
    sub = dist_mat[tree_indices]
    best_local = np.argmin(sub, axis=0)
    min_dist = sub[best_local, np.arange(n)]
    closest_u = tree_indices[best_local]

    bridges = []
    while len(in_tree_comp) < len(unique_comps):
        masked_dist = np.where(in_tree_mask, np.inf, min_dist)
        v_idx = int(np.argmin(masked_dist))
        u_idx = int(closest_u[v_idx])
        bridges.append((vertices[u_idx], vertices[v_idx], float(masked_dist[v_idx])))

        new_comp = int(comps[v_idx])
        in_tree_comp.add(new_comp)
        new_indices = np.where(comps == new_comp)[0]
        in_tree_mask[new_indices] = True

        sub_new = dist_mat[new_indices]
        best_new_local = np.argmin(sub_new, axis=0)
        new_min = sub_new[best_new_local, np.arange(n)]
        better = new_min < min_dist
        min_dist[better] = new_min[better]
        closest_u[better] = new_indices[best_new_local[better]]

    # Insertar enlaces sintéticos en red_fibra
    for u, v, _ in bridges:
        cur.execute(
            """
            INSERT INTO public.red_fibra (longitud_m, prob_falla, es_sintetico, fuente, geom, geom_utm)
            SELECT
                ST_Length(g::geography),
                0.0,
                TRUE,
                'enlace_sintetico_continuidad_mst',
                g,
                ST_Transform(g, 32719)
            FROM (
                SELECT ST_SetSRID(ST_MakeLine(ST_MakePoint(%s, %s), ST_MakePoint(%s, %s)), 4326) AS g
            ) sub;
            """,
            (u[4], u[5], v[4], v[5]),
        )

    # Reconstruir topología con los enlaces reales + sintéticos
    cur.execute("""
        SELECT pgr_createTopology(
            'public.red_fibra',
            0.001,
            'geom',
            'id',
            'source',
            'target',
            clean := true
        );
    """)

    # Reasignar vertex_id más cercano a cada nodo clave
    cur.execute("""
        UPDATE public.nodos_clave nc
        SET
            vertex_id = nearest.id,
            dist_a_red_m = nearest.dist_m
        FROM (
            SELECT
                n.id AS nodo_id,
                v.id,
                ST_Distance(n.geom::geography, v.the_geom::geography) AS dist_m
            FROM public.nodos_clave n
            CROSS JOIN LATERAL (
                SELECT id, the_geom
                FROM public.red_fibra_vertices_pgr
                ORDER BY the_geom <-> n.geom
                LIMIT 1
            ) v
        ) nearest
        WHERE nc.id = nearest.nodo_id;
    """)


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    with open("sql/01_topologia.sql", "r", encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()

    auditar_red(cur, "1. TOPOLOGÍA CRUDA (SOLO SAIP)")

    agregar_enlaces_sinteticos_mst(cur)
    conn.commit()

    auditar_red(cur, "2. TOPOLOGÍA CON ENLACES SINTÉTICOS DE CONTINUIDAD (MST)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()