"""
03_ciencia_datos.py  -  Etapa de Data Science

Preguntas y predicciones sobre el dataset escogido
  A) Prediccion : ¿se puede predecir si un choque tendra heridos o fallecidos?
  B) Clustering : ¿donde se concentran geograficamente los choques con heridos?
  C) Inferencia : ¿cuanto cambia la probabilidad de heridos segun bicicleta, moto, hora...?

Entrada : tablas COLLISIONS y COLLISION_VEHICLE en Oracle.
Salida  : reports/data_science/*.csv y *.png, y las tablas DS_METRICAS,
          DS_PREDICCIONES, DS_FOCOS y DS_ODDS en Oracle.

Uso:
    python scripts/03_ciencia_datos.py            # lee de Oracle y escribe en Oracle
    python scripts/03_ciencia_datos.py --csv      # prueba sin Oracle (usa data/processed)
    python scripts/03_ciencia_datos.py --comparar-crudo   # agrega modelo con datos crudos
"""

# ===========================================================================
# 1. IMPORTACIONES
# ===========================================================================

# argparse: permite que el script reciba opciones desde la terminal
import argparse
# getpass: pide la contraseña sin mostrarla en pantalla.
import getpass
# Path: maneja rutas de carpetas y archivos
from pathlib import Path

# matplotlib: biblioteca para hacer graficos.
import matplotlib
# "Agg" es un modo que dibuja los graficos directamente en archivos PNG, sin abrir
# ventanas. Asi el script corre igual en un servidor o sin pantalla.
# Debe ejecutarse antes de importar pyplot.
matplotlib.use("Agg")
# pyplot es la parte de matplotlib con la que se dibuja.
import matplotlib.pyplot as plt
# numpy: calculo con arreglos de numeros.
import numpy as np
# pandas: tablas en memoria (DataFrame), como una hoja de Excel programable.
import pandas as pd
# statsmodels: modelos estadisticos clasicos, con resultados interpretables
# (coeficientes, intervalos de confianza, valores p). ".formula.api" permite escribir
# el modelo como una formula de texto, por ejemplo "y ~ x1 + x2".
import statsmodels.formula.api as smf
# DBSCAN: algoritmo de clustering por densidad (agrupa puntos cercanos entre si).
from sklearn.cluster import DBSCAN
# ColumnTransformer: aplica un tratamiento distinto a cada grupo de columnas
# (numericas, categoricas, binarias) dentro de un mismo paso.
from sklearn.compose import ColumnTransformer
# HistGradientBoostingClassifier: modelo de "gradient boosting" (muchos arboles de
# decision pequeños que se corrigen entre si). Es el modelo principal.
from sklearn.ensemble import HistGradientBoostingClassifier
# SimpleImputer: rellena valores vacios (nulos) con un valor tipico, por ejemplo la mediana.
from sklearn.impute import SimpleImputer
# permutation_importance: mide cuanto empeora el modelo al "desordenar" una columna;
# cuanto mas empeora, mas importante era esa columna.
from sklearn.inspection import permutation_importance
# LogisticRegression: modelo base, simple, para tener con que comparar.
from sklearn.linear_model import LogisticRegression
# Funciones para medir que tan bien predice un modelo.
from sklearn.metrics import (average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score, RocCurveDisplay)
# Pipeline: encadena pasos (preparar datos -> entrenar modelo) en un solo objeto.
# Garantiza que lo aprendido en entrenamiento se aplique igual en prueba.
from sklearn.pipeline import Pipeline
# OneHotEncoder: convierte texto en columnas 0/1; StandardScaler: pone los numeros en
# una escala comparable (promedio 0 y desviacion 1).
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# ===========================================================================
# 2. CONFIGURACION
# ===========================================================================

# __file__ es la ruta de este script. .resolve() la vuelve absoluta; el primer
# .parent es la carpeta "scripts" y el segundo .parent es la raiz del proyecto.
RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "reports" / "data_science"
# Datos de conexion a Oracle que no son secretos.
# service XEPDB1 = base de datos del usuario en Oracle XE.
ORACLE = {"host": "localhost", "port": 1521, "service": "XEPDB1"}
# Numero que fija el azar de los algoritmos: con la misma semilla, el resultado es
# identico en cada ejecucion.
SEMILLA = 42
# Fecha de corte de la division temporal: los choques anteriores a esta fecha entrenan
# el modelo y los posteriores lo evaluan. Asi se simula predecir "el futuro".
FECHA_CORTE = "2026-04-01"
# Parametros de DBSCAN: dos choques son "vecinos" si estan a menos de 150 metros
EPS_METROS = 150
# Se forma un foco si hay al menos 15 choques en esa vecindad.
MIN_PUNTOS = 15

