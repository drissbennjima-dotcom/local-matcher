import re
from math import isnan
import requests

BASE_URL = "https://api.insee.fr/api-sirene/3.11"
TIMEOUT = 20


def _norm_text(value: str) -> str:
    value = (value or "").strip().upper()
    value = re.sub(r"[’']", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def parse_address(address: str):
    raw = re.sub(r"\s+", " ", (address or "").strip())
    postal = None
    city = ""
    m = re.search(r"\b(\d{5})\b", raw)
    if m:
        postal = m.group(1)
        street_part = raw[:m.start()].strip(" ,")
        city = raw[m.end():].strip(" ,")
    else:
        street_part = raw

    m_num = re.match(r"^(\d+[A-Za-z]?)\s+(.*)$", street_part)
    number = m_num.group(1) if m_num else None
    street = m_num.group(2) if m_num else street_part
    street_types = {
        "RUE", "AVENUE", "AVE", "BOULEVARD", "BD", "PLACE", "PL",
        "CHEMIN", "CHE", "IMPASSE", "IMP", "QUAI", "PASSAGE",
        "ALLEE", "ALLÉE", "COURS", "ROUTE", "RTE", "SQUARE",
        "FAUBOURG", "FG", "VOIE"
    }
    tokens = street.split()
    street_type = None
    if tokens and tokens[0].upper().rstrip(".") in street_types:
        street_type = tokens[0].upper().rstrip(".")
        street = " ".join(tokens[1:])
    return postal, number, street_type, _norm_text(street), _norm_text(city)


def _headers(api_key):
    return {"X-INSEE-Api-Key-Integration": api_key, "Accept": "application/json"}


def _request(api_key, q, nombre=1000, curseur="*"):
    params = {"q": q, "nombre": min(int(nombre), 1000), "curseur": curseur}
    try:
        r = requests.get(f"{BASE_URL}/siret", params=params, headers=_headers(api_key), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"Erreur réseau SIRENE : {exc}") from exc
    if r.status_code == 200:
        return r.json()
    if r.status_code == 404:
        return {"etablissements": [], "header": {}}
    if r.status_code == 401:
        raise RuntimeError("Clé SIRENE refusée (401). Vérifiez Streamlit Secrets.")
    if r.status_code == 403:
        raise RuntimeError("Accès API SIRENE refusé (403). Vérifiez la souscription Accès public.")
    if r.status_code == 429:
        raise RuntimeError("Quota SIRENE atteint (30 requêtes/minute). Réessayez dans quelques secondes.")
    raise RuntimeError(f"Erreur SIRENE : HTTP {r.status_code}: {r.text[:250]}")


def search_establishments(api_key: str, address: str, include_closed=True, max_results=100):
    """Exact-address search, with active and closed states queried separately."""
    if not api_key:
        raise ValueError("Clé SIRENE absente. Ajoutez SIRENE_API_KEY dans Streamlit Secrets.")
    postal, number, street_type, street_name, city = parse_address(address)
    base = []
    if postal: base.append(f"codePostalEtablissement:{postal}")
    if city: base.append(f'libelleCommuneEtablissement:"{city}"')
    if number: base.append(f"numeroVoieEtablissement:{number}")
    if street_type: base.append(f"typeVoieEtablissement:{street_type}")
    if street_name: base.append(f'libelleVoieEtablissement:"{street_name}"')
    if not base:
        return [], "Adresse insuffisante pour une recherche SIRENE."

    variants = [base]
    if postal and number and street_name:
        variants.append([f"codePostalEtablissement:{postal}", f"numeroVoieEtablissement:{number}", f'libelleVoieEtablissement:"{street_name}"'])
    if postal and street_name:
        variants.append([f"codePostalEtablissement:{postal}", f'libelleVoieEtablissement:"{street_name}"'])

    all_results = {}
    used = []
    for status in (["A", "F"] if include_closed else ["A"]):
        found = []
        used_q = None
        for clauses in variants:
            q = " AND ".join(clauses + [f"periode(etatAdministratifEtablissement:{status})"])
            payload = _request(api_key, q, max_results)
            found = payload.get("etablissements", [])
            if found:
                used_q = q
                break
        used.append(f"{status}: {used_q or 'aucun résultat'}")
        for e in found:
            if e.get("siret"):
                all_results[e["siret"]] = e
    return list(all_results.values()), " | ".join(used)


def search_commune_active(api_key: str, citycode: str, max_pages=20):
    """Load active establishments in a commune using Sirene cursor pagination."""
    if not api_key:
        raise ValueError("Clé SIRENE absente.")
    if not citycode:
        raise ValueError("Code commune absent.")

    q = f"codeCommuneEtablissement:{citycode} AND periode(etatAdministratifEtablissement:A)"
    results = []
    cursor = "*"
    for _ in range(max_pages):
        payload = _request(api_key, q, 1000, cursor)
        batch = payload.get("etablissements", [])
        results.extend(batch)
        next_cursor = (payload.get("header") or {}).get("curseurSuivant")
        if not batch or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return results


def _lambert_to_wgs84(x, y):
    if x in (None, "") or y in (None, ""):
        return None, None
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(float(x), float(y))
        return float(lat), float(lon)
    except Exception:
        return None, None


def flatten_establishments(establishments):
    rows = []
    for e in establishments:
        addr = e.get("adresseEtablissement") or {}
        ul = e.get("uniteLegale") or {}
        periods = e.get("periodesEtablissement") or []
        current_period = periods[0] if periods else {}
        enseignes = [current_period.get("enseigne1Etablissement"), current_period.get("enseigne2Etablissement"), current_period.get("enseigne3Etablissement"), current_period.get("denominationUsuelleEtablissement")]
        enseigne = " / ".join([x for x in enseignes if x])
        status = "Fermé" if e.get("etatAdministratifEtablissement") == "F" else "Actif"
        lat, lon = _lambert_to_wgs84(e.get("coordonneeLambertAbscisseEtablissement"), e.get("coordonneeLambertOrdonneeEtablissement"))
        rows.append({
            "Statut": status, "SIRET": e.get("siret", ""), "SIREN": e.get("siren", ""),
            "Enseigne / nom usuel": enseigne, "Entreprise": ul.get("denominationUniteLegale") or "",
            "APE": current_period.get("activitePrincipaleEtablissement") or e.get("activitePrincipaleEtablissement", ""),
            "Date création": e.get("dateCreationEtablissement", ""),
            "Adresse": " ".join([str(x) for x in [addr.get("numeroVoieEtablissement"), addr.get("typeVoieEtablissement"), addr.get("libelleVoieEtablissement")] if x]),
            "Code postal": addr.get("codePostalEtablissement", ""), "Commune": addr.get("libelleCommuneEtablissement", ""),
            "Nb périodes": len(periods), "Historique périodes": periods, "lat": lat, "lon": lon,
        })
    return rows


def summarize_history(periods):
    out=[]
    for p in periods or []:
        name = p.get("enseigne1Etablissement") or p.get("denominationUsuelleEtablissement") or ""
        out.append({"Début": p.get("dateDebut", ""), "Fin": p.get("dateFin", "") or "En cours", "Statut": "Fermé" if p.get("etatAdministratifEtablissement") == "F" else "Actif", "Enseigne / nom usuel": name, "APE": p.get("activitePrincipaleEtablissement") or ""})
    return out
