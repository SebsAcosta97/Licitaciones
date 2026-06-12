# ============================================================
# PIPELINE UNIFICADO - FICHAS DE LICITACIONES
# Portales:
#   1. Galicia
#   2. Comunidad de Madrid
#   3. Plataforma de Contratación del Sector Público
#
# Salida:
#   - Un único Parquet en capa gold, con una fila por licitación.
#   - No genera Excel ni múltiples Parquet técnicos.
# ============================================================

from __future__ import annotations

import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from urllib.parse import urljoin
try:
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
except ImportError:
    ILLEGAL_CHARACTERS_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")



# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path.cwd()

RUTA_INPUT = (
    PROJECT_ROOT
    / "data"
    / "silver"
    / "df_tilos_limpio.parquet"
)

RUTA_MUESTRA_VALIDADA = (
    PROJECT_ROOT
    / "data"
    / "silver"
    / "muestra_10_estratificada_3_portales.parquet"
)

RUTA_SILVER = PROJECT_ROOT / "data" / "silver"
RUTA_GOLD = PROJECT_ROOT / "data" / "gold"
RUTA_BRONZE_DOCUMENTOS = PROJECT_ROOT / "data" / "bronze" / "documentos"

RUTA_SILVER.mkdir(parents=True, exist_ok=True)
RUTA_GOLD.mkdir(parents=True, exist_ok=True)
RUTA_BRONZE_DOCUMENTOS.mkdir(parents=True, exist_ok=True)

# Salida única final.
RUTA_PARQUET_FINAL = RUTA_GOLD / "fichas_licitaciones_muestra_10_unificado_v10.parquet"

DISTRIBUCION_MUESTRA = {
    "galicia": 3,
    "madrid": 3,
    "contratacion_estado": 4,
}

RANDOM_STATE = 42
TIMEOUT = 30
PAUSA_SEGUNDOS = 1
MAX_CARACTERES_EXCEL = 32000

DESCARGAR_DOCUMENTOS_MADRID = False
DESCARGAR_DOCUMENTOS_GALICIA = False
DESCARGAR_DOCUMENTOS_CONTRATACION_ESTADO = False

# Nota: estas banderas desactivan documentos adjuntos.
# Si la URL principal de una licitación ya es PDF, se lee en memoria
# porque es la propia ficha fuente, no un adjunto adicional.

TERMINOS_RELEVANTES = [
    "objeto",
    "solvencia",
    "criterios de adjudicación",
    "prescripciones técnicas",
    "personal",
    "medios personales",
    "medios materiales",
    "experiencia",
    "certificación",
    "obligaciones",
    "plazo",
    "duración",
    "lugar de prestación",
    "presupuesto",
    "medicina",
    "reconocimiento médico",
    "otorrinolaringología",
    "ginecología",
    "urología",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


# ============================================================
# 2. FUNCIONES AUXILIARES GENERALES
# ============================================================

def limpiar_texto(valor: Any) -> str | None:
    """Limpia espacios, saltos de línea y valores vacíos."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except ValueError:
        pass

    texto = str(valor)
    texto = re.sub(r"\s+", " ", texto).strip()

    if texto == "":
        return None

    return texto


def limitar_excel(valor: Any, max_caracteres: int = MAX_CARACTERES_EXCEL) -> Any:
    """Limita textos largos para evitar superar el límite por celda de Excel."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except ValueError:
        pass

    texto = str(valor)

    if len(texto) > max_caracteres:
        return texto[:max_caracteres]

    return texto


def normalizar_lista_para_excel(valor: Any) -> Any:
    """Convierte listas, tuplas o sets a texto plano para Excel."""
    if isinstance(valor, (list, tuple, set)):
        return "; ".join([str(x) for x in valor])

    return valor


def buscar_regex(
    texto: str | None,
    patron: str,
    flags: int = re.IGNORECASE,
) -> str | None:
    """Devuelve el primer grupo capturado por una expresión regular."""
    if texto is None:
        return None

    match = re.search(patron, texto, flags)

    if match:
        return limpiar_texto(match.group(1))

    return None


def buscar_todos_regex(
    texto: str | None,
    patron: str,
    flags: int = re.IGNORECASE,
) -> list[Any]:
    """Devuelve todas las coincidencias de una expresión regular."""
    if texto is None:
        return []

    return re.findall(patron, texto, flags)


def convertir_importe_eur(valor: Any) -> float | None:
    """
    Convierte importes europeos a float.
    Ejemplos:
        15.975 -> 15975.0
        1.449 -> 1449.0
        15.975,35 -> 15975.35
    """
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except ValueError:
        pass

    texto = str(valor).strip()
    texto = texto.replace("EUR.", "")
    texto = texto.replace("EUR", "")
    texto = texto.replace("€", "")
    texto = texto.replace(".", "")
    texto = texto.replace(",", ".")
    texto = texto.strip()

    try:
        return float(texto)
    except ValueError:
        return None


def valor_siguiente(df_lineas: pd.DataFrame, etiqueta: str) -> str | None:
    """Busca una etiqueta y devuelve la línea inmediatamente posterior."""
    coincidencias = df_lineas[
        df_lineas["linea"].str.contains(
            etiqueta,
            case=False,
            na=False,
            regex=False,
        )
    ]

    if coincidencias.empty:
        return None

    orden = coincidencias.iloc[0]["orden"] + 1
    valor = df_lineas.loc[df_lineas["orden"] == orden, "linea"]

    if valor.empty:
        return None

    return limpiar_texto(valor.iloc[0])


def valor_con_salto(
    df_lineas: pd.DataFrame,
    etiqueta: str,
    salto: int,
) -> str | None:
    """Busca una etiqueta y devuelve la línea ubicada n posiciones después."""
    coincidencias = df_lineas[
        df_lineas["linea"].str.contains(
            etiqueta,
            case=False,
            na=False,
            regex=False,
        )
    ]

    if coincidencias.empty:
        return None

    orden = coincidencias.iloc[0]["orden"] + salto
    valor = df_lineas.loc[df_lineas["orden"] == orden, "linea"]

    if valor.empty:
        return None

    return limpiar_texto(valor.iloc[0])


def detectar_portal(detail_url: Any) -> str:
    """Identifica el portal de contratación a partir de la URL."""
    if detail_url is None or pd.isna(detail_url):
        return "sin_url"

    url = str(detail_url).lower()

    if "contratosdegalicia.gal" in url:
        return "galicia"

    if "contratos-publicos.comunidad.madrid" in url:
        return "madrid"

    if "contrataciondelestado.es" in url:
        return "contratacion_estado"

    return "otro"


def descargar_url(url: str, timeout: int = TIMEOUT) -> requests.Response:
    """Descarga una URL usando headers de navegador."""
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
    )
    return response


def detectar_tipo_contenido(response: requests.Response) -> str:
    """Clasifica la respuesta como pdf, html u otro."""
    content_type = response.headers.get("Content-Type", "").lower()
    contenido = response.content

    if contenido.startswith(b"%PDF") or "application/pdf" in content_type:
        return "pdf"

    if "html" in content_type or "<html" in response.text.lower():
        return "html"

    if contenido.startswith(b"<?xml") or "xml" in content_type:
        return "xml"

    return "otro"


def limpiar_nombre_archivo(texto: Any) -> str:
    """Limpia un nombre de archivo para guardarlo en disco."""
    texto = str(texto)
    texto = re.sub(r"[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ_ -]", "", texto)
    texto = texto.strip().replace(" ", "_")

    if texto == "":
        texto = "documento"

    return texto[:80]


def detectar_extension(
    contenido: bytes,
    content_type: str | None,
    url: str,
) -> tuple[str, str]:
    """Detecta extensión real del archivo descargado."""
    content_type = str(content_type).lower()
    url = str(url).lower()

    if contenido.startswith(b"%PDF") or "application/pdf" in content_type:
        return ".pdf", "PDF"

    if contenido.startswith(b"PK"):
        return ".zip", "ZIP_O_OFFICE"

    if contenido.startswith(b"<?xml") or "xml" in content_type:
        return ".xml", "XML"

    if contenido.startswith(b"<!DOC") or "text/html" in content_type:
        return ".html", "HTML"

    if ".pdf" in url:
        return ".pdf", "PDF_POSIBLE"

    return ".bin", "DESCONOCIDO"


def extraer_lineas_html(response: requests.Response) -> tuple[BeautifulSoup, str, pd.DataFrame]:
    """Extrae texto visible y líneas desde HTML."""
    soup = BeautifulSoup(response.text, "html.parser")
    texto = soup.get_text("\n", strip=True)
    lineas = [linea.strip() for linea in texto.split("\n") if linea.strip()]

    df_lineas = pd.DataFrame(
        {
            "orden": range(len(lineas)),
            "linea": lineas,
        }
    )

    return soup, texto, df_lineas


def extraer_enlaces(soup: BeautifulSoup, url_base: str) -> pd.DataFrame:
    """Extrae enlaces de una página HTML."""
    enlaces = []

    for i, enlace in enumerate(soup.find_all("a", href=True)):
        texto = enlace.get_text(" ", strip=True)
        href = enlace.get("href")
        url_completa = urljoin(url_base, href)

        enlaces.append(
            {
                "orden": i,
                "texto_enlace": texto,
                "href": href,
                "url_completa": url_completa,
            }
        )

    return pd.DataFrame(enlaces)


def extraer_texto_pdf_desde_bytes(contenido: bytes) -> tuple[str, list[str], int]:
    """Extrae texto de un PDF leído en memoria."""
    pdf_memoria = BytesIO(contenido)
    reader = PdfReader(pdf_memoria)

    textos = []

    for pagina in reader.pages:
        texto_pagina = pagina.extract_text() or ""
        textos.append(texto_pagina)

    texto_completo = "\n".join(textos)
    lineas = [
        linea.strip()
        for linea in texto_completo.splitlines()
        if linea.strip()
    ]

    return texto_completo, lineas, len(reader.pages)


def extraer_texto_pdf_desde_ruta(ruta_pdf: str | Path) -> dict[str, Any]:
    """Extrae texto de un PDF guardado en disco."""
    try:
        reader = PdfReader(str(ruta_pdf))
        textos = []

        for pagina in reader.pages:
            texto_pagina = pagina.extract_text()
            if texto_pagina:
                textos.append(texto_pagina)

        return {
            "texto_extraido": "\n".join(textos),
            "numero_paginas": len(reader.pages),
            "estado_lectura": "OK",
            "error_lectura": None,
        }

    except Exception as error:
        return {
            "texto_extraido": None,
            "numero_paginas": None,
            "estado_lectura": "ERROR",
            "error_lectura": str(error),
        }


def extraer_fragmentos(
    texto: Any,
    termino: str,
    ventana: int = 600,
) -> list[str]:
    """Extrae fragmentos alrededor de todas las apariciones de un término."""
    texto = str(texto)
    texto_lower = texto.lower()
    termino_lower = termino.lower()

    fragmentos = []
    inicio_busqueda = 0

    while True:
        posicion = texto_lower.find(termino_lower, inicio_busqueda)

        if posicion == -1:
            break

        inicio = max(0, posicion - ventana)
        fin = min(len(texto), posicion + len(termino) + ventana)

        fragmento = texto[inicio:fin].replace("\n", " ").strip()
        fragmentos.append(limitar_excel(fragmento))

        inicio_busqueda = posicion + len(termino)

    return fragmentos


def limpiar_para_excel(valor: Any) -> Any:
    """Limpia valores antes de exportarlos a Excel.

    openpyxl falla cuando el texto extraído de PDFs contiene caracteres
    de control invisibles. Esta función elimina esos caracteres, convierte
    estructuras no escalares a texto y limita la longitud máxima de celda.
    """
    if valor is None:
        return None

    if isinstance(valor, float) and pd.isna(valor):
        return None

    # Mantener numéricos y fechas cuando sea posible.
    if isinstance(valor, (int, float, bool, pd.Timestamp)):
        return valor

    if isinstance(valor, (list, tuple, set)):
        valor = "; ".join([str(x) for x in valor])
    elif isinstance(valor, dict):
        valor = "; ".join([f"{k}: {v}" for k, v in valor.items()])

    valor = str(valor)

    # Excel no admite varios caracteres de control ASCII.
    valor = ILLEGAL_CHARACTERS_RE.sub("", valor)
    valor = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]", "", valor)

    # Normalizar espacios horizontales, pero conservar saltos de línea como espacios
    # para evitar celdas enormes visualmente.
    valor = re.sub(r"\s+", " ", valor).strip()

    # Límite real de Excel: 32.767 caracteres. Dejamos margen.
    if len(valor) > MAX_CARACTERES_EXCEL:
        valor = valor[:MAX_CARACTERES_EXCEL]

    return valor


