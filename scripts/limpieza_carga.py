"""
limpieza_carga.py
Limpieza del dataset de colisiones de Nueva York y carga a Oracle.

Estructura esperada del proyecto:
    Proyecto_NYC_Crashes/
        data/raw/Motor_Vehicle_Collisions_original.csv   (nunca se modifica)
        data/processed/                                  (CSV limpios, se crea solo)
        reports/                                         (registros, se crea solo)
        scripts/limpieza_carga.py

Uso:
    python scripts/limpieza_carga.py                  limpia y carga a Oracle
    python scripts/limpieza_carga.py --solo-limpiar   limpia sin tocar Oracle
    python scripts/limpieza_carga.py ruta/al/archivo.csv

El usuario y la contraseña de Oracle se piden al ejecutar. No se guardan
en ningún archivo.

Reglas aplicadas (ninguna elimina accidentes):
  1. CRASH DATE + CRASH TIME se combinan en CRASH_DATE (tipo DATE).
  2. LOCATION se descarta (duplica LATITUDE y LONGITUDE).
  3. Los valores vacíos se cargan como NULL.
  4. Coordenadas (0, 0) se reemplazan por NULL.
  5. PERSONS_KILLED vacío o distinto a la suma = suma de fallecidos por categoría.
  6. PERSONS_INJURED distinto a la suma por categoría = la suma.
  7. Las 5 columnas de vehículos pasan a la tabla COLLISION_VEHICLE
     (una fila por vehículo; se omiten los vehículos sin tipo ni factor).
"""
import argparse
import getpass
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RAW_DEFAULT = BASE / "data" / "raw" / "Motor_Vehicle_Collisions_original.csv"
PROCESSED = BASE / "data" / "processed"
REPORTS = BASE / "reports"

ORACLE = {"host": "localhost", "port": 1521, "service": "XEPDB1"}

# Columna del CSV -> columna de la tabla COLLISIONS (en orden de la tabla)
COLUMNAS_COLLISIONS = [
    ("COLLISION_ID", "COLLISION_ID"),
    ("CRASH_DATE", "CRASH_DATE"),  # se construye en el paso 1
    ("BOROUGH", "BOROUGH"),
    ("ZIP CODE", "ZIP_CODE"),
    ("LATITUDE", "LATITUDE"),
    ("LONGITUDE", "LONGITUDE"),
    ("ON STREET NAME", "ON_STREET_NAME"),
    ("CROSS STREET NAME", "CROSS_STREET_NAME"),
    ("OFF STREET NAME", "OFF_STREET_NAME"),
    ("NUMBER OF PERSONS INJURED", "PERSONS_INJURED"),
    ("NUMBER OF PERSONS KILLED", "PERSONS_KILLED"),
    ("NUMBER OF PEDESTRIANS INJURED", "PEDESTRIANS_INJURED"),
    ("NUMBER OF PEDESTRIANS KILLED", "PEDESTRIANS_KILLED"),
    ("NUMBER OF CYCLIST INJURED", "CYCLISTS_INJURED"),
    ("NUMBER OF CYCLIST KILLED", "CYCLISTS_KILLED"),
    ("NUMBER OF MOTORIST INJURED", "MOTORIST_INJURED"),
    ("NUMBER OF MOTORIST KILLED", "MOTORIST_KILLED"),
]
COLUMNAS_CONTEO = [n for o, n in COLUMNAS_COLLISIONS if n.startswith(
    ("PERSONS_", "PEDESTRIANS_", "CYCLISTS_", "MOTORIST_"))]
PARTES_HERIDOS = ["PEDESTRIANS_INJURED", "CYCLISTS_INJURED", "MOTORIST_INJURED"]
PARTES_FALLECIDOS = ["PEDESTRIANS_KILLED", "CYCLISTS_KILLED", "MOTORIST_KILLED"]

COLUMNAS_VEHICULOS = (
    [f"VEHICLE TYPE CODE {i}" for i in range(1, 6)]
    + [f"CONTRIBUTING FACTOR VEHICLE {i}" for i in range(1, 6)]
)
COLUMNAS_TEXTO = ["BOROUGH", "ZIP CODE", "ON STREET NAME", "CROSS STREET NAME",
                  "OFF STREET NAME"] + COLUMNAS_VEHICULOS

# Largo máximo de cada columna de texto en Oracle
LARGOS = {"BOROUGH": 20, "ZIP_CODE": 10, "ON_STREET_NAME": 100,
          "CROSS_STREET_NAME": 100, "OFF_STREET_NAME": 100,
          "VEHICLE_TYPE": 100, "CONTRIBUTING_FACTOR": 100}