# Columnas que usara el modelo, agrupadas por tipo, porque cada tipo se prepara distinto:
# numericas (se rellenan vacios y, en la regresion logistica, se escalan)
NUM = ["HORA", "DIA_SEMANA", "ES_FIN_SEMANA", "LATITUDE", "LONGITUDE", "N_VEHICULOS"]
# categoricas (texto; se convierten a columnas 0/1)
CAT = ["BOROUGH", "FACTOR_1"]
# binarias
BIN = ["HAY_BICI", "HAY_MOTO", "HAY_CAMION", "FACTOR_NO_ESPECIFICADO"]


# ===========================================================================
# 3. LECTURA DE DATOS
# Cada funcion devuelve 3 cosas: la conexion (o None), la tabla de choques (c)
# y la tabla de vehiculos (v).
# ===========================================================================
def leer_oracle():
    # Se importa aqui adentro para que el modo --csv funcione aunque oracledb no este instalado.
    import oracledb
    usuario = input("Usuario de Oracle: ").strip()
    clave = getpass.getpass("Contraseña de Oracle: ")
    # DSN = "host:puerto/servicio": la direccion de la base de datos
    dsn = f"{ORACLE['host']}:{ORACLE['port']}/{ORACLE['service']}"
    # Abre la conexion. Si usuario o contraseña estan mal, aqui aparece ORA-01017.
    conn = oracledb.connect(user=usuario, password=clave, dsn=dsn)
    # Cada SELECT se ejecuta EN Oracle y pandas recibe el resultado como DataFrame.
    # Asi se cumple que las consultas del trabajo corren sobre el gestor.
    c = pd.read_sql("SELECT * FROM COLLISIONS", conn)
    v = pd.read_sql("SELECT * FROM COLLISION_VEHICLE", conn)
    c.columns, v.columns = c.columns.str.upper(), v.columns.str.upper()
    return conn, c, v


def leer_csv_procesado():
    # Modo de prueba: lee los CSV que dejo la etapa de Data Engineering (sin Oracle).
    p = RAIZ / "data" / "processed"
    c = pd.read_csv(p / "collisions_clean.csv", encoding="utf-8-sig", parse_dates=["CRASH_DATE"])
    v = pd.read_csv(p / "collision_vehicle_clean.csv", encoding="utf-8-sig")
    # None ocupa el lugar de la conexion: no hay base de datos en este modo.
    return None, c, v


def leer_csv_crudo():
    """Datos tal como se publicaron: sin limpieza (ceros en coordenadas, texto sin normalizar)."""
    # Lee el archivo original de NYC Open Data.
    r = pd.read_csv(RAIZ / "data" / "raw" / "Motor_Vehicle_Collisions_original.csv")
    # El original usa espacios en los nombres ("CRASH DATE"); se cambian por "_" para
    # que coincidan con los nombres de las tablas limpias.
    r.columns = [x.replace(" ", "_") for x in r.columns]
    # Une fecha y hora en una sola columna ("09/01/2025" + " " + "13:00") y la convierte a
    # fecha real. errors="coerce": si una fecha no se puede leer, queda vacia en vez de fallar.
    r["CRASH_DATE"] = pd.to_datetime(r["CRASH_DATE"] + " " + r["CRASH_TIME"],
                                     format="%m/%d/%Y %H:%M", errors="coerce")
    # Renombra las dos columnas de totales para que tengan el mismo nombre que en las
    # tablas limpias. Estos totales quedan sin corregir.
    c = r.rename(columns={"NUMBER_OF_PERSONS_INJURED": "PERSONS_INJURED",
                          "NUMBER_OF_PERSONS_KILLED": "PERSONS_KILLED"})
    # El original trae hasta 5 vehiculos por choque en columnas (ancho). Se pasan a filas
    # (largo), una por vehiculo, como en la tabla COLLISION_VEHICLE.
    filas = []
    for i in range(1, 6):                                   # vehiculos 1 a 5
        # Selecciona el id del choque y el tipo y factor del vehiculo i.
        t = r[["COLLISION_ID", f"VEHICLE_TYPE_CODE_{i}", f"CONTRIBUTING_FACTOR_VEHICLE_{i}"]].copy()
        t.columns = ["COLLISION_ID", "VEHICLE_TYPE", "CONTRIBUTING_FACTOR"]
        t["VEHICLE_CODE"] = i                               # numero de vehiculo (1 a 5)
        # Descarta las filas donde no hay ni tipo ni factor (el vehiculo no existe).
        filas.append(t.dropna(subset=["VEHICLE_TYPE", "CONTRIBUTING_FACTOR"], how="all"))
    # pd.concat apila las 5 tablas en una sola.
    return None, c, pd.concat(filas)