def preparar_para_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Prepara cualquier DataFrame para exportarlo a Excel sin errores."""
    if df is None or df.empty:
        return pd.DataFrame()

    df_excel = df.copy()

    for columna in df_excel.columns:
        df_excel[columna] = df_excel[columna].apply(limpiar_para_excel)

    return df_excel


# ============================================================
# 3. MUESTRA ESTRATIFICADA
# ============================================================

def cargar_datos_base(ruta_input: Path = RUTA_INPUT) -> pd.DataFrame:
    """Carga la base depurada de licitaciones."""
    if not ruta_input.exists():
        raise FileNotFoundError(
            f"No existe el archivo de entrada: {ruta_input}"
        )

    df = pd.read_parquet(ruta_input)
    df["portal"] = df["detail_url"].apply(detectar_portal)

    return df


def generar_muestra_estratificada(
    df: pd.DataFrame,
    distribucion: dict[str, int] = DISTRIBUCION_MUESTRA,
    random_state: int = RANDOM_STATE,
) -> pd.DataFrame:
    """Genera muestra estratificada por portal."""
    muestras = []

    for portal, n in distribucion.items():
        df_portal = df[df["portal"] == portal].copy()

        if df_portal.empty:
            print(f"No hay registros para el portal: {portal}")
            continue

        n_disponible = min(n, len(df_portal))

        muestra_portal = df_portal.sample(
            n=n_disponible,
            random_state=random_state,
        )

        muestras.append(muestra_portal)

    if not muestras:
        raise ValueError("No se pudo generar la muestra. No hay portales válidos.")

    muestra = pd.concat(muestras, ignore_index=True)

    return muestra


# ============================================================
# 4. PARSER GALICIA
# ============================================================

def extraer_resolucion_galicia(df_lineas: pd.DataFrame) -> dict[str, Any]:
    """Extrae la primera fila de la tabla de resolución en Galicia."""
    datos = {
        "lote_resolucion_web": None,
        "participacion_resolucion_web": None,
        "resolucion_web": None,
        "adjudicatario_web": None,
        "importe_adjudicado_web": None,
        "fecha_difusion_resolucion_web": None,
        "plazo_ejecucion_resolucion_web": None,
    }

    coincidencias = df_lineas[
        df_lineas["linea"].str.contains(
            "Datos da resolución do procedemento",
            case=False,
            na=False,
            regex=False,
        )
    ]

    if coincidencias.empty:
        return datos

    inicio = coincidencias.iloc[0]["orden"]
    offsets = {
        "lote_resolucion_web": 9,
        "participacion_resolucion_web": 10,
        "resolucion_web": 11,
        "adjudicatario_web": 12,
        "importe_adjudicado_web": 13,
        "fecha_difusion_resolucion_web": 14,
        "plazo_ejecucion_resolucion_web": 15,
    }

    for campo, offset in offsets.items():
        valor = df_lineas.loc[
            df_lineas["orden"] == inicio + offset,
            "linea",
        ]
        if not valor.empty:
            datos[campo] = limpiar_texto(valor.iloc[0])

    return datos


def extraer_organo_recurso_galicia(df_lineas: pd.DataFrame) -> dict[str, Any]:
    """Extrae datos del bloque Órgano competente para resolver o recurso."""
    datos = {
        "organo_recurso_web": None,
        "direccion_recurso_web": None,
        "localidad_recurso_web": None,
        "cp_recurso_web": None,
        "telefono_recurso_web": None,
        "fax_recurso_web": None,
        "correo_recurso_web": None,
    }

    coincidencias = df_lineas[
        df_lineas["linea"].str.contains(
            "Órgano competente para resolver o recurso",
            case=False,
            na=False,
            regex=False,
        )
    ]

    if coincidencias.empty:
        return datos

    inicio = coincidencias.iloc[0]["orden"]
    offsets = {
        "organo_recurso_web": 2,
        "direccion_recurso_web": 4,
        "localidad_recurso_web": 6,
        "cp_recurso_web": 8,
        "telefono_recurso_web": 10,
        "fax_recurso_web": 12,
        "correo_recurso_web": 14,
    }

    for campo, offset in offsets.items():
        valor = df_lineas.loc[
            df_lineas["orden"] == inicio + offset,
            "linea",
        ]
        if not valor.empty:
            datos[campo] = limpiar_texto(valor.iloc[0])

    return datos


def extraer_publicacion_cpv_nut_galicia(
    df_lineas: pd.DataFrame,
) -> dict[str, Any]:
    """Extrae publicación, CPV y NUT desde Galicia."""
    datos = {
        "fecha_difusion_plataforma_web": None,
        "sello_web": None,
        "cpv_web": None,
        "cpv_lote_web": None,
        "cpv_fecha_difusion_web": None,
        "nut_web": None,
        "nut_lote_web": None,
        "nut_fecha_difusion_web": None,
    }

    coincidencias_sello = df_lineas[
        df_lineas["linea"].str.contains(
            "Selo:",
            case=False,
            na=False,
            regex=False,
        )
    ]

    if not coincidencias_sello.empty:
        orden_sello = coincidencias_sello.iloc[0]["orden"]

        for campo, orden in {
            "fecha_difusion_plataforma_web": orden_sello - 1,
            "sello_web": orden_sello + 1,
        }.items():
            valor = df_lineas.loc[df_lineas["orden"] == orden, "linea"]
            if not valor.empty:
                datos[campo] = limpiar_texto(valor.iloc[0])

    coincidencias_cpv = df_lineas[
        df_lineas["linea"].str.contains(
            "CPV - Vocabulario común de contratos públicos",
            case=False,
            na=False,
            regex=False,
        )
    ]

    if not coincidencias_cpv.empty:
        inicio_cpv = coincidencias_cpv.iloc[0]["orden"]

        for campo, offset in {
            "cpv_web": 4,
            "cpv_lote_web": 5,
            "cpv_fecha_difusion_web": 6,
        }.items():
            valor = df_lineas.loc[
                df_lineas["orden"] == inicio_cpv + offset,
                "linea",
            ]
            if not valor.empty:
                datos[campo] = limpiar_texto(valor.iloc[0])

    coincidencias_nut = df_lineas[
        df_lineas["linea"].str.contains(
            "NUT - Nomenclatura",
            case=False,
            na=False,
            regex=False,
        )
    ]

    if not coincidencias_nut.empty:
        inicio_nut = coincidencias_nut.iloc[0]["orden"]

        for campo, offset in {
            "nut_web": 4,
            "nut_lote_web": 5,
            "nut_fecha_difusion_web": 6,
        }.items():
            valor = df_lineas.loc[
                df_lineas["orden"] == inicio_nut + offset,
                "linea",
            ]
            if not valor.empty:
                datos[campo] = limpiar_texto(valor.iloc[0])

    return datos


def parsear_galicia(
    fila: pd.Series,
    response: requests.Response,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Extrae ficha de una licitación del portal de Galicia."""
    licitacion_id = fila["licitacion_id"]
    url = fila["detail_url"]
    portal = fila["portal"]

    soup, texto, df_lineas = extraer_lineas_html(response)
    enlaces = extraer_enlaces(soup, response.url)

    ficha = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "tipo_fuente": "html_galicia",
        "detail_url": url,
        "url_final": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "titulo_original": fila.get("titulo", None),
        "num_lineas_extraidas": df_lineas.shape[0],

        "estado_procedimiento_web": valor_siguiente(
            df_lineas,
            "Estado do procedemento",
        ),
        "organo_contratacion_web": valor_con_salto(
            df_lineas,
            "Estado do procedemento",
            salto=2,
        ),
        "objeto_web": valor_siguiente(df_lineas, "Obxecto"),
        "tipo_tramitacion_web": valor_siguiente(
            df_lineas,
            "Tipo de tramitación",
        ),
        "tipo_procedimiento_web": valor_siguiente(
            df_lineas,
            "Tipo de procedemento",
        ),
        "tipo_contrato_web": valor_siguiente(df_lineas, "Tipo de contrato"),
        "presupuesto_base_web": valor_siguiente(
            df_lineas,
            "Orzamento base de licitación",
        ),
        "num_lotes_web": valor_siguiente(df_lineas, "Nº lotes"),
        "valor_estimado_web": valor_siguiente(df_lineas, "Valor estimado"),
        "tipo_financiamiento_web": valor_siguiente(
            df_lineas,
            "Tipo de financiamento",
        ),
        "contrato_mixto_web": None,
        "subasta_electronica_web": None,
        "error": None,
    }

    ficha.update(extraer_resolucion_galicia(df_lineas))
    ficha.update(extraer_organo_recurso_galicia(df_lineas))
    ficha.update(extraer_publicacion_cpv_nut_galicia(df_lineas))

    ficha["presupuesto_base_num"] = convertir_importe_eur(
        ficha.get("presupuesto_base_web")
    )
    ficha["valor_estimado_num"] = convertir_importe_eur(
        ficha.get("valor_estimado_web")
    )
    ficha["importe_adjudicado_num"] = convertir_importe_eur(
        ficha.get("importe_adjudicado_web")
    )

    ficha["num_enlaces_detectados"] = enlaces.shape[0]

    texto_matching = " ".join(
        [
            str(ficha.get("titulo_original") or ""),
            str(ficha.get("objeto_web") or ""),
            str(ficha.get("organo_contratacion_web") or ""),
            str(ficha.get("tipo_contrato_web") or ""),
            str(ficha.get("tipo_procedimiento_web") or ""),
            str(ficha.get("cpv_web") or ""),
            str(ficha.get("nut_web") or ""),
            str(ficha.get("adjudicatario_web") or ""),
            str(ficha.get("resolucion_web") or ""),
        ]
    )

    registro_texto = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "numero_expediente": None,
        "texto_matching": limitar_excel(limpiar_texto(texto_matching)),
        "texto_completo": limitar_excel(texto),
    }

    documentos = []
    palabras_docs = [
        "pdf",
        "documento",
        "anuncio",
        "pliego",
        "prego",
        "descargar",
        "contrato",
        "memoria",
        "informe",
    ]

    if not enlaces.empty:
        docs = enlaces[
            enlaces["texto_enlace"].str.contains(
                "|".join(palabras_docs),
                case=False,
                na=False,
                regex=True,
            )
            | enlaces["url_completa"].str.contains(
                "|".join(palabras_docs),
                case=False,
                na=False,
                regex=True,
            )
        ].copy()

        for _, doc in docs.iterrows():
            documentos.append(
                {
                    "licitacion_id": licitacion_id,
                    "portal": portal,
                    "nombre_documento": doc["texto_enlace"],
                    "url_documento": doc["url_completa"],
                    "estado_descarga": "NO_DESCARGADO",
                    "tipo_archivo_detectado": None,
                    "ruta_archivo": None,
                    "texto_extraido": None,
                    "numero_paginas": None,
                    "error": None,
                }
            )

    return ficha, documentos, [], registro_texto


# ============================================================
# 5. PARSER MADRID
# ============================================================

def filtrar_documentos_madrid(df_enlaces: pd.DataFrame) -> pd.DataFrame:
    """Filtra enlaces candidatos a documentos en el portal de Madrid."""
    if df_enlaces.empty:
        return df_enlaces.copy()

    palabras_docs = [
        "memoria",
        "informe",
        "iniciación",
        "aprobación",
        "anuncio",
        "pliego",
        "condiciones",
        "descargar",
        "documento",
        "pdf",
        "contrato",
    ]

    mask = (
        df_enlaces["texto_enlace"].str.contains(
            "|".join(palabras_docs),
            case=False,
            na=False,
            regex=True,
        )
        | df_enlaces["url_completa"].str.contains(
            "|".join(palabras_docs),
            case=False,
            na=False,
            regex=True,
        )
    )

    return df_enlaces[mask].copy()


