# ============================================================
# EXTRACCIÓN CASTILLA Y LEÓN - HTML + DOCUMENTOS ADJUNTOS - V2
#
# Entrada:
#   data/Silver/muestra_20_segovia_18_madrid_2_restantes.parquet
#
# Proceso:
#   1. Carga la muestra desde Silver.
#   2. Filtra registros del dominio contratacion.jcyl.es.
#   3. Extrae la información HTML principal desde detail_url.
#   4. Detecta descargas adjuntas.
#   5. Descarga adjuntos en:
#        data/Silver/documents_castillo_leon/<licitacion_id>/
#   6. Guarda parquets en Silver:
#        castilla_leon_html_info.parquet
#        castilla_leon_documentos_descargados.parquet
#
# Nota:
#   No analiza ni extrae texto de los PDFs/adjuntos.
#   Solo descarga los archivos y guarda una tabla de trazabilidad.
# ============================================================

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path.cwd()

RUTA_SILVER = PROJECT_ROOT / "data" / "Silver"
RUTA_INPUT = RUTA_SILVER / "muestra_20_segovia_18_madrid_2_restantes.parquet"

# Se mantiene el nombre solicitado por el usuario.
RUTA_DOCUMENTOS = RUTA_SILVER / "documents_castillo_leon"

RUTA_SALIDA_HTML = RUTA_SILVER / "castilla_leon_html_info.parquet"
RUTA_SALIDA_DOCUMENTOS = RUTA_SILVER / "castilla_leon_documentos_descargados.parquet"

RUTA_DOCUMENTOS.mkdir(parents=True, exist_ok=True)

TIMEOUT = 30
PAUSA_SEGUNDOS = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


