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
  8. Todos los textos (VARCHAR) se pasan a mayúsculas y sin espacios sobrantes.
"""

# `argparse` (biblioteca estándar): permite que el script reciba opciones escritas
# en la terminal, como --solo-limpiar o la ruta de un CSV.
import argparse

# `getpass` (biblioteca estándar): pide un texto en la terminal SIN mostrarlo en
# pantalla. Se usa para la contraseña de Oracle.
import getpass

import sys

from pathlib import Path

# `numpy` (alias np): biblioteca de cálculo numérico sobre la que está construida
# pandas. Aquí se usa para el valor "vacío" (np.nan), para generar secuencias de
# números (np.arange) y para reconocer tipos numéricos (np.integer, np.floating).
import numpy as np

# `pandas` : biblioteca para trabajar con datos en forma de tabla.
import pandas as pd

# Carpeta raíz del proyecto:
#   __file__            ruta de este archivo .py
#   Path(__file__)      la convierte en objeto Path
#   .resolve()          la vuelve absoluta y limpia
#   .parent             sube un nivel: carpeta scripts/
#   .parent (otra vez)  sube otro nivel: la raíz del proyecto
# Gracias a esto el script funciona sin importar desde qué carpeta se ejecute.
BASE = Path(__file__).resolve().parent.parent

# El operador `/` une partes de una ruta. Es el CSV original, que nunca se modifica.
RAW_DEFAULT = BASE / "data" / "raw" / "Motor_Vehicle_Collisions_original.csv"

# Carpeta de los CSV limpios.
PROCESSED = BASE / "data" / "processed"

# Carpeta de los registros de la limpieza (log, excepciones, verificación).
REPORTS = BASE / "reports"

# Datos de conexión a Oracle que NO son secretos. El usuario y la contraseña no se
# escriben aquí: se piden al ejecutar.
#   service  nombre del servicio (XEPDB1 = base de datos del usuario en Oracle XE 21c)
ORACLE = {"host": "localhost", "port": 1521, "service": "XEPDB1"}

# Lista de parejas (columna del CSV, columna de la tabla COLLISIONS), en el orden
# exacto de la tabla de Oracle. Sirve para seleccionar y renombrar las columnas de una vez.
COLUMNAS_COLLISIONS = [
    ("COLLISION_ID", "COLLISION_ID"),
    ("CRASH_DATE", "CRASH_DATE"), 
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

# Lista con los nombres (ya renombrados) de las 8 columnas de conteo de personas.
# Es una "lista por comprensión": recorre cada pareja (o, n) = (original, nuevo) y se
# queda con el nombre nuevo `n` solo si empieza con alguno de los prefijos indicados
# (startswith acepta una tupla de prefijos). Excluye así ZIP_CODE, LATITUDE, etc.
COLUMNAS_CONTEO = [n for o, n in COLUMNAS_COLLISIONS if n.startswith(
    ("PERSONS_", "PEDESTRIANS_", "CYCLISTS_", "MOTORIST_"))]

# Las tres categorías que deberían sumar el total de heridos y de fallecidos.
PARTES_HERIDOS = ["PEDESTRIANS_INJURED", "CYCLISTS_INJURED", "MOTORIST_INJURED"]
PARTES_FALLECIDOS = ["PEDESTRIANS_KILLED", "CYCLISTS_KILLED", "MOTORIST_KILLED"]

# Nombres de las 10 columnas de vehículos del CSV: 5 de tipo y 5 de factor contribuyente.
# Cada comprensión genera los nombres reemplazando {i} por 1, 2, 3, 4, 5
# (range(1, 6) da 1 a 5). El signo + une las dos listas en una sola.
COLUMNAS_VEHICULOS = (
    [f"VEHICLE TYPE CODE {i}" for i in range(1, 6)]
    + [f"CONTRIBUTING FACTOR VEHICLE {i}" for i in range(1, 6)]
)

# Todas las columnas de texto (las que serán VARCHAR2 en Oracle) con los nombres del CSV.
COLUMNAS_TEXTO = ["BOROUGH", "ZIP CODE", "ON STREET NAME", "CROSS STREET NAME",
                  "OFF STREET NAME"] + COLUMNAS_VEHICULOS

# Largo máximo permitido de cada columna de texto en Oracle (debe coincidir con el DDL).
# Se usa para avisar antes de cargar si algún texto no cabe.
LARGOS = {"BOROUGH": 20, "ZIP_CODE": 10, "ON_STREET_NAME": 100,
          "CROSS_STREET_NAME": 100, "OFF_STREET_NAME": 100,
          "VEHICLE_TYPE": 100, "CONTRIBUTING_FACTOR": 100}

# DDL = "Data Definition Language": las sentencias SQL que crean la estructura.
# Es una lista de textos; cada elemento es una sentencia que se ejecuta en orden.
# (Los comentarios van fuera de los textos: dentro cambiarían el SQL.)
DDL = [
    # 1) Borra la tabla hija si existe. Es un bloque PL/SQL (lenguaje procedural de Oracle):
    #    EXECUTE IMMEDIATE ejecuta una sentencia escrita como texto; PURGE la elimina sin
    #    dejarla en la papelera. Si la tabla no existe, Oracle da el error -942 y el bloque
    #    lo ignora (EXCEPTION ... IF SQLCODE != -942 THEN RAISE); cualquier otro error
    #    sí se propaga. Gracias a esto el script se puede ejecutar muchas veces.
    ("""BEGIN EXECUTE IMMEDIATE 'DROP TABLE COLLISION_VEHICLE PURGE';
        EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;"""),
    # 2) Borra la tabla padre. Va después de la hija porque la hija depende de ella
    #    (llave foránea); si se borrara primero, Oracle lo impediría.
    ("""BEGIN EXECUTE IMMEDIATE 'DROP TABLE COLLISIONS PURGE';
        EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;"""),
    # 3) Crea la tabla de colisiones. NUMBER(12) = número de hasta 12 dígitos;
    #    NUMBER(9,6) = 9 dígitos en total, 6 de ellos decimales; VARCHAR2(n) = texto de
    #    hasta n caracteres; DATE = fecha con hora. NOT NULL = el dato es obligatorio.
    #    PRIMARY KEY = identificador único de cada fila.
    #    CHECK = regla que debe cumplirse: ningún conteo de personas puede ser negativo
    #    (NVL(x, 0) cambia un nulo por 0 para que los vacíos no incumplan la regla).
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
    # 4) Crea la tabla de vehículos. FOREIGN KEY = llave foránea: cada vehículo debe
    #    pertenecer a una colisión que exista en COLLISIONS. Reglas CHECK: el código de
    #    vehículo va de 1 a 5, y cada fila debe tener al menos el tipo o el factor.
    #    UNIQUE: una colisión no puede tener dos veces el mismo número de vehículo.
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
    # 5) Índices: estructuras que aceleran las consultas que filtran o agrupan por
    #    fecha, distrito o tipo de vehículo.
    "CREATE INDEX IX_COLL_DATE    ON COLLISIONS (CRASH_DATE)",
    "CREATE INDEX IX_COLL_BOROUGH ON COLLISIONS (BOROUGH)",
    "CREATE INDEX IX_VEH_TYPE     ON COLLISION_VEHICLE (VEHICLE_TYPE)",
]

# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------
def limpiar(df):
    """Devuelve (collisions, vehicles, log, exc_heridos, exc_fallecidos)."""
    # `log` acumula una fila por cada medición hecha durante la limpieza. Al final
    # se guarda como reports/limpieza_log.csv: es la evidencia de antes y después.
    log = []

    # Función interna (definida dentro de `limpiar`): puede usar la lista `log` directamente.
    def reg(paso, detalle, valor):
        # Agrega un diccionario (una fila) al registro...
        log.append({"paso": paso, "detalle": detalle, "valor": valor})
        # ...y lo muestra también en la terminal para ver el avance.
        print(f"[{paso}] {detalle}: {valor}")

    # Trabaja sobre una copia para no modificar la tabla original que recibió.
    df = df.copy()
    reg("0. Inicio", "Filas leídas", len(df))
    # .isna().any()      ¿hay al menos un identificador vacío?
    # .duplicated().any() ¿hay al menos un identificador repetido?
    # Si pasa cualquiera, SystemExit detiene el programa con un mensaje: sin un
    # identificador único no se puede usar como llave primaria en Oracle.
    if df["COLLISION_ID"].isna().any() or df["COLLISION_ID"].duplicated().any():
        raise SystemExit("COLLISION_ID tiene nulos o repetidos: no se puede usar como clave primaria.")

    # Textos: sin espacios sobrantes, vacíos -> NULL y todo en MAYÚSCULAS
    # (así 'Sedan', 'SEDAN' y 'sedan ' quedan en un mismo grupo al analizar)
    cambios_espacios = 0     # contador de valores corregidos por espacios o vacíos
    cambios_mayus = 0        # contador de valores que cambiaron al pasar a mayúsculas
    for c in COLUMNAS_TEXTO:
        # Columna tal como viene en el CSV.
        original = df[c]
        # .str.strip() quita los espacios al inicio y al final de cada texto.
        sin_espacios = original.str.strip()
        # .mask(condición) reemplaza por "vacío" (NaN) los valores donde la condición es
        # Verdadera: aquí, los textos que quedaron como "" (cadena vacía) pasan a nulos.
        sin_espacios = sin_espacios.mask(sin_espacios == "")
        # .str.upper() pasa todo a mayúsculas.
        mayusculas = sin_espacios.str.upper()
        # Cuenta cuántos valores cambiaron por espacios/vacíos:
        #   original == sin_espacios       compara fila a fila (con un vacío da "indefinido")
        #   .fillna(False)                 trata esos casos indefinidos como "distinto"
        #   ~                              invierte: Verdadero = el valor cambió
        #   original.notna() & ...         solo se cuentan filas que tenían dato
        #   .sum()                         cuenta los Verdadero; int() lo hace entero común
        cambios_espacios += int((original.notna() & ~(original == sin_espacios).fillna(False)).sum())
        # Igual, para el paso de mayúsculas.
        cambios_mayus += int((sin_espacios.notna() & ~(sin_espacios == mayusculas).fillna(False)).sum())
        # .nunique() cuenta valores distintos. Compara antes y después de normalizar:
        # si baja, había variantes de escritura que se unificaron.
        antes, despues = int(original.nunique()), int(mayusculas.nunique())
        if antes != despues:
            reg("0. Texto", f"{c}: valores distintos antes -> después de normalizar", f"{antes} -> {despues}")
        # Reemplaza la columna por su versión normalizada.
        df[c] = mayusculas
    reg("0. Texto", "Valores corregidos por espacios sobrantes o textos vacíos", cambios_espacios)
    reg("0. Texto", "Valores pasados a mayúsculas", cambios_mayus)

    # 1. Fecha + hora en un solo campo DATE
    # pd.to_datetime convierte el texto en fechas; errors="coerce" deja como NaT
    # ("fecha vacía") lo que no pueda interpretar, en lugar de detener el programa.
    # .dt.normalize() deja la hora de cada fecha en 00:00 para poder sumarle la hora real.
    fecha = pd.to_datetime(df["CRASH DATE"], errors="coerce").dt.normalize()
    # Hora como texto: astype("string") la trata como texto, .str.strip() quita espacios.
    t = df["CRASH TIME"].astype("string").str.strip()
    # .str.fullmatch(patrón) es Verdadero si TODO el texto cumple el patrón regex
    # r"\d{1,2}:\d{2}": uno o dos dígitos, dos puntos y dos dígitos (por ejemplo 9:35 o 14:30).
    # .fillna(False) trata los vacíos como "no cumple"; .astype(bool) lo deja como Verdadero/Falso.
    es_hm = t.str.fullmatch(r"\d{1,2}:\d{2}").fillna(False).astype(bool)
    # En los que cumplen, agrega ":00" (los segundos) para que quede H:MM:SS, el formato
    # que entiende la conversión siguiente.
    t = t.mask(es_hm, t + ":00")
    # pd.to_timedelta convierte el texto en una duración (la hora como tiempo transcurrido
    # desde la medianoche); errors="coerce" deja vacío lo que no pueda interpretar.
    hora = pd.to_timedelta(t, errors="coerce")
    reg("1. Fecha", "Filas sin hora válida (quedan a las 00:00)", int(hora.isna().sum()))
    # fecha + duración = fecha con hora. Si la hora falta, .fillna(pd.Timedelta(0)) usa 0 horas.
    df["CRASH_DATE"] = fecha + hora.fillna(pd.Timedelta(0))
    # Si alguna fecha no se pudo interpretar, se detiene: CRASH_DATE no admite nulos en Oracle.
    if df["CRASH_DATE"].isna().any():
        raise SystemExit("Hay fechas que no se pudieron interpretar: CRASH_DATE no admite nulos.")
    reg("1. Fecha", "Rango de CRASH_DATE", f"{df['CRASH_DATE'].min()} a {df['CRASH_DATE'].max()}")

    # 2. LOCATION se descarta; se renombran columnas
    # Cuenta las filas que tienen LOCATION pero les falta alguna coordenada. Al descartar
    # LOCATION se perdería ese dato; si da 0, se descarta sin perder nada.
    sin_coord = df["LOCATION"].notna() & (df["LATITUDE"].isna() | df["LONGITUDE"].isna())
    reg("2. LOCATION", "Filas con LOCATION pero sin coordenadas (se pierden al descartarla)", int(sin_coord.sum()))
    # Selecciona solo las columnas de COLUMNAS_COLLISIONS, en ese orden (`o` = nombre en el
    # CSV; el guion bajo `_` indica que se ignora el segundo valor de la pareja).
    # LOCATION y las columnas de vehículos quedan fuera. .copy() hace una tabla independiente.
    c = df[[o for o, _ in COLUMNAS_COLLISIONS]].copy()
    # Asigna los nombres nuevos (los de Oracle) a todas las columnas de una vez.
    c.columns = [n for _, n in COLUMNAS_COLLISIONS]

    # Tipos numéricos
    for col in COLUMNAS_CONTEO:
        # to_numeric convierte a número (lo que no se pueda queda vacío); .round() redondea
        # decimales como 2.0; astype("Int64") (con I mayúscula) es el tipo entero de pandas
        # que ADMITE valores vacíos. Con int64 normal, una columna con nulos pasaría a decimal.
        c[col] = pd.to_numeric(c[col], errors="coerce").round().astype("Int64")
    c["LATITUDE"] = pd.to_numeric(c["LATITUDE"], errors="coerce")
    c["LONGITUDE"] = pd.to_numeric(c["LONGITUDE"], errors="coerce")
    # El identificador no tiene nulos (se verificó arriba), así que puede ser int64 normal.
    c["COLLISION_ID"] = c["COLLISION_ID"].astype("int64")

    # 4. Coordenadas (0, 0) -> NULL
    # Verdadero donde latitud y longitud valen exactamente 0 a la vez.
    ceros = (c["LATITUDE"] == 0) & (c["LONGITUDE"] == 0)
    reg("4. Coordenadas", "Filas con (0, 0) convertidas a NULL", int(ceros.sum()))
    nulos_antes = int(c["LATITUDE"].isna().sum())
    # .loc[filas, columnas] = valor: asigna el valor a esas filas y columnas.
    # np.nan es el valor "vacío" de pandas/numpy y se guardará como NULL en Oracle.
    c.loc[ceros, ["LATITUDE", "LONGITUDE"]] = np.nan
    reg("4. Coordenadas", "LATITUDE nulos antes -> después", f"{nulos_antes} -> {int(c['LATITUDE'].isna().sum())}")

    # 5. PERSONS_KILLED vacío = suma de categorías
    # Verdadero en las filas donde las tres categorías de fallecidos tienen dato.
    ok_k = c[PARTES_FALLECIDOS].notna().all(axis=1)
    # Suma de las tres categorías por fila (axis=1 suma a lo ancho).
    suma_k = c[PARTES_FALLECIDOS].sum(axis=1)
    # Verdadero donde el total de fallecidos está vacío.
    nulo_k = c["PERSONS_KILLED"].isna()
    reg("5. Fallecidos", "PERSONS_KILLED nulos antes", int(nulo_k.sum()))
    # En las filas con total vacío Y categorías completas, escribe la suma como total.
    # (& significa "y" fila por fila; suma_k[...] toma solo esas filas de la suma.)
    c.loc[nulo_k & ok_k, "PERSONS_KILLED"] = suma_k[nulo_k & ok_k]
    reg("5. Fallecidos", "Rellenados con la suma de categorías", int((nulo_k & ok_k).sum()))
    # ~ok_k = "no están completas": esos nulos no se pueden recuperar y quedan vacíos.
    reg("5. Fallecidos", "Siguen nulos (faltan categorías)", int((nulo_k & ~ok_k).sum()))
    # Filas donde el total SÍ existe pero no coincide con la suma (!= significa "distinto").
    dif_k = ok_k & c["PERSONS_KILLED"].notna() & (c["PERSONS_KILLED"] != suma_k).fillna(False)
    # Guarda esas filas ANTES de corregirlas, con el total original, para dejar evidencia.
    exc_fallecidos = c.loc[dif_k, ["COLLISION_ID", "PERSONS_KILLED"] + PARTES_FALLECIDOS].copy()
    # Renombra la columna para que quede claro que es el valor original.
    exc_fallecidos = exc_fallecidos.rename(columns={"PERSONS_KILLED": "TOTAL_ORIGINAL"})
    # Agrega al registro la suma que se aplicará.
    exc_fallecidos["SUMA_CATEGORIAS"] = suma_k[dif_k]
    # Ahora sí corrige: reemplaza el total por la suma en esas filas.
    c.loc[dif_k, "PERSONS_KILLED"] = suma_k[dif_k]
    reg("5. Fallecidos", "Total corregido por no coincidir con la suma (ver excepciones)", int(dif_k.sum()))

    # 6. PERSONS_INJURED distinto a la suma (o vacío) = la suma
    # Misma lógica que fallecidos, aplicada a heridos.
    ok_i = c[PARTES_HERIDOS].notna().all(axis=1)
    suma_i = c[PARTES_HERIDOS].sum(axis=1)
    nulo_i = c["PERSONS_INJURED"].isna() & ok_i
    dif_i = ok_i & c["PERSONS_INJURED"].notna() & (c["PERSONS_INJURED"] != suma_i).fillna(False)
    # Evidencia previa a la corrección (valor original de las filas que se corregirán).
    exc_heridos = c.loc[dif_i, ["COLLISION_ID", "PERSONS_INJURED"] + PARTES_HERIDOS].copy()
    exc_heridos = exc_heridos.rename(columns={"PERSONS_INJURED": "TOTAL_ORIGINAL"})
    exc_heridos["SUMA_CATEGORIAS"] = suma_i[dif_i]
    # El símbolo | significa "o": corrige tanto las filas con total distinto como las vacías.
    c.loc[dif_i | nulo_i, "PERSONS_INJURED"] = suma_i[dif_i | nulo_i]
    reg("6. Heridos", "Total corregido por no coincidir con la suma", int(dif_i.sum()))
    reg("6. Heridos", "Total vacío rellenado con la suma", int(nulo_i.sum()))

    # 7. Tabla de vehículos (formato largo)
    # Pasa de formato ANCHO (cinco columnas de vehículo por fila) a formato LARGO
    # (una fila por vehículo). Cada vuelta del ciclo construye la tabla de una posición.
    bloques = []
    for i in range(1, 6):
        b = pd.DataFrame({
            # Posición de la fila original: sirve para ordenar al final.
            "_orden": df.index,
            "COLLISION_ID": df["COLLISION_ID"].astype("int64"),
            # Un número solo (i) se repite automáticamente en todas las filas.
            "VEHICLE_CODE": i,
            "VEHICLE_TYPE": df[f"VEHICLE TYPE CODE {i}"],
            "CONTRIBUTING_FACTOR": df[f"CONTRIBUTING FACTOR VEHICLE {i}"],
        })
        bloques.append(b)
    # pd.concat apila las cinco tablas una debajo de otra; ignore_index renumera las filas.
    v = pd.concat(bloques, ignore_index=True)
    posibles = len(v)    # 5 filas por accidente
    # dropna(subset=[...], how="all") elimina las filas donde AMBAS columnas indicadas
    # están vacías (un "vehículo" que no existe, como el 4.º de un choque de dos vehículos).
    v = v.dropna(subset=["VEHICLE_TYPE", "CONTRIBUTING_FACTOR"], how="all")
    # Ordena por accidente original y número de vehículo; descarta la columna auxiliar
    # _orden; reset_index(drop=True) renumera las filas desde 0.
    v = v.sort_values(["_orden", "VEHICLE_CODE"]).drop(columns="_orden").reset_index(drop=True)
    # insert(0, nombre, valores) agrega una columna en la primera posición.
    # np.arange(1, n + 1) genera la secuencia 1, 2, 3, ..., n: el VEHICLE_ID consecutivo.
    v.insert(0, "VEHICLE_ID", np.arange(1, len(v) + 1))
    reg("7. Vehículos", "Posiciones de vehículo revisadas (5 por accidente)", posibles)
    reg("7. Vehículos", "Filas vacías omitidas (sin tipo ni factor)", posibles - len(v))
    reg("7. Vehículos", "Filas en COLLISION_VEHICLE", len(v))
    # .nunique() cuenta los accidentes distintos que sí aparecen en la tabla de vehículos;
    # la resta da los accidentes que quedaron sin ningún vehículo registrado.
    reg("7. Vehículos", "Accidentes sin ningún vehículo registrado",
        int(len(c) - v["COLLISION_ID"].nunique()))

    # Control de largos antes de cargar
    # Se arma un diccionario que une dos partes con ** (combina diccionarios):
    #   - las 5 columnas de texto de COLLISIONS, tomadas de la tabla `c`
    #   - los dos textos de la tabla de vehículos
    # .items() entrega cada pareja (nombre, columna) para recorrerla.
    for nombre, serie in {**{k: c[k] for k in ["BOROUGH", "ZIP_CODE", "ON_STREET_NAME",
                                                 "CROSS_STREET_NAME", "OFF_STREET_NAME"]},
                          "VEHICLE_TYPE": v["VEHICLE_TYPE"],
                          "CONTRIBUTING_FACTOR": v["CONTRIBUTING_FACTOR"]}.items():
        # .str.len() es el largo de cada texto y .max() el mayor. Si la columna está
        # totalmente vacía, el máximo es 0.
        maximo = int(serie.str.len().max()) if serie.notna().any() else 0
        # Expresión condicional: "OK" si cabe, "EXCEDE" si no.
        reg("8. Largos", f"{nombre}: máximo {maximo} (límite {LARGOS[nombre]})", "OK" if maximo <= LARGOS[nombre] else "EXCEDE")
        # Si no cabe, se detiene antes de intentar cargar (Oracle rechazaría el dato).
        if maximo > LARGOS[nombre]:
            raise SystemExit(f"{nombre} supera VARCHAR2({LARGOS[nombre]}). Ajustá el tamaño en DDL y LARGOS.")

    reg("9. Final", "Filas en COLLISIONS", len(c))
    # Devuelve cinco resultados a la vez (una tupla): las dos tablas limpias, el registro
    # convertido en tabla, y las dos tablas de excepciones.
    return c, v, pd.DataFrame(log), exc_heridos, exc_fallecidos

# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------
def a_python(v):
    # Convierte un valor de pandas/numpy en un tipo nativo de Python, que es el que
    # entiende la biblioteca de Oracle. Los vacíos se convierten en None (NULL en Oracle).
    # isinstance(v, str): no se llama pd.isna sobre textos.
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    # Una fecha de pandas (Timestamp) pasa a la fecha estándar de Python.
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    # Un entero de numpy (np.int64, etc.) pasa a int de Python.
    if isinstance(v, np.integer):
        return int(v)
    # Un decimal de numpy pasa a float de Python.
    if isinstance(v, np.floating):
        return float(v)
    # Cualquier otro valor (textos, etc.) se devuelve igual.
    return v

def filas(df):
    # Convierte una tabla de pandas en una lista de tuplas, una por fila, con valores
    # nativos de Python (formato que necesita executemany).
    #   df.astype(object)                  evita que pandas convierta los valores a tipos numpy
    #   .itertuples(index=False, name=None) recorre las filas como tuplas simples
    #   a_python(x) for x in fila           convierte cada valor de la fila
    return [tuple(a_python(x) for x in fila)
            for fila in df.astype(object).itertuples(index=False, name=None)]

def insertar(cur, oracledb, tabla, df, tamanos, lote=20000):
    # Inserta una tabla de pandas en una tabla de Oracle, por lotes.
    columnas = list(df.columns)
    # Arma la sentencia INSERT. Los marcadores :1, :2, :3... son "variables de enlace":
    # Oracle sustituye cada una por el valor correspondiente, lo que es más seguro y rápido
    # que escribir los valores dentro del texto.
    sql = (f"INSERT INTO {tabla} ({', '.join(columnas)}) "
           f"VALUES ({', '.join(':' + str(i + 1) for i in range(len(columnas)))})")
    datos = filas(df)
    # Recorre la lista en bloques de 20 000 filas: range(inicio, fin, paso).
    for i in range(0, len(datos), lote):
        # setinputsizes declara el tipo de cada columna (número, fecha, o largo del texto)
        # para que Oracle no tenga que adivinarlo, algo que falla si la primera fila tiene un vacío.
        cur.setinputsizes(*tamanos)
        # executemany ejecuta el mismo INSERT con muchas filas de una sola vez.
        # datos[i:i + lote] es un trozo (slice) de la lista.
        cur.executemany(sql, datos[i:i + lote])
    print(f"Insertadas {len(datos)} filas en {tabla}")

def cargar_oracle(c, v):
    # La biblioteca oracledb se importa aquí adentro para que el modo --solo-limpiar
    # funcione aunque no esté instalada.
    import oracledb

    # input() pide el usuario mostrando lo que escribís; .strip() quita espacios sobrantes.
    usuario = input("Usuario de Oracle: ").strip()
    # getpass pide la contraseña sin mostrarla en pantalla.
    clave = getpass.getpass("Contraseña de Oracle: ")
    # DSN = "host:puerto/servicio", la dirección de la base de datos.
    dsn = f"{ORACLE['host']}:{ORACLE['port']}/{ORACLE['service']}"
    # Tipos de Oracle para setinputsizes: N = número, D = fecha. Se escriben en una sola
    # línea con asignación múltiple.
    N, D = oracledb.DB_TYPE_NUMBER, oracledb.DB_TYPE_DATE
    # Abre la conexión. Si el usuario o la contraseña son incorrectos, aquí aparece ORA-01017.
    conn = oracledb.connect(user=usuario, password=clave, dsn=dsn)
    # try / except / finally:
    #   try      el trabajo principal
    #   except   si algo falla, deshace todo lo hecho (rollback) y vuelve a lanzar el error
    #   finally  se ejecuta SIEMPRE: cierra la conexión
    try:
        # El cursor es el objeto que ejecuta sentencias SQL.
        cur = conn.cursor()
        for sentencia in DDL:
            cur.execute(sentencia)
        print("Tablas creadas.")
        # Tamaños de las 17 columnas de COLLISIONS, en orden: número, fecha, texto de 20,
        # texto de 10, 2 números (coordenadas), 3 textos de 100, y 8 números (conteos).
        insertar(cur, oracledb, "COLLISIONS", c,
                 [N, D, 20, 10, N, N, 100, 100, 100, N, N, N, N, N, N, N, N])
        # Tabla de vehículos: 3 números y 2 textos de 100.
        insertar(cur, oracledb, "COLLISION_VEHICLE", v, [N, N, N, 100, 100])
        # commit confirma la transacción: hasta aquí nada es definitivo. Si algo falla antes,
        # no queda una carga a medias.
        conn.commit()
        return verificar(cur, c, v)
    except Exception:
        # Deshace los cambios no confirmados.
        conn.rollback()
        # `raise` solo, vuelve a lanzar el mismo error para que se vea en pantalla.
        raise
    finally:
        conn.close()

def verificar(cur, c, v):
    # Función interna: ejecuta una consulta que devuelve un solo valor y lo devuelve.
    # fetchone() trae la primera fila (una tupla) y [0] toma su primer elemento.
    def uno(sql):
        cur.execute(sql)
        return cur.fetchone()[0]

    # Valores esperados, calculados en pandas, para compararlos con lo que hay en Oracle.
    esperado_k = int(c["PERSONS_KILLED"].sum())
    esperado_i = int(c["PERSONS_INJURED"].sum())
    # Lista de pruebas: (nombre, valor esperado, valor que devuelve Oracle).
    pruebas = [
        # La cantidad de filas cargadas debe ser igual a la limpiada.
        ("Filas en COLLISIONS", len(c), uno("SELECT COUNT(*) FROM COLLISIONS")),
        # Los identificadores distintos deben ser tantos como las filas (sin repetidos).
        ("COLLISION_ID distintos", len(c), uno("SELECT COUNT(DISTINCT COLLISION_ID) FROM COLLISIONS")),
        ("Filas en COLLISION_VEHICLE", len(v), uno("SELECT COUNT(*) FROM COLLISION_VEHICLE")),
        # Debe ser 0: ningún vehículo puede quedar sin su accidente (NOT EXISTS busca
        # vehículos sin coincidencia en COLLISIONS).
        ("Vehículos sin accidente (debe ser 0)", 0, uno(
            "SELECT COUNT(*) FROM COLLISION_VEHICLE v WHERE NOT EXISTS "
            "(SELECT 1 FROM COLLISIONS c WHERE c.COLLISION_ID = v.COLLISION_ID)")),
        # Las sumas totales deben coincidir (NVL evita un nulo si la tabla estuviera vacía).
        ("Suma de PERSONS_KILLED", esperado_k, uno("SELECT NVL(SUM(PERSONS_KILLED), 0) FROM COLLISIONS")),
        ("Suma de PERSONS_INJURED", esperado_i, uno("SELECT NVL(SUM(PERSONS_INJURED), 0) FROM COLLISIONS")),
        ("LATITUDE nulos", int(c["LATITUDE"].isna().sum()), uno("SELECT COUNT(*) FROM COLLISIONS WHERE LATITUDE IS NULL")),
        # LEFT JOIN conserva todos los accidentes; los que no tienen vehículo quedan con
        # VEHICLE_ID vacío, y el WHERE los cuenta.
        ("Accidentes sin vehículos", int(len(c) - v["COLLISION_ID"].nunique()), uno(
            "SELECT COUNT(*) FROM COLLISIONS c LEFT JOIN COLLISION_VEHICLE v "
            "ON v.COLLISION_ID = c.COLLISION_ID WHERE v.VEHICLE_ID IS NULL")),
    ]
    filas_v = []
    print("\nVerificación en Oracle:")
    # Cada elemento de `pruebas` se desempaqueta en tres variables.
    for nombre, esperado, obtenido in pruebas:
        # Compara como enteros: OK si coinciden, DIFERENCIA si no.
        estado = "OK" if int(esperado) == int(obtenido) else "DIFERENCIA"
        filas_v.append({"prueba": nombre, "esperado": esperado, "oracle": obtenido, "estado": estado})
        # {estado:10} reserva 10 caracteres de ancho para alinear las columnas.
        print(f"  {estado:10} {nombre}: esperado {esperado}, Oracle {obtenido}")
    # Devuelve los resultados como tabla, para guardarlos en un CSV.
    return pd.DataFrame(filas_v)

# ---------------------------------------------------------------------------
def main():
    # --- Opciones de la línea de comandos -----------------------------------
    ap = argparse.ArgumentParser()
    # Argumento posicional opcional: la ruta del CSV. nargs="?" significa "0 o 1 valor".
    # Si no se escribe, usa RAW_DEFAULT convertido a texto con str().
    ap.add_argument("csv", nargs="?", default=str(RAW_DEFAULT))
    # Opción --solo-limpiar: action="store_true" la vuelve una "bandera": vale True si
    # se escribe y False si no. help es el texto que muestra --help.
    ap.add_argument("--solo-limpiar", action="store_true", help="no conectar a Oracle")
    # Lee lo que se escribió en la terminal y lo guarda en `args`.
    args = ap.parse_args()

    # Path(...).is_file() devuelve True si el archivo existe. Si no, sys.exit termina el
    # programa mostrando el mensaje.
    if not Path(args.csv).is_file():
        sys.exit(f"No existe el archivo: {args.csv}")

    # Diccionario {columna: "str"} (comprensión de diccionario): obliga a pandas a leer esas
    # columnas como TEXTO. Así el código postal no se convierte en 11201.0, y las fechas y
    # horas llegan intactas para convertirlas con control.
    texto = {c: "str" for c in COLUMNAS_TEXTO + ["CRASH TIME", "CRASH DATE", "LOCATION"]}
    # Lee el CSV en una tabla; dtype=texto aplica la regla anterior.
    df = pd.read_csv(args.csv, dtype=texto)
    # Lista de columnas necesarias que NO están en el CSV. Se arma sumando listas:
    #   las columnas básicas + las originales de COLUMNAS_COLLISIONS + las de vehículos.
    # `if o != "CRASH_DATE"` excluye esa, porque se construye en el script y no viene en el CSV.
    # `if c not in df.columns` deja solo las que faltan.
    faltan = [c for c in ["COLLISION_ID", "CRASH DATE", "CRASH TIME", "LOCATION", "LATITUDE", "LONGITUDE"]
              + [o for o, n in COLUMNAS_COLLISIONS if o != "CRASH_DATE"] + COLUMNAS_VEHICULOS
              if c not in df.columns]
    if faltan:
        sys.exit(f"Faltan columnas en el CSV: {faltan}")

    # --- Limpieza -----------------------------------------------------------
    # La función devuelve cinco resultados, que se reciben en cinco variables a la vez.
    c, v, log, exc_i, exc_k = limpiar(df)

    # --- Guardar resultados -------------------------------------------------
    # mkdir crea la carpeta; parents=True crea también las carpetas intermedias;
    # exist_ok=True evita un error si ya existe.
    PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    # to_csv guarda cada tabla. index=False no escribe los números de fila; utf-8-sig hace
    # que Excel muestre bien los acentos.
    c.to_csv(PROCESSED / "collisions_clean.csv", index=False, encoding="utf-8-sig")
    v.to_csv(PROCESSED / "collision_vehicle_clean.csv", index=False, encoding="utf-8-sig")
    log.to_csv(REPORTS / "limpieza_log.csv", index=False, encoding="utf-8-sig")
    exc_i.to_csv(REPORTS / "excepciones_heridos.csv", index=False, encoding="utf-8-sig")
    exc_k.to_csv(REPORTS / "excepciones_fallecidos.csv", index=False, encoding="utf-8-sig")
    print(f"\nArchivos guardados en {PROCESSED} y {REPORTS}")

    # --- Carga a Oracle (se omite con --solo-limpiar) -----------------------
    if args.solo_limpiar:
        print("Modo --solo-limpiar: no se cargó nada a Oracle.")
        # `return` sin valor termina la función aquí.
        return
    # Carga las tablas limpias y recibe la tabla con el resultado de la verificación.
    ver = cargar_oracle(c, v)
    ver.to_csv(REPORTS / "verificacion_oracle.csv", index=False, encoding="utf-8-sig")
    print(f"Verificación guardada en {REPORTS / 'verificacion_oracle.csv'}")


if __name__ == "__main__":
    main()