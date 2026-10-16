-- consultas_analisis.sql
-- Etapa de Data Analytics: consultas sobre Oracle (tablas COLLISIONS y COLLISION_VEHICLE)
-- Ejecutar en SQL Developer con "Ejecutar script" (F5), conectado como <usuario> a XEPDB1.
----------------------------------------------------------------------
-- Q0. Revisar categorias antes de analizar (ver que valores hay realmente)
----------------------------------------------------------------------
SELECT CONTRIBUTING_FACTOR, COUNT(*) AS filas
FROM   COLLISION_VEHICLE
GROUP  BY CONTRIBUTING_FACTOR
ORDER  BY filas DESC
FETCH FIRST 20 ROWS ONLY;

SELECT VEHICLE_TYPE, COUNT(*) AS filas
FROM   COLLISION_VEHICLE
GROUP  BY VEHICLE_TYPE
ORDER  BY filas DESC
FETCH FIRST 30 ROWS ONLY;

----------------------------------------------------------------------
-- Q1. Evolucion mensual de colisiones, heridos y fallecidos
-- Nota: el ultimo mes esta incompleto (datos hasta el 11 de junio de 2026),
-- por eso se agrega el promedio diario para poder compararlo.
----------------------------------------------------------------------
SELECT TO_CHAR(CRASH_DATE, 'YYYY-MM')                          AS mes,
       COUNT(*)                                                 AS colisiones,
       SUM(PERSONS_INJURED)                                     AS heridos,
       SUM(PERSONS_KILLED)                                      AS fallecidos,
       -- TRUNC(fecha) elimina la hora de la fecha
       COUNT(DISTINCT TRUNC(CRASH_DATE))                        AS dias_con_datos,
       ROUND(COUNT(*) / COUNT(DISTINCT TRUNC(CRASH_DATE)), 1)   AS colisiones_por_dia
FROM   COLLISIONS
GROUP  BY TO_CHAR(CRASH_DATE, 'YYYY-MM')
ORDER  BY mes;

----------------------------------------------------------------------
-- Q2. Distribucion por distrito (los nulos se muestran como SIN DATO)
----------------------------------------------------------------------
       -- NVL(valor, reemplazo): Reemplaza un valor null por lo que se indique 
SELECT NVL(BOROUGH, 'SIN DATO')                                    AS distrito,
       COUNT(*)                                                    AS colisiones,
       -- Porcentaje que representa a un grupo respecto a total de registros, 
       -- OVER() hace que se calcule sobre el conjunto completo de resultados, 
       -- no solo el grupo actual.
       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS pct_colisiones,
       SUM(PERSONS_INJURED)                                        AS heridos,
       SUM(PERSONS_KILLED)                                         AS fallecidos
FROM   COLLISIONS
GROUP  BY NVL(BOROUGH, 'SIN DATO')
ORDER  BY colisiones DESC;

----------------------------------------------------------------------
-- Q3a. Colisiones por hora del dia
----------------------------------------------------------------------
SELECT TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24'))                      AS hora,
       COUNT(*)                                                    AS colisiones,
       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS pct
FROM   COLLISIONS
GROUP  BY TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24'))
ORDER  BY hora;

----------------------------------------------------------------------
-- Q3b. Colisiones por dia de la semana (1 = lunes ... 7 = domingo)
-- Se calcula con TRUNC(...,'IW') para que no dependa del idioma de Oracle.
----------------------------------------------------------------------
SELECT num_dia,
       CASE num_dia WHEN 1 THEN 'Lunes'  WHEN 2 THEN 'Martes'
                    WHEN 3 THEN 'Miercoles' WHEN 4 THEN 'Jueves'
                    WHEN 5 THEN 'Viernes' WHEN 6 THEN 'Sabado'
                    ELSE 'Domingo' END                              AS dia,
       COUNT(*)                                                     AS colisiones,
       SUM(PERSONS_INJURED)                                         AS heridos,
       SUM(PERSONS_KILLED)                                          AS fallecidos
