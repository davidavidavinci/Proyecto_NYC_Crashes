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
import sys
import hashlib
from pathlib import Path
import pandas as pd

ruta = "data/raw/Motor_Vehicle_Collisions_original.csv"

OUT_DIR = Path(__file__).resolve().parent  # los archivos se guardan junto al script

COLS = {
    "id": "COLLISION_ID",
    "fecha": "CRASH DATE",
    "lat": "LATITUDE",
    "lon": "LONGITUDE",
    "borough": "BOROUGH",
    "inj_total": "NUMBER OF PERSONS INJURED",
    "inj_parts": ["NUMBER OF PEDESTRIANS INJURED",
                  "NUMBER OF CYCLIST INJURED",
                  "NUMBER OF MOTORIST INJURED"],
    "kill_total": "NUMBER OF PERSONS KILLED",
    "kill_parts": ["NUMBER OF PEDESTRIANS KILLED",
                   "NUMBER OF CYCLIST KILLED",
                   "NUMBER OF MOTORIST KILLED"],
}

# Caja geográfica aproximada de Nueva York
NY_LAT = (40.4, 41.0)
NY_LON = (-74.3, -73.6)

resultados = []

def guardar(frame, nombre, index=False):
    ruta_salida = OUT_DIR / nombre
    frame.to_csv(ruta_salida, index=index, encoding="utf-8-sig")
    print(f"Guardado: {ruta_salida}")

def registrar(revision, detalle, valor):
    resultados.append({"revision": revision, "detalle": detalle, "valor": valor})
    print(f"[{revision}] {detalle}: {valor}")

SALIDAS = {"validacion_reporte.csv", "faltantes_por_columna.csv",
           "fallecidos_nulos_por_anio.csv"}