# ===========================================================================
# 4. CONSTRUCCION DE ATRIBUTOS
# Convierte las dos tablas en UNA fila por choque, con las columnas que usa el modelo.
# ===========================================================================
def construir_atributos(c, v):
    """Una fila por choque. Solo variables conocidas al momento del choque.
    PERSONS_*, PEDESTRIANS_*, CYCLISTS_* y MOTORIST_* se usan solamente para la
    etiqueta: usarlas como atributos seria fuga de datos."""
    # .copy() evita modificar la tabla original por accidente.
    d = c.copy()

    # ETIQUETA (lo que se quiere predecir): 1 si hubo al menos un herido o un fallecido, 0 si no.
    # fillna(0) trata los nulos como 0; ">0" da verdadero/falso; "|" significa "o";
    # astype(int) convierte verdadero/falso a 1/0.
    d["HERIDOS"] = ((d["PERSONS_INJURED"].fillna(0) > 0) | (d["PERSONS_KILLED"].fillna(0) > 0)).astype(int)

    # Atributos de tiempo, sacados de la fecha del choque:
    d["HORA"] = d["CRASH_DATE"].dt.hour                      
    d["DIA_SEMANA"] = d["CRASH_DATE"].dt.dayofweek           
    d["ES_FIN_SEMANA"] = (d["DIA_SEMANA"] >= 5).astype(int)  # 1 si es sabado (5) o domingo (6)
    # Franja horaria: pd.cut agrupa la hora en intervalos. Los cortes [-1,5,11,17,23] forman
    # (-1,5]=madrugada, (5,11]=mañana, (11,17]=tarde, (17,23]=noche. astype(str) lo vuelve texto.
    d["FRANJA"] = pd.cut(d["HORA"], [-1, 5, 11, 17, 23],
                         labels=["MADRUGADA", "MANANA", "TARDE", "NOCHE"]).astype(str)
    # Los distritos vacios (22 %) se marcan como "SIN DATO" para no perder esas filas.
    d["BOROUGH"] = d["BOROUGH"].fillna("SIN DATO")

    # --- Atributos que vienen de la tabla de vehiculos ---
    v = v.copy()
    # Texto del tipo de vehiculo y del factor; los vacios pasan a "" para poder buscar texto.
    t = v["VEHICLE_TYPE"].fillna("").astype(str)
    f = v["CONTRIBUTING_FACTOR"].fillna("").astype(str)
    # str.contains busca palabras en el texto; "|" significa "o" (regex); case=False ignora
    # mayusculas. Cada linea crea una columna verdadero/falso por vehiculo.
    v["BICI"] = t.str.contains("BIK|BICYC|E-B", case=False)               # bicicletas, e-bikes
    v["MOTO"] = t.str.contains("MOTORC|MOTORB|SCOOT|MOPED", case=False)   # motos, scooters
    v["CAMION"] = t.str.contains("TRUCK|BUS|TRACTOR|DUMP|VAN", case=False)  # camiones, buses, vans
    # Verdadero si el factor esta vacio o dice UNSPECIFIED (no se sabe la causa).
    v["NO_ESP"] = f.str.upper().isin(["", "UNSPECIFIED"])
    # Resume los vehiculos de cada choque en una fila: cuantos fueron (size) y si hubo
    # alguno de bici/moto/camion (max de verdadero/falso = "al menos uno").
    por = v.groupby("COLLISION_ID").agg(N_VEHICULOS=("VEHICLE_CODE", "size"),
                                        HAY_BICI=("BICI", "max"), HAY_MOTO=("MOTO", "max"),
                                        HAY_CAMION=("CAMION", "max"))
    # Toma solo el vehiculo 1 de cada choque (su factor contribuyente es el principal).
    # drop_duplicates asegura una fila por choque; set_index prepara la union.
    v1 = v[v["VEHICLE_CODE"] == 1].drop_duplicates("COLLISION_ID").set_index("COLLISION_ID")
    # join pega esas columnas a la tabla de choques usando COLLISION_ID como llave
    # (equivale a un LEFT JOIN de SQL: los choques sin vehiculos se conservan).
    d = d.join(por, on="COLLISION_ID").join(v1[["CONTRIBUTING_FACTOR", "NO_ESP"]], on="COLLISION_ID")
    # Choques sin ningun vehiculo registrado (388): quedan con 0 vehiculos.
    d["N_VEHICULOS"] = d["N_VEHICULOS"].fillna(0)
    # Para esos mismos choques, "no hay bici/moto/camion" = falso = 0.
    for col in ["HAY_BICI", "HAY_MOTO", "HAY_CAMION"]:
        d[col] = d[col].fillna(False).astype(int)
    # Sin vehiculo 1 registrado se considera factor no especificado.
    d["FACTOR_NO_ESPECIFICADO"] = d["NO_ESP"].fillna(True).astype(int)
    # Factor principal agrupado: se conservan los 10 mas frecuentes y el resto pasa a "OTRO".
    # Asi se evitan decenas de categorias casi vacias que el modelo no podria aprender.
    top = d["CONTRIBUTING_FACTOR"].str.upper().value_counts().head(10).index
    # .where(condicion, "OTRO") deja el valor donde se cumple la condicion y pone "OTRO" donde no.
    # Nota: los nulos tambien caen en "OTRO", asi que el fillna final no cambia nada
    # (queda solo por seguridad).
    d["FACTOR_1"] = d["CONTRIBUTING_FACTOR"].str.upper().where(
        d["CONTRIBUTING_FACTOR"].str.upper().isin(top), "OTRO").fillna("SIN DATO")
    return d