FROM  (SELECT c.*,
              -- TRUNC(CRASH_DATE, 'IW'): Devuelve el lunes de la semana en la
              -- que ocurrió el accidente, así es como calcula el día en que  
              -- ocurrió el accidente
              TRUNC(CRASH_DATE) - TRUNC(CRASH_DATE, 'IW') + 1       AS num_dia
       FROM   COLLISIONS c)
GROUP  BY num_dia
ORDER  BY num_dia;

----------------------------------------------------------------------
-- Q3c. Dia de la semana x hora 
----------------------------------------------------------------------
       -- TRUNC(CRASH_DATE, 'IW'): Devuelve el lunes de la semana en la
       -- que ocurrió el accidente, así es como calcula el día en que  
       -- ocurrió el accidente
SELECT TRUNC(CRASH_DATE) - TRUNC(CRASH_DATE, 'IW') + 1              AS num_dia,
       TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24'))                       AS hora,
       COUNT(*)                                                     AS colisiones
FROM   COLLISIONS
GROUP  BY TRUNC(CRASH_DATE) - TRUNC(CRASH_DATE, 'IW') + 1,
          TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24'))
ORDER  BY num_dia, hora;

----------------------------------------------------------------------
-- Q4. Personas afectadas segun tipo de usuario
----------------------------------------------------------------------
SELECT tipo_usuario, heridos, fallecidos,
       /* NULLIF(valor, valor_incompatible) devuelve null si el valor 
       incompatible es 0, sirve para evitar una división entre 0*/
       ROUND(100 * fallecidos / NULLIF(heridos + fallecidos, 0), 2) AS pct_fallecidos_de_victimas
       /* Select 'Peatones' genera campo peatones en la fila que se mostraran 
       los datos*/
FROM  (SELECT 'Peatones'   AS tipo_usuario,
              SUM(PEDESTRIANS_INJURED) AS heridos, 
              SUM(PEDESTRIANS_KILLED) AS fallecidos
       FROM COLLISIONS
       /* UNION ALL une resultados de dos o más consultas, conservando todos los 
       registros*/
       UNION ALL
       SELECT 'Ciclistas',
              SUM(CYCLISTS_INJURED), SUM(CYCLISTS_KILLED)
       FROM COLLISIONS
       UNION ALL
       SELECT 'Motoristas',
              SUM(MOTORIST_INJURED), SUM(MOTORIST_KILLED)
       FROM COLLISIONS)
ORDER  BY heridos DESC;

----------------------------------------------------------------------
-- Q5a. Tipos de vehiculo mas frecuentes en colisiones con heridos
-- Se cuentan colisiones distintas, no filas: una colision con dos sedanes cuenta una vez.
----------------------------------------------------------------------
SELECT v.VEHICLE_TYPE,
       COUNT(DISTINCT v.COLLISION_ID)                               AS colisiones_con_heridos,
       ROUND(100 * COUNT(DISTINCT v.COLLISION_ID) /
            (SELECT COUNT(*) FROM COLLISIONS WHERE PERSONS_INJURED > 0), 2) AS pct_del_total
FROM   COLLISION_VEHICLE v
JOIN   COLLISIONS c ON c.COLLISION_ID = v.COLLISION_ID
WHERE  c.PERSONS_INJURED > 0
  AND  v.VEHICLE_TYPE IS NOT NULL
GROUP  BY v.VEHICLE_TYPE
ORDER  BY colisiones_con_heridos DESC
FETCH FIRST 10 ROWS ONLY;

----------------------------------------------------------------------
-- Q5b. Factores contribuyentes mas frecuentes en colisiones con heridos
-- (incluye "UNSPECIFIED" si existe; revisar Q0 para decidir si se excluye)
----------------------------------------------------------------------
SELECT v.CONTRIBUTING_FACTOR,
       COUNT(DISTINCT v.COLLISION_ID)                               AS colisiones_con_heridos
