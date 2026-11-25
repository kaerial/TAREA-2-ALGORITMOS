import subprocess
import psycopg2

DB_ADMIN = {
    "host": "localhost",
    "port": 5432,
    "dbname": "postgres",
    "user": "postgres",
    "password": "postgrespassword",
}


def main():
    # 1. Exportar tablas con INSERTs estándar (compatible con psql y psycopg2)
    cmd = [
        "docker", "exec", "ti2_postgis_db",
        "pg_dump", "-U", "postgres", "-d", "resiliencia_chile",
        "--inserts", "--no-owner", "--no-privileges",
        "--table=public.fibra_raw",
        "--table=public.sismo_27f",
        "--table=public.red_fibra",
        "--table=public.red_fibra_vertices_pgr",
        "--table=public.nodos_clave",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)

    # Filtrar metacomandos de psql (como \restrict en PG 17+) para máxima compatibilidad
    lineas = [
        ln for ln in res.stdout.splitlines()
        if not ln.startswith("\\")
    ]

    encabezado = [
        "CREATE EXTENSION IF NOT EXISTS postgis;",
        "CREATE EXTENSION IF NOT EXISTS pgrouting;",
        "",
    ]

    with open("main.sql", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(encabezado + lineas) + "\n")

    print("main.sql generado correctamente.")

    # 2. Prueba de fuego: cargar main.sql en una BD completamente vacía
    conn = psycopg2.connect(**DB_ADMIN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP DATABASE IF EXISTS test_vacia_ti2;")
    cur.execute("CREATE DATABASE test_vacia_ti2;")
    cur.close()
    conn.close()

    with open("main.sql", "r", encoding="utf-8") as f:
        sql_content = f.read()

    conn_test = psycopg2.connect(**{**DB_ADMIN, "dbname": "test_vacia_ti2"})
    cur_test = conn_test.cursor()
    cur_test.execute(sql_content)
    conn_test.commit()

    cur_test.execute("SELECT COUNT(*), ROUND(AVG(prob_falla)::numeric, 4) FROM public.red_fibra;")
    n_enlaces, avg_p = cur_test.fetchone()
    print(f"Prueba en BD vacía EXITOSA: {n_enlaces} enlaces cargados | prob_falla promedio = {avg_p}")
    cur_test.close()
    conn_test.close()

    # Limpiar BD temporal de prueba
    conn = psycopg2.connect(**DB_ADMIN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP DATABASE IF EXISTS test_vacia_ti2;")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()