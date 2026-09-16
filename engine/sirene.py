import re
from urllib.parse import quote

import requests

BASE_URL = "https://api.insee.fr/api-sirene/3.11"
TIMEOUT = 20


def _norm_text(value: str) -> str:
    value = (value or "").strip().upper()
    value = re.sub(r"[’']", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def parse_address(address: str):
    """Extract postal code, street number, street type/name and city."""
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

def _street_query(street: str):
    """Build several conservative SIRENE query variants for street matching."""
    street = _norm_text(street)
    # SIRENE stores street type separately; matching the complete phrase is still useful
    # for common names, while a token query is used as fallback.
    return street


def _request_siret_query(api_key: str, q: str, max_results: int, headers: dict):
    """Execute one SIRENE /siret query and normalize 'no result' as empty."""
    params = {"q": q, "nombre": min(int(max_results), 1000)}
    try:
        r = requests.get(f"{BASE_URL}/siret", params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"Erreur réseau SIRENE : {exc}") from exc

    if r.status_code == 200:
        payload = r.json()
        return payload.get("etablissements", [])
    if r.status_code == 404:
        return []
    if r.status_code == 401:
        raise RuntimeError("Clé SIRENE refusée (401). Vérifiez la clé dans Streamlit Secrets.")
    if r.status_code == 403:
        raise RuntimeError("Accès API SIRENE refusé (403). Vérifiez la souscription Accès public.")
    if r.status_code == 429:
        raise RuntimeError("Quota SIRENE atteint (30 requêtes/minute). Réessayez dans quelques secondes.")
    raise RuntimeError(f"Erreur lors de l'appel à SIRENE : HTTP {r.status_code}: {r.text[:250]}")


def search_establishments(api_key: str, address: str, include_closed=True, max_results=100):
    """Search active and, optionally, closed SIRENE establishments at an address.

    V3.2 explicitly searches the administrative status so that closed establishments
    are not lost when the API returns only the currently active records for a broad
    address query. Results are deduplicated by SIRET.
    """
    if not api_key:
        raise ValueError("Clé SIRENE absente. Ajoutez SIRENE_API_KEY dans Streamlit Secrets.")
    if not address.strip():
        return [], "Saisissez une adresse."

    postal, number, street_type, street_name, city = parse_address(address)

    base_clauses = []
    if postal:
        base_clauses.append(f"codePostalEtablissement:{postal}")
    if city:
        base_clauses.append(f'libelleCommuneEtablissement:"{city}"')
    if number:
        base_clauses.append(f"numeroVoieEtablissement:{number}")
    if street_type:
        base_clauses.append(f"typeVoieEtablissement:{street_type}")
    if street_name:
        base_clauses.append(f'libelleVoieEtablissement:"{street_name}"')

    if not base_clauses:
        return [], "Adresse insuffisante pour une recherche SIRENE."

    # Most precise query first. If no result, progressively relax the address.
    address_variants = [base_clauses]
    if postal and number and street_name:
        address_variants.append([
            f"codePostalEtablissement:{postal}",
            f"numeroVoieEtablissement:{number}",
            f'libelleVoieEtablissement:"{street_name}"',
        ])
    if postal and street_name:
        address_variants.append([
            f"codePostalEtablissement:{postal}",
            f'libelleVoieEtablissement:"{street_name}"',
        ])
    if number and street_name:
        address_variants.append([
            f"numeroVoieEtablissement:{number}",
            f'libelleVoieEtablissement:"{street_name}"',
        ])
    if street_name:
        address_variants.append([f'libelleVoieEtablissement:"{street_name}"'])

    # Avoid making the last, street-only fallback too broad when a precise
    # address query has already produced results.
    headers = {
        "X-INSEE-Api-Key-Integration": api_key,
        "Accept": "application/json",
    }

    statuses = ["A"]
    if include_closed:
        statuses.append("F")

    all_results = {}
    queries_used = []

    for status in statuses:
        found_for_status = []
        used_query = None
        for clauses in address_variants:
            q = " AND ".join(clauses + [f"etatAdministratifEtablissement:{status}"])
            establishments = _request_siret_query(api_key, q, max_results, headers)
            if establishments:
                found_for_status = establishments
                used_query = q
                break
        queries_used.append(f"{status}: {used_query or 'aucun résultat'}")
        for e in found_for_status:
            siret = e.get("siret")
            if siret:
                all_results[siret] = e

    # If the explicit active query found nothing, retain the possibility of a
    # broad unfiltered query as a final compatibility fallback.
    if not all_results:
        q = " AND ".join(base_clauses)
        establishments = _request_siret_query(api_key, q, max_results, headers)
        for e in establishments:
            siret = e.get("siret")
            if siret:
                all_results[siret] = e
        if establishments:
            queries_used.append(f"fallback: {q}")

    return list(all_results.values()), " | ".join(queries_used)

def flatten_establishments(establishments):
    rows = []
    for e in establishments:
        addr = e.get("adresseEtablissement") or {}
        ul = e.get("uniteLegale") or {}
        periods = e.get("periodesEtablissement") or []
        current_period = periods[0] if periods else {}
        enseignes = [
            current_period.get("enseigne1Etablissement"),
            current_period.get("enseigne2Etablissement"),
            current_period.get("enseigne3Etablissement"),
            current_period.get("denominationUsuelleEtablissement"),
        ]
        enseigne = " / ".join([x for x in enseignes if x])
        status = "Fermé" if e.get("etatAdministratifEtablissement") == "F" else "Actif"
        rows.append({
            "Statut": status,
            "SIRET": e.get("siret", ""),
            "SIREN": e.get("siren", ""),
            "Enseigne / nom usuel": enseigne,
            "Entreprise": ul.get("denominationUniteLegale") or "",
            "APE": current_period.get("activitePrincipaleEtablissement") or e.get("activitePrincipaleEtablissement", ""),
            "Date création": e.get("dateCreationEtablissement", ""),
            "Adresse": " ".join([str(x) for x in [addr.get("numeroVoieEtablissement"), addr.get("typeVoieEtablissement"), addr.get("libelleVoieEtablissement")] if x]),
            "Code postal": addr.get("codePostalEtablissement", ""),
            "Commune": addr.get("libelleCommuneEtablissement", ""),
            "Nb périodes": len(periods),
            "Historique périodes": periods,
        })
    return rows


def summarize_history(periods):
    out=[]
    for p in periods or []:
        name = p.get("enseigne1Etablissement") or p.get("denominationUsuelleEtablissement") or ""
        activity = p.get("activitePrincipaleEtablissement") or ""
        out.append({
            "Début": p.get("dateDebut", ""),
            "Fin": p.get("dateFin", "") or "En cours",
            "Statut": "Fermé" if p.get("etatAdministratifEtablissement") == "F" else "Actif",
            "Enseigne / nom usuel": name,
            "APE": activity,
        })
    return out
