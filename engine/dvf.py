import time
from datetime import date

import requests

# Micro-API DVF (preuve de concept publique).
# La disponibilité n'est pas garantie : on ajoute donc des retries et un fallback
# par section cadastrale avant de considérer le service indisponible.
BASE_URLS = [
    "https://api.cquest.org/dvf",
    "http://api.cquest.org/dvf",
]
TIMEOUT = 15
RETRIES = 2


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _request(params):
    last_error = None
    for base_url in BASE_URLS:
        for attempt in range(RETRIES + 1):
            try:
                r = requests.get(
                    base_url,
                    params=params,
                    timeout=TIMEOUT,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Local-Matcher/10.2",
                    },
                )
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        last_error = RuntimeError("Réponse DVF illisible")
                        if attempt < RETRIES:
                            time.sleep(0.8 * (attempt + 1))
                            continue
                        raise last_error from exc

                last_error = RuntimeError(f"HTTP {r.status_code}")
                # 5xx = service temporairement indisponible : on retente.
                if r.status_code >= 500 and attempt < RETRIES:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                break
            except requests.RequestException as exc:
                last_error = RuntimeError(f"connexion impossible : {exc}")
                if attempt < RETRIES:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                break

    raise RuntimeError(f"Service DVF temporairement indisponible ({last_error or 'erreur inconnue'})")


def _rows_from_payload(payload, size=200):
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


def search_dvf_by_parcel(parcel_code: str, size: int = 50):
    """Return DVF transactions linked to a cadastral parcel.

    Primary query: exact parcel. Fallback: cadastral section, then filter
    returned rows back to the requested parcel. Buyer/seller identities are
    not inferred from DVF open data.
    """
    code = _clean(parcel_code).upper()
    if not code:
        return []

    # Format attendu : code INSEE (5) + section (4) + parcelle (4).
    # Exemple Mondeville : 14437 + CE + 0138 => 14437000CE0138.
    code_commune = code[:5] if len(code) >= 5 else ""
    section = code[5:9] if len(code) >= 9 else ""

    try:
        payload = _request({"numero_plan": code})
        rows = _rows_from_payload(payload, size)
        if rows:
            return rows
    except RuntimeError as primary_error:
        # Fallback par section : utile si l'API refuse ponctuellement le
        # filtre parcelle mais reste capable de répondre sur la section.
        if code_commune and section:
            try:
                payload = _request({"section": f"{code_commune}{section}"})
                rows = _rows_from_payload(payload, 200)
                filtered = [r for r in rows if _clean(r.get("Parcelle")).upper() == code]
                return filtered[: max(1, min(int(size), 200))]
            except RuntimeError as fallback_error:
                raise RuntimeError(
                    "DVF temporairement indisponible après nouvelle tentative "
                    f"et fallback cadastral ({fallback_error})"
                ) from primary_error
        raise

    return []


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
