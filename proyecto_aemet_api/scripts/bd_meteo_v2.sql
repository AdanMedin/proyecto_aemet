-- =====================================================================
-- SCRIPT - BASE DE DATOS METEOROLÓGICA
-- Datos diarios multi-estación, 10 años de histórico
-- =====================================================================


-- 0) ESQUEMA
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS meteo;

-- 1) TABLA DE ESTACIONES
-- ---------------------------------------------------------------------
CREATE TABLE meteo.estaciones (
    indicativo   VARCHAR(10) PRIMARY KEY,
    nombre       VARCHAR(150) NOT NULL,
    provincia    VARCHAR(100),
    altitud      SMALLINT,
    latitud      NUMERIC(9,6),
    longitud     NUMERIC(9,6),

    CONSTRAINT chk_altitud_valida
        CHECK (altitud IS NULL OR altitud BETWEEN -200 AND 8850),
    CONSTRAINT chk_latitud_valida
        CHECK (latitud IS NULL OR latitud BETWEEN -90 AND 90),
    CONSTRAINT chk_longitud_valida
        CHECK (longitud IS NULL OR longitud BETWEEN -180 AND 180)
);

CREATE INDEX idx_estaciones_provincia ON meteo.estaciones (provincia);

-- 3) TABLA MEDICIONES DIARIAS PARTICIONADA POR AÑO
-- ---------------------------------------------------------------------
CREATE TABLE meteo.mediciones_diarias (
    fecha        DATE        NOT NULL,
    indicativo   VARCHAR(10) NOT NULL,
    tmed         NUMERIC(4,1),
    prec         NUMERIC(6,1),
    precAcum     BOOLEAN,
    precIp       BOOLEAN,
    tmin         NUMERIC(4,1),
    tmax         NUMERIC(4,1),
    dir          SMALLINT,
    velmedia     NUMERIC(4,1),
    racha        NUMERIC(4,1),
    sol          NUMERIC(4,1),
    presmax      NUMERIC(6,1),
    presmin      NUMERIC(6,1),
    hrmedia      SMALLINT,
    hrmax        SMALLINT,
    hrmin        SMALLINT,
    variasHoras  BOOLEAN,
    pintmax      NUMERIC(6,1),

    -- Clave primaria compuesta (obligatoria en tabla particionada: debe incluir la columna de partición)
    CONSTRAINT pk_mediciones_diarias PRIMARY KEY (indicativo, fecha),

    -- Clave foránea hacia estaciones
    CONSTRAINT fk_mediciones_estacion
        FOREIGN KEY (indicativo) REFERENCES meteo.estaciones (indicativo)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    -- Constraints
    CONSTRAINT fecha_indicativo_unique
        UNIQUE (fecha, indicativo)
)
PARTITION BY RANGE (fecha);

-- 4) PARTICIONES ANUALES
-- ---------------------------------------------------------------------
-- Beneficios de particionar por año:
-- - Si filtras por fecha, Postgres solo abre las particiones que caigan en la fecha de la consulta, ignorando el resto.
-- - Puedes insertar en varias particiones en paralelo.
-- - Puedes eliminar particiones antiguas fácilmente (DROP TABLE meteo.mediciones_2014;) eliminando datos de ese año.
-- - Entre otros beneficios.
-- ---------------------------------------------------------------------
CREATE TABLE meteo.mediciones_2016 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2016-01-01') TO ('2017-01-01');
CREATE TABLE meteo.mediciones_2017 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2017-01-01') TO ('2018-01-01');
CREATE TABLE meteo.mediciones_2018 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE meteo.mediciones_2019 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE meteo.mediciones_2020 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE meteo.mediciones_2021 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE meteo.mediciones_2022 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE meteo.mediciones_2023 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE meteo.mediciones_2024 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE meteo.mediciones_2025 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE meteo.mediciones_2026 PARTITION OF meteo.mediciones_diarias
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Partición de seguridad para fechas fuera de rango (evita que INSERT falle)
CREATE TABLE meteo.mediciones_default PARTITION OF meteo.mediciones_diarias DEFAULT;

-- 5) ÍNDICES ADICIONALES
-- ---------------------------------------------------------------------
-- La PK (indicativo, fecha) ya crea un índice por partición automáticamente.
-- Añadimos uno solo por fecha, útil para consultas que no filtran por estación.
CREATE INDEX idx_med_fecha ON meteo.mediciones_diarias (fecha);

-- 6) VISTA ÚLTIMOS 10 DÍAS
-- ---------------------------------------------------------------------
CREATE VIEW meteo.ultimos_10_dias AS
SELECT
    indicativo,
    fecha,
    tmed, prec, tmin, tmax, hrmedia, sol, velmedia, racha, dir, presmax, presmin, hrmax, hrmin, pintmax, precAcum, variasHoras, precIp
FROM meteo.mediciones_diarias
WHERE fecha >= CURRENT_DATE - INTERVAL '10 days';