DDL = [
    ("""BEGIN EXECUTE IMMEDIATE 'DROP TABLE COLLISION_VEHICLE PURGE';
        EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;"""),
    ("""BEGIN EXECUTE IMMEDIATE 'DROP TABLE COLLISIONS PURGE';
        EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;"""),
    """CREATE TABLE COLLISIONS (
        COLLISION_ID         NUMBER(12)    NOT NULL,
        CRASH_DATE           DATE          NOT NULL,
        BOROUGH              VARCHAR2(20),
        ZIP_CODE             VARCHAR2(10),
        LATITUDE             NUMBER(9,6),
        LONGITUDE            NUMBER(9,6),
        ON_STREET_NAME       VARCHAR2(100),
        CROSS_STREET_NAME    VARCHAR2(100),
        OFF_STREET_NAME      VARCHAR2(100),
        PERSONS_INJURED      NUMBER(3),
        PERSONS_KILLED       NUMBER(3),
        PEDESTRIANS_INJURED  NUMBER(3),
        PEDESTRIANS_KILLED   NUMBER(3),
        CYCLISTS_INJURED     NUMBER(3),
        CYCLISTS_KILLED      NUMBER(3),
        MOTORIST_INJURED     NUMBER(3),
        MOTORIST_KILLED      NUMBER(3),
        CONSTRAINT PK_COLLISIONS PRIMARY KEY (COLLISION_ID),
        CONSTRAINT CK_COLL_CONTEOS CHECK (
            NVL(PERSONS_INJURED, 0) >= 0 AND NVL(PERSONS_KILLED, 0) >= 0 AND
            NVL(PEDESTRIANS_INJURED, 0) >= 0 AND NVL(PEDESTRIANS_KILLED, 0) >= 0 AND
            NVL(CYCLISTS_INJURED, 0) >= 0 AND NVL(CYCLISTS_KILLED, 0) >= 0 AND
            NVL(MOTORIST_INJURED, 0) >= 0 AND NVL(MOTORIST_KILLED, 0) >= 0)
    )""",
    """CREATE TABLE COLLISION_VEHICLE (
        VEHICLE_ID           NUMBER(10)    NOT NULL,
        COLLISION_ID         NUMBER(12)    NOT NULL,
        VEHICLE_CODE         NUMBER(1)     NOT NULL,
        VEHICLE_TYPE         VARCHAR2(100),
        CONTRIBUTING_FACTOR  VARCHAR2(100),
        CONSTRAINT PK_COLLISION_VEHICLE PRIMARY KEY (VEHICLE_ID),
        CONSTRAINT FK_VEH_COLLISION FOREIGN KEY (COLLISION_ID)
            REFERENCES COLLISIONS (COLLISION_ID),
        CONSTRAINT CK_VEH_CODE CHECK (VEHICLE_CODE BETWEEN 1 AND 5),
        CONSTRAINT CK_VEH_INFO CHECK (VEHICLE_TYPE IS NOT NULL
                                      OR CONTRIBUTING_FACTOR IS NOT NULL),
        CONSTRAINT UQ_VEH_COLL_CODE UNIQUE (COLLISION_ID, VEHICLE_CODE)
    )""",
    "CREATE INDEX IX_COLL_DATE    ON COLLISIONS (CRASH_DATE)",
    "CREATE INDEX IX_COLL_BOROUGH ON COLLISIONS (BOROUGH)",
    "CREATE INDEX IX_VEH_TYPE     ON COLLISION_VEHICLE (VEHICLE_TYPE)",
]


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------
def limpiar(df):
    """Devuelve (collisions, vehicles, log, exc_heridos, exc_fallecidos)."""
    log = []

    # Va mostrando en la terminal el proceso
    def reg(paso, detalle, valor):
        log.append({"paso": paso, "detalle": detalle, "valor": valor})
        print(f"[{paso}] {detalle}: {valor}")

    df = df.copy()
    reg("0. Inicio", "Filas leídas", len(df))
    if df["COLLISION_ID"].isna().any() or df["COLLISION_ID"].duplicated().any():
        raise SystemExit("COLLISION_ID tiene nulos o repetidos: no se puede usar como clave primaria.")

    # Espacios sobrantes y textos vacíos -> NULL
    cambios = 0
    for c in COLUMNAS_TEXTO:
        original = df[c]
        limpio = original.str.strip()
        limpio = limpio.mask(limpio == "")
        cambios += int((original.notna() & ~(original == limpio).fillna(False)).sum())
        df[c] = limpio
    reg("0. Texto", "Valores de texto corregidos (espacios sobrantes o vacíos)", cambios)

    # 1. Fecha + hora en un solo campo DATE
    fecha = pd.to_datetime(df["CRASH DATE"], errors="coerce").dt.normalize()
    t = df["CRASH TIME"].astype("string").str.strip()
    es_hm = t.str.fullmatch(r"\d{1,2}:\d{2}").fillna(False).astype(bool)
    t = t.mask(es_hm, t + ":00")
    hora = pd.to_timedelta(t, errors="coerce")
    reg("1. Fecha", "Filas sin hora válida (quedan a las 00:00)", int(hora.isna().sum()))
    df["CRASH_DATE"] = fecha + hora.fillna(pd.Timedelta(0))
    if df["CRASH_DATE"].isna().any():
        raise SystemExit("Hay fechas que no se pudieron interpretar: CRASH_DATE no admite nulos.")
    reg("1. Fecha", "Rango de CRASH_DATE", f"{df['CRASH_DATE'].min()} a {df['CRASH_DATE'].max()}")

    # 2. LOCATION se descarta; se renombran columnas
    sin_coord = df["LOCATION"].notna() & (df["LATITUDE"].isna() | df["LONGITUDE"].isna())
    reg("2. LOCATION", "Filas con LOCATION pero sin coordenadas (se pierden al descartarla)", int(sin_coord.sum()))
    c = df[[o for o, _ in COLUMNAS_COLLISIONS]].copy()
    c.columns = [n for _, n in COLUMNAS_COLLISIONS]

    # Tipos numéricos
    for col in COLUMNAS_CONTEO:
        c[col] = pd.to_numeric(c[col], errors="coerce").round().astype("Int64")
    c["LATITUDE"] = pd.to_numeric(c["LATITUDE"], errors="coerce")
    c["LONGITUDE"] = pd.to_numeric(c["LONGITUDE"], errors="coerce")
    c["COLLISION_ID"] = c["COLLISION_ID"].astype("int64")

    # 4. Coordenadas (0, 0) -> NULL
    ceros = (c["LATITUDE"] == 0) & (c["LONGITUDE"] == 0)
    reg("4. Coordenadas", "Filas con (0, 0) convertidas a NULL", int(ceros.sum()))
    nulos_antes = int(c["LATITUDE"].isna().sum())
    c.loc[ceros, ["LATITUDE", "LONGITUDE"]] = np.nan
    reg("4. Coordenadas", "LATITUDE nulos antes -> después", f"{nulos_antes} -> {int(c['LATITUDE'].isna().sum())}")

    # 5. PERSONS_KILLED vacío = suma de categorías
    ok_k = c[PARTES_FALLECIDOS].notna().all(axis=1)
    suma_k = c[PARTES_FALLECIDOS].sum(axis=1)
    nulo_k = c["PERSONS_KILLED"].isna()
    reg("5. Fallecidos", "PERSONS_KILLED nulos antes", int(nulo_k.sum()))
    c.loc[nulo_k & ok_k, "PERSONS_KILLED"] = suma_k[nulo_k & ok_k]
    reg("5. Fallecidos", "Rellenados con la suma de categorías", int((nulo_k & ok_k).sum()))
    reg("5. Fallecidos", "Siguen nulos (faltan categorías)", int((nulo_k & ~ok_k).sum()))
    dif_k = ok_k & c["PERSONS_KILLED"].notna() & (c["PERSONS_KILLED"] != suma_k).fillna(False)
    exc_fallecidos = c.loc[dif_k, ["COLLISION_ID", "PERSONS_KILLED"] + PARTES_FALLECIDOS].copy()
    exc_fallecidos = exc_fallecidos.rename(columns={"PERSONS_KILLED": "TOTAL_ORIGINAL"})
    exc_fallecidos["SUMA_CATEGORIAS"] = suma_k[dif_k]
    c.loc[dif_k, "PERSONS_KILLED"] = suma_k[dif_k]
    reg("5. Fallecidos", "Total corregido por no coincidir con la suma (ver excepciones)", int(dif_k.sum()))

    # 6. PERSONS_INJURED distinto a la suma (o vacío) = la suma
    ok_i = c[PARTES_HERIDOS].notna().all(axis=1)
    suma_i = c[PARTES_HERIDOS].sum(axis=1)
    nulo_i = c["PERSONS_INJURED"].isna() & ok_i
    dif_i = ok_i & c["PERSONS_INJURED"].notna() & (c["PERSONS_INJURED"] != suma_i).fillna(False)
    exc_heridos = c.loc[dif_i, ["COLLISION_ID", "PERSONS_INJURED"] + PARTES_HERIDOS].copy()
    exc_heridos = exc_heridos.rename(columns={"PERSONS_INJURED": "TOTAL_ORIGINAL"})
    exc_heridos["SUMA_CATEGORIAS"] = suma_i[dif_i]
    c.loc[dif_i | nulo_i, "PERSONS_INJURED"] = suma_i[dif_i | nulo_i]
    reg("6. Heridos", "Total corregido por no coincidir con la suma", int(dif_i.sum()))
    reg("6. Heridos", "Total vacío rellenado con la suma", int(nulo_i.sum()))

    # 7. Tabla de vehículos (formato largo)
    bloques = []
    for i in range(1, 6):
        b = pd.DataFrame({
            "_orden": df.index,
            "COLLISION_ID": df["COLLISION_ID"].astype("int64"),
            "VEHICLE_CODE": i,
            "VEHICLE_TYPE": df[f"VEHICLE TYPE CODE {i}"],
            "CONTRIBUTING_FACTOR": df[f"CONTRIBUTING FACTOR VEHICLE {i}"],
        })
        bloques.append(b)
    v = pd.concat(bloques, ignore_index=True)
    posibles = len(v)
    v = v.dropna(subset=["VEHICLE_TYPE", "CONTRIBUTING_FACTOR"], how="all")
    v = v.sort_values(["_orden", "VEHICLE_CODE"]).drop(columns="_orden").reset_index(drop=True)
    v.insert(0, "VEHICLE_ID", np.arange(1, len(v) + 1))
    reg("7. Vehículos", "Posiciones de vehículo revisadas (5 por accidente)", posibles)
    reg("7. Vehículos", "Filas vacías omitidas (sin tipo ni factor)", posibles - len(v))
    reg("7. Vehículos", "Filas en COLLISION_VEHICLE", len(v))
    reg("7. Vehículos", "Accidentes sin ningún vehículo registrado",
        int(len(c) - v["COLLISION_ID"].nunique()))

    # Control de largos antes de cargar
    for nombre, serie in {**{k: c[k] for k in ["BOROUGH", "ZIP_CODE", "ON_STREET_NAME",
                                                 "CROSS_STREET_NAME", "OFF_STREET_NAME"]},
                          "VEHICLE_TYPE": v["VEHICLE_TYPE"],
                          "CONTRIBUTING_FACTOR": v["CONTRIBUTING_FACTOR"]}.items():
        maximo = int(serie.str.len().max()) if serie.notna().any() else 0
        reg("8. Largos", f"{nombre}: máximo {maximo} (límite {LARGOS[nombre]})", "OK" if maximo <= LARGOS[nombre] else "EXCEDE")
        if maximo > LARGOS[nombre]:
            raise SystemExit(f"{nombre} supera VARCHAR2({LARGOS[nombre]}). Ajustá el tamaño en DDL y LARGOS.")

    reg("9. Final", "Filas en COLLISIONS", len(c))
    return c, v, pd.DataFrame(log), exc_heridos, exc_fallecidos


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------
def a_python(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def filas(df):
    return [tuple(a_python(x) for x in fila)
            for fila in df.astype(object).itertuples(index=False, name=None)]


def insertar(cur, oracledb, tabla, df, tamanos, lote=20000):
    columnas = list(df.columns)
    sql = (f"INSERT INTO {tabla} ({', '.join(columnas)}) "
           f"VALUES ({', '.join(':' + str(i + 1) for i in range(len(columnas)))})")
    datos = filas(df)
    for i in range(0, len(datos), lote):
        cur.setinputsizes(*tamanos)
        cur.executemany(sql, datos[i:i + lote])
    print(f"Insertadas {len(datos)} filas en {tabla}")


def cargar_oracle(c, v):
    import oracledb

    usuario = input("Usuario de Oracle: ").strip()
    clave = getpass.getpass("Contraseña de Oracle: ")
    dsn = f"{ORACLE['host']}:{ORACLE['port']}/{ORACLE['service']}"
    N, D = oracledb.DB_TYPE_NUMBER, oracledb.DB_TYPE_DATE
    conn = oracledb.connect(user=usuario, password=clave, dsn=dsn)
    try:
        cur = conn.cursor()
        for sentencia in DDL:
            cur.execute(sentencia)
        print("Tablas creadas.")
        insertar(cur, oracledb, "COLLISIONS", c,
                 [N, D, 20, 10, N, N, 100, 100, 100, N, N, N, N, N, N, N, N])
        insertar(cur, oracledb, "COLLISION_VEHICLE", v, [N, N, N, 100, 100])
        conn.commit()
        return verificar(cur, c, v)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verificar(cur, c, v):
    def uno(sql):
        cur.execute(sql)
        return cur.fetchone()[0]

    esperado_k = int(c["PERSONS_KILLED"].sum())
    esperado_i = int(c["PERSONS_INJURED"].sum())
    pruebas = [
        ("Filas en COLLISIONS", len(c), uno("SELECT COUNT(*) FROM COLLISIONS")),
        ("COLLISION_ID distintos", len(c), uno("SELECT COUNT(DISTINCT COLLISION_ID) FROM COLLISIONS")),
        ("Filas en COLLISION_VEHICLE", len(v), uno("SELECT COUNT(*) FROM COLLISION_VEHICLE")),
        ("Vehículos sin accidente (debe ser 0)", 0, uno(
            "SELECT COUNT(*) FROM COLLISION_VEHICLE v WHERE NOT EXISTS "
            "(SELECT 1 FROM COLLISIONS c WHERE c.COLLISION_ID = v.COLLISION_ID)")),
        ("Suma de PERSONS_KILLED", esperado_k, uno("SELECT NVL(SUM(PERSONS_KILLED), 0) FROM COLLISIONS")),
        ("Suma de PERSONS_INJURED", esperado_i, uno("SELECT NVL(SUM(PERSONS_INJURED), 0) FROM COLLISIONS")),
        ("LATITUDE nulos", int(c["LATITUDE"].isna().sum()), uno("SELECT COUNT(*) FROM COLLISIONS WHERE LATITUDE IS NULL")),
        ("Accidentes sin vehículos", int(len(c) - v["COLLISION_ID"].nunique()), uno(
            "SELECT COUNT(*) FROM COLLISIONS c LEFT JOIN COLLISION_VEHICLE v "
            "ON v.COLLISION_ID = c.COLLISION_ID WHERE v.VEHICLE_ID IS NULL")),
    ]
    filas_v = []
    print("\nVerificación en Oracle:")
    for nombre, esperado, obtenido in pruebas:
        estado = "OK" if int(esperado) == int(obtenido) else "DIFERENCIA"
        filas_v.append({"prueba": nombre, "esperado": esperado, "oracle": obtenido, "estado": estado})
        print(f"  {estado:10} {nombre}: esperado {esperado}, Oracle {obtenido}")
    return pd.DataFrame(filas_v)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=str(RAW_DEFAULT))
    ap.add_argument("--solo-limpiar", action="store_true", help="no conectar a Oracle")
    args = ap.parse_args()

    if not Path(args.csv).is_file():
        sys.exit(f"No existe el archivo: {args.csv}")

    texto = {c: "str" for c in COLUMNAS_TEXTO + ["CRASH TIME", "CRASH DATE", "LOCATION"]}
    df = pd.read_csv(args.csv, dtype=texto)
    faltan = [c for c in ["COLLISION_ID", "CRASH DATE", "CRASH TIME", "LOCATION", "LATITUDE", "LONGITUDE"]
              + [o for o, n in COLUMNAS_COLLISIONS if o != "CRASH_DATE"] + COLUMNAS_VEHICULOS
              if c not in df.columns]
    if faltan:
        sys.exit(f"Faltan columnas en el CSV: {faltan}")

    c, v, log, exc_i, exc_k = limpiar(df)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    c.to_csv(PROCESSED / "collisions_clean.csv", index=False, encoding="utf-8-sig")
    v.to_csv(PROCESSED / "collision_vehicle_clean.csv", index=False, encoding="utf-8-sig")
    log.to_csv(REPORTS / "limpieza_log.csv", index=False, encoding="utf-8-sig")
    exc_i.to_csv(REPORTS / "excepciones_heridos.csv", index=False, encoding="utf-8-sig")
    exc_k.to_csv(REPORTS / "excepciones_fallecidos.csv", index=False, encoding="utf-8-sig")
    print(f"\nArchivos guardados en {PROCESSED} y {REPORTS}")

    if args.solo_limpiar:
        print("Modo --solo-limpiar: no se cargó nada a Oracle.")
        return
    ver = cargar_oracle(c, v)
    ver.to_csv(REPORTS / "verificacion_oracle.csv", index=False, encoding="utf-8-sig")
    print(f"Verificación guardada en {REPORTS / 'verificacion_oracle.csv'}")


if __name__ == "__main__":
    main()