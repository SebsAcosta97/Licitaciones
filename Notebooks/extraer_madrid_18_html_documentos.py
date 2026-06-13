# ============================================================
# EXTRACCIÓN MADRID - HTML ORGANIZADO + DOCUMENTOS PDF
#
# Entrada:
#   data/Silver/muestra_20_segovia*.parquet
#
# Proceso:
#   1. Carga la muestra de 20 licitaciones.
#   2. Filtra licitaciones del portal Comunidad de Madrid.
#   3. Extrae información HTML organizada por secciones.
#   4. Detecta documentos adjuntos por sección.
#   5. Descarga documentos en:
#        data/Silver/documentos_madrid/<licitacion_id>/
#   6. Guarda salidas en Silver:
#        madrid_18_html_secciones.parquet
#        madrid_18_documentos_descargados.parquet
#
# Nota:
#   No analiza ni extrae texto de los PDFs. Solo los descarga.
# ============================================================

from __future__ import annotations

import json
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
RUTA_DOCUMENTOS_MADRID = RUTA_SILVER / "documentos_madrid"

PATRON_MUESTRA = "muestra_20_segovia*.parquet"

RUTA_SALIDA_HTML = RUTA_SILVER / "madrid_18_html_secciones.parquet"
RUTA_SALIDA_DOCUMENTOS = RUTA_SILVER / "madrid_18_documentos_descargados.parquet"

RUTA_DOCUMENTOS_MADRID.mkdir(parents=True, exist_ok=True)

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

SECCIONES_MADRID = [
    "Datos del expediente",
    "División en lotes",
    "Preparación del contrato",
    "Convocatoria",
    "Pliegos de condiciones",
    "Información adicional y puntos de contacto",
    "Licitadores, mesas de contratación e informes",
    "Tablón de anuncios electrónico",
    "Resultados de la licitación",
    "Ejecución del contrato",
    "Otra información",
]

PALABRAS_DOCUMENTOS = [
    "descargar",
    "pdf",
    "pliego",
    "prescripciones",
    "cláusulas",
    "clausulas",
    "memoria",
    "informe",
    "anuncio",
    "contrato",
    "resolución",
    "resolucion",
    "adjudicación",
    "adjudicacion",
    "formalización",
    "formalizacion",
    "documento",
    "acta",
    "mesa",
    "ofertas",
    "licitación",
    "licitacion",
]


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


def normalizar_nombre_columna(texto: str) -> str:
    """Convierte el nombre de una sección en nombre de columna."""
    texto = texto.lower()
    reemplazos = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
    }

    for origen, destino in reemplazos.items():
        texto = texto.replace(origen, destino)

    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")

    return texto


def limpiar_nombre_archivo(texto: Any, max_len: int = 90) -> str:
    """Crea nombres seguros para guardar archivos en disco."""
    texto = limpiar_texto(texto) or "documento"
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
    """Descarga una URL con headers de navegador."""
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    return response


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

    if "text/html" in content_type or contenido[:100].lower().startswith(b"<!doctype"):
        return ".html", "HTML"

    return ".bin", "DESCONOCIDO"


def texto_de_tag(tag: Tag) -> str | None:
    """Extrae texto de un tag conservando saltos de línea razonables."""
    texto = tag.get_text("\n", strip=True)
    texto = re.sub(r"\n{2,}", "\n", texto).strip()
    return texto if texto else None


def extraer_texto_seccion(encabezado: Tag) -> str | None:
    """Extrae texto desde un encabezado hasta el siguiente h2/h3."""
    textos = []

    for sibling in encabezado.find_next_siblings():
        if isinstance(sibling, Tag) and sibling.name in ["h2", "h3"]:
            break

        if not isinstance(sibling, Tag):
            continue

        texto = texto_de_tag(sibling)

        if texto:
            textos.append(texto)

    if not textos:
        return None

    return "\n".join(textos).strip()


def es_link_documento(texto_enlace: Any, url_documento: Any) -> bool:
    """Determina si un enlace parece corresponder a un documento descargable."""
    texto = f"{texto_enlace or ''} {url_documento or ''}".lower()

    if "descargar todos los archivos" in texto:
        return False

    return any(palabra.lower() in texto for palabra in PALABRAS_DOCUMENTOS)


