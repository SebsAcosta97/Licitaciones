from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import re
from io import BytesIO
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

FEED_PERFILES = (
    "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/"
    "licitacionesPerfilesContratanteCompleto3.atom"
)
FEED_AGREGADAS = (
    "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/"
    "PlataformasAgregadasSinMenores.atom"
)
HACIENDA_HIST_PAGES = [
    "https://www.hacienda.gob.es/es-es/gobiernoabierto/datos%20abiertos/paginas/licitacionescontratante.aspx",
    "https://www.hacienda.gob.es/es-es/gobiernoabierto/datos%20abiertos/paginas/licitacionesagregacion.aspx",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
BRONZE_DIR = DATA_DIR / "Bronze"
OUTPUT_DIR = BRONZE_DIR
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cbc_place": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
    "cac_place": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
}


@dataclass
class TenderRecord:
    licitacion_id: str
    title: str
    detail_url: str
    updated: str
    expediente: str
    pais_codigo: str
    pais_nombre: str
    tipo_contrato_codigo: str
    tipo_contrato: str
    subtipo_contrato_codigo: str
    lugar_ejecucion: str
    lugar_ejecucion_codigo: str
    cpv_codes: str
    organo_contratacion: str
    estado_codigo: str
    fecha_publicacion: str
    procedimiento_codigo: str
    sistema_contratacion_codigo: str
    importe_estimado: str
    importe_sin_impuestos: str
    importe_total: str
    forma_presentacion_codigo: str
    financiacion_ue_codigo: str
    financiacion_ue_nombre: str
    fuente_publicacion: str
    tipo_tramitacion_codigo: str
    presentacion_desde: str
    presentacion_hasta: str
    presentacion_hora: str
    notice_types: str
    organo_dir3: str
    organo_nif: str
    org_hierarchy: str
    contrato_duracion: str
    contrato_duracion_unidad: str
    ofertas_recibidas: str
    pymes_ofertas: str
    pyme_adjudicada: str
    adjudicatario: str
    adjudicatario_nif: str


@dataclass
class TenderDocument:
    licitacion_id: str
    expediente: str
    title: str
    detail_url: str
    notice_type_code: str
    publication_media: str
    publication_date: str
    document_type_code: str
    document_type_name: str
    file_name: str
    document_url: str
    estado_licitacion: str
    financiacion_ue: str
    presupuesto_base_sin_impuestos: str
    valor_estimado_contrato: str
    tipo_contrato: str
    codigo_cpv: str
    lugar_ejecucion: str
    sistema_contratacion: str
    procedimiento_contratacion: str
    tipo_tramitacion: str


def build_licitacion_id(expediente: str, detail_url: str) -> str:
    base = f"{expediente}|{detail_url}".strip()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "sin_tipo"


def unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({v.strip() for v in values if v and v.strip()})


SALUD_KEYWORDS = [
    "salud",
    "sanidad",
    "sanitario",
    "sanitaria",
    "hospital",
    "hospitalario",
    "hospitalaria",
    "clinica",
    "clínica",
    "medico",
    "médico",
    "enfermeria",
    "enfermería",
    "farmacia",
    "farmaceutico",
    "farmacéutico",
    "atencion primaria",
    "atención primaria",
    "atencion temprana",
    "atención temprana",
    "diagnostico",
    "diagnóstico",
    "laboratorio",
    "ambulancia",
    "emergencias",
    "urgencias",
    "vacuna",
    "vacunas",
    "servicio de salud",
    "sistema sanitario",
    "consumo",
]

TIPO_CONTRATO_MAP = {
    "1": "Obras",
    "2": "Servicios",
    "3": "Suministros",
    "8": "Patrimonial",
    "21": "Concesion de obras",
    "22": "Concesion de servicios",
    "50": "Mixto",
}
ESTADO_LICITACION_MAP = {
    "EV": "En evaluación",
    "PUB": "Publicado",
    "RES": "Resuelto",
    "ADJ": "Adjudicado",
    "ANUL": "Anulado",
    "DES": "Desierto",
    "CERR": "Cerrado",
}
SERVICIOS_CODE = "2"
SERVICIOS_LABELS = {"SERVICIOS", "SERVICIO", "SERVICES"}


def is_health_tender(item: TenderRecord) -> bool:
    blob = " ".join(
        [
            item.title,
            item.organo_contratacion,
            item.cpv_codes,
            item.lugar_ejecucion,
        ]
    ).lower()
    return any(k in blob for k in SALUD_KEYWORDS)


def download_text(url: str, timeout_seconds: int) -> str:
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, headers=HEADERS, timeout=timeout_seconds)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def download_bytes(url: str, timeout_seconds: int) -> bytes:
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, headers=HEADERS, timeout=timeout_seconds)
    response.raise_for_status()
    return response.content


def discover_historical_zip_urls(timeout_seconds: int) -> list[str]:
    urls: set[str] = set()
    for page in HACIENDA_HIST_PAGES:
        try:
            html = download_text(page, timeout_seconds)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            if href.lower().endswith(".zip") and "contrataciondelsectorpublico.gob.es" in href:
                urls.add(href)
    # Orden estable por nombre para trazabilidad.
    return sorted(urls)


