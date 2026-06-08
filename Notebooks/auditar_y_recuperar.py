
import json
from pathlib import Path

# =========================
# CONFIGURACIÓN
# =========================

BASE_DIR = Path(__file__).resolve().parent.parent

BRONZE_DIR = BASE_DIR / "data" / "Bronze"

LICITACIONES_FILE = BRONZE_DIR / "licitaciones.json"
ADJUDICATARIOS_FILE = BRONZE_DIR / "adjudicatarios_licitaciones.json"

DOCUMENTOS_FILE = BRONZE_DIR / "documentos_licitacion.json"
TIMELINE_DOCS_FILE = BRONZE_DIR / "timeline_documentos.json"
TIMELINE_OTROS_FILE = BRONZE_DIR / "timeline_otros_documentos.json"


# =========================
# UTILIDADES
# =========================

def load_json(path):
    if not path.exists():
        print(f"[ERROR] No existe: {path}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_id(record):
    """
    Intenta encontrar automáticamente el identificador.
    Ajustaremos esto después si tus JSON usan un nombre específico.
    """

    posibles = [
        "licitacion_id",
        "id",
        "expediente",
        "expediente_id",
        "contract_folder_id"
    ]

    for campo in posibles:
        if campo in record and record[campo]:
            return str(record[campo])

    return None


def build_id_set(records):
    ids = set()

    for r in records:
        lic_id = detect_id(r)

        if lic_id:
            ids.add(lic_id)

    return ids


# =========================
# AUDITORÍA
# =========================

def main():

    print("\n====================================")
    print("AUDITORÍA DE CONSISTENCIA")
    print("====================================\n")

    licitaciones = load_json(LICITACIONES_FILE)
    adjudicatarios = load_json(ADJUDICATARIOS_FILE)

    documentos = load_json(DOCUMENTOS_FILE)
    timeline_docs = load_json(TIMELINE_DOCS_FILE)
    timeline_otros = load_json(TIMELINE_OTROS_FILE)

    ids_maestro = (
        build_id_set(licitaciones)
        |
        build_id_set(adjudicatarios)
    )

    ids_documentos = build_id_set(documentos)
    ids_timeline_docs = build_id_set(timeline_docs)
    ids_timeline_otros = build_id_set(timeline_otros)

    print(f"Licitaciones maestro: {len(ids_maestro):,}")

    print("\n----- DOCUMENTOS -----")
    faltan_docs = ids_maestro - ids_documentos
    print(f"Presentes: {len(ids_documentos):,}")
    print(f"Faltantes: {len(faltan_docs):,}")

    print("\n----- TIMELINE DOCS -----")
    faltan_timeline_docs = ids_maestro - ids_timeline_docs
    print(f"Presentes: {len(ids_timeline_docs):,}")
    print(f"Faltantes: {len(faltan_timeline_docs):,}")

    print("\n----- TIMELINE OTROS -----")
    faltan_timeline_otros = ids_maestro - ids_timeline_otros
    print(f"Presentes: {len(ids_timeline_otros):,}")
    print(f"Faltantes: {len(faltan_timeline_otros):,}")

    print("\n====================================")
    print("MUESTRA DE IDS FALTANTES")
    print("====================================")

    for x in list(faltan_docs)[:20]:
        print(x)

    print("\nAuditoría finalizada.")


if __name__ == "__main__":
    main()

