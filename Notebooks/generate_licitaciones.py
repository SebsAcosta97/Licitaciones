#!/usr/bin/env python3
"""
generate_licitaciones.py
========================
ETL script para Los Tilos — GovAI Dashboard v2.0

Lee los cuatro ficheros Parquet de la carpeta Silver, realiza el match
de scoring, enriquece con información de Madrid / Castilla y León,
integra Agenda 2035 y genera el JSON que consume el HTML.

Uso:
    python generate_licitaciones.py \
        --silver ./data/Silver \
        --output ./data/Silver/licitaciones_tilos.json

Dependencias:
    pip install pandas pyarrow rapidfuzz
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

import pandas as pd

# ── RapidFuzz es opcional; si no está se usa similitud básica ──
try:
    from rapidfuzz import fuzz as _fuzz
    def _sim(a: str, b: str) -> float:
        return _fuzz.token_set_ratio(a, b) / 100.0
except ImportError:
    def _sim(a: str, b: str) -> float:
        a_set, b_set = set(a.lower().split()), set(b.lower().split())
        if not a_set or not b_set:
            return 0.0
        return len(a_set & b_set) / len(a_set | b_set)


# ══════════════════════════════════════════════════════════════════
# ODS — Agenda 2035
# ══════════════════════════════════════════════════════════════════

ODS_CATALOG = {
    3:  {"nombre": "ODS 3 — Salud y bienestar",           "icono": "🏥", "color": "#4C9A2A",
         "keywords": ["salud","sanitario","sanidad","médico","hospital","clínica","farmac","bienestar","higiene","enfermería","psicolog"]},
    8:  {"nombre": "ODS 8 — Trabajo decente y crecimiento económico", "icono": "💼", "color": "#A21942",
         "keywords": ["empleo","trabajo","economic","empresas","pyme","actividad económica","contratación","formación profesional","inserción laboral"]},
    9:  {"nombre": "ODS 9 — Industria, innovación e infraestructura", "icono": "🏗️", "color": "#FD6925",
         "keywords": ["infraestructura","innovación","tecnolog","digital","obras","construcción","ingeniería","red","telecomunicaciones","software","sistema informático"]},
    10: {"nombre": "ODS 10 — Reducción de las desigualdades",         "icono": "⚖️", "color": "#DD1367",
         "keywords": ["igualdad","inclusión","discapacidad","accesibilidad","diversidad","integración social","exclusión","vulnerabl","minorías"]},
    11: {"nombre": "ODS 11 — Ciudades y comunidades sostenibles",      "icono": "🏙️", "color": "#FD9D24",
         "keywords": ["urbano","ciudad","municipio","transporte público","vivienda","movilidad","parque","sostenible","patrimonio","rehabilitación"]},
    12: {"nombre": "ODS 12 — Producción y consumo responsables",       "icono": "♻️", "color": "#BF8B2E",
         "keywords": ["residuos","reciclaje","eficiencia energética","energía renovable","huella","sostenib","medio ambiente","verde","circular","ecodiseño"]},
    16: {"nombre": "ODS 16 — Paz, justicia e instituciones sólidas",   "icono": "🏛️", "color": "#00689D",
         "keywords": ["justicia","transparencia","gobernanza","institucional","administración pública","legalidad","seguridad","registro","notaría","auditoría"]},
    17: {"nombre": "ODS 17 — Alianzas para lograr los objetivos",      "icono": "🤝", "color": "#19486A",
         "keywords": ["cooperación","alianza","financiación","subvención","fondos europeos","partenariado","colaboración público-privada","consorcio"]},
}

def _ods_score(text: str, keywords: list) -> float:
    text_l = text.lower()
    hits = sum(1 for kw in keywords if kw in text_l)
    return min(1.0, hits / max(1, len(keywords) * 0.25))


def calcular_agenda_2035(row: dict) -> list:
    """Analiza la licitación y devuelve ODS detectados con justificación."""
    blob = " ".join([
        str(row.get("titulo", "")),
        str(row.get("title", "")),
        str(row.get("cpv_descripcion", "")),
        str(row.get("objeto_contrato", "")),
        str(row.get("resumen_ejecutivo", "")),
        str(row.get("descripcion", "")),
    ])
    resultados = []
    for ods_id, meta in ODS_CATALOG.items():
        score = _ods_score(blob, meta["keywords"])
        if score > 0.0:
            nivel = "Alto" if score >= 0.6 else "Medio" if score >= 0.3 else "Bajo"
            impacto = round(score * 100, 1)
            keywords_encontradas = [kw for kw in meta["keywords"] if kw in blob.lower()]
            justificacion = (
                f"Se detectaron términos clave: {', '.join(keywords_encontradas[:4])}."
                if keywords_encontradas
                else "Alineación indirecta por contexto sectorial."
            )
            resultados.append({
                "ods_id": ods_id,
                "nombre": meta["nombre"],
                "icono": meta["icono"],
                "color": meta["color"],
                "nivel_alineacion": nivel,
                "puntuacion_impacto": impacto,
                "justificacion": justificacion,
            })
    resultados.sort(key=lambda x: -x["puntuacion_impacto"])
    return resultados[:5]  # top 5 ODS más relevantes


# ══════════════════════════════════════════════════════════════════
# Documentación requerida
# ══════════════════════════════════════════════════════════════════

DOC_FIELDS = [
    # (campo_parquet, nombre_display, tipo, obligatorio)
    ("declaracion_responsable",      "Declaración responsable",           "Declaración", True),
    ("solvencia_economica",          "Acreditación solvencia económica",  "Solvencia",   True),
    ("solvencia_tecnica",            "Acreditación solvencia técnica",    "Solvencia",   True),
    ("garantia_provisional",         "Garantía provisional",              "Garantía",    False),
    ("garantia_definitiva",          "Garantía definitiva",               "Garantía",    True),
    ("pliego_clausulas",             "Pliego de cláusulas administrativas","Pliego",     True),
    ("pliego_prescripciones",        "Pliego de prescripciones técnicas", "Pliego",      True),
    ("modelo_oferta",                "Modelo de oferta",                  "Formulario",  True),
    ("registro_licitadores",         "Certificado Registro de Licitadores","Certificado",False),
    ("certificado_calidad",          "Certificado de calidad (ISO/UNE)",  "Certificado", False),
    ("memoria_tecnica",              "Memoria técnica descriptiva",       "Memoria",     True),
    ("plan_igualdad",                "Plan de igualdad",                  "Plan",        False),
]

def _safe(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def extraer_documentacion(row: dict) -> list:
    """Extrae documentos requeridos presentes en la fila."""
    docs = []
    for campo, nombre, tipo, obligatorio in DOC_FIELDS:
        valor = _safe(row.get(campo, ""))
        if valor and valor.lower() not in ("nan", "none", "no", "false"):
            docs.append({
                "nombre": nombre,
                "tipo": tipo,
                "obligatorio": obligatorio,
                "estado": "Disponible",
                "enlace": valor if valor.startswith("http") else None,
                "descripcion": valor if not valor.startswith("http") else "",
            })
        elif obligatorio:
            docs.append({
                "nombre": nombre,
                "tipo": tipo,
                "obligatorio": True,
                "estado": "Pendiente",
                "enlace": None,
                "descripcion": "Documento requerido — pendiente de obtener.",
            })
    return docs


# ══════════════════════════════════════════════════════════════════
# Scoring / prioridad
# ══════════════════════════════════════════════════════════════════

def clasificar_prioridad(score_global: float, prob: float, importe: float) -> str:
    """Devuelve ALTA / MEDIA / BAJA según métricas del modelo."""
    if score_global >= 70 or (prob >= 0.65 and importe <= 750_000):
        return "ALTA"
    if score_global >= 45 or prob >= 0.45:
        return "MEDIA"
    return "BAJA"


def factores_positivos(row: dict, scoring: dict) -> list:
    """Lista de factores positivos detectados."""
    factores = []
    if _safe(scoring.get("score_historico_tilos", "")) not in ("", "0", "0.0"):
        factores.append("Coincidencia con adjudicaciones históricas de Los Tilos")
    cpv = _safe(row.get("cpv_codes", ""))
    if cpv:
        factores.append(f"CPV bien definido: {cpv[:40]}")
    imp = row.get("__importe", 0) or 0
    if 0 < imp <= 150_000:
        factores.append("Importe en rango óptimo (≤ 150 000 €)")
    elif 0 < imp <= 750_000:
        factores.append("Importe moderado (≤ 750 000 €)")
    proc = _safe(row.get("procedimiento_codigo", ""))
    if proc in ("1", "2", "09", "10", "Abierto", "Simplificado"):
        factores.append("Procedimiento abierto / simplificado")
    if _safe(scoring.get("score_cpv", "")) not in ("", "0"):
        factores.append("CPV con alta tasa de éxito histórico")
    if not factores:
        factores.append("Licitación activa y con información completa")
    return factores[:5]


def factores_riesgo(row: dict, scoring: dict) -> list:
    """Lista de factores de riesgo."""
    riesgos = []
    imp = row.get("__importe", 0) or 0
    if imp > 1_500_000:
        riesgos.append("Importe elevado — mayor competencia esperada")
    ofertas = row.get("ofertas_recibidas")
    try:
        if ofertas and float(ofertas) > 5:
            riesgos.append(f"Alta concurrencia: {int(float(ofertas))} ofertas recibidas")
    except (ValueError, TypeError):
        pass
    days_raw = row.get("__dias_restantes")
    try:
        if days_raw is not None and int(days_raw) < 10:
            riesgos.append("Plazo muy ajustado (< 10 días)")
    except (ValueError, TypeError):
        pass
    if _safe(scoring.get("score_global", "")) == "":
        riesgos.append("Sin scoring disponible en el modelo")
    if not riesgos:
        riesgos.append("Sin alertas de riesgo significativas detectadas")
    return riesgos[:4]


# ══════════════════════════════════════════════════════════════════
# Match scoring
# ══════════════════════════════════════════════════════════════════

def match_scoring(row: dict, scoring_df: pd.DataFrame) -> dict:
    """Busca el scoring para una licitación. Prioridad: id → expediente → title → similitud."""
    lid = _safe(row.get("licitacion_id", ""))
    exp = _safe(row.get("expediente", ""))
    title = _safe(row.get("titulo", row.get("title", "")))

    # 1. Por licitacion_id
    if lid:
        hits = scoring_df[scoring_df["licitacion_id"].astype(str).str.strip() == lid]
        if not hits.empty:
            return hits.iloc[0].to_dict()

    # 2. Por expediente
    if exp and "expediente" in scoring_df.columns:
        hits = scoring_df[scoring_df["expediente"].astype(str).str.strip() == exp]
        if not hits.empty:
            return hits.iloc[0].to_dict()

    # 3. Por título exacto
    if title and "titulo" in scoring_df.columns:
        hits = scoring_df[scoring_df["titulo"].astype(str).str.strip() == title]
        if not hits.empty:
            return hits.iloc[0].to_dict()

    # 4. Similitud textual (umbral 0.75)
    if title:
        title_col = "titulo" if "titulo" in scoring_df.columns else (
            "title" if "title" in scoring_df.columns else None
        )
        if title_col:
            best_score, best_idx = 0.0, None
            for idx, sc_title in enumerate(scoring_df[title_col].fillna("").astype(str)):
                s = _sim(title, sc_title)
                if s > best_score:
                    best_score, best_idx = s, idx
            if best_score >= 0.75 and best_idx is not None:
                return scoring_df.iloc[best_idx].to_dict()

    return {}


# ══════════════════════════════════════════════════════════════════
# Enrich enrichment parquet (Madrid / CyL)
# ══════════════════════════════════════════════════════════════════

def match_enrichment(row: dict, enrichment_df: pd.DataFrame) -> dict:
    """Busca la fila de enriquecimiento por id / expediente / similitud."""
    if enrichment_df is None or enrichment_df.empty:
        return {}
    lid = _safe(row.get("licitacion_id", ""))
    exp = _safe(row.get("expediente", ""))

    for col in ["licitacion_id", "id"]:
        if col in enrichment_df.columns and lid:
            hits = enrichment_df[enrichment_df[col].astype(str).str.strip() == lid]
            if not hits.empty:
                return hits.iloc[0].to_dict()

    if "expediente" in enrichment_df.columns and exp:
        hits = enrichment_df[enrichment_df["expediente"].astype(str).str.strip() == exp]
        if not hits.empty:
            return hits.iloc[0].to_dict()

    return {}


# ══════════════════════════════════════════════════════════════════
# Serialización segura
# ══════════════════════════════════════════════════════════════════

def _clean(v):
    """Convierte valores no serializables en tipos básicos JSON."""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (int, float, bool, str)):
        return v
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(vv) for k, vv in v.items()}
    # pandas Timestamp, numpy types, etc.
    try:
        return str(v)
    except Exception:
        return None


def _row_to_json(row) -> dict:
    if isinstance(row, dict):
        return {k: _clean(v) for k, v in row.items()}
    # pandas Series
    return {k: _clean(v) for k, v in row.items()}


# ══════════════════════════════════════════════════════════════════
# Main ETL
# ══════════════════════════════════════════════════════════════════

def load_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"  ⚠️  Fichero no encontrado: {path}", file=sys.stderr)
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        print(f"  ✅ Cargado {path.name}: {len(df)} filas, {len(df.columns)} columnas")
        return df
    except Exception as e:
        print(f"  ❌ Error leyendo {path.name}: {e}", file=sys.stderr)
        return pd.DataFrame()


def etl(silver_dir: str, output_path: str) -> int:
    silver = Path(silver_dir)

    print("\n📂 Cargando ficheros Parquet …\n")

    df_main   = load_parquet(silver / "muestra_20_segovia_18_madrid_2_restantes.parquet")
    df_madrid = load_parquet(silver / "madrid_18_html_secciones.parquet")
    df_cyl    = load_parquet(silver / "castilla_leon_html_info.parquet")
    df_score  = load_parquet(silver / "scoring_multifuente_v4_presentacion_licitaciones_tilos.parquet")

    if df_main.empty:
        print("\n❌ Dataset principal vacío. Abortando.", file=sys.stderr)
        return 1

    # Normalizar columnas clave a string
    for df in [df_main, df_madrid, df_cyl, df_score]:
        if df is None or df.empty:
            continue
        for col in ["licitacion_id", "expediente", "titulo", "title"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

    print(f"\n🔄 Procesando {len(df_main)} licitaciones …\n")

    output = []

    for _, row_s in df_main.iterrows():
        row = row_s.to_dict()

        # ── Identificar origen geográfico ──
        loc_raw = _safe(row.get("lugar_ejecucion", row.get("comunidad_autonoma", "")))
        is_madrid = any(k in loc_raw.lower() for k in ["madrid", "es30", "cm"])
        is_cyl    = any(k in loc_raw.lower() for k in ["castilla", "segovia", "valladolid",
                                                         "burgos", "zamora", "ávila", "palencia",
                                                         "salamanca", "soria", "león", "es41"])

        # ── Match enrichment ──
        enrich_row = {}
        if is_madrid and not df_madrid.empty:
            enrich_row = match_enrichment(row, df_madrid)
        elif is_cyl and not df_cyl.empty:
            enrich_row = match_enrichment(row, df_cyl)
        elif not df_madrid.empty:
            # intento en ambos
            enrich_row = match_enrichment(row, df_madrid)
            if not enrich_row and not df_cyl.empty:
                enrich_row = match_enrichment(row, df_cyl)

        # ── Match scoring ──
        scoring_row = {}
        if not df_score.empty:
            scoring_row = match_scoring(row, df_score)

        # ── Extraer métricas de scoring ──
        score_global  = None
        for col in ["score_global", "score", "puntuacion_global", "scoring_global"]:
            v = _safe(scoring_row.get(col, ""))
            if v:
                try:
                    score_global = float(v)
                    break
                except ValueError:
                    pass
        score_global = score_global or 0.0

        probabilidad = None
        for col in ["probabilidad", "prob", "probabilidad_adjudicacion", "win_probability"]:
            v = _safe(scoring_row.get(col, ""))
            if v:
                try:
                    probabilidad = float(v)
                    break
                except ValueError:
                    pass
        if probabilidad is None:
            probabilidad = round(score_global / 100, 4) if score_global else 0.5

        ranking = _safe(scoring_row.get("ranking", scoring_row.get("rank", "")))
        recomendacion_modelo = _safe(scoring_row.get("recomendacion", scoring_row.get("recommendation", "")))

        # ── Importe ──
        importe_raw = row.get("importe_total") or row.get("importe_sin_impuestos") or 0
        try:
            importe = float(str(importe_raw).replace(",", ".")) if importe_raw else 0.0
        except (ValueError, TypeError):
            importe = 0.0
        row["__importe"] = importe

        # ── Días restantes ──
        deadline_str = _safe(row.get("presentacion_hasta", ""))
        dias_restantes = None
        if deadline_str:
            try:
                from datetime import datetime, timezone
                dl = pd.to_datetime(deadline_str, dayfirst=True, errors="coerce")
                if dl is not pd.NaT:
                    now = pd.Timestamp.now(tz="UTC").tz_localize(None) if dl.tzinfo is None else pd.Timestamp.now(tz="UTC")
                    dias_restantes = max(-999, (dl - now).days)
            except Exception:
                pass
        row["__dias_restantes"] = dias_restantes

        # ── Prioridad ──
        prioridad = clasificar_prioridad(score_global, probabilidad, importe)

        # ── Agenda 2035 ──
        merged_for_ods = {**row, **enrich_row}
        agenda_2035 = calcular_agenda_2035(merged_for_ods)

        # ── Documentación ──
        merged_for_docs = {**row, **enrich_row}
        documentacion = extraer_documentacion(merged_for_docs)

        # ── Factores ──
        factores_pos   = factores_positivos(merged_for_ods, scoring_row)
        factores_risk  = factores_riesgo(row, scoring_row)

        # ── Resumen ejecutivo y secciones HTML ──
        resumen_ejecutivo = (
            _safe(enrich_row.get("resumen_ejecutivo", ""))
            or _safe(enrich_row.get("executive_summary", ""))
            or _safe(enrich_row.get("descripcion", ""))
            or _safe(row.get("objeto_contrato", ""))
        )
        secciones_html = (
            _safe(enrich_row.get("secciones_html", ""))
            or _safe(enrich_row.get("html_sections", ""))
        )

        # ── Construir objeto de salida ──
        record = {
            # === Datos originales principales ===
            **_row_to_json(row),

            # === Enrichment (sin sobreescribir claves de row si ya existen) ===
            **{k: _clean(v) for k, v in enrich_row.items()
               if k not in row or not _safe(row.get(k))},

            # === Scoring ===
            "__scoring": {
                "score_global":    round(score_global, 2),
                "probabilidad":    round(probabilidad, 4),
                "ranking":         ranking,
                "recomendacion":   recomendacion_modelo,
                **{k: _clean(v) for k, v in scoring_row.items()
                   if k not in ("licitacion_id", "expediente", "titulo", "title")},
            },
            "__prioridad":         prioridad,
            "__agenda_2035":       agenda_2035,
            "__documentacion":     documentacion,
            "__factores_positivos": factores_pos,
            "__factores_riesgo":   factores_risk,
            "__resumen_ejecutivo": resumen_ejecutivo,
            "__secciones_html":    secciones_html,
            "__matched_enrichment": bool(enrich_row),
            "__matched_scoring":    bool(scoring_row),
        }

        output.append(record)

    # ── Ordenar por prioridad + score ──
    prio_order = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    output.sort(key=lambda x: (
        prio_order.get(x.get("__prioridad", "BAJA"), 2),
        -(x.get("__scoring", {}).get("score_global", 0) or 0)
    ))

    # ── Guardar JSON ──
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ JSON generado: {out_path}")
    print(f"   Total licitaciones: {len(output)}")
    alta  = sum(1 for r in output if r.get("__prioridad") == "ALTA")
    media = sum(1 for r in output if r.get("__prioridad") == "MEDIA")
    baja  = sum(1 for r in output if r.get("__prioridad") == "BAJA")
    print(f"   🟢 ALTA:  {alta}  |  🟡 MEDIA: {media}  |  ⚪ BAJA: {baja}")
    matched_e = sum(1 for r in output if r.get("__matched_enrichment"))
    matched_s = sum(1 for r in output if r.get("__matched_scoring"))
    print(f"   Enriquecimiento: {matched_e}/{len(output)} matches")
    print(f"   Scoring:         {matched_s}/{len(output)} matches")
    return 0


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="ETL — GovAI Licitaciones Los Tilos v2.0"
    )
    parser.add_argument(
        "--silver",
        default="./data/Silver",
        help="Ruta a la carpeta Silver con los ficheros Parquet",
    )
    parser.add_argument(
        "--output",
        default="./data/Silver/licitaciones_tilos.json",
        help="Ruta del JSON de salida consumido por el HTML",
    )
    args = parser.parse_args()
    sys.exit(etl(args.silver, args.output))


if __name__ == "__main__":
    main()
