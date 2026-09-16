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
        # Support addresses entered as "20 rue X, Ville" even without a postcode.
        # Keeping the city separate prevents it from being included in the street name.
        if "," in raw:
            street_part, city = [part.strip(" ,") for part in raw.split(",", 1)]
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
    """Return WGS84 coordinates while handling Sirene's coordinate formats.

    Sirene 3.11 may expose WGS84 values for most establishments and Lambert-93
    values for some records. We detect the numeric range instead of assuming
    every record is Lambert-93.
    """
    if x in (None, "") or y in (None, ""):
        return None, None
    try:
        xf, yf = float(x), float(y)
        # WGS84 longitude/latitude range.
        if abs(xf) <= 180 and abs(yf) <= 90:
            return float(yf), float(xf)
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(xf, yf)
        if abs(lon) > 180 or abs(lat) > 90:
            return None, None
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
        lat, lon = _lambert_to_wgs84(
            addr.get("coordonneeLambertAbscisseEtablissement"),
            addr.get("coordonneeLambertOrdonneeEtablissement")
        )
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



def _request_siret(api_key: str, siret: str):
    if not api_key:
        raise ValueError("Clé SIRENE absente.")
    try:
        r = requests.get(f"{BASE_URL}/siret/{siret}", headers=_headers(api_key), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"Erreur réseau SIRENE : {exc}") from exc
    if r.status_code == 200:
        return r.json()
    if r.status_code == 404:
        return None
    if r.status_code == 401:
        raise RuntimeError("Clé SIRENE refusée (401). Vérifiez Streamlit Secrets.")
    if r.status_code == 403:
        raise RuntimeError("Accès API SIRENE refusé (403). Vérifiez la souscription Accès public.")
    if r.status_code == 429:
        raise RuntimeError("Quota SIRENE atteint (30 requêtes/minute). Réduisez la recherche historique ou réessayez dans quelques secondes.")
    raise RuntimeError(f"Erreur SIRENE : HTTP {r.status_code}: {r.text[:250]}")


def get_establishment_history(api_key: str, siret: str):
    """Get the complete historized record for one SIRET."""
    return _request_siret(api_key, siret)


