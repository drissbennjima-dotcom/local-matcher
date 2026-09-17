import requests

REVERSE_URL = "https://data.geopf.fr/geocodage/reverse"
TIMEOUT = 15


def reverse_parcel(lat: float, lon: float, limit: int = 3):
    """Find nearby cadastral parcels from a WGS84 point via Géoplateforme.

    The first result is the nearest parcel returned by the national service.
    This is a geographic linkage, not proof of ownership.
    """
    if lat is None or lon is None:
        return []
    try:
        r = requests.get(
            REVERSE_URL,
            params={
                "lon": float(lon),
                "lat": float(lat),
                "index": "parcel",
                "limit": max(1, min(int(limit), 10)),
            },
            timeout=TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Erreur réseau du service cadastral : {exc}") from exc

    if r.status_code == 429:
        raise RuntimeError("Service cadastral temporairement limité (429). Réessayez dans quelques secondes.")
    if r.status_code != 200:
        raise RuntimeError(f"Erreur du service cadastral : HTTP {r.status_code}: {r.text[:250]}")

    features = r.json().get("features", [])
    rows = []
    for feature in features:
        props = feature.get("properties") or {}
        rows.append({
            "Parcelle cadastrale": props.get("id", ""),
            "Département": props.get("departmentcode", ""),
            "Commune cadastrale": props.get("city", ""),
            "Section": props.get("section", ""),
            "Feuille": props.get("sheet", ""),
            "Numéro parcelle": props.get("number", ""),
            "Distance parcelle (m)": props.get("distance", ""),
            "Score parcelle": props.get("score", ""),
        })
    return rows


def best_parcel(lat: float, lon: float):
    rows = reverse_parcel(lat, lon, limit=3)
    return rows[0] if rows else {}