def descargar_y_leer_documentos(
    licitacion_id: str,
    portal: str,
    df_docs: pd.DataFrame,
    carpeta_base: Path,
    descargar: bool = True,
) -> list[dict[str, Any]]:
    """
    Descarga documentos candidatos y lee texto de PDFs.
    Si descargar=False, solo registra metadatos de enlaces.
    """
    registros = []

    if df_docs.empty:
        return registros

    carpeta_licitacion = carpeta_base / str(licitacion_id)
    carpeta_licitacion.mkdir(parents=True, exist_ok=True)

    for i, doc in df_docs.reset_index(drop=True).iterrows():
        nombre_doc = doc.get("texto_enlace", None)
        url_doc = doc.get("url_completa", None)

        registro = {
            "licitacion_id": licitacion_id,
            "portal": portal,
            "nombre_documento": nombre_doc,
            "url_documento": url_doc,
            "content_type": None,
            "tipo_archivo_detectado": None,
            "ruta_archivo": None,
            "estado_descarga": "NO_DESCARGADO",
            "error_descarga": None,
            "texto_extraido": None,
            "numero_paginas": None,
            "estado_lectura": None,
            "error_lectura": None,
        }

        if not descargar:
            registros.append(registro)
            continue

        try:
            respuesta_doc = descargar_url(url_doc)
            respuesta_doc.raise_for_status()

            contenido = respuesta_doc.content
            content_type = respuesta_doc.headers.get("Content-Type", "")

            extension, tipo_archivo = detectar_extension(
                contenido,
                content_type,
                url_doc,
            )

            nombre_archivo = (
                f"{i + 1:02d}_"
                f"{limpiar_nombre_archivo(nombre_doc)}"
                f"{extension}"
            )

            ruta_archivo = carpeta_licitacion / nombre_archivo

            with open(ruta_archivo, "wb") as archivo:
                archivo.write(contenido)

            registro.update(
                {
                    "content_type": content_type,
                    "tipo_archivo_detectado": tipo_archivo,
                    "ruta_archivo": str(ruta_archivo),
                    "estado_descarga": "OK",
                    "error_descarga": None,
                }
            )

            if tipo_archivo in ["PDF", "PDF_POSIBLE"]:
                lectura = extraer_texto_pdf_desde_ruta(ruta_archivo)
                registro.update(lectura)

        except Exception as error:
            registro.update(
                {
                    "estado_descarga": "ERROR",
                    "error_descarga": str(error),
                }
            )

        registros.append(registro)
        time.sleep(PAUSA_SEGUNDOS)

    return registros