def obtener_texto_previo_documento(enlace: Tag, seccion: str) -> str | None:
    """
    Intenta asignar nombre al documento usando texto cercano al enlace.

    En el portal de Madrid muchas veces el enlace visible solo dice 'Descargar'.
    El nombre real aparece antes del enlace dentro de la misma sección.
    """
    candidatos = []

    # 1. Texto del contenedor cercano.
    for parent in enlace.parents:
        if not isinstance(parent, Tag):
            continue

        texto_parent = limpiar_texto(parent.get_text(" ", strip=True))
        if texto_parent and texto_parent.lower() not in ["descargar", "descargar todos los archivos"]:
            candidatos.append(texto_parent)

        if parent.name in ["li", "tr", "div", "article", "section"]:
            break

    # 2. Texto de hermanos anteriores.
    for prev in enlace.find_all_previous(string=True, limit=8):
        texto_prev = limpiar_texto(prev)
        if not texto_prev:
            continue

        texto_prev_lower = texto_prev.lower()

        if texto_prev_lower in ["descargar", "descargar todos los archivos"]:
            continue

        if re.fullmatch(r"[0-9]+(?:[\.,][0-9]+)?\s*(kb|mb|gb)", texto_prev_lower):
            continue

        if texto_prev in SECCIONES_MADRID:
            continue

        candidatos.append(texto_prev)

    # Preferir un candidato que contenga fecha de publicación o palabras documentales.
    for candidato in candidatos:
        candidato_lower = candidato.lower()
        if "publicado" in candidato_lower:
            return candidato

    for candidato in candidatos:
        candidato_lower = candidato.lower()
        if any(p in candidato_lower for p in PALABRAS_DOCUMENTOS if p != "descargar"):
            return candidato

    if candidatos:
        return candidatos[0]

    return f"documento_{seccion}"


def extraer_documentos_seccion(
    encabezado: Tag,
    seccion: str,
    url_base: str,
    licitacion_id: Any,
) -> list[dict[str, Any]]:
    """Extrae enlaces a documentos dentro de una sección HTML."""
    documentos = []

    for sibling in encabezado.find_next_siblings():
        if isinstance(sibling, Tag) and sibling.name in ["h2", "h3"]:
            break

        if not isinstance(sibling, Tag):
            continue

        for enlace in sibling.find_all("a", href=True):
            texto_enlace = limpiar_texto(enlace.get_text(" ", strip=True))
            href = enlace.get("href")
            url_documento = urljoin(url_base, href)

            if not es_link_documento(texto_enlace, url_documento):
                continue

            nombre_documento = obtener_texto_previo_documento(enlace, seccion)

            documentos.append(
                {
                    "licitacion_id": licitacion_id,
                    "seccion": seccion,
                    "texto_enlace": texto_enlace,
                    "nombre_documento": nombre_documento,
                    "href": href,
                    "url_documento": url_documento,
                }
            )

    # Eliminar duplicados por URL dentro de la licitación.
    documentos_unicos = []
    urls_vistas = set()

    for doc in documentos:
        url_doc = doc["url_documento"]
        if url_doc in urls_vistas:
            continue

        documentos_unicos.append(doc)
        urls_vistas.add(url_doc)

    return documentos_unicos


# ============================================================
# 3. EXTRACCIÓN HTML MADRID
# ============================================================

