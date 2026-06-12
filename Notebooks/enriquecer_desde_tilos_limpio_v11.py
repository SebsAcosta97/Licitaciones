"""
Merge corregido entre df_tilos_limpio y fichas_licitaciones_muestra_10_unificado_v10.

Objetivo:
- Usar df_tilos_limpio como tabla referente/base.
- Mantener solo las licitaciones que están en el parquet del pipeline v10,
  para conservar la muestra/proceso trabajado.
- Traer desde el parquet del pipeline únicamente columnas nuevas.
- No duplicar columnas.
- No duplicar filas.
- Guardar un nuevo parquet enriquecido en SILVER.

Lógica:
    df_tilos_limpio
        ↓ filtrar licitaciones que están en pipeline v10
    df_tilos_base
        + columnas nuevas de pipeline v10
        ↓
    fichas_licitaciones_muestra_10_unificado_v11_desde_tilos_limpio.parquet

Uso:
    python enriquecer_desde_tilos_limpio_v11.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


# ============================================================
# 1. Configuración de rutas
# ============================================================

SILVER_DIR = Path("data") / "silver"

ARCHIVO_TILOS_LIMPIO = SILVER_DIR / "df_tilos_limpio.parquet"

ARCHIVO_PIPELINE = (
    SILVER_DIR / "fichas_licitaciones_muestra_10_unificado_v10.parquet"
)

ARCHIVO_SALIDA = (
    SILVER_DIR
    / "fichas_licitaciones_muestra_10_unificado_v11_desde_tilos_limpio.parquet"
)

COLUMNA_LLAVE = "licitacion_id"


# ============================================================
# 2. Funciones auxiliares
# ============================================================

def cargar_parquet(ruta: Path, nombre: str) -> pd.DataFrame:
    """
    Carga un parquet validando que exista.
    """
    if not ruta.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo {nombre} en la ruta: {ruta}"
        )

    df = pd.read_parquet(ruta)

    print(f"\n{nombre}")
    print("=" * 80)
    print(f"Ruta: {ruta}")
    print(f"Filas: {df.shape[0]}")
    print(f"Columnas: {df.shape[1]}")

    return df.copy()


def validar_columna_llave(
    df: pd.DataFrame,
    nombre_df: str,
    columna_llave: str,
) -> None:
    """
    Valida que el DataFrame tenga la columna llave.
    """
    if columna_llave not in df.columns:
        raise ValueError(
            f"El DataFrame {nombre_df} no contiene la columna llave "
            f"'{columna_llave}'."
        )


def normalizar_llave(
    df: pd.DataFrame,
    columna_llave: str,
) -> pd.DataFrame:
    """
    Normaliza la llave como string, eliminando espacios externos.

    Esto reduce errores de merge por diferencias de tipo o espacios.
    """
    df = df.copy()

    df[columna_llave] = (
        df[columna_llave]
        .astype("string")
        .str.strip()
    )

    return df


def diagnosticar_duplicados(
    df: pd.DataFrame,
    nombre_df: str,
    columna_llave: str,
) -> None:
    """
    Imprime diagnóstico de duplicados por llave.
    """
    duplicados = int(df[columna_llave].duplicated().sum())
    unicos = int(df[columna_llave].nunique(dropna=True))

    print(f"\nDiagnóstico de llave - {nombre_df}")
    print("=" * 80)
    print(f"Licitaciones únicas: {unicos}")
    print(f"Duplicados por {columna_llave}: {duplicados}")


def dejar_un_registro_por_licitacion(
    df: pd.DataFrame,
    nombre_df: str,
    columna_llave: str,
) -> pd.DataFrame:
    """
    Deja un único registro por licitación.

    Criterio:
    - Se conserva el primer registro encontrado.
    - Antes se reporta cuántos duplicados existían.
    """
    duplicados = int(df[columna_llave].duplicated().sum())

    if duplicados > 0:
        print(
            f"\nAdvertencia: {nombre_df} tiene {duplicados} filas duplicadas "
            f"por {columna_llave}. Se conservará la primera aparición."
        )

    return df.drop_duplicates(subset=[columna_llave], keep="first").copy()


def seleccionar_columnas_nuevas(
    df_base: pd.DataFrame,
    df_aporte: pd.DataFrame,
    columna_llave: str,
) -> list[str]:
    """
    Selecciona columnas del aporte que no existen en la base.

    Siempre conserva la columna llave para permitir el merge.
    """
    columnas_base = set(df_base.columns)

    columnas_nuevas = [
        columna
        for columna in df_aporte.columns
        if columna not in columnas_base
    ]

    return [columna_llave] + columnas_nuevas


def reporte_columnas_solapadas(
    df_base: pd.DataFrame,
    df_aporte: pd.DataFrame,
    columna_llave: str,
) -> list[str]:
    """
    Identifica columnas que existen en ambos DataFrames.

    Estas columnas no se traen desde el aporte porque el referente es
    df_tilos_limpio.
    """
    columnas_solapadas = sorted(
        set(df_base.columns).intersection(set(df_aporte.columns))
        - {columna_llave}
    )

    return columnas_solapadas


# ============================================================
# 3. Proceso principal
# ============================================================

def main() -> pd.DataFrame:
    """
    Ejecuta el merge corregido usando df_tilos_limpio como referente.
    """
    print("\nINICIO DEL MERGE CORREGIDO")
    print("=" * 80)

    # --------------------------------------------------------
    # 3.1 Cargar archivos
    # --------------------------------------------------------

    df_tilos_limpio = cargar_parquet(
        ARCHIVO_TILOS_LIMPIO,
        "df_tilos_limpio",
    )

    df_pipeline = cargar_parquet(
        ARCHIVO_PIPELINE,
        "fichas_licitaciones_muestra_10_unificado_v10",
    )

    # --------------------------------------------------------
    # 3.2 Validar columna llave
    # --------------------------------------------------------

    validar_columna_llave(
        df_tilos_limpio,
        "df_tilos_limpio",
        COLUMNA_LLAVE,
    )

    validar_columna_llave(
        df_pipeline,
        "df_pipeline",
        COLUMNA_LLAVE,
    )

    # --------------------------------------------------------
    # 3.3 Normalizar llave
    # --------------------------------------------------------

    df_tilos_limpio = normalizar_llave(df_tilos_limpio, COLUMNA_LLAVE)
    df_pipeline = normalizar_llave(df_pipeline, COLUMNA_LLAVE)

    # --------------------------------------------------------
    # 3.4 Diagnóstico inicial de duplicados
    # --------------------------------------------------------

    diagnosticar_duplicados(
        df_tilos_limpio,
        "df_tilos_limpio",
        COLUMNA_LLAVE,
    )

    diagnosticar_duplicados(
        df_pipeline,
        "df_pipeline",
        COLUMNA_LLAVE,
    )

    # --------------------------------------------------------
    # 3.5 Dejar una fila por licitación
    # --------------------------------------------------------

    df_tilos_limpio = dejar_un_registro_por_licitacion(
        df_tilos_limpio,
        "df_tilos_limpio",
        COLUMNA_LLAVE,
    )

    df_pipeline = dejar_un_registro_por_licitacion(
        df_pipeline,
        "df_pipeline",
        COLUMNA_LLAVE,
    )

    # --------------------------------------------------------
    # 3.6 Filtrar df_tilos_limpio a las licitaciones del pipeline
    # --------------------------------------------------------

    ids_pipeline = set(df_pipeline[COLUMNA_LLAVE].dropna())

    df_tilos_base = (
        df_tilos_limpio[
            df_tilos_limpio[COLUMNA_LLAVE].isin(ids_pipeline)
        ]
        .copy()
        .reset_index(drop=True)
    )

    print("\nFiltro de df_tilos_limpio usando licitaciones del pipeline")
    print("=" * 80)
    print(f"Filas df_tilos_limpio después del filtro: {df_tilos_base.shape[0]}")
    print(f"Licitaciones en pipeline: {len(ids_pipeline)}")

    ids_tilos_base = set(df_tilos_base[COLUMNA_LLAVE].dropna())

    ids_pipeline_no_en_tilos = sorted(ids_pipeline - ids_tilos_base)

    print(
        "Licitaciones del pipeline no encontradas en df_tilos_limpio: "
        f"{len(ids_pipeline_no_en_tilos)}"
    )

    if ids_pipeline_no_en_tilos:
        print("\nIDs no encontrados en df_tilos_limpio:")
        for licitacion_id in ids_pipeline_no_en_tilos:
            print(f"- {licitacion_id}")

    if df_tilos_base.empty:
        raise ValueError(
            "Después de filtrar df_tilos_limpio con los IDs del pipeline, "
            "no quedó ninguna licitación. Revisar la llave de cruce."
        )

    # --------------------------------------------------------
    # 3.7 Reportar columnas solapadas
    # --------------------------------------------------------

    columnas_solapadas = reporte_columnas_solapadas(
        df_base=df_tilos_base,
        df_aporte=df_pipeline,
        columna_llave=COLUMNA_LLAVE,
    )

    print("\nColumnas existentes en ambos DataFrames")
    print("=" * 80)
    print(
        "Estas columnas NO se traen desde pipeline porque el referente "
        "es df_tilos_limpio."
    )

    if columnas_solapadas:
        for columna in columnas_solapadas:
            print(f"- {columna}")
    else:
        print("No hay columnas solapadas fuera de la llave.")

    # --------------------------------------------------------
    # 3.8 Seleccionar solo columnas nuevas desde pipeline
    # --------------------------------------------------------

    columnas_aporte = seleccionar_columnas_nuevas(
        df_base=df_tilos_base,
        df_aporte=df_pipeline,
        columna_llave=COLUMNA_LLAVE,
    )

    columnas_nuevas = [
        columna for columna in columnas_aporte
        if columna != COLUMNA_LLAVE
    ]

    print("\nColumnas nuevas que se traen desde pipeline")
    print("=" * 80)

    if columnas_nuevas:
        for columna in columnas_nuevas:
            print(f"- {columna}")
    else:
        print("No hay columnas nuevas para agregar desde pipeline.")

    df_pipeline_aporte = df_pipeline[columnas_aporte].copy()

    # --------------------------------------------------------
    # 3.9 Merge final
    # --------------------------------------------------------

    df_final = df_tilos_base.merge(
        df_pipeline_aporte,
        on=COLUMNA_LLAVE,
        how="left",
        validate="one_to_one",
    )

    # --------------------------------------------------------
    # 3.10 Validaciones finales
    # --------------------------------------------------------

    print("\nValidaciones finales")
    print("=" * 80)
    print(f"Filas df_tilos_base: {df_tilos_base.shape[0]}")
    print(f"Filas df_final: {df_final.shape[0]}")
    print(f"Columnas df_tilos_base: {df_tilos_base.shape[1]}")
    print(f"Columnas df_final: {df_final.shape[1]}")

    if df_final.shape[0] != df_tilos_base.shape[0]:
        raise ValueError(
            "El merge cambió el número de filas. "
            "Esto no debe ocurrir en un merge one_to_one."
        )

    duplicados_final = int(df_final[COLUMNA_LLAVE].duplicated().sum())

    if duplicados_final > 0:
        raise ValueError(
            f"El resultado final tiene {duplicados_final} duplicados "
            f"por {COLUMNA_LLAVE}."
        )

    columnas_duplicadas = df_final.columns[df_final.columns.duplicated()].tolist()

    if columnas_duplicadas:
        raise ValueError(
            "El resultado final tiene columnas duplicadas: "
            f"{columnas_duplicadas}"
        )

    # --------------------------------------------------------
    # 3.11 Guardar parquet final
    # --------------------------------------------------------

    ARCHIVO_SALIDA.parent.mkdir(parents=True, exist_ok=True)

    df_final.to_parquet(
        ARCHIVO_SALIDA,
        index=False,
    )

    print("\nParquet guardado correctamente")
    print("=" * 80)
    print(f"Ruta: {ARCHIVO_SALIDA}")
    print(f"Filas: {df_final.shape[0]}")
    print(f"Columnas: {df_final.shape[1]}")

    print("\nFIN DEL PROCESO")
    print("=" * 80)

    return df_final


if __name__ == "__main__":
    main()
