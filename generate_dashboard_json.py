from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
SOURCE_PATH = DATA_DIR / "licitaciones.json"
OUTPUT_PATH = DATA_DIR / "licitaciones_abiertas.json"
SERVICIOS_CODE = "2"
SERVICIOS_LABEL = "Servicios"

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
}

OPEN_STATE_CODES = {
    "EV", "PUB", "PCP", "PRES", "ABI", "AB", "OPEN", "ACT", "ACTIVA", "ACTIVO"
}

CLOSED_STATE_CODES = {
    "ADJ", "RES", "ANUL", "CERR", "DES", "ARCH", "FORM", "CAN", "CANCELADA", "CANCELADO"
}

OPEN_TEXT_HINTS = {
    "abierta", "abierto", "activa", "activo", "evaluacion", "evaluación", "licitacion", "publicada", "publicado"
}

CLOSED_TEXT_HINTS = {
    "adjudicada", "adjudicado", "resuelta", "resuelto", "formalizada", "cancelada", "cancelado", "anulada", "anulado", "cerrada", "cerrado", "desierta", "desierto", "archivada", "archivado"
}


def log(msg: str) -> None:
    print(f"[generate_dashboard_json] {msg}")


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_contract_code(value: Any) -> str:
    raw = safe_text(value)
    if not raw:
        return ""
    if raw in TIPO_CONTRATO_MAP:
        return raw
    raw_upper = raw.upper()
    if raw_upper in {"SERVICIOS", "SERVICIO", "SERVICES"}:
        return SERVICIOS_CODE
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
    text = safe_text(value)
    if not text:
        return None

    candidates = [
        text,
        text.replace("Z", "+00:00"),
    ]

    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    return None


def is_open_active(row: dict[str, Any]) -> bool:
    code = safe_text(row.get("estado_codigo")).upper()
    state_text = " ".join(
        safe_text(row.get(k)).lower()
        for k in ("estado_codigo", "estado", "estado_nombre", "status", "status_text")
    )

    if code in CLOSED_STATE_CODES:
        return False
    if code in OPEN_STATE_CODES:
        return True

    if any(hint in state_text for hint in CLOSED_TEXT_HINTS):
        return False
    if any(hint in state_text for hint in OPEN_TEXT_HINTS):
        return True

    return False


def is_recent(row: dict[str, Any], cutoff: datetime) -> bool:
    for field in ("fecha_publicacion", "updated", "presentacion_hasta"):
        dt = parse_date(row.get(field))
        if dt and dt >= cutoff:
            return True
    return False


def normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    licitacion_id = safe_text(row.get("licitacion_id"))
    title = safe_text(row.get("title"))
    organo = safe_text(row.get("organo_contratacion"))
    estado = safe_text(row.get("estado_codigo")).upper()
    tipo = normalize_contract_code(row.get("tipo_contrato_codigo"))
    fecha = safe_text(row.get("fecha_publicacion"))
    lugar = safe_text(row.get("lugar_ejecucion"))
    url = safe_text(row.get("url") or row.get("detail_url"))

    if not licitacion_id:
        return None
    if not title and not organo:
        return None

    amount = parse_amount(row.get("importe_total"))
    if amount is None:
        amount = parse_amount(row.get("importe_estimado"))

    normalized = {
        "licitacion_id": licitacion_id,
        "title": title,
        "organo_contratacion": organo,
        "estado_codigo": estado,
        "estado": ESTADO_MAP.get(estado, estado),
        "tipo_contrato_codigo": SERVICIOS_LABEL if tipo == SERVICIOS_CODE else TIPO_CONTRATO_MAP.get(tipo, tipo),
        "tipo_contrato": SERVICIOS_LABEL if tipo == SERVICIOS_CODE else TIPO_CONTRATO_MAP.get(tipo, tipo),
        "importe_total": amount,
        "fecha_publicacion": fecha,
        "lugar_ejecucion": lugar,
        "url": url,
    }
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


def to_json_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


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
        description="Genera dataset optimizado para dashboard frontend de licitaciones abiertas/activas."
    )
    parser.add_argument("--input", type=Path, default=SOURCE_PATH, help="Ruta del JSON histórico")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Ruta de salida optimizada")
    parser.add_argument("--months", type=int, default=6, help="Ventana de recencia en meses")
    parser.add_argument("--max-records", type=int, default=2000, help="Máximo de registros finales")
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output

    if not input_path.exists():
        raise FileNotFoundError(f"No existe archivo de entrada: {input_path}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.months) * 30)

    log(f"Entrada: {input_path}")
    log(f"Salida: {output_path}")
    log(f"Ventana de recencia: últimos {args.months} meses (desde {cutoff.date()})")

    with input_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("El JSON de entrada debe ser una lista de objetos.")

    total_input = len(raw)
    source_size = to_json_size(input_path)

    cleaned = 0
    removed_corrupt = 0
    filtered_open = 0
    filtered_recent = 0

    normalized_rows: list[dict[str, Any]] = []

    for row in raw:
        if not isinstance(row, dict):
            removed_corrupt += 1
            continue

        normalized = normalize_row(row)
        if normalized is None:
            removed_corrupt += 1
            continue

        cleaned += 1

        if normalize_contract_code(row.get("tipo_contrato_codigo")) != SERVICIOS_CODE:
            filtered_open += 1
            continue

        if not is_open_active(row):
            filtered_open += 1
            continue

        if not is_recent(row, cutoff):
            filtered_recent += 1
            continue

        normalized_rows.append(normalized)

    deduped = dedupe_rows(normalized_rows)

    if args.max_records > 0:
        deduped = deduped[: args.max_records]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, separators=(",", ":"))

    output_size = to_json_size(output_path)

    reduction_pct = 0.0
    if total_input > 0:
        reduction_pct = (1 - (len(deduped) / total_input)) * 100

    size_reduction_pct = 0.0
    if source_size > 0:
        size_reduction_pct = (1 - (output_size / source_size)) * 100

    log("--- Métricas ETL ---")
    log(f"Registros entrada: {total_input:,}")
    log(f"Registros normalizados válidos: {cleaned:,}")
    log(f"Descartados por corrupción/estructura: {removed_corrupt:,}")
    log(f"Descartados por no abiertos/activos: {filtered_open:,}")
    log(f"Descartados por no recientes: {filtered_recent:,}")
    log(f"Registros finales: {len(deduped):,}")
    log(f"Reducción de registros: {reduction_pct:.2f}%")
    log(f"Tamaño origen: {human_size(source_size)}")
    log(f"Tamaño salida: {human_size(output_size)}")
    log(f"Reducción de tamaño: {size_reduction_pct:.2f}%")
    log("Dataset frontend generado correctamente.")


if __name__ == "__main__":
    main()
