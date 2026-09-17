import requests
from datetime import date

BASE_URL = "https://api.cquest.org/dvf"
TIMEOUT = 20


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def search_dvf_by_parcel(parcel_code: str, size: int = 50):
    """Return DVF transactions linked to one cadastral parcel.

    Source: public micro-API exposing DVF data. Availability is not guaranteed.
    DVF identifies transactions but does not publish buyer/seller names.
    """
    code = _clean(parcel_code).upper()
    if not code:
        return []

    try:
        r = requests.get(
            BASE_URL,
            params={"numero_plan": code},
            timeout=TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": "Local-Matcher/10.1"},
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Service DVF indisponible : {exc}") from exc

    if r.status_code != 200:
        raise RuntimeError(f"Service DVF : HTTP {r.status_code}")

    try:
        payload = r.json()
    except ValueError as exc:
        raise RuntimeError("Réponse DVF illisible") from exc

    rows = payload.get("resultats", []) or payload.get("features", []) or []
    if isinstance(rows, dict):
        rows = [rows]

    out = []
    seen = set()
    for raw in rows[: max(1, min(int(size), 200))]:
        props = raw.get("properties", raw) if isinstance(raw, dict) else {}
        mutation_id = _clean(props.get("id_mutation"))
        key = (
            mutation_id,
            _clean(props.get("date_mutation")),
            _clean(props.get("numero_disposition")),
            _clean(props.get("valeur_fonciere")),
            _clean(props.get("id_parcelle") or props.get("numero_plan")),
            _clean(props.get("type_local")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "ID mutation": mutation_id,
            "Date mutation": _clean(props.get("date_mutation")),
            "Nature mutation": _clean(props.get("nature_mutation")),
            "Valeur foncière (€)": props.get("valeur_fonciere", ""),
            "Type local": _clean(props.get("type_local")),
            "Surface bâtie (m²)": props.get("surface_reelle_bati", ""),
            "Surface terrain (m²)": props.get("surface_terrain", ""),
            "Nombre de lots": props.get("nombre_lots", ""),
            "Parcelle": _clean(props.get("id_parcelle") or props.get("numero_plan")),
            "Adresse mutation": " ".join(
                x for x in [
                    _clean(props.get("adresse_numero")),
                    _clean(props.get("adresse_suffixe")),
                    _clean(props.get("adresse_nom_voie")),
                ] if x
            ),
            "Commune": _clean(props.get("nom_commune")),
        })

    out.sort(key=lambda x: x.get("Date mutation", ""), reverse=True)
    return out


def dvf_signal(rows):
    """Summarise the latest transaction signal without inferring a buyer/seller."""
    if not rows:
        return {
            "Statut DVF": "Aucune mutation DVF trouvée",
            "Dernière mutation DVF": "",
            "Nature dernière mutation": "",
            "Signal changement propriétaire": "Non démontré",
        }

    latest = rows[0]
    latest_date = _clean(latest.get("Date mutation"))
    try:
        age_years = (date.today() - date.fromisoformat(latest_date[:10])).days / 365.25
    except Exception:
        age_years = None

    if age_years is not None and age_years <= 5:
        signal = "Mutation récente détectée — changement de propriétaire possible"
    else:
        signal = "Mutation ancienne détectée — changement actuel non démontré"

    return {
        "Statut DVF": f"{len(rows)} mutation(s) trouvée(s)",
        "Dernière mutation DVF": latest_date,
        "Nature dernière mutation": latest.get("Nature mutation", ""),
        "Signal changement propriétaire": signal,
    }