def parsear_madrid(
    fila: pd.Series,
    response: requests.Response,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Extrae ficha de una licitación del portal de Madrid."""
    licitacion_id = fila["licitacion_id"]
    url = fila["detail_url"]
    portal = fila["portal"]

    soup, texto, df_lineas = extraer_lineas_html(response)
    enlaces = extraer_enlaces(soup, response.url)

    titulo_web = None
    if not df_lineas.empty:
        titulo_web = df_lineas.loc[df_lineas["orden"] == 0, "linea"].iloc[0]

    ficha = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "tipo_fuente": "html_madrid",
        "detail_url": url,
        "url_final": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "titulo_original": fila.get("titulo", None),
        "titulo_web": limpiar_texto(titulo_web),
        "num_lineas_extraidas": df_lineas.shape[0],

        "tipo_publicacion_web": valor_siguiente(
            df_lineas,
            "Tipo de publicación",
        ),
        "situacion_web": valor_siguiente(df_lineas, "Situación"),
        "tipo_resolucion_web": valor_siguiente(
            df_lineas,
            "Tipo de resolución",
        ),
        "numero_expediente_web": valor_siguiente(
            df_lineas,
            "Número de expediente",
        ),
        "referencia_web": valor_siguiente(df_lineas, "Referencia"),
        "identificador_ted_web": valor_siguiente(
            df_lineas,
            "Identificador del expediente en TED",
        ),
        "codigo_dir3_web": valor_siguiente(
            df_lineas,
            "Código de la entidad adjudicadora",
        ),
        "entidad_adjudicadora_web": valor_siguiente(
            df_lineas,
            "Entidad adjudicadora",
        ),
        "objeto_contrato_web": valor_siguiente(
            df_lineas,
            "Objeto del contrato",
        ),
        "tipo_contrato_web": valor_siguiente(df_lineas, "Tipo de contrato"),
        "contrato_mixto_web": valor_siguiente(df_lineas, "Contrato mixto"),
        "codigo_cpv_web": valor_siguiente(df_lineas, "Código CPV"),
        "legislacion_nacional_web": valor_siguiente(
            df_lineas,
            "Legislación nacional aplicable",
        ),
        "sujeto_regulacion_armonizada_web": valor_siguiente(
            df_lineas,
            "Sujeto a regulación armonizada",
        ),
        "sistema_contratacion_web": valor_siguiente(
            df_lineas,
            "Sistema de contratación",
        ),
        "codigo_nuts_web": valor_siguiente(df_lineas, "Código NUTS"),
        "compra_publica_innovacion_web": valor_siguiente(
            df_lineas,
            "Compra pública de innovación",
        ),
        "financiacion_ue_web": valor_siguiente(
            df_lineas,
            "Financiación de la Unión Europea",
        ),
        "procedimiento_adjudicacion_web": valor_siguiente(
            df_lineas,
            "Procedimiento de adjudicación",
        ),
        "tipo_tramitacion_web": valor_siguiente(
            df_lineas,
            "Tipo de tramitación",
        ),
        "metodo_presentacion_ofertas_web": valor_siguiente(
            df_lineas,
            "Método de presentación de ofertas",
        ),
        "subasta_electronica_web": valor_siguiente(
            df_lineas,
            "Subasta electrónica",
        ),
        "valor_estimado_sin_impuestos_web": valor_siguiente(
            df_lineas,
            "Valor estimado sin impuestos",
        ),
        "presupuesto_base_sin_impuestos_web": valor_siguiente(
            df_lineas,
            "Presupuesto base licitación sin impuestos",
        ),
        "presupuesto_base_total_web": valor_siguiente(
            df_lineas,
            "Presupuesto base licitación. Importe total",
        ),
        "duracion_contrato_web": valor_siguiente(
            df_lineas,
            "Duración del contrato",
        ),
        "fecha_limite_presentacion_web": valor_siguiente(
            df_lineas,
            "Fecha y hora límite de presentación de ofertas o solicitudes de participación",
        ),
        "num_enlaces_detectados": enlaces.shape[0],
        "error": None,
    }

    ficha["valor_estimado_sin_impuestos_num"] = convertir_importe_eur(
        ficha.get("valor_estimado_sin_impuestos_web")
    )
    ficha["presupuesto_base_sin_impuestos_num"] = convertir_importe_eur(
        ficha.get("presupuesto_base_sin_impuestos_web")
    )
    ficha["presupuesto_base_total_num"] = convertir_importe_eur(
        ficha.get("presupuesto_base_total_web")
    )

    df_docs = filtrar_documentos_madrid(enlaces)

    documentos = descargar_y_leer_documentos(
        licitacion_id=licitacion_id,
        portal=portal,
        df_docs=df_docs,
        carpeta_base=RUTA_BRONZE_DOCUMENTOS,
        descargar=DESCARGAR_DOCUMENTOS_MADRID,
    )

    textos_docs = [
        str(doc.get("texto_extraido") or "")
        for doc in documentos
        if doc.get("texto_extraido")
    ]

    texto_matching = " ".join(
        [
            str(ficha.get("titulo_original") or ""),
            str(ficha.get("titulo_web") or ""),
            str(ficha.get("objeto_contrato_web") or ""),
            str(ficha.get("entidad_adjudicadora_web") or ""),
            str(ficha.get("tipo_contrato_web") or ""),
            str(ficha.get("codigo_cpv_web") or ""),
            str(ficha.get("codigo_nuts_web") or ""),
            str(ficha.get("procedimiento_adjudicacion_web") or ""),
            str(ficha.get("duracion_contrato_web") or ""),
            " ".join(textos_docs),
        ]
    )

    registro_texto = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "numero_expediente": ficha.get("numero_expediente_web"),
        "texto_matching": limitar_excel(limpiar_texto(texto_matching)),
        "texto_completo": limitar_excel(texto),
    }

    return ficha, documentos, [], registro_texto


# ============================================================
# 6. PARSER CONTRATACIÓN DEL ESTADO
# ============================================================

def extraer_fecha_hora_publicacion_contratacion(
    texto: str,
) -> tuple[str | None, str | None]:
    """Extrae fecha y hora de publicación desde PDF de contratación_estado."""
    patron = (
        r"Publicado en la Plataforma de Contratación del Sector Público "
        r"el\s+([0-9]{2}-[0-9]{2}-[0-9]{4})\s+a\s+las\s+([0-9]{2}:[0-9]{2})"
    )

    match = re.search(patron, texto, flags=re.IGNORECASE)

    if match:
        return match.group(1), match.group(2)

    return None, None


def parsear_criterios_contratacion_estado(
    licitacion_id: str,
    portal: str,
    numero_expediente: str | None,
    texto: str,
) -> list[dict[str, Any]]:
    """Extrae criterios de adjudicación de contratación_estado."""
    patron_criterio = re.compile(
        r"(?P<criterio>.+?)\s+"
        r"Subtipo Criterio\s*:\s*(?P<subtipo>[^\n]+)\s+"
        r"Ponderación\s*:\s*(?P<ponderacion>[0-9]+)"
        r"(?:\s+Expresión de evaluación\s*:\s*(?P<expresion>.+?))?"
        r"\s+Cantidad Mínima\s*:\s*(?P<cantidad_minima>[0-9]+)"
        r"\s+Cantidad Máxima\s*:\s*(?P<cantidad_maxima>[0-9]+)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    registros = []

    for match in patron_criterio.finditer(texto):
        criterio = limpiar_texto(match.group("criterio"))

        criterio = re.split(
            (
                r"Condiciones de adjudicación|"
                r"Criterios de Adjudicación|"
                r"Criterios evaluables mediante aplicación de fórmulas"
            ),
            criterio,
            flags=re.IGNORECASE,
        )[-1].strip()

        registros.append(
            {
                "licitacion_id": licitacion_id,
                "portal": portal,
                "numero_expediente": numero_expediente,
                "criterio": criterio,
                "subtipo_criterio": limpiar_texto(match.group("subtipo")),
                "ponderacion": int(match.group("ponderacion")),
                "expresion_evaluacion": limpiar_texto(match.group("expresion")),
                "cantidad_minima": int(match.group("cantidad_minima")),
                "cantidad_maxima": int(match.group("cantidad_maxima")),
            }
        )

    return registros


def parsear_lotes_contratacion_estado(
    licitacion_id: str,
    portal: str,
    numero_expediente: str | None,
    texto: str,
) -> list[dict[str, Any]]:
    """Extrae lotes de contratación_estado."""
    matches_lotes = list(
        re.finditer(r"Nº Lote:\s*([0-9]+)", texto, flags=re.IGNORECASE)
    )

    registros = []

    for i, match in enumerate(matches_lotes):
        inicio = match.start()

        if i + 1 < len(matches_lotes):
            fin = matches_lotes[i + 1].start()
        else:
            fin = texto.find("Proceso de Licitación")
            if fin == -1:
                fin = len(texto)

        texto_lote = texto[inicio:fin]

        registro = {
            "licitacion_id": licitacion_id,
            "portal": portal,
            "numero_expediente": numero_expediente,
            "num_lote": match.group(1),
            "objeto_lote": buscar_regex(
                texto_lote,
                r"Objeto del Contrato:\s*([^\n]+)",
            ),
            "descripcion_lote": buscar_regex(
                texto_lote,
                r"Descripción\s+([^\n]+)",
            ),
            "valor_estimado_lote": buscar_regex(
                texto_lote,
                r"Valor estimado del contrato\s+([0-9\.\,]+)\s+EUR",
            ),
            "presupuesto_base_lote": buscar_regex(
                texto_lote,
                r"Presupuesto base de licitación\s+Importe\s+([0-9\.\,]+)\s+EUR",
            ),
            "presupuesto_base_lote_sin_impuestos": buscar_regex(
                texto_lote,
                r"Importe \(sin impuestos\)\s+([0-9\.\,]+)\s+EUR",
            ),
            "lugar_ejecucion_lote": buscar_regex(
                texto_lote,
                r"Lugar de ejecución\s+Subentidad Nacional\s+([^\n]+)",
            ),
            "codigo_subentidad_lote": buscar_regex(
                texto_lote,
                r"Código de Subentidad Territorial\s+([A-Z0-9]+)",
            ),
            "estado_lote": buscar_regex(
                texto_lote,
                r"(Formalizado|Adjudicado|Desierto|Anulado)",
            ),
            "precio_oferta_mas_baja": buscar_regex(
                texto_lote,
                r"Precio de la oferta más baja\s+([0-9\.\,]+)\s+EUR",
            ),
            "precio_oferta_mas_alta": buscar_regex(
                texto_lote,
                r"Precio de la oferta más alta\s+([0-9\.\,]+)\s+EUR",
            ),
            "num_ofertas_pymes": buscar_regex(
                texto_lote,
                r"Número de ofertas recibidas de PYMEs\s+([0-9]+)",
            ),
            "adjudicatario": buscar_regex(
                texto_lote,
                r"Adjudicatario\s+([^\n]+)",
            ),
            "nif_adjudicatario": buscar_regex(
                texto_lote,
                r"NIF\s+([A-Z0-9]+)",
            ),
            "adjudicatario_pyme": buscar_regex(
                texto_lote,
                r"El adjudicatario es una PYME\s*:\s*([^\n]+)",
            ),
            "direccion_adjudicatario": buscar_regex(
                texto_lote,
                r"Dirección Física\s+(.+?)\s+Contacto",
                flags=re.IGNORECASE | re.DOTALL,
            ),
            "telefono_adjudicatario": buscar_regex(
                texto_lote,
                r"Contacto\s+Teléfono\s+([+0-9 ]+)",
            ),
            "email_adjudicatario": buscar_regex(
                texto_lote,
                r"Correo Electrónico\s+([^\s]+@[^\s]+)",
            ),
            "importe_adjudicacion_sin_impuestos": buscar_regex(
                texto_lote,
                r"Importe total ofertado \(sin impuestos\)\s+([0-9\.\,]+)\s+EUR",
            ),
            "importe_adjudicacion_con_impuestos": buscar_regex(
                texto_lote,
                r"Importe total ofertado \(con impuestos\)\s+([0-9\.\,]+)\s+EUR",
            ),
            "numero_contrato": buscar_regex(
                texto_lote,
                r"Número de contrato\s+([^\n]+)",
            ),
            "fecha_formalizacion": buscar_regex(
                texto_lote,
                r"Fecha de Formalización\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
            ),
            "documento_contrato": buscar_regex(
                texto_lote,
                r"Contrato\s+([^\n]+\.pdf)",
            ),
            "fecha_entrada_vigor": buscar_regex(
                texto_lote,
                r"Fecha de Entrada en Vigor del Contrato\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
            ),
            "motivacion": buscar_regex(
                texto_lote,
                r"Motivación\s+(.+?)\s+Fecha del Acuerdo de Adjudicación",
                flags=re.IGNORECASE | re.DOTALL,
            ),
            "fecha_acuerdo_adjudicacion": buscar_regex(
                texto_lote,
                r"Fecha del Acuerdo de Adjudicación\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
            ),
            "texto_lote": limitar_excel(texto_lote),
        }

        for campo in [
            "valor_estimado_lote",
            "presupuesto_base_lote",
            "presupuesto_base_lote_sin_impuestos",
            "precio_oferta_mas_baja",
            "precio_oferta_mas_alta",
            "importe_adjudicacion_sin_impuestos",
            "importe_adjudicacion_con_impuestos",
        ]:
            registro[campo + "_num"] = convertir_importe_eur(
                registro.get(campo)
            )

        registros.append(registro)

    return registros



# ============================================================
# 6B. PARSER CONTRATACIÓN DEL ESTADO HTML
# ============================================================

ETIQUETAS_CORTE_CONTRATACION_HTML = [
    # Español
    "Órgano de contratación",
    "Organo de contratación",
    "Expediente",
    "Objeto del contrato",
    "Enlace a la licitación",
    "Estado de la Licitación",
    "Valor estimado del contrato",
    "Tipo de Contrato",
    "Código CPV",
    "Lugar de ejecución",
    "Sistema de contratación",
    "Procedimiento de contratación",
    "Tipo de tramitación",
    "Método de presentación de la oferta",
    "Fecha fin de presentación de oferta",
    "Otra información",
    "Anuncios y Documentos",
    "Publicación en plataforma",
    "Documento",
    "Ver documentos",
    # Inglés / etiquetas internas PLACSP
    "Details of tender",
    "Contracting Party",
    "File",
    "Subject of the contract",
    "Link to tender",
    "Tender status",
    "Estimated value of the contract",
    "Estimated value",
    "Type of Contract",
    "CPV code",
    "Place of execution",
    "Contracting system",
    "Contracting procedure",
    "Processing type",
    "Method of presentation of the offer",
    "Deadline for submission of tenders",
    "Other information",
    "Announcements and Documents",
    "Publication on platform",
    "Document",
    "View documents",
    # Variantes observadas en la PLACSP renderizada
    "Link to the bidding",
    "State of the Tender",
    "Sistema de contractació",
    "Procediment de contractació",
    "Tipus de tramitació",
    "Mètode de presentació de l'oferta",
    "Data fi de presentació d'oferta",
]


def es_etiqueta_corte_contratacion_html(linea: str) -> bool:
    """Indica si una línea es una etiqueta de corte del HTML PLACSP."""
    linea_norm = str(linea).lower().strip()
    etiquetas_norm = [
        etiqueta.lower().strip()
        for etiqueta in ETIQUETAS_CORTE_CONTRATACION_HTML
    ]
    return linea_norm in etiquetas_norm


def buscar_indice_etiqueta_contratacion_html(
    lineas: list[str],
    etiquetas: str | list[str],
) -> int | None:
    """Busca el índice de una etiqueta exacta en español o inglés."""
    if isinstance(etiquetas, str):
        etiquetas = [etiquetas]

    etiquetas_norm = [etiqueta.lower().strip() for etiqueta in etiquetas]

    for i, linea in enumerate(lineas):
        if str(linea).lower().strip() in etiquetas_norm:
            return i

    return None


def extraer_bloque_contratacion_html(
    lineas: list[str],
    etiquetas_inicio: str | list[str],
    max_lineas: int = 8,
) -> str | None:
    """
    Extrae el valor posterior a una etiqueta de PLACSP HTML.

    La versión HTML de contratación_estado mezcla etiquetas en español, inglés
    y texto técnico de portlets. Esta función corta cuando aparece otra etiqueta
    conocida y elimina ruido explícito del portal.
    """
    idx = buscar_indice_etiqueta_contratacion_html(lineas, etiquetas_inicio)

    if idx is None:
        return None

    valores = []

    for j in range(idx + 1, min(idx + 1 + max_lineas, len(lineas))):
        linea = str(lineas[j]).strip()

        if not linea:
            continue

        if es_etiqueta_corte_contratacion_html(linea):
            break

        ruido = [
            "EN",
            "Menú",
            "Detail",
            "Search",
            "Start session",
            "Register",
            "Bienvenidos",
            "Welcome",
            "Bienvenue",
            "Benvinguts",
        ]

        if linea in ruido:
            continue

        if "Su navegador no permite scripts" in linea:
            continue

        if "your browser" in linea.lower():
            continue

        if "¿Desea continuar?" in linea:
            continue

        if "The bid with the file number" in linea:
            continue

        valores.append(linea)

    if not valores:
        return None

    return limpiar_texto(" ".join(valores))


def limpiar_expediente_contratacion_html(valor: Any) -> str | None:
    """Corrige expedientes pegados a otra etiqueta, por ejemplo: N202600214 Subject of the contract."""
    if valor is None:
        return None

    texto = str(valor)

    # Preferencia para expedientes tipo N202600214.
    match = re.search(r"\bN\d{6,}\b", texto, flags=re.IGNORECASE)
    if match:
        return match.group(0)

    # Fallback para otros formatos alfanuméricos de expediente.
    match = re.search(r"\b[A-Z]{0,5}\d{3,}[A-Z0-9/\-\.]*\b", texto)
    if match:
        return match.group(0)

    return limpiar_texto(texto)


def limpiar_lugar_ejecucion_contratacion_html(valor: Any) -> str | None:
    """Elimina etiquetas pegadas después del lugar de ejecución."""
    if valor is None:
        return None

    texto = str(valor)

    cortes = [
        "Sistema de contratación",
        "Sistema de contractació",
        "Contracting system",
        "Procedimiento de contratación",
        "Procurement procedure",
        "Contracting procedure",
        "Tipo de tramitación",
        "Processing type",
    ]

    for corte in cortes:
        if corte in texto:
            texto = texto.split(corte)[0]

    return limpiar_texto(texto)


def convertir_importe_contratacion_html(valor: Any) -> float | None:
    """Convierte importes tipo '80.366,00 Euros' a float."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except ValueError:
        pass

    match = re.search(r"([0-9\.\,]+)", str(valor))

    if not match:
        return None

    numero = match.group(1).replace(".", "").replace(",", ".")

    try:
        return float(numero)
    except ValueError:
        return None


def limpiar_valor_por_cortes_contratacion_html(
    valor: Any,
    cortes: list[str],
) -> str | None:
    """Corta un campo cuando se le pega la siguiente etiqueta del portal."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except ValueError:
        pass

    texto = str(valor)

    for corte in cortes:
        idx = texto.lower().find(corte.lower())
        if idx != -1:
            texto = texto[:idx]

    return limpiar_texto(texto)


def limpiar_objeto_contratacion_html(valor: Any) -> str | None:
    """Limpia el objeto cuando arrastra enlace o estado de la licitación."""
    return limpiar_valor_por_cortes_contratacion_html(
        valor,
        [
            "Link to the bidding",
            "Link to tender",
            "Enlace a la licitación",
            "State of the Tender",
            "Tender status",
            "Estado de la Licitación",
        ],
    )


def extraer_cpvs_contratacion_html(valor: Any) -> tuple[list[str], list[str]]:
    """
    Extrae todos los códigos CPV y sus descripciones.

    Soporta textos como:
        85100000-Servicios de salud.
        71317200-Servicios de salud y seguridad., 80560000-Servicios...
    """
    if valor is None:
        return [], []

    try:
        if pd.isna(valor):
            return [], []
    except ValueError:
        pass

    texto = limpiar_texto(str(valor)) or ""

    # Cortes defensivos si el CPV arrastra etiquetas posteriores.
    texto = limpiar_valor_por_cortes_contratacion_html(
        texto,
        [
            "Lugar de ejecución",
            "Place of execution",
            "Sistema de contratación",
            "Sistema de contractació",
            "Contracting system",
        ],
    ) or ""

    patron = r"(\d{8})\s*[-–]\s*(.*?)(?=(?:,?\s*\d{8}\s*[-–])|$)"
    matches = re.findall(patron, texto)

    codigos = []
    descripciones = []

    for codigo, descripcion in matches:
        codigos.append(codigo.strip())
        descripcion = limpiar_texto(descripcion.strip(" ,;"))
        if descripcion:
            descripciones.append(descripcion)

    if codigos:
        return list(dict.fromkeys(codigos)), list(dict.fromkeys(descripciones))

    codigos = re.findall(r"\b\d{8}\b", texto)
    return list(dict.fromkeys(codigos)), []


def extraer_cpv_contratacion_html(valor: Any) -> tuple[str | None, str | None]:
    """Compatibilidad hacia atrás: devuelve el primer CPV y su primera descripción."""
    codigos, descripciones = extraer_cpvs_contratacion_html(valor)

    codigo = codigos[0] if codigos else None
    descripcion = descripciones[0] if descripciones else None

    return codigo, descripcion


def extraer_primer_url_contratacion_html(
    lineas: list[str],
    contiene: str | None = None,
) -> str | None:
    """Extrae la primera URL de las líneas, con filtro opcional."""
    for linea in lineas:
        if "http" not in str(linea):
            continue

        if contiene is None or contiene.lower() in str(linea).lower():
            return str(linea).strip()

    return None


def extraer_estado_contratacion_html(texto: str, lineas: list[str]) -> str | None:
    """Fallback para estado de licitación cuando no aparece por etiqueta."""
    estado = extraer_bloque_contratacion_html(
        lineas,
        ["Estado de la Licitación", "Tender status", "State of the Tender"],
        max_lineas=2,
    )

    if estado is not None:
        return estado

    estados = [
        "Evaluación",
        "Adjudicada",
        "Formalizada",
        "Resuelta",
        "En plazo",
        "Anulada",
        "Desierta",
        "Evaluation",
        "Awarded",
        "Formalized",
        "Resolved",
    ]

    for estado_posible in estados:
        if re.search(rf"\b{re.escape(estado_posible)}\b", texto, flags=re.IGNORECASE):
            return estado_posible

    return None


def extraer_valor_estimado_contratacion_html(texto: str, lineas: list[str]) -> str | None:
    """Extrae valor estimado con etiqueta o por patrón de importe en Euros."""
    valor = extraer_bloque_contratacion_html(
        lineas,
        ["Valor estimado del contrato", "Estimated value of the contract", "Estimated value"],
        max_lineas=2,
    )

    if valor is not None:
        return valor

    # Fallback: busca importes en euros, excluyendo líneas técnicas obvias.
    for linea in lineas:
        linea_str = str(linea)
        if re.search(r"[0-9\.\,]+\s*(Euros|EUR|€)", linea_str, flags=re.IGNORECASE):
            return limpiar_texto(linea_str)

    match = re.search(r"([0-9\.\,]+\s*(?:Euros|EUR|€))", texto, flags=re.IGNORECASE)
    if match:
        return limpiar_texto(match.group(1))

    return None


def parsear_contratacion_estado_html(
    fila: pd.Series,
    response: requests.Response,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
    Parser específico para el formato HTML de contratación_estado.

    Este formato no es igual al PDF directo. La página mezcla texto visible,
    etiquetas en inglés y elementos técnicos de portlets. No se leen PDFs
    adjuntos; solo se inventarian enlaces/documentos visibles.
    """
    licitacion_id = fila["licitacion_id"]
    url = fila["detail_url"]
    portal = fila["portal"]

    soup, texto, df_lineas = extraer_lineas_html(response)
    lineas = df_lineas["linea"].astype(str).tolist()
    enlaces = extraer_enlaces(soup, response.url)

    titulo_pagina = soup.title.get_text(strip=True) if soup.title else None

    organo_contratacion = extraer_bloque_contratacion_html(
        lineas,
        ["Órgano de contratación", "Organo de contratación", "Contracting Party"],
        max_lineas=4,
    )

    expediente = extraer_bloque_contratacion_html(
        lineas,
        ["Expediente", "File"],
        max_lineas=3,
    )
    expediente = limpiar_expediente_contratacion_html(expediente)

    objeto_contrato = extraer_bloque_contratacion_html(
        lineas,
        ["Objeto del contrato", "Subject of the contract"],
        max_lineas=5,
    )

    if objeto_contrato is None:
        objeto_contrato = fila.get("titulo", None)

    objeto_contrato = limpiar_objeto_contratacion_html(objeto_contrato)

    enlace_licitacion = extraer_bloque_contratacion_html(
        lineas,
        ["Enlace a la licitación", "Link to tender", "Link to the bidding"],
        max_lineas=4,
    )

    if enlace_licitacion is None:
        enlace_licitacion = extraer_primer_url_contratacion_html(
            lineas,
            contiene="detalle_licitacion",
        )

    estado_licitacion = extraer_estado_contratacion_html(texto, lineas)
    valor_estimado_texto = extraer_valor_estimado_contratacion_html(texto, lineas)

    tipo_contrato = extraer_bloque_contratacion_html(
        lineas,
        ["Tipo de Contrato", "Type of Contract"],
        max_lineas=2,
    )

    codigo_cpv_texto = extraer_bloque_contratacion_html(
        lineas,
        ["Código CPV", "CPV code"],
        max_lineas=2,
    )

    # Fallback para CPV si la etiqueta no se detecta bien.
    if codigo_cpv_texto is None:
        cpv_match = re.search(r"\b\d{8}\b\s*[-–]\s*[^\n]+", texto)
        if cpv_match:
            codigo_cpv_texto = limpiar_texto(cpv_match.group(0))

    cpv_codes, cpv_descripciones = extraer_cpvs_contratacion_html(codigo_cpv_texto)

    if not cpv_codes:
        cpv_codes = list(dict.fromkeys(re.findall(r"\b\d{8}\b", texto)))
        cpv_descripciones = []

    codigo_cpv = cpv_codes[0] if cpv_codes else None
    descripcion_cpv = cpv_descripciones[0] if cpv_descripciones else None

    lugar_ejecucion = extraer_bloque_contratacion_html(
        lineas,
        ["Lugar de ejecución", "Place of execution"],
        max_lineas=3,
    )
    lugar_ejecucion = limpiar_lugar_ejecucion_contratacion_html(lugar_ejecucion)
    lugar_ejecucion = limpiar_valor_por_cortes_contratacion_html(
        lugar_ejecucion,
        [
            "Sistema de contratación",
            "Sistema de contractació",
            "Contracting system",
            "Procedimiento de contratación",
            "Contracting procedure",
        ],
    )

    sistema_contratacion = extraer_bloque_contratacion_html(
        lineas,
        ["Sistema de contratación", "Sistema de contractació", "Contracting system"],
        max_lineas=2,
    )
    sistema_contratacion = limpiar_valor_por_cortes_contratacion_html(
        sistema_contratacion,
        [
            "Procedimiento de contratación",
            "Procediment de contractació",
            "Contracting procedure",
            "Tipo de tramitación",
            "Processing type",
        ],
    )

    procedimiento_contratacion = extraer_bloque_contratacion_html(
        lineas,
        ["Procedimiento de contratación", "Procediment de contractació", "Contracting procedure"],
        max_lineas=2,
    )
    procedimiento_contratacion = limpiar_valor_por_cortes_contratacion_html(
        procedimiento_contratacion,
        [
            "Tipo de tramitación",
            "Tipus de tramitació",
            "Processing type",
            "Otra información",
            "Other information",
        ],
    )

    tipo_tramitacion = extraer_bloque_contratacion_html(
        lineas,
        ["Tipo de tramitación", "Tipus de tramitació", "Processing type"],
        max_lineas=2,
    )
    tipo_tramitacion = limpiar_valor_por_cortes_contratacion_html(
        tipo_tramitacion,
        [
            "Otra información",
            "Other information",
            "Método de presentación",
            "Method of presentation",
        ],
    )

    metodo_presentacion = extraer_bloque_contratacion_html(
        lineas,
        ["Método de presentación de la oferta", "Method of presentation of the offer"],
        max_lineas=2,
    )

    fecha_fin_presentacion = extraer_bloque_contratacion_html(
        lineas,
        ["Fecha fin de presentación de oferta", "Deadline for submission of tenders"],
        max_lineas=2,
    )

    documentos = []
    if not enlaces.empty:
        patrones_documentos = [
            "GetDocumentByIdServlet",
            ".pdf",
            "documento",
            "document",
            "xml",
            "csv",
        ]

        for i, enlace in enlaces.iterrows():
            texto_enlace = enlace.get("texto_enlace")
            url_enlace = enlace.get("url_enlace")
            contenido = f"{texto_enlace} {url_enlace}".lower()

            if any(patron.lower() in contenido for patron in patrones_documentos):
                documentos.append(
                    {
                        "licitacion_id": licitacion_id,
                        "portal": portal,
                        "num_documento": i + 1,
                        "nombre_documento": texto_enlace,
                        "url_documento": url_enlace,
                        "documento_leido": False,
                        "tipo_documento": "enlace_detectado_no_descargado",
                    }
                )

    ficha = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "tipo_fuente": "html_contratacion_estado",
        "detail_url": url,
        "url_final": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "titulo_original": fila.get("titulo", None),
        "titulo_web": titulo_pagina,
        "num_lineas_extraidas": df_lineas.shape[0],
        "num_enlaces_detectados": enlaces.shape[0],
        "num_documentos_detectados": len(documentos),

        # Campos normalizados al esquema del pipeline.
        "numero_expediente_web": expediente,
        "entidad_adjudicadora_web": organo_contratacion,
        "organo_contratacion_web": organo_contratacion,
        "objeto_contrato_web": objeto_contrato,
        "objeto_web": objeto_contrato,
        "resolucion_web": estado_licitacion,
        "valor_estimado_contrato": valor_estimado_texto,
        "valor_estimado_contrato_num": convertir_importe_contratacion_html(valor_estimado_texto),
        "tipo_contrato_web": tipo_contrato,
        "codigo_cpv_web": codigo_cpv_texto,
        "cpv_codes": cpv_codes,
        "cpv_descripciones": cpv_descripciones,
        "lugar_ejecucion": lugar_ejecucion,
        "sistema_contratacion_web": sistema_contratacion,
        "procedimiento_adjudicacion_web": procedimiento_contratacion,
        "tipo_tramitacion_web": tipo_tramitacion,
        "metodo_presentacion_ofertas_web": metodo_presentacion,
        "fecha_limite_presentacion_web": fecha_fin_presentacion,
        "detalle_licitacion_url": enlace_licitacion,
        "error": None,
    }

    texto_matching = " ".join(
        [
            str(ficha.get("titulo_original") or ""),
            str(ficha.get("titulo_web") or ""),
            str(expediente or ""),
            str(organo_contratacion or ""),
            str(objeto_contrato or ""),
            str(estado_licitacion or ""),
            str(valor_estimado_texto or ""),
            str(tipo_contrato or ""),
            str(codigo_cpv_texto or ""),
            str(descripcion_cpv or ""),
            str(lugar_ejecucion or ""),
            str(procedimiento_contratacion or ""),
            str(tipo_tramitacion or ""),
            str(metodo_presentacion or ""),
            str(fecha_fin_presentacion or ""),
            texto,
        ]
    )

    registro_texto = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "numero_expediente": expediente,
        "texto_matching": limitar_excel(limpiar_texto(texto_matching)),
        "texto_completo": limitar_excel(texto),
    }

    return ficha, documentos, [], registro_texto