FROM   COLLISION_VEHICLE v
JOIN   COLLISIONS c ON c.COLLISION_ID = v.COLLISION_ID
WHERE  c.PERSONS_INJURED > 0
  AND  v.CONTRIBUTING_FACTOR IS NOT NULL
GROUP  BY v.CONTRIBUTING_FACTOR
ORDER  BY colisiones_con_heridos DESC
FETCH FIRST 10 ROWS ONLY;

----------------------------------------------------------------------
/* Q6. Relacion entre la hora y los factores contribuyentes
Top 5 de factores en cada franja horaria. pct_en_franja = peso del factor dentro
de su franja. Una colision puede tener varios factores, por lo que el porcentaje
es sobre el total de factores, no de colisiones. */
----------------------------------------------------------------------
WITH base AS (
    SELECT TO_NUMBER(TO_CHAR(c.CRASH_DATE, 'HH24'))  AS hora,
           v.CONTRIBUTING_FACTOR                      AS factor,
           v.COLLISION_ID
    FROM   COLLISIONS c
    JOIN   COLLISION_VEHICLE v ON v.COLLISION_ID = c.COLLISION_ID
    WHERE  v.CONTRIBUTING_FACTOR IS NOT NULL
      AND  v.CONTRIBUTING_FACTOR <> 'UNSPECIFIED'
),
franjas AS (
    SELECT CASE WHEN hora BETWEEN 0  AND 5  THEN '1. Madrugada (00-05)'
                WHEN hora BETWEEN 6  AND 11 THEN '2. Manana (06-11)'
                WHEN hora BETWEEN 12 AND 17 THEN '3. Tarde (12-17)'
                ELSE                             '4. Noche (18-23)' END AS franja,
           factor,
           COLLISION_ID
    FROM   base
),
conteo AS (
    SELECT franja, factor, COUNT(DISTINCT COLLISION_ID) AS colisiones
    FROM   franjas
    GROUP  BY franja, factor
),
ranking AS (
    SELECT franja, factor, colisiones,
           ROUND(100 * colisiones / SUM(colisiones) OVER (PARTITION BY franja), 2) AS pct_en_franja,
           /*ROW_NUMBER numera las filas, OVER indica que es una función de 
           ventana, calcula el número mirando otras filas, pero no las junta, 
           PARTITION divide las filas en grupos de franja horaria*/
           ROW_NUMBER() OVER (PARTITION BY franja ORDER BY colisiones DESC)       AS posicion
    FROM   conteo
)
SELECT franja, posicion, factor, colisiones, pct_en_franja
FROM   ranking
WHERE  posicion <= 5
ORDER  BY franja, posicion;