# ===========================================================================
# 5. A) PREDICCION
# ===========================================================================
def prep(escalar):
    # Pasos para las columnas numericas: primero rellenar vacios con la mediana
    # (resistente a valores extremos) y, si escalar=True, estandarizar la escala.
    # La regresion logistica necesita escala parecida entre columnas; los arboles no.
    num = [("imp", SimpleImputer(strategy="median"))] + ([("sc", StandardScaler())] if escalar else [])
    return ColumnTransformer([
        # Cada tupla es (nombre del paso, que hacer, a que columnas).
        ("num", Pipeline(num), NUM),
        # OneHot: cada categoria pasa a ser una columna 0/1. handle_unknown="ignore" evita
        # errores si en prueba aparece una categoria nueva; min_frequency=20 agrupa en una sola
        # las categorias con menos de 20 casos.
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CAT),
        # Las binarias ya estan listas: "passthrough" = pasar sin tocar.
        ("bin", "passthrough", BIN)])


def modelos():
    # Devuelve un diccionario {nombre: modelo}. Cada modelo es un Pipeline:
    # paso "p" (preparar datos) + paso "m" (el modelo).
    return {
        # Modelo base: regresion logistica. max_iter=1000 le da suficientes intentos de
        # converger; random_state fija el azar.
        "Regresion logistica (base)": Pipeline([("p", prep(True)),
            ("m", LogisticRegression(max_iter=1000, random_state=SEMILLA))]),
        # Modelo principal: gradient boosting con arboles de profundidad maxima 4,
        # tasa de aprendizaje 0.08 (pasos pequeños) y 200 rondas de arboles.
        "Gradient boosting": Pipeline([("p", prep(False)),
            ("m", HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08,
                                                 max_iter=200, random_state=SEMILLA))]),
    }


def evaluar(nombre, modelo, Xtr, ytr, Xte, yte, etiqueta):
    # Entrena (fit) con los datos de entrenamiento: Xtr = atributos, ytr = respuesta real.
    modelo.fit(Xtr, ytr)
    # Para cada choque de prueba, el modelo da la probabilidad de heridos (columna 1 = clase "1").
    p = modelo.predict_proba(Xte)[:, 1]
    # Decision: si la probabilidad es >= 0.5 se predice "con heridos" (1), si no 0.
    # Ese 0.5 es el "umbral" y se puede ajustar.
    pred = (p >= 0.5).astype(int)
    # Metricas, todas calculadas SOLO sobre el conjunto de prueba (datos que el modelo no vio):
    #   ROC_AUC  : que tan bien ordena los casos con heridos por encima de los demas (0.5 = azar, 1 = perfecto)
    #   PR_AUC   : igual, pero mas sensible cuando las clases estan desbalanceadas
    #   precision: de los que predijo "con heridos", cuantos realmente lo eran
    #   recall   : de los que realmente tenian heridos, cuantos detecto
    #   F1       : promedio armonico de precision y recall
    #   tasa_base: proporcion real de heridos en prueba (lo que acertaria un modelo ingenuo)
    fila = {"conjunto": etiqueta, "modelo": nombre, "n_train": len(Xtr), "n_test": len(Xte),
            "ROC_AUC": roc_auc_score(yte, p), "PR_AUC": average_precision_score(yte, p),
            "precision": precision_score(yte, pred), "recall": recall_score(yte, pred),
            "F1": f1_score(yte, pred), "tasa_base_test": yte.mean()}
    return fila, p, pred


def correr(c, v, etiqueta):
    # Construye la tabla de atributos (una fila por choque).
    d = construir_atributos(c, v)
    # DIVISION TEMPORAL: tr (entrenamiento) = choques antes del corte; te (prueba) = desde el corte.
    # No es aleatoria a proposito: el modelo nunca ve el "futuro" durante el entrenamiento.
    tr, te = d[d["CRASH_DATE"] < FECHA_CORTE], d[d["CRASH_DATE"] >= FECHA_CORTE]
    # Lista final de columnas que se le dan al modelo.
    X = NUM + CAT + BIN
    filas, mejor = [], None
    # Recorre los dos modelos (nombre, objeto) y los evalua con los mismos datos.
    for nombre, m in modelos().items():
        fila, p, pred = evaluar(nombre, m, tr[X], tr["HERIDOS"], te[X], te["HERIDOS"], etiqueta)
        filas.append(fila)
        # Guarda lo necesario del modelo principal para hacer los graficos y guardar predicciones.
        if nombre == "Gradient boosting":
            mejor = (m, te, p, pred)
    # Devuelve: tabla de metricas, el mejor modelo, la lista de columnas y la tabla completa d.
    return pd.DataFrame(filas), mejor, X, d


