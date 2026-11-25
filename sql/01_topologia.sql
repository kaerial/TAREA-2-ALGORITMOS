-- 1. Crear tabla de enlaces de la red de fibra óptica
DROP TABLE IF EXISTS public.red_fibra CASCADE;
DROP TABLE IF EXISTS public.red_fibra_vertices_pgr CASCADE;

CREATE TABLE public.red_fibra (
    id SERIAL PRIMARY KEY,
    source INTEGER,
    target INTEGER,
    longitud_m DOUBLE PRECISION,
    prob_falla DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    es_sintetico BOOLEAN NOT NULL DEFAULT FALSE,
    fuente TEXT NOT NULL DEFAULT 'SAIP_fibra_optica_detectada',
    geom GEOMETRY(LineString, 4326),
    geom_utm GEOMETRY(LineString, 32719)
);

INSERT INTO public.red_fibra (longitud_m, prob_falla, es_sintetico, fuente, geom, geom_utm)
SELECT
    ST_Length(geom::geography) AS longitud_m,
    0.0 AS prob_falla,
    FALSE AS es_sintetico,
    'SAIP_fibra_optica_detectada' AS fuente,
    ST_Force2D(geom) AS geom,
    ST_Transform(ST_Force2D(geom), 32719) AS geom_utm
FROM public.fibra_raw
WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom);

CREATE INDEX idx_red_fibra_geom ON public.red_fibra USING GIST (geom);
CREATE INDEX idx_red_fibra_geom_utm ON public.red_fibra USING GIST (geom_utm);

-- 2. Construir topología de red con pgr_createTopology
-- Tolerancia: 0.001 grados (~111 m) para absorber pequeñas brechas de digitalización
SELECT pgr_createTopology(
    'public.red_fibra',
    0.001,
    'geom',
    'id',
    'source',
    'target',
    clean := true
);

-- 3. Tabla de nodos clave (Criterio 1.0)
DROP TABLE IF EXISTS public.nodos_clave CASCADE;

CREATE TABLE public.nodos_clave (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    ubicacion TEXT NOT NULL,
    tipo TEXT NOT NULL,
    rol TEXT NOT NULL,
    fuente TEXT NOT NULL,
    vertex_id BIGINT,
    dist_a_red_m DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326)
);

INSERT INTO public.nodos_clave (nombre, ubicacion, tipo, rol, fuente, geom) VALUES
('Arica', 'Región de Arica y Parinacota', 'Aterrizaje cable submarino / Frontera Norte', 'Genera / Recibe', 'Oficio SUBTEL 3517 (p.81); Submarine Cable Map (p.27)', ST_SetSRID(ST_MakePoint(-70.3126, -18.4783), 4326)),
('Iquique', 'Región de Tarapacá', 'Nodo troncal Norte Grande', 'Intercambia / Recibe', 'Estudio Yafun NIC Labs (2018)', ST_SetSRID(ST_MakePoint(-70.1431, -20.2141), 4326)),
('Antofagasta', 'Región de Antofagasta', 'Anillo urbano / Nodo troncal', 'Intercambia / Recibe', 'Oficio SUBTEL 3517 (p.81)', ST_SetSRID(ST_MakePoint(-70.3975, -23.6509), 4326)),
('La Serena', 'Región de Coquimbo', 'Nodo troncal Norte Chico', 'Recibe', 'Estudio Yafun NIC Labs (2018)', ST_SetSRID(ST_MakePoint(-71.2519, -29.9027), 4326)),
('Valparaíso', 'Región de Valparaíso', 'Aterrizaje cables submarinos / Anillo urbano', 'Genera / Intercambia', 'Oficio SUBTEL 3517 (p.81); Submarine Cable Map (p.27)', ST_SetSRID(ST_MakePoint(-71.6127, -33.0472), 4326)),
('Santiago (PIT Chile / DCs)', 'Región Metropolitana', 'IXP/PIT, Data Centers y salida internacional a Argentina', 'Genera / Intercambia / Recibe', 'Estudio Yafun (pp.10, 22, 75); Oficio SUBTEL 3517', ST_SetSRID(ST_MakePoint(-70.6693, -33.4489), 4326)),
('Rancagua', 'Región de O''Higgins', 'Nodo troncal Ruta 5 Centro', 'Recibe', 'Estudio Yafun NIC Labs (2018)', ST_SetSRID(ST_MakePoint(-70.7399, -34.1708), 4326)),
('Talca', 'Región del Maule', 'Nodo troncal Maule (zona 27F)', 'Intercambia / Recibe', 'Estudio Yafun NIC Labs (2018)', ST_SetSRID(ST_MakePoint(-71.6554, -35.4264), 4326)),
('Concepción', 'Región del Biobío', 'Anillo urbano / Nodo troncal Centro-Sur', 'Intercambia / Recibe', 'Oficio SUBTEL 3517 (p.81)', ST_SetSRID(ST_MakePoint(-73.0444, -36.8201), 4326)),
('Temuco', 'Región de La Araucanía', 'Anillo urbano / Nodo troncal Sur', 'Intercambia / Recibe', 'Oficio SUBTEL 3517 (p.81)', ST_SetSRID(ST_MakePoint(-72.5904, -38.7359), 4326)),
('Valdivia', 'Región de Los Ríos', 'Nodo troncal Los Ríos', 'Recibe', 'Estudio Yafun NIC Labs (2018)', ST_SetSRID(ST_MakePoint(-73.2459, -39.8142), 4326)),
('Puerto Montt', 'Región de Los Lagos', 'Fin backbone terrestre tradicional / Enlace FOA', 'Intercambia / Recibe', 'Oficio SUBTEL 3517 (p.81); Estudio Yafun (p.46)', ST_SetSRID(ST_MakePoint(-72.9424, -41.4693), 4326));

-- 4. Asociar cada nodo clave al vértice más cercano de la topología
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