def extraer_madrid_html_y_documentos(fila: pd.Series) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Extrae información HTML organizada por secciones y documentos detectados.

    No analiza contenido PDF.
    """
    licitacion_id = fila["licitacion_id"]
    detail_url = fila["detail_url"]

    registro = {
        "licitacion_id": licitacion_id,
        "portal": "madrid",
        "detail_url": detail_url,
        "url_response": None,
        "status_code": None,
        "content_type": None,
        "titulo_html": None,
        "n_secciones_encontradas": 0,
        "n_documentos_detectados": 0,
        "documentos_detectados_json": None,
        "texto_html_organizado": None,
        "error": None,
    }

    for seccion in SECCIONES_MADRID:
        registro[normalizar_nombre_columna(seccion)] = None

    documentos = []

    try:
        response = descargar_url(detail_url)

        registro["url_response"] = response.url
        registro["status_code"] = response.status_code
        registro["content_type"] = response.headers.get("content-type")

        if response.status_code != 200:
            registro["error"] = f"Status code diferente de 200: {response.status_code}"
            return registro, documentos

        soup = BeautifulSoup(response.text, "html.parser")

        registro["titulo_html"] = (
            soup.title.get_text(" ", strip=True)
            if soup.title
            else None
        )

        encabezados_validos = []

        for encabezado in soup.find_all(["h2", "h3"]):
            nombre_seccion = limpiar_texto(encabezado.get_text(" ", strip=True))

            if nombre_seccion not in SECCIONES_MADRID:
                continue

            encabezados_validos.append(nombre_seccion)

            col = normalizar_nombre_columna(nombre_seccion)
            registro[col] = extraer_texto_seccion(encabezado)

            docs_seccion = extraer_documentos_seccion(
                encabezado=encabezado,
                seccion=nombre_seccion,
                url_base=response.url,
                licitacion_id=licitacion_id,
            )
            documentos.extend(docs_seccion)

        textos_organizados = []

        for seccion in SECCIONES_MADRID:
            col = normalizar_nombre_columna(seccion)
            texto = registro.get(col)

            if texto:
                textos_organizados.append(f"## {seccion}\n{texto}")

        registro["texto_html_organizado"] = "\n\n".join(textos_organizados)
        registro["n_secciones_encontradas"] = len(set(encabezados_validos))

        # Duplicar documentos por URL dentro de licitación.
        docs_unicos = []
        urls_vistas = set()

        for doc in documentos:
            url_doc = doc["url_documento"]
            if url_doc in urls_vistas:
                continue
            docs_unicos.append(doc)
            urls_vistas.add(url_doc)

        documentos = docs_unicos
        registro["n_documentos_detectados"] = len(documentos)

        if documentos:
            registro["documentos_detectados_json"] = json.dumps(
                documentos,
                ensure_ascii=False,
                default=str,
            )

    except Exception as exc:
        registro["error"] = str(exc)

    return registro, documentos


# ============================================================
# 4. DESCARGA DE DOCUMENTOS
# ============================================================

def descargar_documentos_licitacion(
    licitacion_id: Any,
    documentos: list[dict[str, Any]],
    carpeta_base: Path = RUTA_DOCUMENTOS_MADRID,
) -> list[dict[str, Any]]:
    """Descarga los documentos detectados en carpeta por licitación."""
    registros_descarga = []

    carpeta_licitacion = carpeta_base / limpiar_id_carpeta(licitacion_id)
    carpeta_licitacion.mkdir(parents=True, exist_ok=True)

    for i, doc in enumerate(documentos, start=1):
        url_documento = doc.get("url_documento")
        nombre_documento = doc.get("nombre_documento") or f"documento_{i:02d}"

        registro = {
            **doc,
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
                registros_descarga.append(registro)
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

        registros_descarga.append(registro)
        time.sleep(PAUSA_SEGUNDOS)

    return registros_descarga


# ============================================================
# 5. CARGA Y ORQUESTACIÓN
# ============================================================

def cargar_muestra() -> pd.DataFrame:
    """Carga el parquet de muestra desde Silver."""
    archivos = sorted(RUTA_SILVER.glob(PATRON_MUESTRA))

    print("Archivos encontrados:")
    for archivo in archivos:
        print("-", archivo.name)

    if len(archivos) == 0:
        raise FileNotFoundError(
            f"No se encontró ningún parquet que empiece por {PATRON_MUESTRA} en {RUTA_SILVER}"
        )

    ruta_parquet = archivos[0]
    df_muestra = pd.read_parquet(ruta_parquet)

    print("Ruta cargada:", ruta_parquet)
    print("Shape muestra:", df_muestra.shape)

    return df_muestra


def filtrar_madrid(df_muestra: pd.DataFrame) -> pd.DataFrame:
    """Filtra licitaciones del portal Comunidad de Madrid."""
    if "dominio_url" not in df_muestra.columns:
        raise KeyError("No existe la columna dominio_url en df_muestra.")

    if "detail_url" not in df_muestra.columns:
        raise KeyError("No existe la columna detail_url en df_muestra.")

    df_madrid = df_muestra[
        df_muestra["dominio_url"]
        .astype(str)
        .str.contains("contratos-publicos.comunidad.madrid", case=False, na=False)
    ].copy()

    df_madrid = df_madrid.reset_index(drop=True)

    print("Registros Madrid:", df_madrid.shape)

    return df_madrid


def ejecutar_extraccion_madrid(df_madrid: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ejecuta extracción HTML y descarga documental para Madrid."""
    registros_html = []
    registros_documentos_descargados = []

    for i, fila in df_madrid.reset_index(drop=True).iterrows():
        print("=" * 100)
        print(f"Procesando Madrid {i + 1}/{len(df_madrid)}")
        print("Licitación:", fila.get("licitacion_id"))
        print("URL:", fila.get("detail_url"))

        registro_html, documentos = extraer_madrid_html_y_documentos(fila)
        registros_html.append(registro_html)

        print("Status:", registro_html.get("status_code"))
        print("Secciones encontradas:", registro_html.get("n_secciones_encontradas"))
        print("Documentos detectados:", len(documentos))
        print("Error HTML:", registro_html.get("error"))

        if documentos:
            descargas = descargar_documentos_licitacion(
                licitacion_id=fila.get("licitacion_id"),
                documentos=documentos,
            )
            registros_documentos_descargados.extend(descargas)

            n_ok = sum(1 for d in descargas if d.get("estado_descarga") == "OK")
            n_error = sum(1 for d in descargas if d.get("estado_descarga") == "ERROR")

            print("Documentos descargados OK:", n_ok)
            print("Documentos con error:", n_error)

        time.sleep(PAUSA_SEGUNDOS)

    df_html = pd.DataFrame(registros_html)
    df_documentos = pd.DataFrame(registros_documentos_descargados)

    return df_html, df_documentos