def main(ruta):
    with open(ruta, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    df = pd.read_csv(ruta)

    # 0. Trazabilidad / reproducibilidad
    registrar("0. Origen", "Filas", len(df))
    registrar("0. Origen", "Columnas", df.shape[1])
    print("\nTipos de datos por columna:")
    print(df.dtypes)
    print("\nPrimeras filas:")
    print(df.head())
    registrar("0. Origen", "SHA-256 del archivo", sha)

    # 1. Integridad de la clave primaria
    registrar("1. PK", "COLLISION_ID nulos", int(df[COLS["id"]].isna().sum()))
    registrar("1. PK", "COLLISION_ID duplicados", int(df[COLS["id"]].duplicated().sum()))

    # 2. Rango de fechas
    fechas = pd.to_datetime(df[COLS["fecha"]], errors="coerce")
    registrar("2. Fechas", "Fechas inválidas o nulas", int(fechas.isna().sum()))
    registrar("2. Fechas", "Fecha mínima", fechas.min())
    registrar("2. Fechas", "Fecha máxima", fechas.max())

    # 2b. Valores faltantes por columna
    faltantes = pd.DataFrame({
        "nulos": df.isna().sum(),
        "porcentaje": (df.isna().mean() * 100).round(2),
    }).sort_values("porcentaje", ascending=False)
    guardar(faltantes, "faltantes_por_columna.csv", index=True)
    print("\nPorcentaje de faltantes por columna:")
    print(faltantes[faltantes["nulos"] > 0])
    registrar("2b. Faltantes", "Columnas con al menos un nulo", int((faltantes["nulos"] > 0).sum()))
    registrar("2b. Faltantes", "Filas completamente duplicadas", int(df.duplicated().sum()))

    # 2c. Análisis de NUMBER OF PERSONS KILLED nulo (no asumir que significa 0)
    k = COLS["kill_total"]
    k_nulo = df[k].isna()
    registrar("2c. Fallecidos nulos", "Filas con total de fallecidos nulo", int(k_nulo.sum()))
    registrar("2c. Fallecidos nulos", "...y también heridos nulo",
              int((k_nulo & df[COLS["inj_total"]].isna()).sum()))
    partes_ok = df[COLS["kill_parts"]].notna().all(axis=1)
    registrar("2c. Fallecidos nulos", "...con las 3 categorías informadas (total recuperable por suma)",
              int((k_nulo & partes_ok).sum()))
    registrar("2c. Fallecidos nulos", "...sin ninguna categoría informada",
              int((k_nulo & df[COLS["kill_parts"]].isna().all(axis=1)).sum()))
    por_anio = fechas[k_nulo].dt.year.value_counts().sort_index()
    print("\nFallecidos nulos por año (¿hay patrón temporal?):")
    print(por_anio)
    guardar(por_anio.rename("nulos").to_frame(), "fallecidos_nulos_por_anio.csv", index=True)
    print("\nMuestra de filas sin dato de fallecidos:")
    print(df.loc[k_nulo, [COLS["id"], COLS["fecha"], COLS["inj_total"], k]].head(10))
    print("\nEstadísticas de fallecidos (valores existentes):")
    print(df[k].describe())

    # 2d. Columnas de vehículos: huecos en la secuencia (afecta la tabla larga)
    huecos = 0
    for i in range(1, 5):
        actual = df[f"VEHICLE TYPE CODE {i}"].isna()
        siguiente = df[f"VEHICLE TYPE CODE {i + 1}"].notna()
        huecos += int((actual & siguiente).sum())
    registrar("2d. Vehículos", "Filas con un vehículo informado después de uno vacío", huecos)
    sin_vehiculo = df[[f"VEHICLE TYPE CODE {i}" for i in range(1, 6)]].isna().all(axis=1)
    registrar("2d. Vehículos", "Accidentes sin ningún tipo de vehículo", int(sin_vehiculo.sum()))

    # 3. Consistencia de heridos y fallecidos (solo filas completas)
    for nombre, total, partes in [
        ("heridos", COLS["inj_total"], COLS["inj_parts"]),
        ("fallecidos", COLS["kill_total"], COLS["kill_parts"]),
    ]:
        cols = [total] + partes
        completas = df[cols].notna().all(axis=1)
        suma = df.loc[completas, partes].sum(axis=1)
        dif = (df.loc[completas, total] != suma).sum()
        registrar("3. Consistencia", f"Filas {nombre} comparables", int(completas.sum()))
        registrar("3. Consistencia", f"Filas con total de {nombre} distinto a la suma", int(dif))

    # 3b. Valores negativos en todas las columnas numéricas de personas
    numericas = [COLS["inj_total"], COLS["kill_total"]] + COLS["inj_parts"] + COLS["kill_parts"]
    for c in numericas:
        registrar("3b. Negativos", c, int((df[c] < 0).sum()))

    # 4. Validación geográfica
    lat, lon = df[COLS["lat"]], df[COLS["lon"]]
    registrar("4. Geografía", "Solo una coordenada informada", int((lat.isna() != lon.isna()).sum()))
    ambas = lat.notna() & lon.notna()
    fuera_mundo = ambas & (~lat.between(-90, 90) | ~lon.between(-180, 180))
    registrar("4. Geografía", "Fuera de rango mundial", int(fuera_mundo.sum()))
    ceros = ambas & (lat == 0) & (lon == 0)
    registrar("4. Geografía", "Coordenadas (0, 0)", int(ceros.sum()))
    fuera_ny = ambas & ~ceros & (~lat.between(*NY_LAT) | ~lon.between(*NY_LON))
    registrar("4. Geografía", "Fuera de la caja de Nueva York (sin contar 0,0)", int(fuera_ny.sum()))

    # 5. Calidad de categorías
    b = df[COLS["borough"]]
    registrar("5. Categorías", "BOROUGH nulos", int(b.isna().sum()))
    registrar("5. Categorías", "BOROUGH distintos", b.nunique())
    con_espacios = b.dropna().astype(str)
    registrar("5. Categorías", "BOROUGH con espacios sobrantes",
              int((con_espacios != con_espacios.str.strip()).sum()))
    registrar("5. Categorías", "BOROUGH variantes de mayúsculas",
              int(con_espacios.str.strip().str.upper().nunique() != con_espacios.str.strip().nunique()))

if __name__ == "__main__":
    try:
        main(ruta)
    finally:
        guardar(pd.DataFrame(resultados), "validacion_reporte.csv")