def extract_year_from_zip_url(url: str) -> int | None:
    # Ejemplos:
    # ..._2026.zip
    # ..._202604.zip
    m = re.search(r"_(20\d{2})(?:\d{2})?\.zip$", url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def load_atom_texts_from_zip_url(url: str, timeout_seconds: int) -> list[str]:
    out: list[str] = []
    try:
        content = download_bytes(url, timeout_seconds)
    except Exception:
        return out
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            for name in zf.namelist():
                low = name.lower()
                if not (low.endswith(".atom") or low.endswith(".xml")):
                    continue
                try:
                    raw = zf.read(name)
                    text = raw.decode("utf-8", errors="ignore")
                    if "<feed" in text:
                        out.append(text)
                except Exception:
                    continue
    except Exception:
        return out
    return out


def first_text(entry: ET.Element, paths: Iterable[str]) -> str:
    for path in paths:
        value = entry.findtext(path, default="", namespaces=NS).strip()
        if value:
            return value
    return ""


def first_elem(entry: ET.Element, paths: Iterable[str]) -> ET.Element | None:
    for path in paths:
        el = entry.find(path, NS)
        if el is not None:
            return el
    return None


def collect_texts(entry: ET.Element, path: str) -> list[str]:
    out: list[str] = []
    for el in entry.findall(path, NS):
        text = (el.text or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def collect_winning_parties(entry: ET.Element) -> tuple[str, str]:
    names = collect_texts(
        entry, ".//cac:TenderResult/cac:WinningParty/cac:PartyName/cbc:Name"
    )
    ids = collect_texts(
        entry, ".//cac:TenderResult/cac:WinningParty/cac:PartyIdentification/cbc:ID"
    )
    return "; ".join(names), "; ".join(ids)


def tipo_contrato_label(code: str) -> str:
    c = (code or "").strip()
    return TIPO_CONTRATO_MAP.get(c, c)


def normalize_tipo_contrato_codigo(raw_code: str) -> str:
    raw = (raw_code or "").strip()
    if not raw:
        return ""
    if raw in TIPO_CONTRATO_MAP:
        return raw
    raw_upper = raw.upper()
    if raw_upper in SERVICIOS_LABELS:
        return SERVICIOS_CODE
    for code, label in TIPO_CONTRATO_MAP.items():
        if raw_upper == label.upper():
            return code
    return raw


def is_service_contract(tipo_contrato_codigo: str) -> bool:
    return normalize_tipo_contrato_codigo(tipo_contrato_codigo) == SERVICIOS_CODE


def extract_documents_from_entry(
    entry: ET.Element,
    licitacion_id: str,
    expediente: str,
    title: str,
    detail_url: str,
    licitacion_context: dict[str, str],
) -> list[TenderDocument]:
    docs: list[TenderDocument] = []
    valid_infos = entry.findall(".//cac_place:ValidNoticeInfo", NS)
    for valid_info in valid_infos:
        notice_type_code = (
            valid_info.findtext("cbc_place:NoticeTypeCode", default="", namespaces=NS).strip()
        )
        publication_media = (
            valid_info.findtext(
                "cac_place:AdditionalPublicationStatus/cbc_place:PublicationMediaName",
                default="",
                namespaces=NS,
            ).strip()
        )
        doc_refs = valid_info.findall(
            "cac_place:AdditionalPublicationStatus/cac_place:AdditionalPublicationDocumentReference",
            NS,
        )
        for doc_ref in doc_refs:
            publication_date = (
                doc_ref.findtext("cbc:IssueDate", default="", namespaces=NS).strip()
            )
            doc_type_el = doc_ref.find("cbc:DocumentTypeCode", NS)
            document_type_code = ""
            document_type_name = ""
            if doc_type_el is not None:
                document_type_code = (doc_type_el.text or "").strip()
                document_type_name = doc_type_el.attrib.get("name", "").strip()

            file_name = (
                doc_ref.findtext(
                    "cac:Attachment/cac:ExternalReference/cbc:FileName",
                    default="",
                    namespaces=NS,
                ).strip()
            )
            document_url = (
                doc_ref.findtext(
                    "cac:Attachment/cac:ExternalReference/cbc:URI",
                    default="",
                    namespaces=NS,
                ).strip()
            )
            docs.append(
                TenderDocument(
                    licitacion_id=licitacion_id,
                    expediente=expediente,
                    title=title,
                    detail_url=detail_url,
                    notice_type_code=notice_type_code,
                    publication_media=publication_media,
                    publication_date=publication_date,
                    document_type_code=document_type_code,
                    document_type_name=document_type_name,
                    file_name=file_name,
                    document_url=document_url,
                    estado_licitacion=licitacion_context.get("estado_licitacion", ""),
                    financiacion_ue=licitacion_context.get("financiacion_ue", ""),
                    presupuesto_base_sin_impuestos=licitacion_context.get(
                        "presupuesto_base_sin_impuestos", ""
                    ),
                    valor_estimado_contrato=licitacion_context.get(
                        "valor_estimado_contrato", ""
                    ),
                    tipo_contrato=licitacion_context.get("tipo_contrato", ""),
                    codigo_cpv=licitacion_context.get("codigo_cpv", ""),
                    lugar_ejecucion=licitacion_context.get("lugar_ejecucion", ""),
                    sistema_contratacion=licitacion_context.get("sistema_contratacion", ""),
                    procedimiento_contratacion=licitacion_context.get(
                        "procedimiento_contratacion", ""
                    ),
                    tipo_tramitacion=licitacion_context.get("tipo_tramitacion", ""),
                )
            )
    return docs


def parse_atom(
    xml_text: str, query: str = "", limit: int = 0, services_only: bool = True
) -> tuple[list[TenderRecord], list[TenderDocument]]:
    root = ET.fromstring(xml_text)
    out: list[TenderRecord] = []
    docs_out: list[TenderDocument] = []
    query_norm = query.strip().lower()

    for entry in root.findall("a:entry", NS):
        title = first_text(entry, ["a:title"])
        detail_url = ""
        first_link = entry.find("a:link", NS)
        if first_link is not None:
            detail_url = first_link.attrib.get("href", "").strip()

        updated = first_text(entry, ["a:updated"])
        expediente = first_text(entry, [".//cbc:ContractFolderID"])
        licitacion_id = build_licitacion_id(expediente, detail_url)

        pais_codigo = first_text(
            entry,
            [
                ".//cac:ProcurementProject/cac:RealizedLocation/cac:Address/cac:Country/cbc:IdentificationCode",
                ".//cac_place:LocatedContractingParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode",
            ],
        )
        pais_nombre = first_text(
            entry,
            [
                ".//cac:ProcurementProject/cac:RealizedLocation/cac:Address/cac:Country/cbc:Name",
                ".//cac_place:LocatedContractingParty/cac:Party/cac:PostalAddress/cac:Country/cbc:Name",
            ],
        )
        tipo_contrato_codigo_raw = first_text(entry, [".//cac:ProcurementProject/cbc:TypeCode"])
        tipo_contrato_codigo = normalize_tipo_contrato_codigo(tipo_contrato_codigo_raw)
        if services_only and not is_service_contract(tipo_contrato_codigo):
            continue
        tipo_contrato = tipo_contrato_label(tipo_contrato_codigo)
        subtipo_contrato_codigo = first_text(
            entry, [".//cac:ProcurementProject/cbc:SubTypeCode"]
        )
        lugar_ejecucion = first_text(
            entry, [".//cac:ProcurementProject/cac:RealizedLocation/cbc:CountrySubentity"]
        )
        lugar_ejecucion_codigo = first_text(
            entry, [".//cac:ProcurementProject/cac:RealizedLocation/cbc:CountrySubentityCode"]
        )
        cpv_codes = ";".join(
            collect_texts(
                entry,
                ".//cac:ProcurementProject/cac:RequiredCommodityClassification/cbc:ItemClassificationCode",
            )
        )

        organo = first_text(
            entry, [".//cac_place:LocatedContractingParty/cac:Party/cac:PartyName/cbc:Name"]
        )
        organo_dir3 = first_text(
            entry,
            [
                ".//cac_place:LocatedContractingParty/cac:Party/cac:PartyIdentification/cbc:ID[@schemeName='DIR3']"
            ],
        )
        organo_nif = first_text(
            entry,
            [
                ".//cac_place:LocatedContractingParty/cac:Party/cac:PartyIdentification/cbc:ID[@schemeName='NIF']"
            ],
        )
        hierarchy_nodes = collect_texts(
            entry, ".//cac_place:LocatedContractingParty/cac_place:ParentLocatedParty/cac:PartyName/cbc:Name"
        )
        org_hierarchy = ">".join(hierarchy_nodes)
        estado = first_text(
            entry, [".//cbc_place:ContractFolderStatusCode", ".//cbc:ContractFolderStatusCode"]
        )

        # Fecha/fuente/publicacion desde bloques de avisos (ValidNoticeInfo).
        notice_dates = collect_texts(
            entry,
            ".//cac_place:ValidNoticeInfo/cac_place:AdditionalPublicationStatus/"
            "cac_place:AdditionalPublicationDocumentReference/cbc:IssueDate",
        )
        fecha_publicacion = max(notice_dates) if notice_dates else ""

        notice_types = ";".join(
            collect_texts(entry, ".//cac_place:ValidNoticeInfo/cbc_place:NoticeTypeCode")
        )
        fuente_publicacion = ";".join(
            collect_texts(entry, ".//cac_place:ValidNoticeInfo/cac_place:AdditionalPublicationStatus/cbc_place:PublicationMediaName")
        )

        procedimiento = first_text(entry, [".//cac:TenderingProcess/cbc:ProcedureCode"])
        sistema_contratacion = first_text(
            entry, [".//cac:TenderingProcess/cbc:ContractingSystemCode"]
        )
        tipo_tramitacion = first_text(entry, [".//cac:TenderingProcess/cbc:UrgencyCode"])
        forma_presentacion = first_text(
            entry, [".//cac:TenderingProcess/cbc:SubmissionMethodCode"]
        )
        presentacion_desde = first_text(
            entry,
            [
                ".//cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:StartDate",
                ".//cac:TenderingProcess/cac:DocumentAvailabilityPeriod/cbc:StartDate",
            ],
        )
        presentacion_hasta = first_text(
            entry, [".//cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"]
        )
        presentacion_hora = first_text(
            entry, [".//cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndTime"]
        )
        contrato_duracion = first_text(
            entry, [".//cac:ProcurementProject/cac:PlannedPeriod/cbc:DurationMeasure"]
        )
        dur_el = first_elem(entry, [".//cac:ProcurementProject/cac:PlannedPeriod/cbc:DurationMeasure"])
        contrato_duracion_unidad = ""
        if dur_el is not None:
            contrato_duracion_unidad = dur_el.attrib.get("unitCode", "").strip()

        importe_estimado = first_text(
            entry, [".//cac:ProcurementProject/cac:BudgetAmount/cbc:EstimatedOverallContractAmount"]
        )
        importe_sin_impuestos = first_text(
            entry, [".//cac:ProcurementProject/cac:BudgetAmount/cbc:TaxExclusiveAmount"]
        )
        importe_total = first_text(
            entry, [".//cac:ProcurementProject/cac:BudgetAmount/cbc:TotalAmount"]
        )

        financiacion_el = first_elem(
            entry, [".//cac:TenderingTerms/cbc:FundingProgramCode"]
        )
        financiacion_ue_codigo = ""
        financiacion_ue_nombre = ""
        if financiacion_el is not None:
            financiacion_ue_codigo = (financiacion_el.text or "").strip()
            financiacion_ue_nombre = financiacion_el.attrib.get("name", "").strip()

        ofertas_recibidas = first_text(entry, [".//cac:TenderResult/cbc:ReceivedTenderQuantity"])
        pymes_ofertas = first_text(entry, [".//cac:TenderResult/cbc:SMEsReceivedTenderQuantity"])
        pyme_adjudicada = first_text(entry, [".//cac:TenderResult/cbc:SMEAwardedIndicator"])
        adjudicatario, adjudicatario_nif = collect_winning_parties(entry)
        search_blob = " ".join(
            [
                title,
                detail_url,
                expediente,
                organo,
                lugar_ejecucion,
                cpv_codes,
                estado,
                pais_nombre,
                adjudicatario,
                adjudicatario_nif,
            ]
        ).lower()
        if query_norm and query_norm not in search_blob:
            continue

        docs_out.extend(
            extract_documents_from_entry(
                entry,
                licitacion_id,
                expediente,
                title,
                detail_url,
                licitacion_context={
                    "estado_licitacion": estado,
                    "financiacion_ue": financiacion_ue_nombre or financiacion_ue_codigo,
                    "presupuesto_base_sin_impuestos": importe_sin_impuestos,
                    "valor_estimado_contrato": importe_estimado,
                    "tipo_contrato": tipo_contrato,
                    "codigo_cpv": cpv_codes,
                    "lugar_ejecucion": lugar_ejecucion,
                    "sistema_contratacion": sistema_contratacion,
                    "procedimiento_contratacion": procedimiento,
                    "tipo_tramitacion": tipo_tramitacion,
                },
            )
        )
        out.append(
            TenderRecord(
                licitacion_id=licitacion_id,
                title=title,
                detail_url=detail_url,
                updated=updated,
                expediente=expediente,
                pais_codigo=pais_codigo,
                pais_nombre=pais_nombre,
                tipo_contrato_codigo=tipo_contrato,
                tipo_contrato=tipo_contrato,
                subtipo_contrato_codigo=subtipo_contrato_codigo,
                lugar_ejecucion=lugar_ejecucion,
                lugar_ejecucion_codigo=lugar_ejecucion_codigo,
                cpv_codes=cpv_codes,
                organo_contratacion=organo,
                estado_codigo=estado,
                fecha_publicacion=fecha_publicacion,
                procedimiento_codigo=procedimiento,
                sistema_contratacion_codigo=sistema_contratacion,
                importe_estimado=importe_estimado,
                importe_sin_impuestos=importe_sin_impuestos,
                importe_total=importe_total,
                forma_presentacion_codigo=forma_presentacion,
                financiacion_ue_codigo=financiacion_ue_codigo,
                financiacion_ue_nombre=financiacion_ue_nombre,
                fuente_publicacion=fuente_publicacion,
                tipo_tramitacion_codigo=tipo_tramitacion,
                presentacion_desde=presentacion_desde,
                presentacion_hasta=presentacion_hasta,
                presentacion_hora=presentacion_hora,
                notice_types=notice_types,
                organo_dir3=organo_dir3,
                organo_nif=organo_nif,
                org_hierarchy=org_hierarchy,
                contrato_duracion=contrato_duracion,
                contrato_duracion_unidad=contrato_duracion_unidad,
                ofertas_recibidas=ofertas_recibidas,
                pymes_ofertas=pymes_ofertas,
                pyme_adjudicada=pyme_adjudicada,
                adjudicatario=adjudicatario,
                adjudicatario_nif=adjudicatario_nif,
            )
        )

        if limit > 0 and len(out) >= limit:
            break

    return out, docs_out


def build_filter_catalog(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str | int]]]:
    fields = [
        "pais_nombre",
        "tipo_contrato_codigo",
        "subtipo_contrato_codigo",
        "lugar_ejecucion",
        "organo_contratacion",
        "estado_codigo",
        "procedimiento_codigo",
        "sistema_contratacion_codigo",
        "forma_presentacion_codigo",
        "financiacion_ue_codigo",
        "financiacion_ue_nombre",
        "fuente_publicacion",
        "tipo_tramitacion_codigo",
    ]
    catalog: dict[str, list[dict[str, str | int]]] = {}
    for field in fields:
        counts: dict[str, int] = {}
        for row in rows:
            value = row.get(field, "").strip()
            if not value:
                value = "No informado"
            counts[value] = counts.get(value, 0) + 1
        catalog[field] = [
            {"value": value, "count": count}
            for value, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        ]
    return catalog