def exportar_resultados(df_html: pd.DataFrame, df_documentos: pd.DataFrame) -> None:
    """Guarda resultados en Silver."""
    df_html.to_parquet(RUTA_SALIDA_HTML, index=False)

    print("=" * 100)
    print("Parquet HTML guardado en:")
    print(RUTA_SALIDA_HTML)
    print("Shape HTML:", df_html.shape)

    if df_documentos.empty:
        print("No hay documentos descargados para guardar.")
        df_documentos.to_parquet(RUTA_SALIDA_DOCUMENTOS, index=False)
    else:
        df_documentos.to_parquet(RUTA_SALIDA_DOCUMENTOS, index=False)

    print("Parquet documentos guardado en:")
    print(RUTA_SALIDA_DOCUMENTOS)
    print("Shape documentos:", df_documentos.shape)

    if not df_html.empty:
        print("\nResumen secciones encontradas:")
        print(df_html["n_secciones_encontradas"].value_counts(dropna=False).to_string())

    if not df_documentos.empty:
        print("\nResumen descargas:")
        print(df_documentos["estado_descarga"].value_counts(dropna=False).to_string())

        print("\nResumen tipos de archivo:")
        print(df_documentos["tipo_archivo_detectado"].value_counts(dropna=False).to_string())

        print("\nDocumentos por licitación:")
        print(
            df_documentos
            .groupby("licitacion_id", dropna=False)
            .size()
            .reset_index(name="n_documentos")
            .sort_values("n_documentos", ascending=False)
            .to_string(index=False)
        )


def main() -> None:
    """Función principal."""
    df_muestra = cargar_muestra()
    df_madrid = filtrar_madrid(df_muestra)

    if df_madrid.empty:
        raise ValueError("No hay registros de Madrid para procesar.")

    df_html, df_documentos = ejecutar_extraccion_madrid(df_madrid)
    exportar_resultados(df_html, df_documentos)

    print("=" * 100)
    print("Proceso terminado.")
    print("Carpeta de documentos Madrid:")
    print(RUTA_DOCUMENTOS_MADRID)


if __name__ == "__main__":
    main()
