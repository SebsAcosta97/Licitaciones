from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
SILVER_DIR = DATA_DIR / "Silver"
SOURCE_PATH = SILVER_DIR / "df_tilos_limpio.parquet"
OUTPUT_PATH = SILVER_DIR / "licitaciones_abiertas.json"

EXPORT_COLUMNS = [
    "licitacion_id",
    "titulo",
    "expediente",
    "detail_url",
    "url",
    "updated",
    "fecha_publicacion",
    "presentacion_hasta",
    "presentacion_hora",
    "organo_contratacion",
    "organo_dir3",
    "estado",
    "estado_codigo",
    "tipo_contrato",
    "tipo_contrato_codigo",
    "procedimiento_codigo",
    "importe_total",
    "importe_sin_impuestos",
    "adjudicatario",
    "adjudicatario_nif",
    "cpv_codes",
    "cpv_descripcion",
    "cpv_nivel",
    "lugar_ejecucion",
    "lugar_ejecucion_codigo",
    "contrato_duracion",
    "contrato_duracion_unidad",
    "ofertas_recibidas",
    "fuente_publicacion",
    "notice_types",
]

TIPO_CONTRATO_MAP = {
    "1": "Obras",
    "2": "Servicios",
    "3": "Suministros",
    "8": "Patrimonial",
    "21": "Concesión de obras",
    "22": "Concesión de servicios",
    "50": "Mixto",
}

ESTADO_MAP = {
    "EV": "En evaluación",
    "PUB": "Publicado",
    "RES": "Resuelto",
    "ADJ": "Adjudicado",
    "ANUL": "Anulado",
    "DES": "Desierto",
    "CERR": "Cerrado",
    "PRE": "En presentación",
}


def log(msg: str) -> None:
    print(f"[generate_dashboard_json] {msg}")


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def normalize_contract_code(value: Any) -> str:
    raw = safe_text(value)
    if not raw:
        return ""
    if raw in TIPO_CONTRATO_MAP:
        return raw
    raw_upper = raw.upper()
    for code, label in TIPO_CONTRATO_MAP.items():
        if raw_upper == label.upper():
            return code
    return raw


def parse_amount(value: Any) -> float | None:
    raw = safe_text(value)
    if not raw:
        return None
    normalized = "".join(ch for ch in raw if ch.isdigit() or ch in ",.-")
    if not normalized:
        return None
    normalized = normalized.replace(",", ".")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    if amount < 0:
        return None
    return round(amount, 2)


def parse_date(value: Any) -> datetime | None:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        dt = value.to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    text = safe_text(value)
    if not text:
        return None

    for candidate in [text, text.replace("Z", "+00:00")]:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def is_pub(row: dict[str, Any]) -> bool:
    """Devuelve True solo si el estado_codigo es exactamente 'PUB'."""
    return safe_text(row.get("estado_codigo")).upper() == "PUB"


def normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    licitacion_id = safe_text(row.get("licitacion_id"))
    title = safe_text(row.get("titulo") or row.get("title"))
    organo = safe_text(row.get("organo_contratacion"))
    estado = safe_text(row.get("estado_codigo")).upper()
    tipo = safe_text(row.get("tipo_contrato"))
    tipo_codigo = safe_text(row.get("tipo_contrato_codigo"))
    lugar = safe_text(row.get("lugar_ejecucion") or row.get("lugar_ejecucion_codigo"))

    if not licitacion_id:
        return None
    if not title and not organo:
        return None

    importe_sin_impuestos = parse_amount(row.get("importe_sin_impuestos"))
    importe_total = parse_amount(row.get("importe_total"))
    if importe_total is None:
        importe_total = importe_sin_impuestos

    normalized: dict[str, Any] = {}
    for column in EXPORT_COLUMNS:
        value = row.get(column)
        if column == "licitacion_id":
            normalized[column] = licitacion_id
        elif column == "titulo":
            normalized[column] = title
        elif column == "estado_codigo":
            normalized[column] = estado
        elif column == "estado":
            normalized[column] = safe_text(value) or ESTADO_MAP.get(estado, estado)
        elif column == "tipo_contrato":
            normalized[column] = tipo or TIPO_CONTRATO_MAP.get(normalize_contract_code(tipo_codigo), tipo_codigo)
        elif column == "tipo_contrato_codigo":
            normalized[column] = tipo_codigo or normalize_contract_code(tipo)
        elif column == "importe_total":
            normalized[column] = importe_total
        elif column == "importe_sin_impuestos":
            normalized[column] = importe_sin_impuestos
        elif column == "lugar_ejecucion":
            normalized[column] = lugar
        elif column in {"updated", "fecha_publicacion", "presentacion_hasta"}:
            dt = parse_date(value)
            normalized[column] = dt.date().isoformat() if dt else ""
        elif column in {"contrato_duracion", "ofertas_recibidas"}:
            normalized[column] = parse_amount(value)
        else:
            normalized[column] = safe_text(value)

    normalized["title"] = title
    return normalized


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}

    def row_score(item: dict[str, Any]) -> tuple[int, float, int]:
        amount = item.get("importe_total")
        amount_score = amount if isinstance(amount, (int, float)) else 0.0
        date_score = parse_date(item.get("fecha_publicacion"))
        date_ts = int(date_score.timestamp()) if date_score else 0
        filled = sum(1 for v in item.values() if v not in (None, ""))
        return (filled, amount_score, date_ts)

    for row in rows:
        key = row["licitacion_id"]
        current = by_id.get(key)
        if current is None or row_score(row) > row_score(current):
            by_id[key] = row

    out = list(by_id.values())
    out.sort(
        key=lambda x: parse_date(x.get("fecha_publicacion")) or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )
    return out


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    n = float(num_bytes)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{num_bytes} B"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera dataset con licitaciones en estado PUB para el dashboard frontend."
    )
    parser.add_argument("--input", type=Path, default=SOURCE_PATH, help="Ruta del parquet de entrada")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Ruta de salida JSON")
    parser.add_argument("--max-records", type=int, default=2000, help="Máximo de registros finales (0 = sin límite)")
    args = parser.parse_args()

    input_path: Path = args.input
    output_path: Path = args.output

    if not input_path.exists():
        raise FileNotFoundError(f"No existe archivo de entrada: {input_path}")

    log(f"Entrada: {input_path}")
    log(f"Salida:  {output_path}")

    df = pd.read_parquet(input_path)
    raw = df.to_dict(orient="records")
    total_input = len(raw)

    removed_corrupt = 0
    filtered_non_pub = 0
    normalized_rows: list[dict[str, Any]] = []

    for row in raw:
        if not isinstance(row, dict):
            removed_corrupt += 1
            continue

        # Filtro principal: solo PUB
        if not is_pub(row):
            filtered_non_pub += 1
            continue

        normalized = normalize_row(row)
        if normalized is None:
            removed_corrupt += 1
            continue

        normalized_rows.append(normalized)

    deduped = dedupe_rows(normalized_rows)

    if args.max_records > 0:
        deduped = deduped[: args.max_records]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, separators=(",", ":"))

    source_size = input_path.stat().st_size
    output_size = output_path.stat().st_size

    log("--- Métricas ETL ---")
    log(f"Registros entrada:            {total_input:,}")
    log(f"Descartados (no PUB):         {filtered_non_pub:,}")
    log(f"Descartados (corruptos):      {removed_corrupt:,}")
    log(f"Registros PUB normalizados:   {len(normalized_rows):,}")
    log(f"Registros finales (deduped):  {len(deduped):,}")
    log(f"Tamaño entrada:               {human_size(source_size)}")
    log(f"Tamaño salida:                {human_size(output_size)}")
    log("Dataset frontend generado correctamente.")


if __name__ == "__main__":
    main()