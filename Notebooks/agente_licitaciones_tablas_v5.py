"""
Agente flexible para consultar licitaciones enriquecidas.

Versión v5.

Correcciones frente a v4:
- Interpreta "con cpv" como columna adicional a mostrar.
- Tolera typo "cvp" y lo mapea a CPV.
- Tolera "importe sin impuesto" en singular y lo mapea a importe_sin_impuestos.
- En preguntas tipo:
    "mayor importe sin impuesto con cpv"
  devuelve:
    licitacion_id | titulo | importe_sin_impuestos | cpv_codes | cpv_descripcion
- En preguntas tipo:
    "importe sin impuestos por titulo con cpv"
  devuelve:
    licitacion_id | titulo | cpv_codes/cpv_descripcion | importe_sin_impuestos
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd


class AgenteLicitacionesTablas:
    """
    Agente analítico local para consultar un parquet de licitaciones.

    No usa IA generativa. Convierte preguntas simples en consultas
    controladas sobre pandas.
    """

    def __init__(
        self,
        ruta_parquet: Optional[str | Path] = None,
        columna_id: str = "licitacion_id",
    ) -> None:
        self.columna_id = columna_id

        if ruta_parquet is None:
            self.ruta_parquet = (
                Path("data")
                / "silver"
                / "fichas_licitaciones_muestra_10_unificado_v11_desde_tilos_limpio.parquet"
            )
        else:
            self.ruta_parquet = Path(ruta_parquet)

        self.df = self._cargar_parquet()
        self._validar_dataframe()
        self.columnas_normalizadas = self._crear_indice_columnas()

        self.alias_columnas = {
            # Llave / identificadores
            "id": ["licitacion_id", "expediente"],
            "id licitacion": ["licitacion_id"],
            "id de licitacion": ["licitacion_id"],
            "id licencia": ["licitacion_id"],
            "id de licencia": ["licitacion_id"],
            "licencia": ["licitacion_id"],
            "licitacion": ["licitacion_id", "titulo"],
            "expediente": ["expediente", "licitacion_id"],

            # Campos descriptivos
            "titulo": ["titulo"],
            "titulos": ["titulo"],
            "objeto": ["objeto_contrato", "objeto", "titulo"],
            "organo": ["organo_contratacion", "organo_dir3"],
            "órgano": ["organo_contratacion", "organo_dir3"],
            "entidad": ["organo_contratacion"],
            "estado": ["estado", "estado_codigo"],

            # CPV, incluyendo typo común CVP
            "cpv": ["cpv_codes", "cpv_descripcion", "cpv_nivel"],
            "cvp": ["cpv_codes", "cpv_descripcion", "cpv_nivel"],
            "cpv descripcion": ["cpv_descripcion"],
            "cvp descripcion": ["cpv_descripcion"],
            "codigo cpv": ["cpv_codes"],
            "código cpv": ["cpv_codes"],
            "codigos cpv": ["cpv_codes"],
            "códigos cpv": ["cpv_codes"],

            "region": ["nombre_region", "codigo_nuts"],
            "provincia": ["nombre_region", "codigo_nuts"],
            "pais": ["nombre_region", "pais", "pais_destino", "pais_venta"],
            "tipo contrato": ["tipo_contrato", "tipo_contrato_codigo"],
            "procedimiento": ["procedimiento", "procedimiento_codigo"],

            # Valores económicos
            "importe": ["importe_sin_impuestos", "importe", "presupuesto", "valor"],
            "importe sin impuesto": ["importe_sin_impuestos"],
            "importe sin impuestos": ["importe_sin_impuestos"],
            "valor": ["importe_sin_impuestos", "valor", "presupuesto", "precio"],
            "presupuesto": ["presupuesto", "importe_sin_impuestos", "valor"],
            "precio": ["precio", "importe_sin_impuestos", "valor"],
            "monto": ["importe_sin_impuestos", "valor", "presupuesto"],

            # Fechas
            "fecha": ["fecha_publicacion", "presentacion_hasta", "updated"],
            "fecha publicacion": ["fecha_publicacion"],
            "fecha publicación": ["fecha_publicacion"],
            "presentacion": ["presentacion_hasta", "presentacion_hora"],
            "presentación": ["presentacion_hasta", "presentacion_hora"],

            # Otros
            "url": ["url", "detail_url"],
            "link": ["url", "detail_url"],
            "adjudicatario": ["adjudicatario", "adjudicatario_nif"],
        }

    # ========================================================
    # Métodos base
    # ========================================================

    @staticmethod
    def _normalizar_texto(texto: str) -> str:
        texto = str(texto).lower().strip()
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = texto.replace("_", " ")
        texto = re.sub(r"[^a-z0-9 ]+", " ", texto)
        texto = re.sub(r"\s+", " ", texto).strip()
        return texto

    @staticmethod
    def _quitar_duplicados(valores: list[str]) -> list[str]:
        salida = []

        for valor in valores:
            if valor not in salida:
                salida.append(valor)

        return salida

    def _cargar_parquet(self) -> pd.DataFrame:
        if not self.ruta_parquet.exists():
            raise FileNotFoundError(
                f"No se encontró el parquet en la ruta: {self.ruta_parquet}"
            )

        return pd.read_parquet(self.ruta_parquet).copy()

    def _validar_dataframe(self) -> None:
        if self.df.empty:
            raise ValueError("El parquet está vacío.")

        if self.columna_id not in self.df.columns:
            raise ValueError(f"No existe la columna llave '{self.columna_id}'.")

    def _crear_indice_columnas(self) -> dict[str, str]:
        return {
            self._normalizar_texto(columna): columna
            for columna in self.df.columns
        }

    # ========================================================
    # Detección de columnas
    # ========================================================

    def _resolver_alias(self, texto: str) -> list[str]:
        texto_norm = self._normalizar_texto(texto)
        columnas = []

        alias_ordenados = sorted(
            self.alias_columnas.keys(),
            key=lambda x: len(self._normalizar_texto(x)),
            reverse=True,
        )

        for alias in alias_ordenados:
            alias_norm = self._normalizar_texto(alias)

            if not alias_norm:
                continue

            if alias_norm in texto_norm:
                for candidata in self.alias_columnas[alias]:
                    candidata_norm = self._normalizar_texto(candidata)

                    for columna_norm, columna_original in self.columnas_normalizadas.items():
                        if candidata_norm == columna_norm:
                            columnas.append(columna_original)

        return self._quitar_duplicados(columnas)

    def _detectar_columnas_exactas(self, texto: str) -> list[str]:
        texto_norm = self._normalizar_texto(texto)
        columnas = []

        columnas_ordenadas = sorted(
            self.columnas_normalizadas.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )

        for columna_norm, columna_original in columnas_ordenadas:
            if columna_norm and columna_norm in texto_norm:
                columnas.append(columna_original)

        return self._quitar_duplicados(columnas)

    def _detectar_columnas(self, texto: str) -> list[str]:
        columnas_exactas = self._detectar_columnas_exactas(texto)

        if columnas_exactas:
            return columnas_exactas

        columnas_alias = self._resolver_alias(texto)

        if columnas_alias:
            return columnas_alias

        texto_norm = self._normalizar_texto(texto)
        tokens = set(texto_norm.split())
        columnas = []

        palabras_no_informativas = {
            "de", "por", "para", "cada", "con", "sin",
            "los", "las", "el", "la", "un", "una",
            "que", "me", "dame", "saca", "muestra",
            "tabla", "valores", "valor",
        }

        for columna_norm, columna_original in self.columnas_normalizadas.items():
            partes = [
                parte
                for parte in columna_norm.split()
                if len(parte) >= 5 and parte not in palabras_no_informativas
            ]

            if any(parte in tokens for parte in partes):
                columnas.append(columna_original)

        return self._quitar_duplicados(columnas)

    def _separar_partes_pregunta(
        self,
        pregunta: str,
    ) -> tuple[list[str], list[str], list[str]]:
        """
        Separa la pregunta en:
        - campos_valor: campo principal consultado.
        - campos_grupo: campo después de "por", "de cada", "según".
        - campos_extra: campo después de "con".

        Ejemplos:
            "importe sin impuestos por titulo con cpv"
                valor = importe_sin_impuestos
                grupo = titulo
                extra = cpv_codes, cpv_descripcion

            "mayor importe sin impuesto con cpv"
                valor = importe_sin_impuestos
                grupo = []
                extra = cpv_codes, cpv_descripcion
        """
        pregunta_norm = self._normalizar_texto(pregunta)

        # 1. Separar extras después de "con".
        texto_principal = pregunta_norm
        texto_extra = ""

        if " con " in f" {pregunta_norm} ":
            partes_con = pregunta_norm.split(" con ", maxsplit=1)
            texto_principal = partes_con[0].strip()
            texto_extra = partes_con[1].strip()

        campos_extra = self._detectar_columnas(texto_extra) if texto_extra else []

        # 2. Separar grupo después de conectores tipo "por".
        conectores_grupo = [
            " por cada ",
            " de cada ",
            " para cada ",
            " segun ",
            " por ",
        ]

        for conector in conectores_grupo:
            conector_norm = self._normalizar_texto(conector)

            if f" {conector_norm} " in f" {texto_principal} ":
                partes = texto_principal.split(conector_norm, maxsplit=1)
                izquierda = partes[0].strip()
                derecha = partes[1].strip()

                campos_valor = self._detectar_columnas(izquierda)
                campos_grupo = self._detectar_columnas(derecha)

                return campos_valor, campos_grupo, campos_extra

        campos_valor = self._detectar_columnas(texto_principal)

        return campos_valor, [], campos_extra

    # ========================================================
    # Intenciones
    # ========================================================

    def _contiene_alguno(self, pregunta: str, patrones: list[str]) -> bool:
        pregunta_norm = self._normalizar_texto(pregunta)
        return any(patron in pregunta_norm for patron in patrones)

    def _es_pregunta_resumen(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            [
                "que informacion",
                "que tiene",
                "resumen",
                "estructura",
                "describe el parquet",
                "informacion disponible",
            ],
        )

    def _es_pregunta_cantidad(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            [
                "cuantas licitaciones",
                "cantidad de licitaciones",
                "numero de licitaciones",
                "total de licitaciones",
                "cuantos registros",
                "cuantas filas",
            ],
        )

    def _es_pregunta_columnas(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            ["columnas", "campos", "variables", "diccionario"],
        )

    def _es_pregunta_nulos(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            ["nulos", "faltantes", "missing", "vacios", "vacias"],
        )

    def _es_pregunta_frecuencia(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            [
                "se repite mas",
                "mas se repite",
                "mas frecuente",
                "frecuencia",
                "moda",
                "repetidos",
            ],
        )

    def _es_pregunta_mayores(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            [
                "mayor",
                "mas alto",
                "maximo",
                "maxima",
                "top",
                "alto",
                "superior",
                "descendente",
            ],
        )

    def _es_pregunta_menores(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            [
                "menor",
                "mas bajo",
                "minimo",
                "minima",
                "bajo",
                "inferior",
                "ascendente",
            ],
        )

    def _es_pregunta_tabla(self, pregunta: str) -> bool:
        return self._contiene_alguno(
            pregunta,
            [
                "por",
                "por cada",
                "de cada",
                "para cada",
                "segun",
                "tabla",
                "saca",
                "dame",
                "muestra",
                "listar",
                "lista",
                "con",
            ],
        )

    # ========================================================
    # Respuestas
    # ========================================================

    def resumen_general(self) -> None:
        filas = len(self.df)
        columnas = len(self.df.columns)
        licitaciones_unicas = self.df[self.columna_id].nunique(dropna=True)
        duplicados = int(self.df[self.columna_id].duplicated().sum())

        print("\nResumen del parquet")
        print("=" * 100)
        print(f"Archivo: {self.ruta_parquet}")
        print(f"Filas: {filas}")
        print(f"Columnas: {columnas}")
        print(f"Licitaciones únicas: {licitaciones_unicas}")
        print(f"Duplicados por {self.columna_id}: {duplicados}")

    def cantidad_licitaciones(self) -> None:
        print("\nCantidad de licitaciones")
        print("=" * 100)
        print(f"Filas: {len(self.df)}")
        print(f"Licitaciones únicas: {self.df[self.columna_id].nunique(dropna=True)}")
        print(
            f"Duplicados por {self.columna_id}: "
            f"{int(self.df[self.columna_id].duplicated().sum())}"
        )

    def listar_columnas(self) -> None:
        print("\nColumnas disponibles")
        print("=" * 100)

        for i, columna in enumerate(self.df.columns, start=1):
            tipo = self.df[columna].dtype
            nulos = int(self.df[columna].isna().sum())
            unicos = int(self.df[columna].nunique(dropna=True))
            print(f"{i}. {columna} | tipo={tipo} | nulos={nulos} | únicos={unicos}")

    def nulos(self, top: int = 20) -> None:
        tabla = (
            self.df.isna()
            .sum()
            .reset_index()
            .rename(columns={"index": "columna", 0: "nulos"})
        )
        tabla["porcentaje_nulos"] = (tabla["nulos"] / len(self.df) * 100).round(2)
        tabla = tabla.sort_values(
            by=["nulos", "porcentaje_nulos"],
            ascending=False,
        ).head(top)

        print(f"\nTop {top} columnas con más nulos")
        print("=" * 100)
        print(tabla.to_string(index=False))

    def describir_columna(self, columna: str) -> None:
        if columna not in self.df.columns:
            print(f"No existe la columna: {columna}")
            return

        serie = self.df[columna]
        total = len(serie)
        nulos = int(serie.isna().sum())
        unicos = int(serie.nunique(dropna=True))

        print(f"\nColumna: {columna}")
        print("=" * 100)
        print(f"Tipo: {serie.dtype}")
        print(f"Registros: {total}")
        print(f"Nulos: {nulos}")
        print(f"Valores únicos: {unicos}")

        if pd.api.types.is_numeric_dtype(serie):
            print("\nResumen numérico:")
            print(serie.describe().to_string())
        else:
            tabla = serie.value_counts(dropna=False).head(10).reset_index()
            tabla.columns = [columna, "frecuencia"]
            tabla["porcentaje"] = (tabla["frecuencia"] / total * 100).round(2)

            print("\nValores más frecuentes:")
            print(tabla.to_string(index=False))

    def tabla_campos(
        self,
        campos_valor: list[str],
        campos_grupo: Optional[list[str]] = None,
        campos_extra: Optional[list[str]] = None,
        ordenar: bool = False,
        ascendente: bool = False,
        max_filas: int = 50,
    ) -> None:
        if campos_grupo is None:
            campos_grupo = []

        if campos_extra is None:
            campos_extra = []

        campos_valor = [col for col in campos_valor if col in self.df.columns]
        campos_grupo = [col for col in campos_grupo if col in self.df.columns]
        campos_extra = [col for col in campos_extra if col in self.df.columns]

        if not campos_valor and not campos_grupo and not campos_extra:
            print("No detecté columnas válidas para construir la tabla.")
            return

        if not campos_grupo:
            campos_grupo = [self.columna_id]

            if "titulo" in self.df.columns:
                campos_grupo.append("titulo")

        columnas_tabla = self._quitar_duplicados(
            campos_grupo + campos_extra + campos_valor
        )

        # Si la tabla se muestra por título, se agrega ID para trazabilidad.
        if (
            "titulo" in columnas_tabla
            and self.columna_id in self.df.columns
            and self.columna_id not in columnas_tabla
        ):
            columnas_tabla = [self.columna_id] + columnas_tabla

        tabla = self.df[columnas_tabla].copy()

        columna_orden = None

        if campos_valor:
            for columna in campos_valor:
                if pd.api.types.is_numeric_dtype(tabla[columna]):
                    columna_orden = columna
                    break

        if ordenar and columna_orden is not None:
            tabla = tabla.sort_values(
                by=columna_orden,
                ascending=ascendente,
                na_position="last",
            )

        print("\nTabla solicitada")
        print("=" * 100)
        print(f"Columnas: {columnas_tabla}")
        print(f"Filas mostradas: {min(max_filas, len(tabla))} de {len(tabla)}")
        print(tabla.head(max_filas).to_string(index=False))

    def frecuencia_columna(self, columna: str, top: int = 10) -> None:
        if columna not in self.df.columns:
            print(f"No existe la columna: {columna}")
            return

        tabla = self.df[columna].value_counts(dropna=False).head(top).reset_index()
        tabla.columns = [columna, "frecuencia"]
        tabla["porcentaje"] = (tabla["frecuencia"] / len(self.df) * 100).round(2)

        print(f"\nFrecuencias de {columna}")
        print("=" * 100)
        print(tabla.to_string(index=False))

    def ranking_repetidos(self, top: int = 15) -> None:
        resultados = []

        for columna in self.df.columns:
            serie = self.df[columna].dropna()

            if serie.empty:
                continue

            unicos = int(serie.nunique(dropna=True))

            if unicos > max(50, len(self.df) * 0.8):
                continue

            conteo = serie.value_counts(dropna=True)

            if conteo.empty:
                continue

            resultados.append(
                {
                    "columna": columna,
                    "valor_mas_frecuente": conteo.index[0],
                    "frecuencia": int(conteo.iloc[0]),
                    "porcentaje_total": round(conteo.iloc[0] / len(self.df) * 100, 2),
                    "valores_unicos": unicos,
                }
            )

        resultado = pd.DataFrame(resultados)

        print("\nCampos donde más se repiten valores")
        print("=" * 100)

        if resultado.empty:
            print("No hay campos adecuados para este análisis.")
            return

        resultado = resultado.sort_values(
            by=["porcentaje_total", "frecuencia"],
            ascending=False,
        ).head(top)

        print(resultado.to_string(index=False))

    def buscar_texto_global(self, texto: str, max_filas: int = 10) -> None:
        texto_norm = self._normalizar_texto(texto)
        mascara = pd.Series(False, index=self.df.index)

        for columna in self.df.columns:
            serie = self.df[columna].astype(str).map(self._normalizar_texto)
            mascara = mascara | serie.str.contains(texto_norm, na=False)

        resultado = self.df.loc[mascara].copy()

        print(f"\nBúsqueda global: {texto}")
        print("=" * 100)
        print(f"Coincidencias encontradas: {len(resultado)}")

        if resultado.empty:
            print("No se encontraron coincidencias.")
            return

        columnas_preferidas = [
            self.columna_id,
            "titulo",
            "objeto_contrato",
            "organo_contratacion",
            "estado",
            "tipo_contrato",
            "cpv_codes",
            "cpv_descripcion",
            "nombre_region",
            "importe_sin_impuestos",
            "url",
            "detail_url",
        ]

        columnas_mostrar = [
            col for col in columnas_preferidas if col in resultado.columns
        ]

        if not columnas_mostrar:
            columnas_mostrar = list(resultado.columns[:8])

        print(resultado[columnas_mostrar].head(max_filas).to_string(index=False))

    def _extraer_texto_busqueda(self, pregunta: str) -> str:
        pregunta_norm = self._normalizar_texto(pregunta)

        patrones = [
            r"buscar texto[: ]+(.*)",
            r"busca[: ]+(.*)",
            r"buscar[: ]+(.*)",
            r"encuentra[: ]+(.*)",
            r"filtra[: ]+(.*)",
        ]

        for patron in patrones:
            match = re.search(patron, pregunta_norm)
            if match:
                return match.group(1).strip()

        return pregunta.strip()

    # ========================================================
    # Método principal
    # ========================================================

    def preguntar(self, pregunta: str) -> None:
        pregunta_norm = self._normalizar_texto(pregunta)

        if not pregunta_norm:
            print("La pregunta está vacía.")
            return

        if self._es_pregunta_resumen(pregunta):
            self.resumen_general()
            return

        if self._es_pregunta_cantidad(pregunta):
            self.cantidad_licitaciones()
            return

        if self._es_pregunta_columnas(pregunta):
            self.listar_columnas()
            return

        if self._es_pregunta_nulos(pregunta):
            self.nulos()
            return

        campos_valor, campos_grupo, campos_extra = self._separar_partes_pregunta(
            pregunta
        )

        if self._es_pregunta_frecuencia(pregunta):
            columnas = self._quitar_duplicados(
                campos_valor + campos_grupo + campos_extra
            )

            if columnas:
                for columna in columnas:
                    self.frecuencia_columna(columna)
            else:
                self.ranking_repetidos()

            return

        if self._es_pregunta_tabla(pregunta) and (
            campos_valor or campos_grupo or campos_extra
        ):
            ordenar = (
                self._es_pregunta_mayores(pregunta)
                or self._es_pregunta_menores(pregunta)
            )
            ascendente = self._es_pregunta_menores(pregunta)

            self.tabla_campos(
                campos_valor=campos_valor,
                campos_grupo=campos_grupo,
                campos_extra=campos_extra,
                ordenar=ordenar,
                ascendente=ascendente,
            )
            return

        if (
            campos_valor
            and (
                self._es_pregunta_mayores(pregunta)
                or self._es_pregunta_menores(pregunta)
            )
        ):
            grupo_default = [self.columna_id]

            if "titulo" in self.df.columns:
                grupo_default.append("titulo")

            self.tabla_campos(
                campos_valor=campos_valor,
                campos_grupo=grupo_default,
                campos_extra=campos_extra,
                ordenar=True,
                ascendente=self._es_pregunta_menores(pregunta),
            )
            return

        if len(campos_valor) == 1 and not campos_grupo and not campos_extra:
            self.describir_columna(campos_valor[0])
            return

        if campos_valor or campos_grupo or campos_extra:
            self.tabla_campos(
                campos_valor=campos_valor,
                campos_grupo=campos_grupo,
                campos_extra=campos_extra,
            )
            return

        if any(
            termino in pregunta_norm
            for termino in ["busca", "buscar", "encuentra", "filtra", "contiene"]
        ):
            texto = self._extraer_texto_busqueda(pregunta)
            self.buscar_texto_global(texto)
            return

        self.buscar_texto_global(pregunta)


def ejecutar_consola() -> None:
    agente = AgenteLicitacionesTablas()

    print("\nAGENTE DE LICITACIONES - TABLAS V5")
    print("=" * 100)
    print("Ejemplos:")
    print("- mayor importe sin impuesto con cpv")
    print("- mayor importe sin impuestos con cvp")
    print("- importe sin impuestos por titulo con cpv")
    print("- importe sin impuestos por id de licitacion")
    print("- cpv por titulo")
    print("- que columnas tiene")
    print("- salir")

    while True:
        pregunta = input("\nPregunta: ").strip()

        if pregunta.lower() in {"salir", "exit", "quit"}:
            print("Fin de la sesión.")
            break

        agente.preguntar(pregunta)


if __name__ == "__main__":
    ejecutar_consola()
