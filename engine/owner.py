import requests

BASE_URL = "https://opendata.koumoul.com/data-fair/api/v1/datasets/parcelles-des-personnes-morales/lines"
TIMEOUT = 20


def _query(base_url, parcel_code, size=20):
    params = {
        "format": "json",
        "size": max(1, min(int(size), 100)),
        "select": "code_parcelle,departement,code_commune,nom_commune,adresse,contenance_parcelle,code_droit,numero_siren,groupe_personne,code_forme_juridique,forme_juridique_abregee,denomination",
        "qs": f'code_parcelle:"{parcel_code}"',
    }
    r = requests.get(base_url, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"Erreur base propriétaires : HTTP {r.status_code}: {r.text[:250]}")
    return r.json()


def search_moral_owners(parcel_code: str, size: int = 20):
    """Find legal-entity rights holders for a cadastral parcel.

    Source: Koumoul's public MAJIC-derived API, based on DGFiP open-data files.
    The data is a dated cadastral snapshot and is not proof of ownership today.
    """
    code = (parcel_code or "").strip().upper()
    if not code:
        return []
    payload = _query(BASE_URL, code, size=size)
    rows = payload.get("results", []) or []
    out = []
    for row in rows:
        out.append({
            "Parcelle": row.get("code_parcelle", ""),
            "Dénomination": row.get("denomination", ""),
            "SIREN": row.get("numero_siren", ""),
            "Forme juridique": row.get("forme_juridique_abregee", ""),
            "Code droit": row.get("code_droit", ""),
            "Groupe personne": row.get("groupe_personne", ""),
            "Contenance (m²)": row.get("contenance_parcelle", ""),
            "Adresse foncière": row.get("adresse", ""),
            "Commune": row.get("nom_commune", ""),
        })
    return out