def search_succession_links(api_key: str, siret: str):
    """Return establishment succession links for a SIRET."""
    try:
        r = requests.get(
            f"{BASE_URL}/siret/liensSuccession",
            params={"q": f"siretEtablissementPredecesseur:{siret} OR siretEtablissementSuccesseur:{siret}"},
            headers=_headers(api_key), timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Erreur réseau SIRENE : {exc}") from exc
    if r.status_code == 200:
        return r.json().get("liensSuccession", [])
    if r.status_code == 404:
        return []
    if r.status_code == 401:
        raise RuntimeError("Clé SIRENE refusée (401). Vérifiez Streamlit Secrets.")
    if r.status_code == 403:
        raise RuntimeError("Accès API SIRENE refusé (403). Vérifiez la souscription Accès public.")
    if r.status_code == 429:
        raise RuntimeError("Quota SIRENE atteint (30 requêtes/minute). Réessayez dans quelques secondes.")
    raise RuntimeError(f"Erreur SIRENE succession : HTTP {r.status_code}: {r.text[:250]}")


def _address_signature_from_raw(e):
    addr = e.get("adresseEtablissement") or {}
    return (
        str(addr.get("numeroVoieEtablissement") or "").strip().upper(),
        _norm_text(addr.get("typeVoieEtablissement") or ""),
        _norm_text(addr.get("libelleVoieEtablissement") or ""),
        str(addr.get("codePostalEtablissement") or "").strip(),
    )


def address_signature(address: str):
    postal, number, street_type, street_name, _city = parse_address(address)
    return (
        str(number or "").strip().upper(),
        _norm_text(street_type or ""),
        _norm_text(street_name or ""),
        str(postal or "").strip(),
    )


def history_periods_for_record(record):
    """Flatten the historized periods of one SIRET for UI display."""
    if not record:
        return []
    e = record.get("etablissement") or record
    ul = e.get("uniteLegale") or {}
    periods = e.get("periodesEtablissement") or []
    out = []
    for p in periods:
        name = p.get("enseigne1Etablissement") or p.get("denominationUsuelleEtablissement") or ""
        out.append({
            "SIRET": e.get("siret", ""),
            "Début": p.get("dateDebut", ""),
            "Fin": p.get("dateFin", "") or "En cours",
            "Statut": "Fermé" if p.get("etatAdministratifEtablissement") == "F" else "Actif",
            "Enseigne / nom usuel": name,
            "Entreprise": ul.get("denominationUniteLegale") or "",
            "APE": p.get("activitePrincipaleEtablissement") or "",
        })
    return out


def build_local_history(api_key: str, rows, target_address: str, max_seeds=20):
    """Build a cautious local history from all available SIRENE period data."""
    seeds = [r for r in rows if r.get("SIRET")][:max_seeds]
    timeline = []
    succession_rows = []
    seen_sirets = set()
    calls = 0

    # Use every historized period already returned by the address search.
    for row in seeds:
        siret = row.get("SIRET", "")
        periods = row.get("Historique périodes") or []
        if periods:
            seen_sirets.add(siret)
            for p in periods:
                name = (
                    p.get("enseigne1Etablissement")
                    or p.get("enseigne2Etablissement")
                    or p.get("enseigne3Etablissement")
                    or p.get("denominationUsuelleEtablissement")
                    or ""
                )
                timeline.append({
                    "SIRET": siret,
                    "Début": p.get("dateDebut", ""),
                    "Fin": p.get("dateFin", "") or "En cours",
                    "Statut": "Fermé" if p.get("etatAdministratifEtablissement") == "F" else "Actif",
                    "Enseigne / nom usuel": name,
                    "Entreprise": row.get("Entreprise", ""),
                    "APE": p.get("activitePrincipaleEtablissement") or row.get("APE", ""),
                    "Source": "Périodes SIRENE retournées à l'adresse",
                })
        else:
            # Keep a current snapshot instead of incorrectly saying that
            # there is no history.
            seen_sirets.add(siret)
            timeline.append({
                "SIRET": siret,
                "Début": row.get("Date création", ""),
                "Fin": "En cours" if row.get("Statut") == "Actif" else "",
                "Statut": row.get("Statut", ""),
                "Enseigne / nom usuel": row.get("Enseigne / nom usuel", ""),
                "Entreprise": row.get("Entreprise", ""),
                "APE": row.get("APE", ""),
                "Source": "Fiche établissement à l'adresse (sans périodes détaillées)",
            })

    # Detailed SIRET lookup only when the address search returned no periods.
    cache = {}
    def fetch(siret):
        nonlocal calls
        if siret not in cache:
            cache[siret] = get_establishment_history(api_key, siret)
            calls += 1
        return cache[siret]

    for row in seeds:
        siret = row.get("SIRET", "")
        if not siret or (row.get("Historique périodes") or []):
            continue
        record = fetch(siret)
        detailed = history_periods_for_record(record)
        if detailed:
            timeline = [r for r in timeline if r.get("SIRET") != siret]
            for item in detailed:
                item["Source"] = "Historique détaillé SIRENE"
            timeline.extend(detailed)

    # Succession links, limited to selected seed establishments.
    for siret in list(seen_sirets)[:max_seeds]:
        try:
            links = search_succession_links(api_key, siret)
        except Exception:
            links = []
        for link in links:
            pred = link.get("siretEtablissementPredecesseur", "")
            succ = link.get("siretEtablissementSuccesseur", "")
            if not pred or not succ or pred == succ:
                continue
            succession_rows.append({
                "Prédécesseur": pred,
                "Successeur": succ,
                "Date du lien": link.get("dateLienSuccession", ""),
                "Continuité économique": "Oui" if link.get("continuiteEconomique") else "Non",
                "Transfert de siège": "Oui" if link.get("transfertSiege") else "Non",
            })

    dedup = {}
    for r in timeline:
        key = (
            r.get("SIRET", ""), r.get("Début", ""), r.get("Fin", ""),
            r.get("APE", ""), r.get("Enseigne / nom usuel", ""), r.get("Statut", "")
        )
        if key not in dedup or "Historique détaillé" in r.get("Source", ""):
            dedup[key] = r
    timeline = list(dedup.values())
    timeline.sort(key=lambda x: (x.get("Début", ""), x.get("SIRET", "")), reverse=True)

    succ_dedup = {
        (r["Prédécesseur"], r["Successeur"], r["Date du lien"]): r
        for r in succession_rows
    }
    succession_rows = list(succ_dedup.values())
    return timeline, succession_rows, calls