# ============================================================
# 2. FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(valor: Any) -> str | None:
    """Limpia espacios, saltos de línea y valores vacíos."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass

    texto = str(valor)
    texto = re.sub(r"\s+", " ", texto).strip()

    if texto == "":
        return None

    return texto


def limpiar_nombre_archivo(texto: Any, max_len: int = 90) -> str:
    """Crea un nombre seguro para archivos."""
    texto = limpiar_texto(texto) or "documento"
    texto = texto.replace("Descargar", "")
    texto = texto.replace("descargar", "")
    texto = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚáéíóúÑñ_\- ]+", "", texto)
    texto = re.sub(r"\s+", "_", texto).strip("_")

    if texto == "":
        texto = "documento"

    return texto[:max_len]


def limpiar_id_carpeta(valor: Any, max_len: int = 90) -> str:
    """Crea un nombre seguro de carpeta a partir del licitacion_id."""
    texto = str(valor) if valor is not None else "sin_id"
    texto = re.sub(r"[^A-Za-z0-9_\-]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")

    if texto == "":
        texto = "sin_id"

    return texto[:max_len]


def descargar_url(url: str) -> requests.Response:
    """Descarga una URL usando headers de navegador."""
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    return response


def detectar_tipo_contenido(response: requests.Response) -> str:
    """Clasifica la respuesta principal."""
    content_type = str(response.headers.get("content-type", "")).lower()

    if response.content.startswith(b"%PDF") or "application/pdf" in content_type:
        return "pdf_directo"

    if "text/html" in content_type or "<html" in response.text.lower():
        return "html"

    if "xml" in content_type or response.content.startswith(b"<?xml"):
        return "xml"

    return "otro"


def detectar_extension(contenido: bytes, content_type: str | None, url: str) -> tuple[str, str]:
    """Detecta extensión y tipo del archivo descargado."""
    content_type = str(content_type).lower()
    url = str(url).lower()

    if contenido.startswith(b"%PDF") or "application/pdf" in content_type or ".pdf" in url:
        return ".pdf", "PDF"

    if contenido.startswith(b"PK"):
        return ".zip", "ZIP_O_OFFICE"

    if contenido.startswith(b"<?xml") or "xml" in content_type:
        return ".xml", "XML"

    if "text/html" in content_type:
        return ".html", "HTML"

    return ".bin", "DESCONOCIDO"


def texto_tag(tag: Tag) -> str | None:
    """Extrae texto de un tag HTML."""
    texto = tag.get_text("\n", strip=True)
    texto = re.sub(r"\n{2,}", "\n", texto).strip()
    return texto if texto else None


def valor_despues_de_encabezado(soup: BeautifulSoup, etiqueta: str) -> str | None:
    """
    Busca h2/h3/h4 con una etiqueta y toma el texto posterior hasta el siguiente encabezado.
    """
    etiqueta_norm = etiqueta.lower().strip().rstrip(":")

    for tag in soup.find_all(["h2", "h3", "h4"]):
        texto_encabezado = limpiar_texto(tag.get_text(" ", strip=True))

        if texto_encabezado is None:
            continue

        texto_encabezado_norm = texto_encabezado.lower().strip().rstrip(":")

        if texto_encabezado_norm != etiqueta_norm:
            continue

        textos = []

        for sibling in tag.find_next_siblings():
            if isinstance(sibling, Tag) and sibling.name in ["h2", "h3", "h4"]:
                break

            if not isinstance(sibling, Tag):
                continue

            texto = texto_tag(sibling)

            if texto:
                textos.append(texto)

        if textos:
            return "\n".join(textos).strip()

    return None


def extraer_bloque_encabezado(soup: BeautifulSoup, etiqueta: str) -> str | None:
    """Alias semántico para bloques largos por encabezado."""
    return valor_despues_de_encabezado(soup, etiqueta)


def extraer_texto_principal(soup: BeautifulSoup) -> str | None:
    """
    Extrae texto desde el título h1 hasta antes de Mapa Web o Pie de página.
    Evita gran parte del menú superior.
    """
    h1 = soup.find("h1")

    if h1 is None:
        texto = soup.get_text("\n", strip=True)
        return re.sub(r"\n{2,}", "\n", texto).strip()

    textos = [h1.get_text(" ", strip=True)]

    for sibling in h1.find_next_siblings():
        if not isinstance(sibling, Tag):
            continue

        texto_sibling = limpiar_texto(sibling.get_text(" ", strip=True))

        if texto_sibling in ["Mapa Web", "Pie de página"]:
            break

        if sibling.name in ["h2", "h3", "h4"] and texto_sibling in ["Mapa Web", "Pie de página"]:
            break

        texto = texto_tag(sibling)

        if texto:
            textos.append(texto)

    texto_principal = "\n".join(textos).strip()
    texto_principal = re.sub(r"\n{2,}", "\n", texto_principal)

    return texto_principal


def extraer_descargas(soup: BeautifulSoup, url_base: str, licitacion_id: Any) -> list[dict[str, Any]]:
    """
    Extrae enlaces de documentos descargables en Castilla y León.

    Corrección v2:
    El portal puede mostrar el encabezado "Descargas", pero los enlaces no
    siempre quedan como hermanos directos del h3/h4. Por eso se detectan
    globalmente todos los enlaces cuyo texto contenga "Descargar", excluyendo
    enlaces de navegación como "Ir a descargas".
    """
    registros = []

    for enlace in soup.find_all("a", href=True):
        texto_enlace = limpiar_texto(enlace.get_text(" ", strip=True))
        href = enlace.get("href")
        url_documento = urljoin(url_base, href)

        if not texto_enlace:
            continue

        texto_lower = texto_enlace.lower()

        # Excluir navegación/anclas internas.
        if "ir a descargas" in texto_lower:
            continue

        # Adjuntos reales del portal: normalmente contienen "Descargar".
        if "descargar" not in texto_lower:
            continue

        nombre_documento = texto_enlace
        nombre_documento = re.sub(
            r"\b[Dd]escargar\b",
            "",
            nombre_documento,
        )
        nombre_documento = limpiar_texto(nombre_documento) or texto_enlace

        registros.append(
            {
                "licitacion_id": licitacion_id,
                "seccion": "Descargas",
                "nombre_documento": nombre_documento,
                "texto_enlace": texto_enlace,
                "href": href,
                "url_documento": url_documento,
            }
        )

    # Quitar duplicados por URL.
    registros_unicos = []
    urls_vistas = set()

    for registro in registros:
        url_documento = registro["url_documento"]

        if url_documento in urls_vistas:
            continue

        registros_unicos.append(registro)
        urls_vistas.add(url_documento)

    return registros_unicos

# ============================================================
# 3. EXTRACCIÓN CASTILLA Y LEÓN
# ============================================================

def extraer_castilla_leon_html_y_descargas(fila: pd.Series) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extrae una licitación de Castilla y León desde detail_url."""
    licitacion_id = fila["licitacion_id"]
    detail_url = fila["detail_url"]

    registro = {
        "licitacion_id": licitacion_id,
        "portal": "castilla_leon",
        "detail_url": detail_url,
        "url_response": None,
        "status_code": None,
        "content_type": None,
        "tipo_contenido": None,
        "titulo_html": None,
        "titulo_licitacion": None,
        "estado_licitacion": None,
        "organismo_promotor": None,
        "descripcion": None,
        "numero_expediente": None,
        "tipo_contrato": None,
        "tipo_tramitacion": None,
        "procedimiento_adjudicacion": None,
        "forma_adjudicacion": None,
        "valor_estimado": None,
        "importe_licitacion_sin_iva": None,
        "cpv": None,
        "publicidad_perfil_contratante": None,
        "publicidad_boletines": None,
        "lotes": None,
        "datos_presentacion_ofertas": None,
        "apertura_criterios_no_evaluables": None,
        "texto_principal_html": None,
        "n_descargas_detectadas": 0,
        "error": None,
    }

    descargas = []

    try:
        response = descargar_url(detail_url)

        registro["url_response"] = response.url
        registro["status_code"] = response.status_code
        registro["content_type"] = response.headers.get("content-type")
        registro["tipo_contenido"] = detectar_tipo_contenido(response)

        if response.status_code != 200:
            registro["error"] = f"Status code diferente de 200: {response.status_code}"
            return registro, descargas

        if registro["tipo_contenido"] != "html":
            registro["error"] = f"Tipo de contenido no HTML: {registro['tipo_contenido']}"
            return registro, descargas

        soup = BeautifulSoup(response.text, "html.parser")

        registro["titulo_html"] = (
            soup.title.get_text(" ", strip=True)
            if soup.title
            else None
        )

        h1 = soup.find("h1")
        registro["titulo_licitacion"] = (
            limpiar_texto(h1.get_text(" ", strip=True))
            if h1
            else None
        )

        registro["estado_licitacion"] = valor_despues_de_encabezado(soup, "Estado de la licitación")
        registro["organismo_promotor"] = valor_despues_de_encabezado(soup, "Organismo que lo promueve")
        registro["descripcion"] = valor_despues_de_encabezado(soup, "Descripción")
        registro["numero_expediente"] = valor_despues_de_encabezado(soup, "Número de expediente")
        registro["tipo_contrato"] = valor_despues_de_encabezado(soup, "Tipo de contrato")
        registro["tipo_tramitacion"] = valor_despues_de_encabezado(soup, "Tipo de tramitación")
        registro["procedimiento_adjudicacion"] = valor_despues_de_encabezado(soup, "Procedimiento de adjudicación")
        registro["forma_adjudicacion"] = valor_despues_de_encabezado(soup, "Forma de adjudicación")
        registro["valor_estimado"] = valor_despues_de_encabezado(soup, "Valor estimado")
        registro["importe_licitacion_sin_iva"] = valor_despues_de_encabezado(soup, "Importe de la licitación sin IVA")
        registro["cpv"] = valor_despues_de_encabezado(soup, "CPV")
        registro["publicidad_perfil_contratante"] = extraer_bloque_encabezado(soup, "Publicidad en el perfil del contratante")
        registro["publicidad_boletines"] = extraer_bloque_encabezado(soup, "Publicidad en boletines")
        registro["lotes"] = extraer_bloque_encabezado(soup, "Lotes")
        registro["datos_presentacion_ofertas"] = extraer_bloque_encabezado(soup, "Datos para la presentación de ofertas")
        registro["apertura_criterios_no_evaluables"] = extraer_bloque_encabezado(
            soup,
            "Apertura de los criterios no evaluables mediante fórmulas (sobre 2)",
        )
        registro["texto_principal_html"] = extraer_texto_principal(soup)

        descargas = extraer_descargas(
            soup=soup,
            url_base=response.url,
            licitacion_id=licitacion_id,
        )

        registro["n_descargas_detectadas"] = len(descargas)

    except Exception as exc:
        registro["error"] = str(exc)

    return registro, descargas


