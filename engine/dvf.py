from datetime import date

import requests

# API Données foncières du Cerema — DVF+ open-data.
# Plusieurs points d'accès sont testés dans l'ordre pour éviter qu'une
# indisponibilité DNS d'un endpoint bloque Local Matcher.
BASE_URLS = [
    "https://apidf-preprod.cerema.fr",
    "https://apidf.k8-dev.cerema.fr",
    "https://apidf.cerema.fr",
]
TIMEOUT = 20
MAX_PAGES = 8
PAGE_SIZE = 500


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalise_parcelle(value):
    return "".join(ch for ch in _clean(value).upper() if ch.isalnum())


def _parcel_in_value(value, parcel_code):
    target = _normalise_parcelle(parcel_code)
    if not target:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_parcel_in_value(v, target) for v in value)
    text = _clean(value)
    if not text:
        return False
    normalised = _normalise_parcelle(text)
    return target == normalised or target in normalised


def _payload_rows(payload):
    if not isinstance(payload, dict):
        return []
    for key in ("results", "resultats", "data", "items", "features"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _request(base_url, params):
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/dvf_opendata/mutations/",
            params=params,
            timeout=TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "Local-Matcher/10.4",
            },
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"connexion impossible: {exc}") from exc

    if response.status_code == 429:
        raise RuntimeError("API DVF+ temporairement limitée (429).")
    if response.status_code >= 400:
        detail = response.text[:250].replace("\n", " ")
        raise RuntimeError(f"HTTP {response.status_code} ({detail})")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("Réponse DVF+ illisible.") from exc


def _extract_rows(parcel_code, payload):
    rows = []
    for raw in _payload_rows(payload):
        if not isinstance(raw, dict):
            continue
        props = raw.get("properties", raw)
        if not isinstance(props, dict):
            continue

        parcel_values = [
            props.get("idpar"),
            props.get("id_parcelle"),
            props.get("numero_plan"),
            props.get("l_idpar"),
            props.get("l_idparmut"),
        ]
        if not any(_parcel_in_value(v, parcel_code) for v in parcel_values):
            continue

        rows.append({
            "ID mutation": _clean(props.get("idmutation") or props.get("id_mutation")),
            "Date mutation": _clean(props.get("datemut") or props.get("date_mutation")),
            "Année mutation": _clean(props.get("anneemut") or props.get("annee_mutation")),
            "Nature mutation": _clean(props.get("libnatmut") or props.get("nature_mutation") or props.get("lib_nature_mutation")),
            "Valeur foncière (€)": props.get("valeurfonc", props.get("valeur_fonciere", "")),
            "Type de bien": _clean(props.get("libtypbien") or props.get("type_local")),
            "Surface bâtie (m²)": props.get("sbati", props.get("surface_reelle_bati", "")),
            "Surface terrain (m²)": props.get("sterr", props.get("surface_terrain", "")),
            "Nombre de lots": props.get("nblot", props.get("nombre_lots", "")),
            "Parcelle": _clean(props.get("idpar") or props.get("id_parcelle") or parcel_code),
            "Commune": _clean(props.get("nomcomm") or props.get("nom_commune") or props.get("commune")),
        })

    dedup = {}
    for row in rows:
        key = (row["ID mutation"], row["Date mutation"], row["Valeur foncière (€)"])
        dedup[key] = row
    rows = list(dedup.values())
    rows.sort(key=lambda r: r.get("Date mutation", ""), reverse=True)
    return rows


def search_dvf_by_parcel(parcel_code: str, lat=None, lon=None, size: int = 50):
    """Retourne les mutations DVF+ liées à une parcelle cadastrale.

    Recherche une petite emprise autour du point géocodé puis filtre strictement
    sur la référence cadastrale. Trois endpoints Cerema sont essayés pour
    absorber une indisponibilité ponctuelle ou DNS d'un point d'accès.
    """
    code = _normalise_parcelle(parcel_code)
    if not code:
        return []
    if lat is None or lon is None:
        raise RuntimeError("Coordonnées du local nécessaires pour interroger DVF+.")

    lat = float(lat)
    lon = float(lon)
    delta = 0.0025
    bbox = [lon - delta, lat - delta, lon + delta, lat + delta]
    params = {
        "in_bbox": ",".join(f"{v:.6f}" for v in bbox),
        "fields": "all",
        "page_size": PAGE_SIZE,
    }

    errors = []
    for base_url in BASE_URLS:
        try:
            all_rows = []
            next_url = None
            for page in range(1, MAX_PAGES + 1):
                if next_url:
                    response = requests.get(
                        next_url,
                        timeout=TIMEOUT,
                        headers={"Accept": "application/json", "User-Agent": "Local-Matcher/10.4"},
                    )
                    if response.status_code >= 400:
                        detail = response.text[:250].replace("\n", " ")
                        raise RuntimeError(f"HTTP {response.status_code} ({detail})")
                    payload = response.json()
                else:
                    page_params = dict(params)
                    page_params["page"] = page
                    payload = _request(base_url, page_params)

                page_rows = _extract_rows(code, payload)
                all_rows.extend(page_rows)

                next_value = payload.get("next") if isinstance(payload, dict) else None
                if next_value:
                    next_url = next_value
                else:
                    raw_count = len(_payload_rows(payload))
                    if raw_count < PAGE_SIZE:
                        break
                    next_url = None

            dedup = {}
            for row in all_rows:
                key = (row["ID mutation"], row["Date mutation"], row["Valeur foncière (€)"])
                dedup[key] = row
            rows = list(dedup.values())
            rows.sort(key=lambda r: r.get("Date mutation", ""), reverse=True)
            return rows[: max(1, min(int(size), 200))]
        except Exception as exc:
            errors.append(f"{base_url}: {exc}")
            continue

    raise RuntimeError(
        "DVF+ temporairement indisponible après essai des points d'accès Cerema. "
        + " | ".join(errors[:3])
    )


def dvf_signal(rows):
    """Résume le signal de mutation sans inférer l'identité de l'acquéreur."""
    if not rows:
        return {
            "Statut DVF": "Aucune mutation DVF+ trouvée sur la parcelle",
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
        "Statut DVF": f"{len(rows)} mutation(s) trouvée(s) sur la parcelle",
        "Dernière mutation DVF": latest_date,
        "Nature dernière mutation": latest.get("Nature mutation", ""),
        "Signal changement propriétaire": signal,
    }