# ===========================================================================
# 6. B) CLUSTERING: focos geograficos de choques con heridos
# ===========================================================================
def _moda(x):
    # Devuelve el valor MAS FRECUENTE de un grupo (la "moda"); sirve para nombrar un foco
    # con su distrito y calle mas comunes. El guion bajo inicial indica "uso interno".
    x = x.dropna()                          # ignora los vacios
    x = x[x != "SIN DATO"]                  # ignora la etiqueta de dato faltante
    # .mode() da los valores mas frecuentes; .iat[0] toma el primero. Si no queda nada, "SIN DATO".
    return x.mode().iat[0] if len(x) else "SIN DATO"


def focos(d, etiqueta):
    """DBSCAN (distancia haversine, en metros reales) sobre choques con heridos.
    Un foco es una zona con al menos MIN_PUNTOS choques a menos de EPS_METROS entre si."""
    # Se queda solo con choques con heridos que tengan latitud y longitud (notna = no nulo).
    g = d[(d["HERIDOS"] == 1) & d["LATITUDE"].notna() & d["LONGITUDE"].notna()].copy()
    # La distancia haversine mide sobre la esfera terrestre y exige coordenadas en RADIANES.
    rad = np.radians(g[["LATITUDE", "LONGITUDE"]].to_numpy())
    # Ejecuta DBSCAN. eps se da en radianes: metros / radio de la Tierra (6.371.000 m).
    # algorithm="ball_tree" es el metodo de busqueda de vecinos compatible con haversine.
    # fit_predict devuelve un numero de foco por choque: 0, 1, 2... o -1 = "ruido" (sin foco).
    g["FOCO"] = DBSCAN(eps=EPS_METROS / 6371000, min_samples=MIN_PUNTOS,
                       metric="haversine", algorithm="ball_tree").fit_predict(rad)
    # f = choques que SI pertenecen a un foco (numero >= 0).
    f = g[g["FOCO"] >= 0]
    # Resume cada foco en una fila: cuantos choques, cuantos heridos, centro (promedio de
    # coordenadas), y el distrito y calle mas frecuentes.
    res = f.groupby("FOCO").agg(N_CHOQUES=("COLLISION_ID", "size"),
                                N_HERIDOS=("PERSONS_INJURED", "sum"),
                                LAT=("LATITUDE", "mean"), LON=("LONGITUDE", "mean"),
                                DISTRITO=("BOROUGH", _moda),
                                CALLE=("ON_STREET_NAME", _moda)).reset_index()
    # Ordena del foco mas grande al mas pequeño y renumera el indice desde 0.
    res = res.sort_values("N_CHOQUES", ascending=False).reset_index(drop=True)
    # Agrega al inicio una columna que indica si son datos TRATADO o CRUDO.
    res.insert(0, "CONJUNTO", etiqueta)
    # Resumen de una linea por ejecucion, para comparar tratado contra crudo.
    resumen = {"conjunto": etiqueta, "choques_con_heridos_y_coordenadas": len(g),
               "focos": len(res), "pct_en_focos": round(100 * len(f) / len(g), 1),
               "foco_mas_grande": int(res["N_CHOQUES"].iloc[0]),     # tamaño del foco #1
               "lat_foco_mas_grande": round(res["LAT"].iloc[0], 4),  # dónde esta ese foco
               "lon_foco_mas_grande": round(res["LON"].iloc[0], 4)}
    # --- Grafico de los focos ---
    fig, ax = plt.subplots(figsize=(6, 6))               # figura (6x6 pulgadas) y sus ejes
    ruido = g[g["FOCO"] < 0]                             # choques que no forman foco
    # Puntos grises pequeños para el ruido; puntos de colores (uno por foco) para los focos.
    ax.scatter(ruido["LONGITUDE"], ruido["LATITUDE"], s=1, c="lightgray", label="fuera de focos")
    ax.scatter(f["LONGITUDE"], f["LATITUDE"], s=3, c=f["FOCO"], cmap="tab20", label="focos")
    ax.set_xlabel("Longitud"); ax.set_ylabel("Latitud")  # nombres de los ejes
    ax.set_title(f"Focos de choques con heridos ({etiqueta.lower()})"); ax.legend(markerscale=5)
    # tight_layout acomoda margenes; savefig guarda el PNG (dpi=150 = buena resolucion);
    # close libera memoria.
    fig.tight_layout(); fig.savefig(SALIDA / f"focos_{etiqueta.lower()}.png", dpi=150); plt.close(fig)
    # Guarda la tabla de focos en CSV (index=False: sin la columna de numeros de fila).
    res.to_csv(SALIDA / f"focos_{etiqueta.lower()}.csv", index=False)
    return res, resumen