def merge_unique_records(items: list[TenderRecord]) -> list[TenderRecord]:
    out: list[TenderRecord] = []
    seen: set[str] = set()
    for item in items:
        key = f"{item.expediente}|{item.detail_url}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def merge_unique_docs(docs: list[TenderDocument]) -> list[TenderDocument]:
    out: list[TenderDocument] = []
    seen: set[str] = set()
    for doc in docs:
        key = "|".join(
            [
                doc.expediente,
                doc.licitacion_id,
                doc.notice_type_code,
                doc.publication_date,
                doc.document_type_code,
                doc.file_name,
                doc.document_url,
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def _normalize_label(text: str) -> str:
    low = (text or "").strip().lower()
    low = (
        low.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )
    return re.sub(r"\s+", " ", low)


def _extract_award_info_from_detail_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    adjudicatario = ""
    adjudicatario_nif = ""

    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = _normalize_label(cells[0].get_text(" ", strip=True))
        value = cells[1].get_text(" ", strip=True)
        if not value:
            continue
        if ("adjudicatario" in label or "empresa adjudicataria" in label) and not adjudicatario:
            adjudicatario = value
        if ("nif" in label or "cif" in label) and not adjudicatario_nif:
            adjudicatario_nif = value

    if not adjudicatario:
        page_text = soup.get_text("\n", strip=True)
        m = re.search(
            r"(?:adjudicatari[oa]|empresa adjudicataria)\s*[:\-]?\s*([^\n\r]+)",
            page_text,
            flags=re.IGNORECASE,
        )
        if m:
            adjudicatario = m.group(1).strip()
    if not adjudicatario_nif:
        page_text = soup.get_text("\n", strip=True)
        m = re.search(r"\b(?:NIF|CIF)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\./]{6,})", page_text)
        if m:
            adjudicatario_nif = m.group(1).strip()

    return adjudicatario, adjudicatario_nif


def enrich_awardees_from_detail_pages(
    items: list[TenderRecord], timeout_seconds: int, max_pages: int
) -> tuple[int, int]:
    selected = items if max_pages <= 0 else items[:max_pages]
    scanned = 0
    updated = 0
    for item in selected:
        if item.adjudicatario and item.adjudicatario_nif:
            continue
        if not item.detail_url:
            continue
        scanned += 1
        try:
            html = download_text(item.detail_url, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        adj, nif = _extract_award_info_from_detail_html(html)
        before = (item.adjudicatario, item.adjudicatario_nif)
        if not item.adjudicatario and adj:
            item.adjudicatario = adj
        if not item.adjudicatario_nif and nif:
            item.adjudicatario_nif = nif
        after = (item.adjudicatario, item.adjudicatario_nif)
        if after != before:
            updated += 1
    return scanned, updated


def build_dictionaries(
    items: list[TenderRecord], docs: list[TenderDocument]
) -> dict[str, list[dict[str, str]]]:
    estado_map = {
        "PUB": "Publicada",
        "RES": "Resuelta",
        "ADJ": "Adjudicada",
        "ANUL": "Anulada",
        "EV": "En evaluacion",
    }
    tipo_contrato_map = {
        "1": "Obras",
        "2": "Servicios",
        "3": "Suministros",
        "8": "Patrimonial",
        "21": "Concesion de obras",
        "22": "Concesion de servicios",
        "50": "Mixto",
    }
    procedimiento_map = {
        "1": "Abierto",
        "2": "Restringido",
        "3": "Negociado",
        "9": "Abierto simplificado",
    }
    sistema_map = {
        "0": "No aplica",
        "1": "Acuerdo marco",
        "2": "Sistema dinamico de adquisicion",
    }
    tramitacion_map = {
        "1": "Ordinaria",
        "2": "Urgente",
        "3": "Emergencia",
    }
    presentacion_map = {
        "1": "Electronica",
        "2": "Manual",
        "3": "Mixta",
    }
    notice_map = {
        "DOC_CN": "Anuncio de licitacion",
        "DOC_CD": "Pliego / documentacion",
        "DOC_CAN_ADJ": "Anuncio de adjudicacion",
        "DOC_FORM": "Formalizacion",
    }

    cpv_codes = unique_sorted(
        code
        for item in items
        for code in item.cpv_codes.split(";")
        if code.strip()
    )
    cpv_rows = [{"code": code, "descripcion": ""} for code in cpv_codes]

    def build_rows(codes: list[str], mapping: dict[str, str]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for c in unique_sorted(codes):
            rows.append({"code": c, "descripcion": mapping.get(c, "Por clasificar")})
        return rows

    return {
        "estado_licitacion": build_rows([x.estado_codigo for x in items], estado_map),
        "tipo_contrato": build_rows([x.tipo_contrato_codigo for x in items], tipo_contrato_map),
        "procedimiento_contratacion": build_rows(
            [x.procedimiento_codigo for x in items], procedimiento_map
        ),
        "sistema_contratacion": build_rows(
            [x.sistema_contratacion_codigo for x in items], sistema_map
        ),
        "tipo_tramitacion": build_rows(
            [x.tipo_tramitacion_codigo for x in items], tramitacion_map
        ),
        "forma_presentacion": build_rows(
            [x.forma_presentacion_codigo for x in items], presentacion_map
        ),
        "notice_type_code": build_rows([x.notice_type_code for x in docs], notice_map),
        "codigo_cpv": cpv_rows,
    }


def extract_text_from_document(url: str, timeout_seconds: int) -> tuple[str, str]:
    if not url:
        return "", "sin_url"
    try:
        content = download_bytes(url, timeout_seconds=timeout_seconds)
    except Exception as e:
        return "", f"error_descarga:{type(e).__name__}"

    lower = url.lower()
    if lower.endswith(".pdf") or b"%PDF" in content[:8]:
        if PdfReader is None:
            return "", "pdf_sin_libreria"
        try:
            reader = PdfReader(BytesIO(content))
            text_parts: list[str] = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts).strip(), "ok_pdf"
        except Exception as e:
            return "", f"error_pdf:{type(e).__name__}"

    try:
        decoded = content.decode("utf-8", errors="ignore")
    except Exception:
        decoded = content.decode("latin-1", errors="ignore")

    if "<html" in decoded.lower():
        soup = BeautifulSoup(decoded, "html.parser")
        text = soup.get_text("\n", strip=True)
        return text, "ok_html"

    if "<?xml" in decoded.lower() or "<" in decoded[:50]:
        try:
            root = ET.fromstring(decoded)
            text = " ".join(x.strip() for x in root.itertext() if x and x.strip())
            return text, "ok_xml"
        except Exception:
            pass

    return decoded.strip(), "ok_texto"


def save_document_contents(
    docs: list[TenderDocument], timeout_seconds: int, max_docs: int
) -> None:
    selected = docs if max_docs <= 0 else docs[:max_docs]
    rows: list[dict[str, str]] = []
    for doc in selected:
        text, status = extract_text_from_document(doc.document_url, timeout_seconds)
        rows.append(
            {
                "licitacion_id": doc.licitacion_id,
                "expediente": doc.expediente,
                "title": doc.title,
                "detail_url": doc.detail_url,
                "document_type_name": doc.document_type_name,
                "file_name": doc.file_name,
                "document_url": doc.document_url,
                "extract_status": status,
                "text": text,
            }
        )

    (OUTPUT_DIR / "contenido_documentos.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    


def build_document_timeline_from_feed(docs: list[TenderDocument]) -> list[dict[str, str]]:
    notice_map = {
        "DOC_CN": "Anuncio de licitación",
        "DOC_CD": "Pliego / Documentación",
        "DOC_CAN_ADJ": "Anuncio de adjudicación",
        "DOC_FORM": "Formalización",
    }
    rows: list[dict[str, str]] = []
    for d in docs:
        url = d.document_url or ""
        ext = ""
        if url.lower().endswith(".pdf"):
            ext = "pdf"
        elif url.lower().endswith(".xml"):
            ext = "xml"
        elif url.lower().endswith(".html") or "html" in url.lower():
            ext = "html"
        row = {
            "licitacion_id": d.licitacion_id,
            "expediente": d.expediente,
            "title": d.title,
            "detail_url": d.detail_url,
            "fecha_publicacion": d.publication_date,
            "documento": d.document_type_name
            or d.document_type_code
            or notice_map.get(d.notice_type_code, "")
            or d.notice_type_code
            or "No informado",
            "url_documento": url,
            "tipo_enlace": ext or "no_informado",
            "url_html": "",
            "url_xml": "",
            "url_pdf": "",
            "origen": "feed_atom",
        }
        if row["tipo_enlace"] == "html":
            row["url_html"] = url
        elif row["tipo_enlace"] == "xml":
            row["url_xml"] = url
        elif row["tipo_enlace"] == "pdf":
            row["url_pdf"] = url
        rows.append(
            {
                **row
            }
        )
    return rows


def build_document_timeline_from_detail_pages(
    items: list[TenderRecord], timeout_seconds: int, max_pages: int
) -> list[dict[str, str]]:
    selected = items if max_pages <= 0 else items[:max_pages]
    rows: list[dict[str, str]] = []
    for it in selected:
        try:
            html = download_text(it.detail_url, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        # Estrategia robusta: cualquier fila con enlaces de documento del FileSystem.
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            all_links = tr.find_all("a", href=True)
            doc_links = []
            for a in all_links:
                href = (a.get("href") or "").strip()
                if "GetDocumentByIdServlet" in href:
                    doc_links.append(a)
            if not doc_links:
                continue

            fecha = cells[0].get_text(" ", strip=True)
            documento = cells[1].get_text(" ", strip=True)
            if not fecha and not documento:
                continue

            for a in doc_links:
                href = (a.get("href") or "").strip()
                label = a.get_text(" ", strip=True).lower()
                img_alt = ""
                img = a.find("img")
                if img is not None:
                    img_alt = (img.get("alt") or "").lower()
                hint = f"{label} {img_alt} {href.lower()}"
                link_type = "otro"
                if "pdf" in hint:
                    link_type = "pdf"
                elif "xml" in hint:
                    link_type = "xml"
                elif "html" in hint:
                    link_type = "html"
                row = {
                    "licitacion_id": it.licitacion_id,
                    "expediente": it.expediente,
                    "title": it.title,
                    "detail_url": it.detail_url,
                    "fecha_publicacion": fecha,
                    "documento": documento or "No informado",
                    "url_documento": href,
                    "tipo_enlace": link_type,
                    "url_html": "",
                    "url_xml": "",
                    "url_pdf": "",
                    "origen": "detalle_web",
                }
                if link_type == "html":
                    row["url_html"] = href
                elif link_type == "xml":
                    row["url_xml"] = href
                elif link_type == "pdf":
                    row["url_pdf"] = href
                rows.append(
                    {
                        **row
                    }
                )
    return rows


def save_document_timeline(
    items: list[TenderRecord],
    docs: list[TenderDocument],
    timeout_seconds: int,
    max_pages: int,
) -> None:
    rows = build_document_timeline_from_feed(docs)
    rows.extend(
        build_document_timeline_from_detail_pages(
            items, timeout_seconds=timeout_seconds, max_pages=max_pages
        )
    )
    unique: dict[str, dict[str, str]] = {}
    for r in rows:
        k = "|".join(
            [
                r["licitacion_id"],
                r["fecha_publicacion"],
                r["documento"],
                r["url_documento"],
                r["tipo_enlace"],
                r["origen"],
            ]
        )
        if k not in unique:
            unique[k] = r
        else:
            # Completa huecos de URL por tipo si aparece otra variante.
            for fld in ["url_html", "url_xml", "url_pdf", "url_documento"]:
                if not unique[k].get(fld) and r.get(fld):
                    unique[k][fld] = r[fld]
    out = list(unique.values())
    out.sort(key=lambda x: (x["licitacion_id"], x["fecha_publicacion"], x["documento"]))

    (OUTPUT_DIR / "timeline_documentos.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    


def first_match(text: str, patterns: list[str]) -> str:
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return (m.group(1) or "").strip()
    return ""


def extract_document_insights() -> None:
    path = OUTPUT_DIR / "contenido_documentos.json"
    if not path.exists():
        return
    rows = json.loads(path.read_text(encoding="utf-8"))
    insights: list[dict[str, str]] = []
    for r in rows:
        text = (r.get("text") or "").replace("\r", "\n")
        text_norm = re.sub(r"\n+", "\n", text)
        insight = {
            "licitacion_id": r.get("licitacion_id", ""),
            "expediente": r.get("expediente", ""),
            "title": r.get("title", ""),
            "document_type_name": r.get("document_type_name", ""),
            "document_url": r.get("document_url", ""),
            "extract_status": r.get("extract_status", ""),
            "objeto_contrato": first_match(
                text_norm,
                [
                    r"t[íi]tulo\s*[:\-]\s*(.+)",
                    r"objeto(?: del contrato)?\s*[:\-]\s*(.+)",
                    r"descripci[oó]n(?: del contrato)?\s*[:\-]\s*(.+)",
                ],
            ),
            "presupuesto_base": first_match(
                text_norm,
                [
                    r"presupuesto base(?: de licitaci[oó]n)?\s*[:\-]\s*([^\n]+)",
                    r"importe(?: base)?\s*[:\-]\s*([^\n]+)",
                    r"por l[oa]s? siguiente[s]? importes?\s*[:\-]\s*([^\n]+)",
                ],
            ),
            "valor_estimado": first_match(
                text_norm,
                [
                    r"valor estimado(?: del contrato)?\s*[:\-]\s*([^\n]+)",
                    r"v\.?e\.?c\.?\s*[:\-]\s*([^\n]+)",
                ],
            ),
            "fecha_fin_presentacion": first_match(
                text_norm,
                [
                    r"fecha fin de presentaci[oó]n(?: de oferta)?\s*[:\-]\s*([^\n]+)",
                    r"plazo de presentaci[oó]n\s*[:\-]\s*([^\n]+)",
                ],
            ),
            "criterios_adjudicacion": first_match(
                text_norm,
                [
                    r"criterios? de adjudicaci[oó]n\s*[:\-]\s*([^\n]+)",
                    r"criterios? de valoraci[oó]n\s*[:\-]\s*([^\n]+)",
                    r"oferta mejor valorada\s*[:\-]\s*([^\n]+)",
                ],
            ),
            "solvencia": first_match(
                text_norm,
                [
                    r"solvencia(?: econ[oó]mica y financiera| t[eé]cnica)?\s*[:\-]\s*([^\n]+)",
                    r"requisitos? de solvencia\s*[:\-]\s*([^\n]+)",
                ],
            ),
            "cpv_texto": first_match(
                text_norm,
                [
                    r"c[oó]digo cpv\s*[:\-]\s*([^\n]+)",
                    r"\bcpv\b\s*[:\-]\s*([^\n]+)",
                ],
            ),
            "lugar_ejecucion_texto": first_match(
                text_norm,
                [
                    r"lugar de ejecuci[oó]n\s*[:\-]\s*([^\n]+)",
                    r"emplazamiento\s*[:\-]\s*([^\n]+)",
                    r"ubicad[oa]\s+en\s+([^\n]+)",
                ],
            ),
        }
        if any(
            insight[k]
            for k in [
                "objeto_contrato",
                "presupuesto_base",
                "valor_estimado",
                "fecha_fin_presentacion",
                "criterios_adjudicacion",
                "solvencia",
                "cpv_texto",
                "lugar_ejecucion_texto",
            ]
        ):
            insights.append(insight)

    (OUTPUT_DIR / "insights_documentos.json").write_text(
        json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    


def load_existing_records(path: Path) -> list[dict]:

    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return []


def save_outputs(items: list[TenderRecord], docs: list[TenderDocument]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in items]
    doc_rows = [asdict(doc) for doc in docs]

    licitaciones_path = OUTPUT_DIR / "licitaciones.json"

    existing_rows = load_existing_records(licitaciones_path)

    existing_dict = {
        row["licitacion_id"]: row
        for row in existing_rows
        if "licitacion_id" in row
    }

    new_count = 0
    updated_count = 0

    for row in rows:

        lic_id = row["licitacion_id"]

        row["fecha_scraping"] = date.today().isoformat()

        if lic_id not in existing_dict:
            existing_rows.append(row)
            existing_dict[lic_id] = row
            new_count += 1
        else:
            existing_dict[lic_id].update(row)
            updated_count += 1

    licitaciones_path.write_text(
        json.dumps(existing_rows, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    docs_path = OUTPUT_DIR / "documentos_licitacion.json"

    existing_docs = load_existing_records(docs_path)

    existing_doc_keys = {
        (
            d.get("licitacion_id", ""),
            d.get("document_url", ""),
        ): d
        for d in existing_docs
    }

    new_docs = 0

    for row in doc_rows:

        row["fecha_scraping"] = date.today().isoformat()

        key = (
            row.get("licitacion_id", ""),
            row.get("document_url", ""),
        )
    

        if key not in existing_doc_keys:
            existing_docs.append(row)
            existing_doc_keys[key] = row
            new_docs += 1

    docs_path.write_text(
        json.dumps(existing_docs, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Nuevas licitaciones agregadas: {new_count}")
    print(f"Licitaciones actualizadas: {updated_count}")
    print(f"Nuevos documentos agregados: {new_docs}")

    

    docs_by_type: dict[str, list[dict[str, str]]] = {}
    for doc in docs:
        label = doc.document_type_name or doc.document_type_code or "sin_tipo_documento"
        docs_by_type.setdefault(label, []).append(asdict(doc))

    docs_type_dir = OUTPUT_DIR / "documentos_por_tipo"
    docs_type_dir.mkdir(parents=True, exist_ok=True)
    for label, rows_by_type in docs_by_type.items():
        slug = slugify(label)
        (docs_type_dir / f"{slug}.json").write_text(
            json.dumps(rows_by_type, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        
                

    filter_catalog = build_filter_catalog(rows)
    dictionaries = build_dictionaries(items, docs)
    

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extraccion de licitaciones y campos de filtro desde feed ATOM oficial."
    )
    parser.add_argument(
        "--source",
        choices=["perfiles", "agregadas", "all"],
        default="perfiles",
        help="Fuente de datos abiertos.",
    )
    parser.add_argument(
        "--query",
        default="",
        help="Filtro opcional por texto (titulo, expediente, organo, CPV, etc.).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximo de registros (0 = todos).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Timeout HTTP en segundos.",
    )
    parser.add_argument(
        "--include-historical",
        action="store_true",
        help="Incluye históricos (ZIP anuales/mensuales) de datos abiertos de Hacienda.",
    )
    parser.add_argument(
        "--historical-last-years",
        type=int,
        default=5,
        help="Ventana de años para histórico (incluye año actual). Ej: 5 => ultimos 5 años.",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Filtra y deja solo licitaciones del area salud/medicina.",
    )
    parser.add_argument(
        "--timeline-docs",
        action="store_true",
        help="Genera timeline de documentos (que, cuando, URL) combinando feed + detalle web.",
    )
    parser.add_argument(
        "--max-detail-pages",
        type=int,
        default=30,
        help="Maximo de fichas detalle a revisar para enriquecer timeline (0=todas).",
    )
    parser.add_argument(
        "--enrich-award-from-detail",
        action="store_true",
        help="Completa adjudicatario/NIF desde la ficha web cuando falte en el feed ATOM.",
    )
    parser.add_argument(
        "--services-only",
        action="store_true",
        default=True,
        help="Limita extracción a contratos de SERVICIOS desde origen (por defecto activo).",
    )
    parser.add_argument(
        "--all-contract-types",
        action="store_true",
        help="Desactiva filtro de servicios y extrae todos los tipos de contrato.",
    )
    args = parser.parse_args()
    services_only = args.services_only and not args.all_contract_types

    if args.source == "perfiles":
        urls = [FEED_PERFILES]
    elif args.source == "agregadas":
        urls = [FEED_AGREGADAS]
    else:
        urls = [FEED_PERFILES, FEED_AGREGADAS]

    all_items: list[TenderRecord] = []
    all_docs: list[TenderDocument] = []
    for url in urls:
        raw_feed = download_text(url, timeout_seconds=args.timeout)
        try:
            items, docs = parse_atom(
                raw_feed, query=args.query, limit=0, services_only=services_only
            )
        except MemoryError:
            continue
        if args.health_only:
            items = [x for x in items if is_health_tender(x)]
            allowed_ids = {x.licitacion_id for x in items}
            docs = [d for d in docs if d.licitacion_id in allowed_ids]
        all_items.extend(items)
        all_docs.extend(docs)

    hist_zip_count = 0
    hist_atom_count = 0
    if args.include_historical:
        zip_urls = discover_historical_zip_urls(args.timeout)
        current_year = date.today().year
        min_year = current_year - max(1, args.historical_last_years) + 1
        zip_urls = [
            u
            for u in zip_urls
            if (extract_year_from_zip_url(u) is not None and extract_year_from_zip_url(u) >= min_year)
        ]
        hist_zip_count = len(zip_urls)
        for zip_url in zip_urls:
            atom_texts = load_atom_texts_from_zip_url(zip_url, args.timeout)
            hist_atom_count += len(atom_texts)
            for atom_text in atom_texts:
                try:
                    items, docs = parse_atom(
                        atom_text, query=args.query, limit=0, services_only=services_only
                    )
                except MemoryError:
                    continue
                if args.health_only:
                    items = [x for x in items if is_health_tender(x)]
                    allowed_ids = {x.licitacion_id for x in items}
                    docs = [d for d in docs if d.licitacion_id in allowed_ids]
                all_items.extend(items)
                all_docs.extend(docs)

    items = merge_unique_records(all_items)
    docs = merge_unique_docs(all_docs)

    if args.health_only:
        items = [x for x in items if is_health_tender(x)]
        allowed_ids = {x.licitacion_id for x in items}
        docs = [d for d in docs if d.licitacion_id in allowed_ids]

    if args.limit > 0:
        items = items[: args.limit]
        allowed_keys = {f"{x.expediente}|{x.detail_url}" for x in items}
        docs = [d for d in docs if f"{d.expediente}|{d.detail_url}" in allowed_keys]

    scanned_award = 0
    updated_award = 0
    if args.enrich_award_from_detail and items:
        scanned_award, updated_award = enrich_awardees_from_detail_pages(
            items, timeout_seconds=args.timeout, max_pages=args.max_detail_pages
        )

    save_outputs(items, docs)
    if args.timeline_docs:
        save_document_timeline(
            items, docs, timeout_seconds=args.timeout, max_pages=args.max_detail_pages
        )

    print(f"Fuente: {args.source}")
    print(f"Registros exportados: {len(items)}")
    print(f"Documentos exportados: {len(docs)}")
    if args.health_only:
        print("Filtro aplicado: solo sector salud/medicina")
    if services_only:
        print("Filtro aplicado: solo contratos tipo SERVICIOS")
    if args.include_historical:
        print(f"Historicos ZIP detectados: {hist_zip_count}")
        print(f"Historicos ATOM cargados: {hist_atom_count}")
        print(f"Rango historico aplicado: {min_year}-{date.today().year}")
   
    if args.enrich_award_from_detail:
        print(
            f"Adjudicatario enriquecido desde detalle web: {updated_award} actualizados (revisadas {scanned_award} fichas)"
        )
    print(f"Salida: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