# ============================================================
# 4. DESCARGA DE ADJUNTOS
# ============================================================

def descargar_adjuntos_licitacion(
    licitacion_id: Any,
    descargas: list[dict[str, Any]],
    carpeta_base: Path = RUTA_DOCUMENTOS,
) -> list[dict[str, Any]]:
    """Descarga adjuntos en carpeta por licitación."""
    registros = []

    carpeta_licitacion = carpeta_base / limpiar_id_carpeta(licitacion_id)
    carpeta_licitacion.mkdir(parents=True, exist_ok=True)

    for i, descarga in enumerate(descargas, start=1):
        url_documento = descarga.get("url_documento")
        nombre_documento = descarga.get("nombre_documento") or f"documento_{i:02d}"

        registro = {
            **descarga,
            "num_documento": i,
            "estado_descarga": None,
            "status_code_descarga": None,
            "content_type_descarga": None,
            "tipo_archivo_detectado": None,
            "tamano_bytes": None,
            "ruta_archivo": None,
            "error_descarga": None,
        }

        try:
            response_doc = descargar_url(url_documento)

            registro["status_code_descarga"] = response_doc.status_code
            registro["content_type_descarga"] = response_doc.headers.get("content-type")
            registro["tamano_bytes"] = len(response_doc.content)

            if response_doc.status_code != 200:
                registro["estado_descarga"] = "ERROR"
                registro["error_descarga"] = f"Status code diferente de 200: {response_doc.status_code}"
                registros.append(registro)
                continue

            extension, tipo_archivo = detectar_extension(
                contenido=response_doc.content,
                content_type=response_doc.headers.get("content-type"),
                url=url_documento,
            )

            registro["tipo_archivo_detectado"] = tipo_archivo

            nombre_archivo = (
                f"{i:02d}_"
                f"{limpiar_nombre_archivo(nombre_documento)}"
                f"{extension}"
            )

            ruta_archivo = carpeta_licitacion / nombre_archivo

            with open(ruta_archivo, "wb") as archivo:
                archivo.write(response_doc.content)

            registro["estado_descarga"] = "OK"
            registro["ruta_archivo"] = str(ruta_archivo)

        except Exception as exc:
            registro["estado_descarga"] = "ERROR"
            registro["error_descarga"] = str(exc)

        registros.append(registro)
        time.sleep(PAUSA_SEGUNDOS)

    return registros