def parsear_contratacion_estado_pdf(
    fila: pd.Series,
    response: requests.Response,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parser especializado para PDFs de contratación_estado."""
    licitacion_id = fila["licitacion_id"]
    url = fila["detail_url"]
    portal = fila["portal"]

    texto, lineas, num_paginas = extraer_texto_pdf_desde_bytes(
        response.content
    )

    fecha_publicacion_txt, hora_publicacion = (
        extraer_fecha_hora_publicacion_contratacion(texto)
    )

    cpv_matches = re.findall(r"(\b\d{8}\b)\s*-\s*([^\n]+)", texto)
    cpv_codes = list(dict.fromkeys([codigo for codigo, _ in cpv_matches]))
    cpv_descripciones = list(
        dict.fromkeys(
            [limpiar_texto(descripcion) for _, descripcion in cpv_matches]
        )
    )

    match_apertura = re.search(
        r"El día\s+([0-9]{2}/[0-9]{2}/[0-9]{4})\s+a las\s+([0-9]{2}:[0-9]{2})",
        texto,
        flags=re.IGNORECASE,
    )

    fecha_apertura = None
    hora_apertura = None

    if match_apertura:
        fecha_apertura = match_apertura.group(1)
        hora_apertura = match_apertura.group(2)

    ficha = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "tipo_fuente": "pdf_contratacion_estado",
        "detail_url": url,
        "url_final": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "content_disposition": response.headers.get("Content-Disposition"),
        "titulo_original": fila.get("titulo", None),
        "num_paginas": num_paginas,
        "num_lineas_extraidas": len(lineas),

        "tipo_anuncio": buscar_regex(
            texto,
            r"(Anuncio de formalización de contrato|Anuncio de licitación|Anuncio de adjudicación)",
        ),
        "numero_expediente_web": buscar_regex(
            texto,
            r"Número de Expediente\s+([^\n]+)",
        ),
        "fecha_publicacion_texto": fecha_publicacion_txt,
        "hora_publicacion": hora_publicacion,
        "contrato_sujeto_regulacion_armonizada": buscar_regex(
            texto,
            r"Contrato Sujeto a regulación armonizada\s+([^\n]+)",
        ),
        "directiva_aplicacion": buscar_regex(
            texto,
            r"Directiva de aplicación\s*([^\n]+)",
        ),
        "entidad_adjudicadora_web": buscar_regex(
            texto,
            r"Entidad Adjudicadora\s*([^\n]+)",
        ),
        "tipo_administracion": buscar_regex(
            texto,
            r"Tipo de Administración\s+([^\n]+)",
        ),
        "actividad_principal": buscar_regex(
            texto,
            r"Actividad Principal\s+([^\n]+)",
        ),
        "tipo_entidad_adjudicadora": buscar_regex(
            texto,
            r"Tipo de Entidad Adjudicadora\s+([^\n]+)",
        ),
        "perfil_contratante": buscar_regex(
            texto,
            r"Perfil del Contratante\s*(https?://[^\s]+)",
        ),
        "telefono_entidad": buscar_regex(texto, r"Teléfono\s+([0-9+ ]+)"),
        "fax_entidad": buscar_regex(texto, r"Fax\s+([0-9+ ]+)"),
        "email_entidad": buscar_regex(
            texto,
            r"Correo Electrónico\s+([^\s]+@[^\s]+)",
        ),
        "objeto_contrato_web": buscar_regex(
            texto,
            r"Objeto del Contrato:\s*([^\n]+)",
        ),
        "descripcion_general": buscar_regex(
            texto,
            r"Descripción\s+(.*?)(?:Valor estimado del contrato)",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "valor_estimado_contrato": buscar_regex(
            texto,
            r"Valor estimado del contrato\s+([0-9\.\,]+)\s+EUR",
        ),
        "presupuesto_base_importe": buscar_regex(
            texto,
            r"Presupuesto base de licitación\s+Importe\s+([0-9\.\,]+)\s+EUR",
        ),
        "presupuesto_base_sin_impuestos": buscar_regex(
            texto,
            r"Importe \(sin impuestos\)\s+([0-9\.\,]+)\s+EUR",
        ),
        "cpv_codes": cpv_codes,
        "cpv_descripciones": cpv_descripciones,
        "plazo_ejecucion_inicio": buscar_regex(
            texto,
            r"Plazo de Ejecución\s+Del\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
        ),
        "plazo_ejecucion_fin": buscar_regex(
            texto,
            r"Plazo de Ejecución\s+Del\s+[0-9]{2}/[0-9]{2}/[0-9]{4}\s+al\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
        ),
        "lugar_ejecucion": buscar_regex(
            texto,
            r"Lugar de ejecución\s+Subentidad Nacional\s+([^\n]+)",
        ),
        "codigo_subentidad_territorial": buscar_regex(
            texto,
            r"Código de Subentidad Territorial\s+([A-Z0-9]+)",
        ),
        "num_lotes_web": buscar_regex(texto, r"Nº de Lotes:\s*([0-9]+)"),
        "num_lotes_resultado": buscar_regex(
            texto,
            r"Nº de Lotes cuyo resultado se indica en este anuncio:\s*([0-9]+)",
        ),
        "se_debe_ofertar": buscar_regex(
            texto,
            r"Se debe ofertar:\s*([^\n]+)",
        ),
        "max_lotes_presentacion": buscar_regex(
            texto,
            r"Número máximo de lotes a los que se puede presentar:?\s*([0-9]+)",
        ),
        "max_lotes_adjudicacion": buscar_regex(
            texto,
            r"Número máximo de lotes que se puede adjudicar a un licitador:?\s*([0-9]+)",
        ),
        "procedimiento_adjudicacion_web": buscar_regex(
            texto,
            r"Procedimiento\s+([^\n]+)",
        ),
        "tipo_tramitacion_web": buscar_regex(
            texto,
            r"Tramitación\s+([^\n]+)",
        ),
        "tramitacion_gasto": buscar_regex(
            texto,
            r"Tramitación del Gasto\s+([^\n]+)",
        ),
        "sistema_contratacion_web": buscar_regex(
            texto,
            r"Sistema de Contratación\s+([^\n]+)",
        ),
        "metodo_presentacion_ofertas_web": buscar_regex(
            texto,
            r"Presentación de la oferta\s+([^\n]+)",
        ),
        "plazo_obtencion_pliegos": buscar_regex(
            texto,
            r"Plazo de Obtención de Pliegos\s+Hasta el\s+([^\n]+)",
        ),
        "fecha_limite_presentacion_web": buscar_regex(
            texto,
            r"Plazo de Presentación de Oferta\s+Hasta el\s+([^\n]+)",
        ),
        "fecha_apertura_oferta": fecha_apertura,
        "hora_apertura_oferta": hora_apertura,
        "tipo_acto_apertura": buscar_regex(
            texto,
            r"Tipo de Acto\s*:\s*([^\n]+)",
        ),
        "detalle_licitacion_url": buscar_regex(
            texto,
            r"(https://contrataciondelestado\.es/wps/poc\?uri=deeplink:detalle_licitacion[^\s]+)",
        ),
        "programas_financiacion": buscar_regex(
            texto,
            r"Programas de Financiación\s+([^\n]+)",
        ),
        "id_documento": buscar_regex(texto, r"ID\s+([0-9]+)"),
        "uuid_documento": buscar_regex(texto, r"UUID\s+([0-9\-]+)"),
        "sello_tiempo": buscar_regex(
            texto,
            r"SELLO DE TIEMPO\s+(.+?)\s+N\.Serie",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "error": None,
    }

    for campo in [
        "valor_estimado_contrato",
        "presupuesto_base_importe",
        "presupuesto_base_sin_impuestos",
    ]:
        ficha[campo + "_num"] = convertir_importe_eur(ficha.get(campo))

    ficha["fecha_publicacion"] = pd.to_datetime(
        ficha.get("fecha_publicacion_texto"),
        format="%d-%m-%Y",
        errors="coerce",
    )

    # ========================================================
    # Homologación de PDF directo a campos comunes del resumen
    # ========================================================
    # Contratación del Estado puede venir como HTML o como PDF directo.
    # Para que la ficha_resumen sea comparable, aquí replicamos los
    # campos PDF en las columnas *_web usadas por el resumen común.

    objeto_pdf = ficha.get("objeto_contrato_web")

    if objeto_pdf is None:
        objeto_pdf = buscar_regex(
            texto,
            r"Objeto del Contrato:\s*([^\n]+)",
        )

    entidad_pdf = ficha.get("entidad_adjudicadora_web")

    ficha["titulo_original"] = objeto_pdf or ficha.get("titulo_original")
    ficha["objeto_web"] = objeto_pdf
    ficha["objeto_contrato_web"] = objeto_pdf
    ficha["organo_contratacion_web"] = entidad_pdf
    ficha["procedimiento_adjudicacion_web"] = ficha.get(
        "procedimiento_adjudicacion_web"
    )
    ficha["tipo_tramitacion_web"] = ficha.get("tipo_tramitacion_web")
    ficha["sistema_contratacion_web"] = ficha.get("sistema_contratacion_web")
    ficha["metodo_presentacion_ofertas_web"] = ficha.get(
        "metodo_presentacion_ofertas_web"
    )
    ficha["codigo_cpv_web"] = "; ".join(ficha.get("cpv_codes", []))
    ficha["resolucion_web"] = ficha.get("tipo_anuncio")

    lotes = parsear_lotes_contratacion_estado(
        licitacion_id=licitacion_id,
        portal=portal,
        numero_expediente=ficha.get("numero_expediente_web"),
        texto=texto,
    )

    criterios = parsear_criterios_contratacion_estado(
        licitacion_id=licitacion_id,
        portal=portal,
        numero_expediente=ficha.get("numero_expediente_web"),
        texto=texto,
    )

    texto_matching = " ".join(
        [
            str(ficha.get("titulo_original") or ""),
            str(ficha.get("objeto_contrato_web") or ""),
            str(ficha.get("descripcion_general") or ""),
            str(ficha.get("entidad_adjudicadora_web") or ""),
            str(ficha.get("lugar_ejecucion") or ""),
            str(ficha.get("procedimiento_adjudicacion_web") or ""),
            str(ficha.get("tipo_tramitacion_web") or ""),
            " ".join(cpv_descripciones),
            " ".join(cpv_codes),
            texto,
        ]
    )

    registro_texto = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "numero_expediente": ficha.get("numero_expediente_web"),
        "texto_matching": limitar_excel(limpiar_texto(texto_matching)),
        "texto_completo": limitar_excel(texto),
    }

    return ficha, [], lotes + criterios, registro_texto


# ============================================================
# 7. PARSER GENÉRICO
# ============================================================

def parsear_html_generico(
    fila: pd.Series,
    response: requests.Response,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parser base para HTML no especializado."""
    licitacion_id = fila["licitacion_id"]
    url = fila["detail_url"]
    portal = fila["portal"]

    soup, texto, df_lineas = extraer_lineas_html(response)
    enlaces = extraer_enlaces(soup, response.url)

    titulo_pagina = soup.title.get_text(strip=True) if soup.title else None
    cpv_codes = list(dict.fromkeys(re.findall(r"\b\d{8}\b", texto)))

    ficha = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "tipo_fuente": "html_generico",
        "detail_url": url,
        "url_final": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "titulo_original": fila.get("titulo", None),
        "titulo_web": titulo_pagina,
        "num_lineas_extraidas": df_lineas.shape[0],
        "numero_expediente_web": buscar_regex(
            texto,
            r"(?:Expediente|Número de Expediente)\s*[:\-]?\s*([^\n]+)",
        ),
        "entidad_adjudicadora_web": buscar_regex(
            texto,
            r"(?:Entidad Adjudicadora|Órgano de contratación|Organo de contratación)\s*[:\-]?\s*([^\n]+)",
        ),
        "objeto_contrato_web": buscar_regex(
            texto,
            r"(?:Objeto del Contrato|Objeto|Descripción)\s*[:\-]?\s*([^\n]+)",
        ),
        "valor_estimado_contrato": buscar_regex(
            texto,
            r"Valor estimado.*?([0-9\.\,]+)\s*EUR",
        ),
        "presupuesto_base_importe": buscar_regex(
            texto,
            r"Presupuesto base.*?([0-9\.\,]+)\s*EUR",
        ),
        "cpv_codes": cpv_codes,
        "num_enlaces_detectados": enlaces.shape[0],
        "error": None,
    }

    ficha["valor_estimado_contrato_num"] = convertir_importe_eur(
        ficha.get("valor_estimado_contrato")
    )
    ficha["presupuesto_base_importe_num"] = convertir_importe_eur(
        ficha.get("presupuesto_base_importe")
    )

    texto_matching = " ".join(
        [
            str(ficha.get("titulo_original") or ""),
            str(ficha.get("titulo_web") or ""),
            str(ficha.get("numero_expediente_web") or ""),
            str(ficha.get("entidad_adjudicadora_web") or ""),
            str(ficha.get("objeto_contrato_web") or ""),
            " ".join(cpv_codes),
            texto,
        ]
    )

    registro_texto = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "numero_expediente": ficha.get("numero_expediente_web"),
        "texto_matching": limitar_excel(limpiar_texto(texto_matching)),
        "texto_completo": limitar_excel(texto),
    }

    return ficha, [], [], registro_texto