----------------------------------------------------------------------
-- Q7. Tendencia mensual: variacion contra el mes anterior y media movil
-- Pregunta: la cantidad de colisiones sube, baja o se estabiliza con el tiempo?
-- Se usa colisiones POR DIA, para poder comparar meses de distinta duracion
-- y el ultimo mes, que esta incompleto (datos hasta el 11 de junio de 2026).
-- Tecnicas: CTE, LAG (valor del mes anterior), AVG con ventana de 3 meses.
----------------------------------------------------------------------
WITH mensual AS (
    SELECT TO_CHAR(CRASH_DATE, 'YYYY-MM')                           AS mes,
           COUNT(*)                                                  AS colisiones,
           COUNT(DISTINCT TRUNC(CRASH_DATE))                         AS dias_con_datos,
           SUM(PERSONS_INJURED)                                      AS heridos
    FROM   COLLISIONS
    GROUP  BY TO_CHAR(CRASH_DATE, 'YYYY-MM')
),
diario AS (
    SELECT mes, colisiones, heridos, dias_con_datos,
           ROUND(colisiones / dias_con_datos, 1)                     AS colisiones_por_dia
    FROM   mensual
),
con_anterior AS (
    SELECT d.*,
           /*LAG toma el valor de la fila anterior*/
           LAG(colisiones_por_dia) OVER (ORDER BY mes)               AS mes_anterior
    FROM   diario d
)
SELECT mes,
       colisiones,
       dias_con_datos,
       colisiones_por_dia,
       mes_anterior,
       ROUND(100 * (colisiones_por_dia - mes_anterior)
                 / NULLIF(mes_anterior, 0), 1)                       AS variacion_pct,
       ROUND(AVG(colisiones_por_dia) OVER (
                 /* ROWS BETWEEN 2 PRECEDING AND CURRENT ROW define los valores 
                 que usará, desde dos filas antes hasta la fila actual, es 
                 decir, tres filas */
                 ORDER BY mes ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 1) AS media_movil_3_meses
FROM   con_anterior
ORDER  BY mes;

----------------------------------------------------------------------
-- Q8. Indice de gravedad relativa por distrito y franja horaria
-- Pregunta: en que combinaciones de distrito y horario las colisiones
-- terminan en heridos con mas frecuencia que el promedio general?
-- indice_gravedad = (% de colisiones con heridos del grupo) / (% general)
--   > 1  mas grave que el promedio      < 1  menos grave
-- Solo grupos con al menos 100 colisiones, para no sacar conclusiones de pocos casos.
-- Tecnicas: CTE encadenadas, CASE, CROSS JOIN con el promedio global.
----------------------------------------------------------------------
WITH base AS (
    SELECT NVL(BOROUGH, 'SIN DATO')                                  AS distrito,
           CASE WHEN TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24')) BETWEEN 0  AND 5  THEN '1. MADRUGADA (00-05)'
                WHEN TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24')) BETWEEN 6  AND 11 THEN '2. MANANA (06-11)'
                WHEN TO_NUMBER(TO_CHAR(CRASH_DATE, 'HH24')) BETWEEN 12 AND 17 THEN '3. TARDE (12-17)'
                ELSE                                                               '4. NOCHE (18-23)' END AS franja,
           CASE WHEN PERSONS_INJURED > 0 THEN 1 ELSE 0 END           AS con_heridos,
           CASE WHEN PERSONS_KILLED  > 0 THEN 1 ELSE 0 END           AS con_fallecidos
    FROM   COLLISIONS
    WHERE  PERSONS_INJURED IS NOT NULL
),
grupos AS (
    SELECT distrito, franja,
           COUNT(*)              AS colisiones,
           SUM(con_heridos)      AS n_con_heridos,
           SUM(con_fallecidos)   AS n_con_fallecidos
    FROM   base
    GROUP  BY distrito, franja
),
tot AS (
    SELECT SUM(con_heridos) / COUNT(*) AS tasa_general
    FROM   base
)
SELECT g.distrito,
       g.franja,
       g.colisiones,
       ROUND(100 * g.n_con_heridos / g.colisiones, 1)                AS pct_con_heridos,
       ROUND(100 * t.tasa_general, 1)                                AS pct_general,
       ROUND((g.n_con_heridos / g.colisiones) / t.tasa_general, 2)   AS indice_gravedad,
       g.n_con_fallecidos                                            AS colisiones_con_fallecidos
FROM   grupos g
CROSS  JOIN tot t
WHERE  g.colisiones >= 100
ORDER  BY indice_gravedad DESC;

----------------------------------------------------------------------
-- Q9. Combinaciones de vehiculos que chocan entre si
-- Pregunta: que pares de tipos de vehiculo coinciden mas en una colision,
-- y cuales de esos pares terminan en heridos con mas frecuencia?
-- LEAST y GREATEST hacen que (SEDAN, BICYCLE) y (BICYCLE, SEDAN) sean la misma pareja.
-- Una colision con 3 vehiculos aporta varias parejas; el DISTINCT evita contar
-- dos veces la misma pareja dentro de la misma colision.
-- Tecnicas: autounion de COLLISION_VEHICLE, LEAST/GREATEST, DISTINCT, HAVING.
----------------------------------------------------------------------
WITH parejas AS (
    SELECT DISTINCT
           a.COLLISION_ID,
           -- LEAST toma el menor de los dos valores
           LEAST(a.VEHICLE_TYPE, b.VEHICLE_TYPE)     AS tipo_1,
           -- GREATEST toma el MAYOR de los dos valores
           GREATEST(a.VEHICLE_TYPE, b.VEHICLE_TYPE)  AS tipo_2
    FROM   COLLISION_VEHICLE a
    JOIN   COLLISION_VEHICLE b
           /* Lo que hace es juntar dos vehículos de la misma colisión y ordenar 
           según el código(el código indica nivel de involucración) */
           ON  b.COLLISION_ID = a.COLLISION_ID
           AND b.VEHICLE_CODE > a.VEHICLE_CODE
    WHERE  a.VEHICLE_TYPE IS NOT NULL
      AND  b.VEHICLE_TYPE IS NOT NULL
)
SELECT p.tipo_1 || ' + ' || p.tipo_2                                  AS combinacion,
       COUNT(*)                                                       AS colisiones,
       ROUND(100 * AVG(CASE WHEN c.PERSONS_INJURED > 0 THEN 1 ELSE 0 END), 1) AS pct_con_heridos,
       SUM(c.PERSONS_INJURED)                                         AS heridos,
       SUM(c.PERSONS_KILLED)                                          AS fallecidos
FROM   parejas p
JOIN   COLLISIONS c ON c.COLLISION_ID = p.COLLISION_ID
GROUP  BY p.tipo_1, p.tipo_2
HAVING COUNT(*) >= 50
ORDER  BY colisiones DESC
FETCH FIRST 15 ROWS ONLY;

/*
Q10. concentracion geografica de las colisiones
Pregunta: ¿Las colisiones se reparten parejo por la ciudad o se concentran
en pocos lugares?
Se agrupan en celdas de ~100 m (coordenadas redondeadas a 3 decimales).
Las coordenadas (0, 0) quedaron como NULL en la limpieza y no entran.
*/
WITH celdas AS (
    SELECT ROUND(LATITUDE, 3)    AS lat,
           ROUND(LONGITUDE, 3)   AS lon,
           COUNT(*)              AS colisiones,
           SUM(PERSONS_INJURED)  AS heridos,
           SUM(PERSONS_KILLED)   AS fallecidos
    FROM   COLLISIONS
    WHERE  LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
    GROUP  BY ROUND(LATITUDE, 3), ROUND(LONGITUDE, 3)
),
ranking AS (
    SELECT lat, lon, colisiones, heridos, fallecidos,
           /* RANK asigna un número de posición a cada fila dentro del resultado,
           basándose en el orden de la coumna especificada*/
           RANK() OVER (ORDER BY colisiones DESC)                                  AS posicion,
           ROUND(100 * colisiones / SUM(colisiones) OVER (), 2)                    AS pct_del_total,
           /* ROUND() calcula el porcentaje acumulado de colisiones, ordenando 
           las ubicaciones desde la que tiene más colisiones hasta la que tiene
           menos, SUM() calcula la suma acumulada de las colisiones.
           UNBOUNDED PRECEDING: comienza desde la primera fila del conjunto
           ordenado.*/
           ROUND(100 * SUM(colisiones) OVER (ORDER BY colisiones DESC, lat, lon
                                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                     / SUM(colisiones) OVER (), 2)                                 AS pct_acumulado
    FROM   celdas
)
SELECT posicion, lat, lon, colisiones, heridos, fallecidos, pct_del_total, pct_acumulado
FROM   ranking
WHERE  posicion <= 15
ORDER  BY posicion, lat, lon;
