-- Agrega columnas de diagnóstico sísmico si no existen
ALTER TABLE public.red_fibra
    ADD COLUMN IF NOT EXISTS dist_epicentro_m DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pga_g DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pgv_cms DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tasa_falla_km DOUBLE PRECISION;

-- Calcula distancia en metros (UTM 19S / EPSG:32719) y aplica el modelo
-- Youngs et al. (1997) + HAZUS-MH (FEMA, 2003) + Poisson por longitud de enlace
WITH sismo AS (
    SELECT
        magnitud AS mw,
        profundidad_km AS h_km,
        ST_Transform(geom, 32719) AS geom_utm
    FROM public.sismo_27f
    LIMIT 1
),
calculo AS (
    SELECT
        r.id,
        ST_Distance(r.geom_utm, s.geom_utm) AS dist_m,
        SQRT(POWER(ST_Distance(r.geom_utm, s.geom_utm) / 1000.0, 2) + POWER(s.h_km, 2)) AS r_hypo_km,
        r.longitud_m / 1000.0 AS l_km,
        s.mw,
        s.h_km
    FROM public.red_fibra r
    CROSS JOIN sismo s
),
pga_calc AS (
    SELECT
        id,
        dist_m,
        l_km,
        EXP(
            -0.6687
            + 1.438 * mw
            - 2.329 * LN(r_hypo_km + 1.097 * EXP(0.617 * mw))
            + 0.00648 * h_km
        ) AS pga
    FROM calculo
)
UPDATE public.red_fibra r
SET
    dist_epicentro_m = p.dist_m,
    pga_g = p.pga,
    pgv_cms = 100.0 * p.pga,
    tasa_falla_km = 0.0001 * POWER(100.0 * p.pga, 2.25),
    prob_falla = LEAST(1.0, GREATEST(0.0, 1.0 - EXP(-(0.0001 * POWER(100.0 * p.pga, 2.25)) * p.l_km)))
FROM pga_calc p
WHERE r.id = p.id;