# ============================================================
# 5. ORQUESTACIÓN
# ============================================================

def cargar_muestra() -> pd.DataFrame:
    """Carga la muestra desde Silver."""
    if not RUTA_INPUT.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {RUTA_INPUT}")

    df = pd.read_parquet(RUTA_INPUT)

    print("Archivo cargado:", RUTA_INPUT)
    print("Shape muestra:", df.shape)

    return df


def filtrar_castilla_leon(df_muestra: pd.DataFrame) -> pd.DataFrame:
    """Filtra registros del dominio contratacion.jcyl.es."""
    columnas_requeridas = ["dominio_url", "detail_url", "licitacion_id"]

    for columna in columnas_requeridas:
        if columna not in df_muestra.columns:
            raise KeyError(f"No existe la columna requerida: {columna}")

    df_castilla = df_muestra[
        df_muestra["dominio_url"]
        .astype(str)
        .str.contains("contratacion.jcyl.es", case=False, na=False)
    ].copy()

    df_castilla = df_castilla.reset_index(drop=True)

    print("Registros Castilla y León:", df_castilla.shape)

    return df_castilla


def ejecutar_extraccion(df_castilla: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ejecuta extracción HTML y descarga de adjuntos."""
    registros_html = []
    registros_documentos = []

    for i, fila in df_castilla.reset_index(drop=True).iterrows():
        print("=" * 100)
        print(f"Procesando Castilla y León {i + 1}/{len(df_castilla)}")
        print("Licitación:", fila.get("licitacion_id"))
        print("URL:", fila.get("detail_url"))

        registro_html, descargas = extraer_castilla_leon_html_y_descargas(fila)
        registros_html.append(registro_html)

        print("Status:", registro_html.get("status_code"))
        print("Tipo contenido:", registro_html.get("tipo_contenido"))
        print("Descargas detectadas:", len(descargas))
        print("Error HTML:", registro_html.get("error"))

        if descargas:
            documentos = descargar_adjuntos_licitacion(
                licitacion_id=fila.get("licitacion_id"),
                descargas=descargas,
            )
            registros_documentos.extend(documentos)

            n_ok = sum(1 for doc in documentos if doc.get("estado_descarga") == "OK")
            n_error = sum(1 for doc in documentos if doc.get("estado_descarga") == "ERROR")

            print("Documentos descargados OK:", n_ok)
            print("Documentos con error:", n_error)

        time.sleep(PAUSA_SEGUNDOS)

    df_html = pd.DataFrame(registros_html)
    df_documentos = pd.DataFrame(registros_documentos)

    return df_html, df_documentos


def exportar_resultados(df_html: pd.DataFrame, df_documentos: pd.DataFrame) -> None:
    """Exporta resultados a Silver."""
    df_html.to_parquet(RUTA_SALIDA_HTML, index=False)
    df_documentos.to_parquet(RUTA_SALIDA_DOCUMENTOS, index=False)

    print("=" * 100)
    print("Parquet HTML guardado en:")
    print(RUTA_SALIDA_HTML)
    print("Shape HTML:", df_html.shape)

    print("\nParquet documentos guardado en:")
    print(RUTA_SALIDA_DOCUMENTOS)
    print("Shape documentos:", df_documentos.shape)

    print("\nCarpeta de documentos:")
    print(RUTA_DOCUMENTOS)

    if not df_html.empty:
        print("\nResumen HTML:")
        columnas_resumen = [
            "licitacion_id",
            "status_code",
            "tipo_contenido",
            "titulo_licitacion",
            "n_descargas_detectadas",
            "error",
        ]
        columnas_resumen = [col for col in columnas_resumen if col in df_html.columns]
        print(df_html[columnas_resumen].to_string(index=False))

    if not df_documentos.empty:
        print("\nResumen descargas:")
        print(df_documentos["estado_descarga"].value_counts(dropna=False).to_string())

        print("\nTipos de archivo:")
        print(df_documentos["tipo_archivo_detectado"].value_counts(dropna=False).to_string())


def main() -> None:
    """Función principal."""
    df_muestra = cargar_muestra()
    df_castilla = filtrar_castilla_leon(df_muestra)

    if df_castilla.empty:
        raise ValueError("No hay registros de Castilla y León para procesar.")

    df_html, df_documentos = ejecutar_extraccion(df_castilla)
    exportar_resultados(df_html, df_documentos)

    print("=" * 100)
    print("Proceso terminado.")


if __name__ == "__main__":
    main()
