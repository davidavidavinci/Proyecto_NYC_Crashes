"""
validacion.py
Inspección y validación de calidad de datos del dataset de colisiones.
Script independiente: no requiere ningún otro archivo.
Genera un reporte (validacion_reporte.csv) para documentar en el informe.

Uso:  python scripts/validacion.py [ruta_al_csv]
Si no se indica la ruta, busca un único .csv en la carpeta actual, en data/
o junto al script.
Ajustá los nombres de columnas en COLS si tu CSV usa otros.
"""
import hashlib

# `Path` del módulo `pathlib` representa rutas de archivos y carpetas como objetos.
from pathlib import Path

# `pandas` es la biblioteca para trabajar con datos en forma de tabla.
import pandas as pd

# ---------------------------------------------------------------------------
# CONFIGURACIÓN: rutas y nombres de columnas
# ---------------------------------------------------------------------------

# Ruta del CSV original. Es una ruta RELATIVA: se interpreta desde la carpeta en
# la que se ejecuta el comando, no desde donde está el script. Por eso hay que
# correr el programa desde la raíz del proyecto (Proyecto_NYC_Crashes).
ruta = "data/raw/Motor_Vehicle_Collisions_original.csv"

# Carpeta donde se guardarán los CSV de resultados:
#   __file__          ruta de este archivo .py
#   Path(__file__)    la convierte en un objeto Path
#   .resolve()        la convierte en ruta absoluta y limpia (sin "..")
#   .parent           sube un nivel: la carpeta que contiene al script (scripts/)
OUT_DIR = Path(__file__).resolve().parent  # los archivos se guardan junto al script

# Diccionario que reúne los nombres de las columnas del CSV en un solo lugar.
COLS = {
    "id": "COLLISION_ID",              # identificador único de cada colisión
    "fecha": "CRASH DATE",             # fecha en que ocurrió
    "lat": "LATITUDE",                 # latitud
    "lon": "LONGITUDE",                # longitud
    "borough": "BOROUGH",              # distrito de Nueva York
    "inj_total": "NUMBER OF PERSONS INJURED",   # total de personas heridas
    # Lista con las tres categorías de heridos; deberían sumar el total.
    "inj_parts": ["NUMBER OF PEDESTRIANS INJURED",
                  "NUMBER OF CYCLIST INJURED",
                  "NUMBER OF MOTORIST INJURED"],
    "kill_total": "NUMBER OF PERSONS KILLED",   # total de personas fallecidas
    # Lista con las tres categorías de fallecidos; deberían sumar el total.
    "kill_parts": ["NUMBER OF PEDESTRIANS KILLED",
                   "NUMBER OF CYCLIST KILLED",
                   "NUMBER OF MOTORIST KILLED"],
}

# Caja geográfica aproximada de Nueva York: pares (mínimo, máximo).
# Sirve para detectar coordenadas que están en el mundo pero fuera de la ciudad.
NY_LAT = (40.4, 41.0)      # latitud mínima y máxima
NY_LON = (-74.3, -73.6)    # longitud mínima y máxima (valores negativos: oeste)

# Lista vacía donde se irán guardando los resultados de cada revisión.
# Al final se convierte en una tabla y se guarda como validacion_reporte.csv.
resultados = []


# ---------------------------------------------------------------------------
# FUNCIONES AUXILIARES
# ---------------------------------------------------------------------------

def guardar(frame, nombre, index=False):
    # Guarda una tabla de pandas como CSV en la carpeta del script.
    # `OUT_DIR / nombre` une la carpeta con el nombre del archivo (el operador `/`
    # de Path arma rutas). Ejemplo: scripts/validacion_reporte.csv
    ruta_salida = OUT_DIR / nombre
    # `to_csv` escribe la tabla en un archivo CSV.
    #   index=index         si es True, también escribe los nombres de las filas.
    #   encoding="utf-8-sig"  guarda con un marcador que hace que Excel muestre
    #                         bien los acentos y la ñ.
    frame.to_csv(ruta_salida, index=index, encoding="utf-8-sig")
    print(f"Guardado: {ruta_salida}")