def parsear_pdf_generico(
    fila: pd.Series,
    response: requests.Response,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parser base para PDF no especializado."""
    licitacion_id = fila["licitacion_id"]
    url = fila["detail_url"]
    portal = fila["portal"]

    texto, lineas, num_paginas = extraer_texto_pdf_desde_bytes(
        response.content
    )

    cpv_codes = list(dict.fromkeys(re.findall(r"\b\d{8}\b", texto)))

    ficha = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "tipo_fuente": "pdf_generico",
        "detail_url": url,
        "url_final": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "content_disposition": response.headers.get("Content-Disposition"),
        "titulo_original": fila.get("titulo", None),
        "num_paginas": num_paginas,
        "num_lineas_extraidas": len(lineas),
        "numero_expediente_web": buscar_regex(
            texto,
            r"(?:Expediente|Número de Expediente)\s*[:\-]?\s*([^\n]+)",
        ),
        "entidad_adjudicadora_web": buscar_regex(
            texto,
            r"(?:Entidad Adjudicadora|Órgano de contratación|Organo de contratación)\s*[:\-]?\s*([^\n]+)",
        ),
        "objeto_contrato_web": buscar_regex(
            texto,
            r"(?:Objeto del Contrato|Objeto|Descripción)\s*[:\-]?\s*([^\n]+)",
        ),
        "valor_estimado_contrato": buscar_regex(
            texto,
            r"Valor estimado.*?([0-9\.\,]+)\s*EUR",
        ),
        "presupuesto_base_importe": buscar_regex(
            texto,
            r"Presupuesto base.*?([0-9\.\,]+)\s*EUR",
        ),
        "cpv_codes": cpv_codes,
        "error": None,
    }

    ficha["valor_estimado_contrato_num"] = convertir_importe_eur(
        ficha.get("valor_estimado_contrato")
    )
    ficha["presupuesto_base_importe_num"] = convertir_importe_eur(
        ficha.get("presupuesto_base_importe")
    )

    texto_matching = " ".join(
        [
            str(ficha.get("titulo_original") or ""),
            str(ficha.get("numero_expediente_web") or ""),
            str(ficha.get("entidad_adjudicadora_web") or ""),
            str(ficha.get("objeto_contrato_web") or ""),
            " ".join(cpv_codes),
            texto,
        ]
    )

    registro_texto = {
        "licitacion_id": licitacion_id,
        "portal": portal,
        "numero_expediente": ficha.get("numero_expediente_web"),
        "texto_matching": limitar_excel(limpiar_texto(texto_matching)),
        "texto_completo": limitar_excel(texto),
    }

    return ficha, [], [], registro_texto


# ============================================================
# 8. ORQUESTADOR
# ============================================================

def procesar_licitacion(
    fila: pd.Series,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Procesa una licitación según portal y tipo de contenido."""
    licitacion_id = fila["licitacion_id"]
    portal = fila["portal"]
    url = fila["detail_url"]

    try:
        response = descargar_url(url)
        tipo_contenido = detectar_tipo_contenido(response)

        if response.status_code != 200:
            error = {
                "licitacion_id": licitacion_id,
                "portal": portal,
                "detail_url": url,
                "url_final": response.url,
                "status_code": response.status_code,
                "error": "Respuesta HTTP diferente de 200",
            }
            return None, [], [], None, error

        if portal == "galicia" and tipo_contenido == "html":
            ficha, documentos, registros_extra, texto_matching = parsear_galicia(
                fila,
                response,
            )

        elif portal == "madrid" and tipo_contenido == "html":
            ficha, documentos, registros_extra, texto_matching = parsear_madrid(
                fila,
                response,
            )

        elif portal == "contratacion_estado" and tipo_contenido == "pdf":
            ficha, documentos, registros_extra, texto_matching = (
                parsear_contratacion_estado_pdf(fila, response)
            )

        elif portal == "contratacion_estado" and tipo_contenido == "html":
            ficha, documentos, registros_extra, texto_matching = (
                parsear_contratacion_estado_html(fila, response)
            )

        elif tipo_contenido == "html":
            ficha, documentos, registros_extra, texto_matching = (
                parsear_html_generico(fila, response)
            )

        elif tipo_contenido == "pdf":
            ficha, documentos, registros_extra, texto_matching = (
                parsear_pdf_generico(fila, response)
            )

        else:
            ficha = {
                "licitacion_id": licitacion_id,
                "portal": portal,
                "tipo_fuente": "no_soportado",
                "detail_url": url,
                "url_final": response.url,
                "status_code": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "error": "Tipo de contenido no soportado",
            }
            documentos = []
            registros_extra = []
            texto_matching = None

        return ficha, documentos, registros_extra, texto_matching, None

    except Exception as error:
        registro_error = {
            "licitacion_id": licitacion_id,
            "portal": portal,
            "detail_url": url,
            "error": str(error),
        }
        return None, [], [], None, registro_error


def ejecutar_pipeline(muestra: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Ejecuta el proceso completo sobre una muestra de licitaciones."""
    fichas_generales = []
    documentos = []
    lotes = []
    criterios = []
    textos_matching = []
    errores = []

    for idx, fila in muestra.reset_index(drop=True).iterrows():
        print("=" * 80)
        print(f"Procesando {idx + 1}/{len(muestra)}")
        print("Portal:", fila["portal"])
        print("Licitación:", fila["licitacion_id"])
        print("URL:", fila["detail_url"])

        ficha, docs, extras, texto_matching, error = procesar_licitacion(fila)

        if ficha is not None:
            fichas_generales.append(ficha)

        if docs:
            documentos.extend(docs)

        # Contratación del Estado retorna lotes y criterios en extras.
        for registro in extras:
            if "num_lote" in registro:
                lotes.append(registro)
            elif "criterio" in registro:
                criterios.append(registro)

        if texto_matching is not None:
            textos_matching.append(texto_matching)

        if error is not None:
            errores.append(error)

        time.sleep(PAUSA_SEGUNDOS)

    tablas = {
        "muestra": muestra.copy(),
        "ficha_general": pd.DataFrame(fichas_generales),
        "documentos": pd.DataFrame(documentos),
        "lotes": pd.DataFrame(lotes),
        "criterios": pd.DataFrame(criterios),
        "textos_matching": pd.DataFrame(textos_matching),
        "errores": pd.DataFrame(errores),
    }

    return tablas


# ============================================================
# 9. EVIDENCIAS DOCUMENTALES
# ============================================================

def construir_evidencias_documentales(
    df_documentos: pd.DataFrame,
    terminos: list[str] = TERMINOS_RELEVANTES,
) -> pd.DataFrame:
    """Construye tabla de fragmentos de evidencia desde textos documentales."""
    evidencias = []

    if df_documentos.empty or "texto_extraido" not in df_documentos.columns:
        return pd.DataFrame(evidencias)

    for _, fila in df_documentos.iterrows():
        texto = fila.get("texto_extraido", None)

        if texto is None or pd.isna(texto):
            continue

        for termino in terminos:
            fragmentos = extraer_fragmentos(
                texto=texto,
                termino=termino,
                ventana=600,
            )

            for i, fragmento in enumerate(fragmentos, start=1):
                evidencias.append(
                    {
                        "licitacion_id": fila.get("licitacion_id"),
                        "portal": fila.get("portal"),
                        "nombre_documento": fila.get("nombre_documento"),
                        "url_documento": fila.get("url_documento"),
                        "ruta_archivo": fila.get("ruta_archivo"),
                        "numero_paginas": fila.get("numero_paginas"),
                        "termino": termino,
                        "numero_fragmento": i,
                        "fragmento_evidencia": fragmento,
                    }
                )

    return pd.DataFrame(evidencias)


# ============================================================
# 10. EXPORTACIÓN
# ============================================================

def construir_ficha_resumen(df_general: pd.DataFrame) -> pd.DataFrame:
    """Crea una vista resumen con columnas existentes."""
    columnas_resumen = [
        "licitacion_id",
        "portal",
        "tipo_fuente",
        "tipo_anuncio",
        "titulo_original",
        "titulo_web",
        "numero_expediente_web",
        "fecha_publicacion",
        "fecha_publicacion_texto",
        "hora_publicacion",
        "entidad_adjudicadora_web",
        "organo_contratacion_web",
        "objeto_contrato_web",
        "objeto_web",
        "descripcion_general",
        "tipo_contrato_web",
        "tipo_procedimiento_web",
        "procedimiento_adjudicacion_web",
        "tipo_tramitacion_web",
        "sistema_contratacion_web",
        "metodo_presentacion_ofertas_web",
        "codigo_cpv_web",
        "cpv_codes",
        "cpv_descripciones",
        "codigo_nuts_web",
        "lugar_ejecucion",
        "codigo_subentidad_territorial",
        "valor_estimado_num",
        "valor_estimado_contrato_num",
        "valor_estimado_sin_impuestos_num",
        "presupuesto_base_num",
        "presupuesto_base_importe_num",
        "presupuesto_base_sin_impuestos_num",
        "presupuesto_base_total_num",
        "importe_adjudicado_num",
        "num_lotes_web",
        "num_lotes_resultado",
        "fecha_limite_presentacion_web",
        "plazo_presentacion_oferta",
        "duracion_contrato_web",
        "adjudicatario_web",
        "resolucion_web",
        "detail_url",
        "url_final",
        "error",
    ]

    if df_general is None or df_general.empty:
        return pd.DataFrame()

    columnas_existentes = [
        columna for columna in columnas_resumen
        if columna in df_general.columns
    ]

    return df_general[columnas_existentes].copy()


def limpiar_nombre_archivo(valor: Any, max_len: int = 80) -> str:
    """Crea nombres seguros para archivos de salida."""
    valor = str(valor) if valor is not None else "sin_id"
    valor = re.sub(r"[^A-Za-z0-9_\-]+", "_", valor)
    valor = re.sub(r"_+", "_", valor).strip("_")
    return valor[:max_len] or "sin_id"


def agregar_campo_ficha(registros: list[dict[str, Any]], seccion: str,
                        campo: str, valor: Any) -> None:
    """Agrega una fila a la ficha vertical si el valor existe."""
    if valor is None:
        return

    try:
        if pd.isna(valor):
            return
    except (TypeError, ValueError):
        pass

    if isinstance(valor, (list, tuple, set)) and len(valor) == 0:
        return

    valor_limpio = limpiar_para_excel(valor)

    if valor_limpio in [None, "", "nan", "NaT"]:
        return

    registros.append(
        {
            "seccion": seccion,
            "campo": campo,
            "valor": valor_limpio,
        }
    )


def agregar_campos_desde_fila(registros: list[dict[str, Any]], seccion: str,
                              fila: dict[str, Any], campos: list[str]) -> None:
    """Agrega varios campos desde un diccionario/fila."""
    for campo in campos:
        if campo in fila:
            agregar_campo_ficha(registros, seccion, campo, fila.get(campo))


def crear_ficha_unica_licitacion(
    licitacion_id: Any,
    tablas: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Crea una sola ficha vertical para una licitación.

    Salida:
        DataFrame con columnas: seccion, campo, valor.
    """
    registros: list[dict[str, Any]] = []

    df_muestra = tablas.get("muestra", pd.DataFrame())
    df_general = tablas.get("ficha_general", pd.DataFrame())
    df_lotes = tablas.get("lotes", pd.DataFrame())
    df_criterios = tablas.get("criterios", pd.DataFrame())
    df_textos = tablas.get("textos_matching", pd.DataFrame())
    df_errores = tablas.get("errores", pd.DataFrame())

    def filtrar_por_id(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "licitacion_id" not in df.columns:
            return pd.DataFrame()

        return df[df["licitacion_id"].astype(str) == str(licitacion_id)].copy()

    muestra_id = filtrar_por_id(df_muestra)
    general_id = filtrar_por_id(df_general)
    lotes_id = filtrar_por_id(df_lotes)
    criterios_id = filtrar_por_id(df_criterios)
    textos_id = filtrar_por_id(df_textos)
    errores_id = filtrar_por_id(df_errores)

    if general_id.empty and muestra_id.empty:
        return pd.DataFrame(columns=["seccion", "campo", "valor"])

    fila_muestra = muestra_id.iloc[0].to_dict() if not muestra_id.empty else {}
    fila_general = general_id.iloc[0].to_dict() if not general_id.empty else {}

    # ========================================================
    # 1. Identificación
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "1. Identificación",
        {**fila_muestra, **fila_general},
        [
            "licitacion_id",
            "portal",
            "tipo_fuente",
            "tipo_anuncio",
            "numero_expediente",
            "numero_expediente_web",
            "titulo_original",
            "titulo_web",
            "detail_url",
            "url_original",
            "url_final",
            "status_code",
            "content_type",
            "error",
        ],
    )

    # ========================================================
    # 2. Entidad adjudicadora
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "2. Entidad adjudicadora",
        fila_general,
        [
            "entidad_adjudicadora",
            "entidad_adjudicadora_web",
            "organo_contratacion_web",
            "tipo_administracion",
            "actividad_principal",
            "tipo_entidad_adjudicadora",
            "perfil_contratante",
            "telefono_entidad",
            "fax_entidad",
            "email_entidad",
        ],
    )

    # ========================================================
    # 3. Objeto contractual
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "3. Objeto contractual",
        fila_general,
        [
            "objeto_contrato",
            "objeto_contrato_web",
            "objeto_web",
            "descripcion_general",
            "tipo_contrato_web",
            "codigo_cpv_web",
            "cpv_codes",
            "cpv_descripciones",
            "codigo_nuts_web",
            "lugar_ejecucion",
            "codigo_subentidad_territorial",
        ],
    )

    # ========================================================
    # 4. Importes
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "4. Importes",
        fila_general,
        [
            "valor_estimado",
            "valor_estimado_num",
            "valor_estimado_contrato",
            "valor_estimado_contrato_num",
            "valor_estimado_sin_impuestos",
            "valor_estimado_sin_impuestos_num",
            "presupuesto_base",
            "presupuesto_base_num",
            "presupuesto_base_importe",
            "presupuesto_base_importe_num",
            "presupuesto_base_sin_impuestos",
            "presupuesto_base_sin_impuestos_num",
            "presupuesto_base_total",
            "presupuesto_base_total_num",
            "importe_adjudicado",
            "importe_adjudicado_num",
        ],
    )

    # ========================================================
    # 5. Fechas y plazos
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "5. Fechas y plazos",
        fila_general,
        [
            "fecha_publicacion",
            "fecha_publicacion_texto",
            "hora_publicacion",
            "plazo_ejecucion_inicio",
            "plazo_ejecucion_fin",
            "plazo_obtencion_pliegos",
            "plazo_presentacion_oferta",
            "fecha_limite_presentacion_web",
            "fecha_apertura_oferta",
            "hora_apertura_oferta",
            "duracion_contrato_web",
        ],
    )

    # ========================================================
    # 6. Procedimiento
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "6. Procedimiento",
        fila_general,
        [
            "procedimiento",
            "tipo_procedimiento_web",
            "procedimiento_adjudicacion_web",
            "tramitacion",
            "tipo_tramitacion_web",
            "sistema_contratacion_web",
            "metodo_presentacion_ofertas_web",
            "tramitacion_gasto",
            "sistema_contratacion",
            "presentacion_oferta",
            "tipo_acto_apertura",
            "contrato_sujeto_regulacion_armonizada",
            "directiva_aplicacion",
            "programas_financiacion",
            "resolucion_web",
        ],
    )

    # ========================================================
    # 7. Lotes
    # ========================================================
    agregar_campos_desde_fila(
        registros,
        "7. Resumen lotes",
        fila_general,
        [
            "num_lotes",
            "num_lotes_web",
            "num_lotes_resultado",
            "se_debe_ofertar",
            "max_lotes_presentacion",
            "max_lotes_adjudicacion",
        ],
    )

    if not lotes_id.empty:
        for _, lote in lotes_id.iterrows():
            fila_lote = lote.to_dict()
            num_lote = fila_lote.get("num_lote", "sin_numero")
            seccion = f"8. Lote {num_lote}"

            agregar_campos_desde_fila(
                registros,
                seccion,
                fila_lote,
                [
                    "objeto_lote",
                    "descripcion_lote",
                    "valor_estimado_lote",
                    "valor_estimado_lote_num",
                    "presupuesto_base_lote",
                    "presupuesto_base_lote_num",
                    "presupuesto_base_lote_sin_impuestos",
                    "presupuesto_base_lote_sin_impuestos_num",
                    "lugar_ejecucion_lote",
                    "codigo_subentidad_lote",
                    "estado_lote",
                    "precio_oferta_mas_baja",
                    "precio_oferta_mas_baja_num",
                    "precio_oferta_mas_alta",
                    "precio_oferta_mas_alta_num",
                    "num_ofertas_pymes",
                    "adjudicatario",
                    "adjudicatario_web",
                    "nif_adjudicatario",
                    "adjudicatario_pyme",
                    "direccion_adjudicatario",
                    "telefono_adjudicatario",
                    "email_adjudicatario",
                    "importe_adjudicacion_sin_impuestos",
                    "importe_adjudicacion_sin_impuestos_num",
                    "importe_adjudicacion_con_impuestos",
                    "importe_adjudicacion_con_impuestos_num",
                    "numero_contrato",
                    "fecha_formalizacion",
                    "fecha_entrada_vigor",
                    "fecha_acuerdo_adjudicacion",
                    "documento_contrato",
                    "motivacion",
                ],
            )

    # ========================================================
    # 9. Criterios
    # ========================================================
    if not criterios_id.empty:
        for idx, criterio in criterios_id.reset_index(drop=True).iterrows():
            fila_criterio = criterio.to_dict()
            seccion = f"9. Criterio {idx + 1}"

            agregar_campos_desde_fila(
                registros,
                seccion,
                fila_criterio,
                [
                    "criterio",
                    "subtipo_criterio",
                    "ponderacion",
                    "expresion_evaluacion",
                    "cantidad_minima",
                    "cantidad_maxima",
                ],
            )

    # ========================================================
    # 10. Texto para matching
    # ========================================================
    if not textos_id.empty:
        fila_texto = textos_id.iloc[0].to_dict()
        agregar_campos_desde_fila(
            registros,
            "10. Texto para matching",
            fila_texto,
            [
                "texto_matching",
                "texto_completo",
            ],
        )

    # ========================================================
    # 11. Errores
    # ========================================================
    if not errores_id.empty:
        for _, error in errores_id.iterrows():
            fila_error = error.to_dict()
            agregar_campos_desde_fila(
                registros,
                "11. Errores",
                fila_error,
                ["error", "status_code", "url_original", "url_final"],
            )

    if not registros:
        return pd.DataFrame(columns=["seccion", "campo", "valor"])

    return pd.DataFrame(registros)