# ===========================================================================
# 7. C) INFERENCIA: regresion logistica con odds ratios
# ===========================================================================
def inferencia(d):
    """Regresion logistica explicativa (statsmodels). Se usan todos los choques porque el
    objetivo es medir asociaciones, no predecir. Se reporta el odds ratio = exp(coeficiente):
    1 = sin efecto; >1 = mas probabilidad de heridos; <1 = menos."""
    d = d.copy()
    # Limita el numero de vehiculos a 5 para que unos pocos choques extremos no distorsionen.
    d["N_VEH_C"] = d["N_VEHICULOS"].clip(upper=5)
    # Formula del modelo: "respuesta ~ explicativas". C(columna, Treatment('X')) trata la
    # columna como categorias y fija X como GRUPO DE REFERENCIA: los demas grupos se
    # comparan contra el (tarde para la franja, Brooklyn para el distrito).
    formula = ("HERIDOS ~ HAY_BICI + HAY_MOTO + HAY_CAMION + FACTOR_NO_ESPECIFICADO"
               " + ES_FIN_SEMANA + N_VEH_C"
               " + C(FRANJA, Treatment('TARDE')) + C(BOROUGH, Treatment('BROOKLYN'))")
    # logit = regresion logistica. fit() la ajusta; disp=False evita imprimir el proceso.
    m = smf.logit(formula, data=d).fit(disp=False)
    # Intervalos de confianza al 95 % de los coeficientes (columna 0 = inferior, 1 = superior).
    ci = m.conf_int()
    # np.exp convierte los coeficientes en ODDS RATIOS. Si el intervalo incluye al 1,
    # el efecto podria ser nulo. P_VALOR: probabilidad de ver este efecto por azar si en
    # realidad no existiera; menor a 0.05 se considera "estadisticamente significativo".
    out = pd.DataFrame({"VARIABLE": m.params.index, "ODDS_RATIO": np.exp(m.params.values),
                        "IC95_INF": np.exp(ci[0].values), "IC95_SUP": np.exp(ci[1].values),
                        "P_VALOR": m.pvalues.values})
    # El "Intercept" (constante del modelo) no es una variable que interprete el grupo.
    out = out[out["VARIABLE"] != "Intercept"]
    # Limpia los nombres feos de statsmodels, p. ej. "C(FRANJA, Treatment('TARDE'))[T.NOCHE]"
    # pasa a "FRANJA=NOCHE (ref TARDE)". r"..." es una expresion regular y \1, \2, \3 son
    # los textos capturados entre parentesis.
    out["VARIABLE"] = (out["VARIABLE"].str.replace(r"C\((\w+), Treatment\('(\w+)'\)\)\[T\.(.*)\]",
                                                   r"\1=\3 (ref \2)", regex=True))
    # Ordena de mayor a menor odds ratio.
    out = out.sort_values("ODDS_RATIO", ascending=False).reset_index(drop=True)
    out.round(4).to_csv(SALIDA / "odds_ratios.csv", index=False)     # guarda con 4 decimales
    # --- Grafico de bosque (forest plot): un punto por variable con su intervalo ---
    fig, ax = plt.subplots(figsize=(7, 5))
    y = np.arange(len(out))[::-1]                       # posiciones verticales (de arriba a abajo)
    # xerr = distancia del punto a cada extremo del intervalo (izquierda y derecha).
    ax.errorbar(out["ODDS_RATIO"], y, xerr=[out["ODDS_RATIO"] - out["IC95_INF"],
                out["IC95_SUP"] - out["ODDS_RATIO"]], fmt="o", capsize=3)
    # Linea punteada en 1 (= sin efecto) y eje horizontal logaritmico (los odds ratios son
    # multiplicativos, asi 0.5 y 2 quedan a la misma distancia de 1).
    ax.axvline(1, ls="--", color="gray"); ax.set_xscale("log")
    ax.set_yticks(y, out["VARIABLE"]); ax.set_xlabel("Odds ratio (escala log, IC 95 %)")
    ax.set_title("Asociacion con la probabilidad de heridos")
    fig.tight_layout(); fig.savefig(SALIDA / "odds_ratios.png", dpi=150); plt.close(fig)
    # prsquared = pseudo R2 de McFadden: que parte de la variacion explica el modelo.
    return out, m.prsquared


