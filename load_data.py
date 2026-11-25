import json
import geopandas as gpd
import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "resiliencia_chile",
    "user": "postgres",
    "password": "postgrespassword",
}


def main():
    shp_path = "data/saip/Respuesta_T5610/fibra_optica_detectada.shp"
    try:
        gdf = gpd.read_file(shp_path)
    except Exception as e:
        print(f"Error al leer con GDAL/geopandas: {e}")
        for ext in ("shp", "shx", "dbf", "prj"):
            try:
                with open(f"data/fibra_optica_detectada.{ext}", "rb") as f:
                    print(f"Primeros 32 bytes de .{ext}:", f.read(32).hex())
            except FileNotFoundError:
                pass
        return

    print("CRS detectado:", gdf.crs)
    print("Columnas:", list(gdf.columns))
    print("Filas leídas:", len(gdf))

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute("CREATE EXTENSION IF NOT EXISTS pgrouting;")

    # 1. Cargar fibra_optica_detectada.shp -> public.fibra_raw
    cur.execute("DROP TABLE IF EXISTS public.fibra_raw CASCADE;")
    cur.execute("""
        CREATE TABLE public.fibra_raw (
            id SERIAL PRIMARY KEY,
            atributos JSONB,
            geom GEOMETRY(Geometry, 4326)
        );
    """)

    attr_cols = [c for c in gdf.columns if c != "geometry"]
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        attrs = {c: (None if row[c] != row[c] else row[c]) for c in attr_cols}
        cur.execute(
            """
            INSERT INTO public.fibra_raw (atributos, geom)
            VALUES (%s, ST_Force2D(ST_SetSRID(ST_GeomFromText(%s), 4326)));
            """,
            (json.dumps(attrs, default=str), geom.wkt),
        )

    cur.execute("CREATE INDEX idx_fibra_raw_geom ON public.fibra_raw USING GIST (geom);")

    # 2. Cargar sismo_27f.geojson -> public.sismo_27f
    with open("data/sismo_27f.geojson", "r", encoding="utf-8-sig") as f:
        sismo_data = json.load(f)

    cur.execute("DROP TABLE IF EXISTS public.sismo_27f CASCADE;")
    cur.execute("""
        CREATE TABLE public.sismo_27f (
            event_id TEXT PRIMARY KEY,
            magnitud DOUBLE PRECISION,
            lugar TEXT,
            profundidad_km DOUBLE PRECISION,
            fecha_ms BIGINT,
            geom GEOMETRY(Point, 4326)
        );
    """)

    for feat in sismo_data["features"]:
        props = feat["properties"]
        coords = feat["geometry"]["coordinates"]  # [lon, lat, depth_km]
        geom_2d = json.dumps({"type": "Point", "coordinates": [coords[0], coords[1]]})
        cur.execute(
            """
            INSERT INTO public.sismo_27f (event_id, magnitud, lugar, profundidad_km, fecha_ms, geom)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326));
            """,
            (
                feat["id"],
                props.get("mag"),
                props.get("place"),
                coords[2] if len(coords) > 2 else None,
                props.get("time"),
                geom_2d,
            ),
        )

    conn.commit()

    # 3. Resumen de verificación
    cur.execute("""
        SELECT COUNT(*) AS tramos,
               GeometryType(geom) AS tipo_geom,
               ROUND((SUM(ST_Length(geom::geography)) / 1000.0)::numeric, 2) AS km_totales
        FROM public.fibra_raw
        GROUP BY tipo_geom;
    """)
    print("\n--- RESUMEN FIBRA ---")
    for row in cur.fetchall():
        print(f"Tramos: {row[0]} | Tipo: {row[1]} | Km totales: {row[2]}")

    cur.execute("SELECT event_id, magnitud, lugar, profundidad_km, ST_AsText(geom) FROM public.sismo_27f;")
    print("\n--- RESUMEN SISMO 27F ---")
    for row in cur.fetchall():
        print(row)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()