def exportar_excels_individuales_unicos(tablas: dict[str, pd.DataFrame]) -> None:
    """
    Exporta un Excel por licitación con una única hoja: 'Ficha'.
    No exporta múltiples hojas por licitación.
    """
    RUTA_EXCEL_INDIVIDUALES.mkdir(parents=True, exist_ok=True)

    df_muestra = tablas.get("muestra", pd.DataFrame())
    df_general = tablas.get("ficha_general", pd.DataFrame())

    if df_muestra is not None and not df_muestra.empty:
        ids = df_muestra[["licitacion_id", "portal"]].drop_duplicates()
    elif df_general is not None and not df_general.empty:
        ids = df_general[["licitacion_id", "portal"]].drop_duplicates()
    else:
        print("No hay licitaciones para exportar.")
        return

    for _, fila_id in ids.iterrows():
        licitacion_id = fila_id.get("licitacion_id")
        portal = fila_id.get("portal", "portal")

        id_seguro = limpiar_nombre_archivo(licitacion_id)
        portal_seguro = limpiar_nombre_archivo(portal)

        ruta_excel = (
            RUTA_EXCEL_INDIVIDUALES
            / f"ficha_{portal_seguro}_{id_seguro}.xlsx"
        )

        df_ficha = crear_ficha_unica_licitacion(
            licitacion_id=licitacion_id,
            tablas=tablas,
        )

        df_ficha = preparar_para_excel(df_ficha)

        with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
            df_ficha.to_excel(writer, sheet_name="Ficha", index=False)

        print("Ficha exportada:", ruta_excel)