# ===========================================================================
# 8. FIGURAS DEL MODELO PREDICTIVO
# ===========================================================================
def figuras(mejor, X):
    # Desempaqueta lo guardado en correr(): modelo, datos de prueba, probabilidades y predicciones.
    m, te, p, pred = mejor
    SALIDA.mkdir(parents=True, exist_ok=True)           # crea la carpeta si no existe

    # Curva ROC: eje x = falsos positivos, eje y = verdaderos positivos, probando todos
    # los umbrales. Cuanto mas se aleja de la diagonal (el azar), mejor.
    fig, ax = plt.subplots(figsize=(5, 4))
    RocCurveDisplay.from_predictions(te["HERIDOS"], p, ax=ax, name="Gradient boosting")
    ax.plot([0, 1], [0, 1], "--", color="gray"); ax.set_title("Curva ROC (conjunto de prueba)")
    fig.tight_layout(); fig.savefig(SALIDA / "roc.png", dpi=150); plt.close(fig)

    # Matriz de confusion: filas = realidad, columnas = prediccion. Cuenta aciertos y errores.
    cm = confusion_matrix(te["HERIDOS"], pred)
    fig, ax = plt.subplots(figsize=(4, 4)); ax.imshow(cm, cmap="Blues")   # colores segun el conteo
    for (i, j), z in np.ndenumerate(cm):                # escribe el numero dentro de cada celda
        ax.text(j, i, f"{z:,}", ha="center", va="center")
    ax.set_xticks([0, 1], ["Sin heridos", "Con heridos"]); ax.set_yticks([0, 1], ["Sin heridos", "Con heridos"])
    ax.set_xlabel("Predicho"); ax.set_ylabel("Real"); ax.set_title("Matriz de confusion")
    fig.tight_layout(); fig.savefig(SALIDA / "matriz_confusion.png", dpi=150); plt.close(fig)

    # Importancia por permutacion: baraja una columna a la vez en los datos de prueba y mide
    # cuanto cae el ROC-AUC. n_repeats=5 repite el barajado 5 veces y promedia.
    r = permutation_importance(m, te[X], te["HERIDOS"], n_repeats=5, random_state=SEMILLA,
                               scoring="roc_auc")
    imp = pd.Series(r.importances_mean, index=X).sort_values()   # una importancia por columna
    fig, ax = plt.subplots(figsize=(6, 4)); imp.plot.barh(ax=ax)  # grafico de barras horizontales
    ax.set_title("Importancia por permutacion (caida de ROC-AUC)")
    fig.tight_layout(); fig.savefig(SALIDA / "importancia.png", dpi=150); plt.close(fig)
    imp.sort_values(ascending=False).rename("caida_roc_auc").to_csv(SALIDA / "importancia.csv")


# ===========================================================================
# 9. GUARDAR RESULTADOS EN ORACLE
# Es lo que cierra la cadena Engineering -> Analytics -> Science: la ultima etapa
# devuelve su producto a la base de datos.
# ===========================================================================
def guardar_oracle(conn, metricas, mejor, focos_df, odds):
    m, te, p, pred = mejor
    cur = conn.cursor()                                  # el cursor ejecuta sentencias SQL
    # Borra las tablas DS_ si ya existian (para poder volver a ejecutar el script). El bloque
    # PL/SQL ignora el error -942 ("la tabla no existe") y deja pasar cualquier otro error.
    for t in ("DS_PREDICCIONES", "DS_METRICAS", "DS_FOCOS", "DS_ODDS"):
        cur.execute(f"""BEGIN EXECUTE IMMEDIATE 'DROP TABLE {t}';
                        EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;""")
    # Tabla de metricas del modelo. (PRECISION_ y RECALL_ llevan "_" porque PRECISION es
    # palabra reservada de Oracle.)
    cur.execute("""CREATE TABLE DS_METRICAS (CONJUNTO VARCHAR2(20), MODELO VARCHAR2(40),
                   N_TRAIN NUMBER, N_TEST NUMBER, ROC_AUC NUMBER, PR_AUC NUMBER,
                   PRECISION_ NUMBER, RECALL_ NUMBER, F1 NUMBER, TASA_BASE_TEST NUMBER)""")
    # Una prediccion por choque de prueba. La llave foranea (FOREIGN KEY) garantiza que cada
    # COLLISION_ID exista en COLLISIONS: asi la tabla queda enlazada con lo de Data Engineering.
    cur.execute("""CREATE TABLE DS_PREDICCIONES (COLLISION_ID NUMBER(12) PRIMARY KEY,
                   PROB_HERIDOS NUMBER(6,5), PRED NUMBER(1), REAL NUMBER(1),
                   CONSTRAINT FK_DSP FOREIGN KEY (COLLISION_ID) REFERENCES COLLISIONS (COLLISION_ID))""")
    # Tabla de focos geograficos (resultado del clustering).
    cur.execute("""CREATE TABLE DS_FOCOS (FOCO NUMBER(5) PRIMARY KEY, N_CHOQUES NUMBER,
                   N_HERIDOS NUMBER, LAT NUMBER(9,6), LON NUMBER(9,6),
                   DISTRITO VARCHAR2(20), CALLE VARCHAR2(100))""")
    # Tabla de odds ratios (resultado de la inferencia).
    cur.execute("""CREATE TABLE DS_ODDS (VARIABLE VARCHAR2(120), ODDS_RATIO NUMBER,
                   IC95_INF NUMBER, IC95_SUP NUMBER, P_VALOR NUMBER)""")
    # executemany inserta muchas filas de una vez. ":1, :2..." son marcadores que se
    # reemplazan por los valores de cada fila. Los valores se convierten a int/float/str de
    # Python porque Oracle no acepta los tipos propios de numpy. [:100] recorta el texto al
    # maximo de la columna.
    cur.executemany("INSERT INTO DS_FOCOS VALUES (:1,:2,:3,:4,:5,:6,:7)",
                    [(int(r.FOCO), int(r.N_CHOQUES), int(r.N_HERIDOS), float(r.LAT), float(r.LON),
                      str(r.DISTRITO), str(r.CALLE)[:100]) for r in focos_df.itertuples()])
    cur.executemany("INSERT INTO DS_ODDS VALUES (:1,:2,:3,:4,:5)",
                    [(str(r.VARIABLE)[:120], float(r.ODDS_RATIO), float(r.IC95_INF),
                      float(r.IC95_SUP), float(r.P_VALOR)) for r in odds.itertuples()])
    # Metricas: se convierte cada valor numpy a float/int normal de Python (el texto queda igual).
    cur.executemany("INSERT INTO DS_METRICAS VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9,:10)",
                    [tuple(float(x) if isinstance(x, (np.floating, float)) else
                           (int(x) if isinstance(x, (np.integer,)) else x) for x in r)
                     for r in metricas.itertuples(index=False)])
    # Predicciones: zip junta cuatro listas (id, probabilidad, prediccion, valor real) en filas.
    cur.executemany("INSERT INTO DS_PREDICCIONES VALUES (:1,:2,:3,:4)",
                    list(zip(te["COLLISION_ID"].astype(int).tolist(), np.round(p, 5).tolist(),
                             pred.tolist(), te["HERIDOS"].astype(int).tolist())))
    # commit confirma todos los cambios; sin esto Oracle los descartaria al cerrar.
    conn.commit()
    print("Resultados guardados en Oracle: DS_METRICAS, DS_PREDICCIONES, DS_FOCOS y DS_ODDS")