def registrar(revision, detalle, valor):
    # Anota el resultado de una revisión y también lo muestra en pantalla.
    # Agrega a la lista `resultados` un diccionario con tres datos. Cada diccionario
    # será una fila del reporte final: a qué revisión pertenece, qué se midió y cuánto dio.
    resultados.append({"revision": revision, "detalle": detalle, "valor": valor})
    print(f"[{revision}] {detalle}: {valor}")

# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL: todas las revisiones
# ---------------------------------------------------------------------------

def main(ruta):
    # --- Huella digital del archivo -----------------------------------------
    # `with open(...) as f:` abre el archivo y garantiza que se cierre al terminar.
    # El modo "rb" significa "read binary": lee los bytes tal cual, sin interpretarlos
    # como texto. Así la huella corresponde exactamente al archivo.
    with open(ruta, "rb") as f:
        # f.read()                  lee todo el contenido del archivo en memoria.
        # hashlib.sha256(...)       calcula el hash SHA-256 de esos bytes.
        # .hexdigest()              devuelve el resultado como texto hexadecimal
        sha = hashlib.sha256(f.read()).hexdigest()

    # Carga el CSV en un DataFrame (la tabla de pandas). Desde aquí `df` contiene
    # las 66 023 filas y las 29 columnas.
    df = pd.read_csv(ruta)

    # 0. Trazabilidad / reproducibilidad ------------------------------------
    # len(df) es la cantidad de filas.
    registrar("0. Origen", "Filas", len(df))
    # df.shape es una pareja (filas, columnas); el índice [1] toma las columnas.
    registrar("0. Origen", "Columnas", df.shape[1])
    print("\nTipos de datos por columna:")
    # df.dtypes muestra el tipo de dato que pandas asignó a cada columna
    # (int64 = entero, float64 = decimal, object/str = texto).
    print(df.dtypes)
    print("\nPrimeras filas:")
    # df.head() muestra las primeras 5 filas, para ver cómo se ven los datos.
    print(df.head())
    # Guarda la huella del archivo en el reporte: es la evidencia de que el CSV
    # original no fue modificado.
    registrar("0. Origen", "SHA-256 del archivo", sha)

    # 1. Integridad de la clave primaria ------------------------------------
    # df[COLS["id"]]  selecciona la columna COLLISION_ID.
    # .isna()         devuelve una columna de Verdadero/Falso: Verdadero donde falta el dato.
    # .sum()          suma esos valores (Verdadero cuenta como 1), o sea, cuenta los nulos.
    # int(...)        convierte el resultado a un entero común de Python.
    registrar("1. PK", "COLLISION_ID nulos", int(df[COLS["id"]].isna().sum()))
    # .duplicated()   marca como Verdadero cada valor que ya apareció antes en la columna.
    # Si da 0, todos los identificadores son únicos y sirven como clave primaria.
    registrar("1. PK", "COLLISION_ID duplicados", int(df[COLS["id"]].duplicated().sum()))

    # 2. Rango de fechas ----------------------------------------------------
    # pd.to_datetime convierte el texto de la columna en fechas reales.
    # errors="coerce": si un valor no se puede interpretar como fecha, en lugar de
    # detener el programa lo convierte en NaT ("fecha vacía"), y así se puede contar.
    fechas = pd.to_datetime(df[COLS["fecha"]], errors="coerce")
    # Cuenta las fechas vacías o que no se pudieron interpretar.
    registrar("2. Fechas", "Fechas inválidas o nulas", int(fechas.isna().sum()))
    registrar("2. Fechas", "Fecha mínima", fechas.min())
    registrar("2. Fechas", "Fecha máxima", fechas.max())

    # 2b. Valores faltantes por columna -------------------------------------
    # Se arma una tabla nueva a partir de un diccionario: cada clave será una columna.
    faltantes = pd.DataFrame({
        # df.isna()         tabla de Verdadero/Falso del mismo tamaño que df.
        # .sum()            suma por columna: cuántos nulos tiene cada una.
        "nulos": df.isna().sum(),
        # .mean()           promedio por columna de Verdadero/Falso = proporción de nulos.
        # * 100             la pasa a porcentaje.
        # .round(2)         la redondea a 2 decimales.
        "porcentaje": (df.isna().mean() * 100).round(2),
    # Ordena la tabla de mayor a menor porcentaje de nulos (las más problemáticas arriba).
    }).sort_values("porcentaje", ascending=False)
    # Guarda la tabla como CSV. index=True porque los nombres de las filas son los
    # nombres de las columnas del dataset, y se quieren conservar.
    guardar(faltantes, "faltantes_por_columna.csv", index=True)
    print("\nPorcentaje de faltantes por columna:")
    # faltantes["nulos"] > 0 es un filtro de Verdadero/Falso; al ponerlo entre
    # corchetes, se muestran solo las columnas que sí tienen nulos.
    print(faltantes[faltantes["nulos"] > 0])
    # Cuenta cuántas columnas tienen al menos un nulo.
    registrar("2b. Faltantes", "Columnas con al menos un nulo", int((faltantes["nulos"] > 0).sum()))
    # df.duplicated() sin argumentos compara filas completas. Como COLLISION_ID es
    # único, una fila nunca puede ser idéntica a otra, y por eso casi siempre da 0.
    registrar("2b. Faltantes", "Filas completamente duplicadas", int(df.duplicated().sum()))

    # 2c. Análisis de NUMBER OF PERSONS KILLED nulo (no asumir que significa 0)
    # Guarda en `k` el nombre de la columna, para no repetir COLS["kill_total"].
    k = COLS["kill_total"]
    # Columna de Verdadero/Falso: Verdadero en las filas donde el total de fallecidos está vacío.
    k_nulo = df[k].isna()
    registrar("2c. Fallecidos nulos", "Filas con total de fallecidos nulo", int(k_nulo.sum()))
    # Cuenta las filas donde el total de fallecidos Y el total de heridos están vacíos a la vez.
    registrar("2c. Fallecidos nulos", "...y también heridos nulo",
              int((k_nulo & df[COLS["inj_total"]].isna()).sum()))
    # df[lista_de_columnas]  selecciona las tres columnas de categorías de fallecidos.
    # .notna()               Verdadero donde SÍ hay dato.
    # .all(axis=1)           para cada fila (axis=1), Verdadero solo si las tres columnas
    #                        tienen dato. Son las filas donde el total se puede reconstruir sumando.
    partes_ok = df[COLS["kill_parts"]].notna().all(axis=1)
    registrar("2c. Fallecidos nulos", "...con las 3 categorías informadas (total recuperable por suma)",
              int((k_nulo & partes_ok).sum()))
    # .isna().all(axis=1) es Verdadero cuando las tres categorías están vacías.
    registrar("2c. Fallecidos nulos", "...sin ninguna categoría informada",
              int((k_nulo & df[COLS["kill_parts"]].isna().all(axis=1)).sum()))
    # fechas[k_nulo]     se queda solo con las fechas de las filas sin total de fallecidos.
    # .dt.year           extrae el año de cada fecha.
    # .value_counts()    cuenta cuántas veces aparece cada año.
    # .sort_index()      ordena el resultado por año, de menor a mayor.
    # Sirve para ver si los nulos se concentran en un período.
    por_anio = fechas[k_nulo].dt.year.value_counts().sort_index()
    print("\nFallecidos nulos por año:")
    print(por_anio)
    # .rename("nulos")   le pone nombre a la columna de conteos.
    # .to_frame()        convierte la serie en una tabla, para poder guardarla como CSV.
    guardar(por_anio.rename("nulos").to_frame(), "fallecidos_nulos_por_anio.csv", index=True)
    print("\nMuestra de filas sin dato de fallecidos:")
    # df.loc[filas, columnas] selecciona filas (las que tienen k_nulo = Verdadero) y las
    # columnas indicadas; .head(10) muestra solo las primeras 10.
    print(df.loc[k_nulo, [COLS["id"], COLS["fecha"], COLS["inj_total"], k]].head(10))
    print("\nEstadísticas de fallecidos (valores existentes):")
    # .describe() resume la columna: cantidad, promedio, desviación, mínimo,
    # cuartiles y máximo. Ignora los nulos.
    print(df[k].describe())

    # 2d. Columnas de vehículos: huecos en la secuencia (afecta la tabla larga)
    # Un "hueco" es un accidente con un vehículo informado después de uno vacío
    # (por ejemplo, vehículo 3 con dato pero vehículo 2 vacío).
    huecos = 0    # contador que se irá acumulando
    # range(1, 5) recorre 1, 2, 3, 4 (el 5 no entra). Cada vuelta compara la posición i
    # con la siguiente (i + 1).
    for i in range(1, 5):
        # f"VEHICLE TYPE CODE {i}" arma el nombre de la columna: VEHICLE TYPE CODE 1, 2...
        actual = df[f"VEHICLE TYPE CODE {i}"].isna()           # vehículo i vacío
        siguiente = df[f"VEHICLE TYPE CODE {i + 1}"].notna()   # vehículo i+1 con dato
        # Filas donde pasan las dos cosas a la vez = un hueco. Se suma al contador.
        huecos += int((actual & siguiente).sum())
    registrar("2d. Vehículos", "Filas con un vehículo informado después de uno vacío", huecos)
    # Se toman las 5 columnas de tipo de vehículo con una "lista por comprensión":
    # [expresión for i in range(1, 6)] genera una lista con los nombres de 1 a 5.
    # .isna().all(axis=1) es Verdadero para los accidentes donde las cinco están vacías.
    sin_vehiculo = df[[f"VEHICLE TYPE CODE {i}" for i in range(1, 6)]].isna().all(axis=1)
    registrar("2d. Vehículos", "Accidentes sin ningún tipo de vehículo", int(sin_vehiculo.sum()))

    # 3. Consistencia de heridos y fallecidos (solo filas completas)
    # El ciclo recorre dos casos. Cada elemento de la lista es una tupla de tres valores
    # que se "desempaquetan" en las variables nombre, total y partes:
    #   (etiqueta, columna del total, lista de columnas de categorías)
    for nombre, total, partes in [
        ("heridos", COLS["inj_total"], COLS["inj_parts"]),
        ("fallecidos", COLS["kill_total"], COLS["kill_parts"]),
    ]:
        # Lista con la columna del total más las tres categorías (concatena listas con +).
        cols = [total] + partes
        # Verdadero en las filas donde las cuatro columnas tienen dato. Solo esas se
        # pueden comparar; así los vacíos no se cuentan como inconsistencias.
        completas = df[cols].notna().all(axis=1)
        # Para esas filas, suma las tres categorías (.sum(axis=1) suma a lo ancho, por fila).
        suma = df.loc[completas, partes].sum(axis=1)
        # Compara el total registrado con la suma. != significa "distinto";
        # .sum() cuenta cuántas filas no coinciden.
        dif = (df.loc[completas, total] != suma).sum()
        registrar("3. Consistencia", f"Filas {nombre} comparables", int(completas.sum()))
        registrar("3. Consistencia", f"Filas con total de {nombre} distinto a la suma", int(dif))

    # 3b. Valores negativos en todas las columnas numéricas de personas
    # Arma una lista con las 8 columnas numéricas sumando listas: dos totales más las
    # tres categorías de heridos y las tres de fallecidos.
    numericas = [COLS["inj_total"], COLS["kill_total"]] + COLS["inj_parts"] + COLS["kill_parts"]
    for c in numericas:
        # (df[c] < 0) es Verdadero donde el valor es negativo (un nulo no cuenta). .sum() los cuenta.
        # Una cantidad de personas nunca debería ser negativa.
        registrar("3b. Negativos", c, int((df[c] < 0).sum()))

    # 4. Validación geográfica ----------------------------------------------
    # Asigna dos variables a la vez: lat = columna de latitud, lon = columna de longitud.
    lat, lon = df[COLS["lat"]], df[COLS["lon"]]
    # lat.isna() != lon.isna() es Verdadero cuando UNA de las dos está vacía y la otra no
    # (una coordenada sola no sirve para ubicar un punto).
    registrar("4. Geografía", "Solo una coordenada informada", int((lat.isna() != lon.isna()).sum()))
    # Filas que tienen las dos coordenadas. Las siguientes pruebas se hacen solo sobre ellas
    # para que los nulos no se cuenten como errores.
    ambas = lat.notna() & lon.notna()
    # .between(a, b)   Verdadero si el valor está entre a y b.
    # ~                 invierte Verdadero/Falso ("no está dentro del rango").
    # |                 significa "o". Es decir: latitud fuera de -90 a 90, o longitud
    #                   fuera de -180 a 180. Esos valores son imposibles en la Tierra.
    fuera_mundo = ambas & (~lat.between(-90, 90) | ~lon.between(-180, 180))
    registrar("4. Geografía", "Fuera de rango mundial", int(fuera_mundo.sum()))
    # Coordenadas exactamente (0, 0): es un punto en el océano frente a África; se usa
    # como valor falso cuando no se conoce la ubicación.
    ceros = ambas & (lat == 0) & (lon == 0)
    registrar("4. Geografía", "Coordenadas (0, 0)", int(ceros.sum()))
    # Coordenadas válidas en el mundo pero fuera de la caja de Nueva York.
    # ~ceros excluye los (0, 0) para no contarlos dos veces.
    # El asterisco en *NY_LAT "desempaqueta" la pareja (40.4, 41.0) en dos argumentos,
    # igual que escribir between(40.4, 41.0).
    fuera_ny = ambas & ~ceros & (~lat.between(*NY_LAT) | ~lon.between(*NY_LON))
    registrar("4. Geografía", "Fuera de la caja de Nueva York (sin contar 0,0)", int(fuera_ny.sum()))

    # 5. Calidad de categorías ----------------------------------------------
    b = df[COLS["borough"]]    # columna de distritos
    registrar("5. Categorías", "BOROUGH nulos", int(b.isna().sum()))
    # .nunique() cuenta los valores distintos (ignora los nulos). Debería dar 5 distritos;
    # si da más, hay variantes de escritura.
    registrar("5. Categorías", "BOROUGH distintos", b.nunique())
    # .dropna() quita los nulos; .astype(str) asegura que todo sea texto.
    con_espacios = b.dropna().astype(str)
    # .str.strip() quita los espacios al inicio y al final de cada texto. Si el texto
    # original es distinto del texto sin espacios, tenía espacios sobrantes.
    registrar("5. Categorías", "BOROUGH con espacios sobrantes",
              int((con_espacios != con_espacios.str.strip()).sum()))
    # Compara la cantidad de valores distintos al pasar todo a mayúsculas (.str.upper())
    # contra la cantidad sin pasarlo. Si son distintas, hay variantes de mayúsculas
    # (por ejemplo, Brooklyn y BROOKLYN). El resultado es 1 (hay variantes) o 0 (no hay).
    registrar("5. Categorías", "BOROUGH variantes de mayúsculas",
              int(con_espacios.str.strip().str.upper().nunique() != con_espacios.str.strip().nunique()))


# ---------------------------------------------------------------------------
# PUNTO DE ENTRADA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        main(ruta)
    finally:
        # Convierte la lista `resultados` (diccionarios) en una tabla y la guarda como
        # validacion_reporte.csv. Al estar en `finally`, el reporte se escribe aunque
        # una revisión falle a la mitad, con lo que alcanzó a calcularse.
        guardar(pd.DataFrame(resultados), "validacion_reporte.csv")