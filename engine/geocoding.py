import requests

GEOCODER_URL = "https://data.geopf.fr/geocodage/search/"
TIMEOUT = 15


def geocode_address(address: str):
    """Geocode an address with the current Géoplateforme geocoder."""
    if not address or not address.strip():
        raise ValueError("Adresse vide.")
    try:
        r = requests.get(
            GEOCODER_URL,
            params={"q": address.strip(), "limit": 1},
            timeout=TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Erreur réseau du géocodeur : {exc}") from exc

    if r.status_code == 429:
        raise RuntimeError("Géocodeur temporairement limité (429). Réessayez dans quelques secondes.")
    if r.status_code != 200:
        raise RuntimeError(f"Erreur du géocodeur : HTTP {r.status_code}: {r.text[:250]}")

    features = r.json().get("features", [])
    if not features:
        raise RuntimeError("Adresse introuvable par le géocodeur national.")

    f = features[0]
    coords = f.get("geometry", {}).get("coordinates", [])
    props = f.get("properties", {})
    if len(coords) < 2:
        raise RuntimeError("Le géocodeur n'a pas fourni de coordonnées.")

    return {
        "label": props.get("label") or address,
        "lon": float(coords[0]),
        "lat": float(coords[1]),
        "citycode": props.get("citycode") or "",
        "city": props.get("city") or "",
        "postcode": props.get("postcode") or "",
        "type": props.get("type") or "",
    }