# ===========================================================================
# 10. PROGRAMA PRINCIPAL: aqui se decide el orden en que todo se ejecuta
# ===========================================================================
def main():
    # Define las opciones que acepta el script desde la terminal.
    ap = argparse.ArgumentParser()
    # store_true: la opcion es un interruptor; si se escribe --csv vale True, si no, False.
    ap.add_argument("--csv", action="store_true", help="leer data/processed en vez de Oracle")
    ap.add_argument("--comparar-crudo", action="store_true", help="entrenar tambien con datos crudos")
    a = ap.parse_args()                                  # lee lo que se escribio en la terminal
    SALIDA.mkdir(parents=True, exist_ok=True)            # crea la carpeta de resultados si falta

    # Lee los datos: de los CSV si se uso --csv; de Oracle en caso contrario.
    conn, c, v = leer_csv_procesado() if a.csv else leer_oracle()

    # --- A) Prediccion con datos tratados ---
    print("\n=== A) Prediccion de heridos ===")
    met, mejor, X, d = correr(c, v, "TRATADO")
    todo = [met]                                         # lista de tablas de metricas a unir luego
    # --- B) Clustering con datos tratados ---
    foc, res = focos(d, "TRATADO")
    resumenes = [res]
    # Si se pidio --comparar-crudo, se repite todo con los datos originales sin limpiar.
    if a.comparar_crudo:
        _, cr, vr = leer_csv_crudo()                     # "_" descarta la conexion (no se usa)
        mc, _, _, dc = correr(cr, vr, "CRUDO")           # modelo con datos crudos
        todo.append(mc)
        resumenes.append(focos(dc, "CRUDO")[1])          # [1] = solo el resumen, no la tabla
    # Une las metricas (tratado + crudo) y las guarda con 4 decimales.
    metricas = pd.concat(todo, ignore_index=True)
    metricas.round(4).to_csv(SALIDA / "metricas.csv", index=False)
    pd.DataFrame(resumenes).to_csv(SALIDA / "focos_resumen.csv", index=False)
    figuras(mejor, X)                                    # ROC, matriz de confusion, importancia
    # Muestra los resultados en pantalla (to_string(index=False) los imprime sin numero de fila).
    print(metricas.round(3).to_string(index=False))
    print("\n=== B) Focos de choques con heridos (DBSCAN) ===")
    print(pd.DataFrame(resumenes).to_string(index=False))
    print(foc.head(10).round(4).to_string(index=False))  # los 10 focos mas grandes

    # --- C) Inferencia con datos tratados ---
    print("\n=== C) Inferencia: odds ratios ===")
    odds, r2 = inferencia(d)
    print(odds.round(3).to_string(index=False))
    print(f"Pseudo R2 de McFadden: {r2:.3f}")

    # Si hay conexion a Oracle (no se uso --csv), escribe los resultados en la base y la cierra.
    if conn is not None:
        guardar_oracle(conn, metricas, mejor, foc, odds)
        conn.close()


# Este bloque hace que main() se ejecute solo cuando el archivo se corre directamente
# ("python 03_ciencia_datos.py"), no cuando otro programa lo importa.
if __name__ == "__main__":
    main()