def sanitizar_para_json(valor: Any) -> Any:
    """
    Convierte valores de pandas/numpy a tipos serializables en JSON.
    """
    if valor is None:
        return None

    if isinstance(valor, float) and pd.isna(valor):
        return None

    if pd.isna(valor) if not isinstance(valor, (list, dict, tuple, set)) else False:
        return None

    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()

    if isinstance(valor, (list, tuple, set)):
        return [sanitizar_para_json(x) for x in valor]

    if isinstance(valor, dict):
        return {str(k): sanitizar_para_json(v) for k, v in valor.items()}

    return valor


def dataframe_a_json_por_licitacion(
    df: pd.DataFrame,
    licitacion_id: str,
) -> str | None:
    """
    Convierte los registros 1:N asociados a una licitación en JSON.

    Ejemplo de uso:
        - lotes asociados a una licitación
        - documentos detectados
        - criterios detectados
        - errores detectados
    """
    if df is None or df.empty:
        return None

    if "licitacion_id" not in df.columns:
        return None

    df_filtrado = df[df["licitacion_id"] == licitacion_id].copy()

    if df_filtrado.empty:
        return None

    registros = []

    for registro in df_filtrado.to_dict(orient="records"):
        registros.append({
            str(k): sanitizar_para_json(v)
            for k, v in registro.items()
        })

    return json.dumps(
        registros,
        ensure_ascii=False,
        default=str,
    )


def contar_registros_por_licitacion(
    df: pd.DataFrame,
    licitacion_id: str,
) -> int:
    """
    Cuenta registros asociados a una licitación en una tabla 1:N.
    """
    if df is None or df.empty:
        return 0

    if "licitacion_id" not in df.columns:
        return 0

    return int((df["licitacion_id"] == licitacion_id).sum())


def convertir_columnas_complejas_a_json(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte listas/diccionarios a JSON string para que el Parquet final sea
    estable y fácil de leer posteriormente.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    df_final = df.copy()

    for columna in df_final.columns:
        if df_final[columna].map(lambda x: isinstance(x, (list, dict, tuple, set))).any():
            df_final[columna] = df_final[columna].apply(
                lambda x: json.dumps(
                    sanitizar_para_json(x),
                    ensure_ascii=False,
                    default=str,
                ) if isinstance(x, (list, dict, tuple, set)) else x
            )

    return df_final


def construir_parquet_unificado(tablas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Construye el producto final de la capa gold.

    Estructura:
        - 1 fila = 1 licitación
        - columnas principales homologadas desde ficha_resumen
        - texto_matching incorporado
        - lotes/documentos/criterios/errores en JSON por licitación

    Esta salida es la que se usará para la siguiente fase de matching.
    """
    df_general = tablas.get("ficha_general", pd.DataFrame())
    df_documentos = tablas.get("documentos", pd.DataFrame())
    df_lotes = tablas.get("lotes", pd.DataFrame())
    df_criterios = tablas.get("criterios", pd.DataFrame())
    df_textos = tablas.get("textos_matching", pd.DataFrame())
    df_errores = tablas.get("errores", pd.DataFrame())

    if df_general is None or df_general.empty:
        raise ValueError("No hay ficha_general para construir el parquet final.")

    df_resumen = construir_ficha_resumen(df_general)

    if df_resumen.empty:
        raise ValueError("No se pudo construir ficha_resumen.")

    df_final = df_resumen.copy()

    # Incorporar texto de matching.
    if df_textos is not None and not df_textos.empty:
        columnas_texto = [
            col for col in [
                "licitacion_id",
                "texto_matching",
                "texto_completo",
            ]
            if col in df_textos.columns
        ]

        df_final = df_final.merge(
            df_textos[columnas_texto].drop_duplicates("licitacion_id"),
            on="licitacion_id",
            how="left",
        )

    # Incorporar relaciones 1:N como JSON.
    df_final["lotes_json"] = df_final["licitacion_id"].apply(
        lambda x: dataframe_a_json_por_licitacion(df_lotes, x)
    )

    df_final["documentos_json"] = df_final["licitacion_id"].apply(
        lambda x: dataframe_a_json_por_licitacion(df_documentos, x)
    )

    df_final["criterios_json"] = df_final["licitacion_id"].apply(
        lambda x: dataframe_a_json_por_licitacion(df_criterios, x)
    )

    df_final["errores_json"] = df_final["licitacion_id"].apply(
        lambda x: dataframe_a_json_por_licitacion(df_errores, x)
    )

    # Indicadores de control.
    df_final["num_lotes_detectados"] = df_final["licitacion_id"].apply(
        lambda x: contar_registros_por_licitacion(df_lotes, x)
    )

    df_final["num_documentos_detectados"] = df_final["licitacion_id"].apply(
        lambda x: contar_registros_por_licitacion(df_documentos, x)
    )

    df_final["num_criterios_detectados"] = df_final["licitacion_id"].apply(
        lambda x: contar_registros_por_licitacion(df_criterios, x)
    )

    df_final["num_errores_detectados"] = df_final["licitacion_id"].apply(
        lambda x: contar_registros_por_licitacion(df_errores, x)
    )

    df_final["tiene_lotes"] = df_final["num_lotes_detectados"] > 0
    df_final["tiene_documentos_detectados"] = df_final["num_documentos_detectados"] > 0
    df_final["tiene_criterios"] = df_final["num_criterios_detectados"] > 0
    df_final["tiene_error"] = df_final["num_errores_detectados"] > 0

    df_final = convertir_columnas_complejas_a_json(df_final)

    return df_final


def exportar_resultados(tablas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Exporta un único Parquet final en la capa gold.

    No genera Excel individuales ni Parquet intermedios.
    """
    # Se mantiene la construcción de evidencias en memoria por trazabilidad,
    # pero no se exporta como fichero separado.
    df_evidencias = construir_evidencias_documentales(tablas["documentos"])
    tablas["evidencias"] = df_evidencias

    df_final = construir_parquet_unificado(tablas)

    df_final.to_parquet(
        RUTA_PARQUET_FINAL,
        index=False,
    )

    print("Parquet final unificado generado en:")
    print(RUTA_PARQUET_FINAL)

    print("\nDimensión del parquet final:")
    print(df_final.shape)

    print("\nDistribución por portal:")
    print(df_final["portal"].value_counts(dropna=False).to_string())

    print("\nDistribución por tipo_fuente:")
    print(df_final["tipo_fuente"].value_counts(dropna=False).to_string())

    return df_final


# ============================================================
# 11. EJECUCIÓN PRINCIPAL
# ============================================================

if RUTA_MUESTRA_VALIDADA.exists():
    print("Cargando muestra validada existente:")
    print(RUTA_MUESTRA_VALIDADA)
    muestra = pd.read_parquet(RUTA_MUESTRA_VALIDADA)

    if "portal" not in muestra.columns:
        muestra["portal"] = muestra["detail_url"].apply(detectar_portal)
else:
    print("No existe muestra validada. Se genera desde df_tilos_limpio.parquet.")
    df = cargar_datos_base(RUTA_INPUT)
    muestra = generar_muestra_estratificada(
        df=df,
        distribucion=DISTRIBUCION_MUESTRA,
        random_state=RANDOM_STATE,
    )
    muestra.to_parquet(RUTA_MUESTRA_VALIDADA, index=False)

print("Distribución de la muestra:")
print(muestra["portal"].value_counts(dropna=False).to_string())

tablas_resultado = ejecutar_pipeline(muestra)

df_fichas_unificadas = exportar_resultados(tablas_resultado)

print("\nColumnas del parquet final:")
print(list(df_fichas_unificadas.columns))

print("\nVista previa del parquet final:")
columnas_vista = [
    col for col in [
        "licitacion_id",
        "portal",
        "tipo_fuente",
        "titulo_original",
        "objeto_web",
        "valor_estimado_num",
        "cpv_codes",
        "num_lotes_detectados",
        "num_documentos_detectados",
        "tiene_error",
    ]
    if col in df_fichas_unificadas.columns
]
print(df_fichas_unificadas[columnas_vista].to_string())
