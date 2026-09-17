import re
import unicodedata
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


def _normalize_address_text(value: str) -> str:
    """Normalize address text for strict post-filtering of SIRENE results."""
    value = (value or "").strip().upper()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[’']", "", value)
    value = re.sub(r"[^A-Z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _matches_exact_address(establishment, postal, number, street_type, street_name, city):
    """Return True only when the returned SIRENE address matches the requested address."""
    addr = establishment.get("adresseEtablissement") or {}

    wanted_number = _normalize_address_text(number)
    wanted_street = _normalize_address_text(street_name)
    wanted_type = _normalize_address_text(street_type)
    wanted_postal = _normalize_address_text(postal)
    wanted_city = _normalize_address_text(city)

    returned_number = _normalize_address_text(addr.get("numeroVoieEtablissement"))
    returned_street = _normalize_address_text(addr.get("libelleVoieEtablissement"))
    returned_type = _normalize_address_text(addr.get("typeVoieEtablissement"))
    returned_postal = _normalize_address_text(addr.get("codePostalEtablissement"))
    returned_city = _normalize_address_text(addr.get("libelleCommuneEtablissement"))

    if wanted_number and returned_number != wanted_number:
        return False
    if wanted_street and returned_street != wanted_street:
        return False
    if wanted_type and returned_type != wanted_type:
        return False
    if wanted_postal and returned_postal != wanted_postal:
        return False
    if wanted_city and returned_city != wanted_city:
        return False
    return True


def search_establishments(api_key: str, address: str, include_closed=True, max_results=100):
    """Strict exact-address search, with active and closed states queried separately.

    The API query contains the street number whenever one was supplied. Results are
    also post-filtered against the returned SIRENE address so an API response can
    never silently mix another street number into an exact-address result.
    """
    if not api_key:
        raise ValueError("Clé SIRENE absente. Ajoutez SIRENE_API_KEY dans Streamlit Secrets.")

    postal, number, street_type, street_name, city = parse_address(address)
    base = []
    if postal:
        base.append(f"codePostalEtablissement:{postal}")
    if city:
        base.append(f'libelleCommuneEtablissement:"{city}"')
    if number:
        base.append(f"numeroVoieEtablissement:{number}")
    if street_type:
        base.append(f"typeVoieEtablissement:{street_type}")
    if street_name:
        base.append(f'libelleVoieEtablissement:"{street_name}"')

    if not base:
        return [], "Adresse insuffisante pour une recherche SIRENE."

    all_results = {}
    used = []
    statuses = ["A", "F"] if include_closed else ["A"]

    for status in statuses:
        q = " AND ".join(base + [f"periode(etatAdministratifEtablissement:{status})"])
        payload = _request(api_key, q, max_results)
        raw_found = payload.get("etablissements", [])
        found = [
            e for e in raw_found
            if _matches_exact_address(e, postal, number, street_type, street_name, city)
        ]
        used.append(f"{status}: {q}")
        for e in found:
            if e.get("siret"):
                all_results[e["siret"]] = e

    if number:
        scope_note = f"Adresse exacte n°{number}"
    else:
        scope_note = "Adresse exacte (sans numéro de voie)"
    return list(all_results.values()), f"{scope_note} | " + " | ".join(used)


def _search_commune_status(api_key: str, citycode: str, status: str, max_pages=20):
    """Load establishments of one administrative status in a commune."""
    if not api_key:
        raise ValueError("Clé SIRENE absente.")
    if not citycode:
        raise ValueError("Code commune absent.")
    if status not in {"A", "F"}:
        raise ValueError("Statut SIRENE invalide.")

    q = f"codeCommuneEtablissement:{citycode} AND periode(etatAdministratifEtablissement:{status})"
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


def search_commune_active(api_key: str, citycode: str, max_pages=20):
    """Load active establishments in a commune using Sirene cursor pagination."""
    return _search_commune_status(api_key, citycode, "A", max_pages=max_pages)


def search_commune_active_closed(api_key: str, citycode: str, max_pages=20):
    """Load active and closed establishments in a commune.

    Active and closed records are queried separately so the zone's current
    commercial analysis can remain based on active establishments while a
    separate historical layer can identify former occupants.
    """
    active = _search_commune_status(api_key, citycode, "A", max_pages=max_pages)
    closed = _search_commune_status(api_key, citycode, "F", max_pages=max_pages)
    by_siret = {}
    for item in active + closed:
        siret = item.get("siret")
        if siret:
            by_siret[siret] = item
    return list(by_siret.values()), active, closed


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


def flatten_establishments(establishments, forced_status=None):
    rows = []
    for e in establishments:
        addr = e.get("adresseEtablissement") or {}
        ul = e.get("uniteLegale") or {}
        periods = e.get("periodesEtablissement") or []
        current_period = periods[0] if periods else {}
        enseignes = [current_period.get("enseigne1Etablissement"), current_period.get("enseigne2Etablissement"), current_period.get("enseigne3Etablissement"), current_period.get("denominationUsuelleEtablissement")]
        enseigne = " / ".join([x for x in enseignes if x])
        # The status can be represented in the current historized period rather
        # than at the establishment root. For zone layers, forced_status is used
        # because active and closed records are queried separately.
        raw_status = forced_status or current_period.get("etatAdministratifEtablissement") or e.get("etatAdministratifEtablissement")
        status = "Fermé" if raw_status == "F" else "Actif"
        lat, lon = _lambert_to_wgs84(
            addr.get("coordonneeLambertAbscisseEtablissement"),
            addr.get("coordonneeLambertOrdonneeEtablissement")
        )
        date_debut_actif = current_period.get("dateDebut", "") if raw_status == "A" else ""
        date_fermeture = ""
        if raw_status == "F":
            f_periods = [p for p in periods if isinstance(p, dict) and p.get("etatAdministratifEtablissement") == "F"]
            closure_dates = [p.get("dateDebut", "") for p in f_periods if p.get("dateDebut")]
            if closure_dates:
                date_fermeture = sorted(closure_dates, reverse=True)[0]
        rows.append({
            "Statut": status, "SIRET": e.get("siret", ""), "SIREN": e.get("siren", ""),
            "Enseigne / nom usuel": enseigne, "Entreprise": ul.get("denominationUniteLegale") or "",
            "APE": current_period.get("activitePrincipaleEtablissement") or e.get("activitePrincipaleEtablissement", ""),
            "Date création": e.get("dateCreationEtablissement", ""),
            "Adresse": " ".join([str(x) for x in [addr.get("numeroVoieEtablissement"), addr.get("typeVoieEtablissement"), addr.get("libelleVoieEtablissement")] if x]),
            "Code postal": addr.get("codePostalEtablissement", ""), "Commune": addr.get("libelleCommuneEtablissement", ""),
            "Date début actif": date_debut_actif, "Date fermeture": date_fermeture,
            "Nb périodes": len(periods), "Historique périodes": periods, "lat": lat, "lon": lon,
        })
    return rows



def address_signature_from_row(row):
    """Normalized address key for comparing active and closed zone records."""
    return (
        _normalize_address_text(row.get("Adresse", "")),
        _normalize_address_text(row.get("Code postal", "")),
        _normalize_address_text(row.get("Commune", "")),
    )


def add_vacancy_signals(closed_rows, active_rows):
    """Add a cautious vacancy signal by comparing exact SIRENE addresses.

    This does not prove physical vacancy. It only distinguishes a closed
    establishment for which an active establishment is already recorded at the
    same address from one for which no active SIRENE establishment is detected.
    """
    active_by_address = {}
    for row in active_rows or []:
        key = address_signature_from_row(row)
        if all(key):
            active_by_address.setdefault(key, []).append(row)

    enriched = []
    for row in closed_rows or []:
        item = dict(row)
        key = address_signature_from_row(item)
        matches = active_by_address.get(key, []) if all(key) else []
        item["Occupant actif détecté"] = "; ".join(
            (m.get("Enseigne / nom usuel") or m.get("Entreprise") or m.get("SIRET") or "")
            for m in matches[:5]
        )
        if matches:
            item["Signal de vacance"] = "Non concluant : actif détecté à la même adresse"
            item["Niveau de signal"] = "Faible"
        elif all(key):
            item["Signal de vacance"] = "Vacance potentielle : aucun actif SIRENE détecté à la même adresse"
            item["Niveau de signal"] = "À vérifier"
        else:
            item["Signal de vacance"] = "Indéterminé : adresse insuffisamment exploitable"
            item["Niveau de signal"] = "Indéterminé"
        enriched.append(item)
    return enriched

def _parse_iso_date(value):
    if not value:
        return None
    try:
        from datetime import date
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def add_occupation_chronology(closed_rows, active_rows):
    """Reconstruct a cautious occupancy chronology from closed/active SIRENE rows.

    The goal is to distinguish current occupancy from a possible historical gap.
    This is an analytical signal, not proof of physical vacancy or lease duration.
    """
    active_by_address = {}
    for row in active_rows or []:
        key = address_signature_from_row(row)
        if all(key):
            start = _parse_iso_date(row.get("Date début actif") or row.get("Date création"))
            active_by_address.setdefault(key, []).append((start, row))

    enriched = []
    for row in closed_rows or []:
        item = dict(row)
        key = address_signature_from_row(item)
        closure = _parse_iso_date(item.get("Date fermeture"))
        candidates = sorted(active_by_address.get(key, []), key=lambda x: (x[0] is None, x[0] or _parse_iso_date("9999-12-31"))) if all(key) else []

        later = [(d, r) for d, r in candidates if d and closure and d > closure]
        prior_or_same = [(d, r) for d, r in candidates if not (d and closure and d > closure)]

        item["Nouvel occupant détecté"] = ""
        item["Début nouvel occupant"] = ""
        item["Durée intervalle (mois)"] = ""
        item["Vacance historique"] = "Indéterminée"
        item["Chronologie"] = ""

        if closure and later:
            start, occupant = later[0]
            months = round((start - closure).days / 30.4375, 1)
            name = occupant.get("Enseigne / nom usuel") or occupant.get("Entreprise") or occupant.get("SIRET") or "Occupant actif"
            item["Nouvel occupant détecté"] = name
            item["Début nouvel occupant"] = start.isoformat()
            item["Durée intervalle (mois)"] = months
            item["Vacance historique"] = "Possible : intervalle détecté"
            item["Chronologie"] = f"Fermeture {closure.isoformat()} → nouvel occupant {start.isoformat()}"
        elif closure and prior_or_same:
            names = []
            for _d, occupant in prior_or_same[:5]:
                names.append(occupant.get("Enseigne / nom usuel") or occupant.get("Entreprise") or occupant.get("SIRET") or "Occupant actif")
            item["Nouvel occupant détecté"] = "; ".join(dict.fromkeys(names))
            item["Vacance historique"] = "Non démontrée : actif déjà présent à l'adresse"
            item["Chronologie"] = f"Fermeture {closure.isoformat()} + activité active déjà détectée"
        elif closure:
            item["Vacance historique"] = "À vérifier : aucun actif postérieur détecté"
            item["Chronologie"] = f"Fermeture {closure.isoformat()} → aucun actif postérieur détecté"
        else:
            item["Vacance historique"] = "Indéterminée : date de fermeture indisponible"
            item["Chronologie"] = "Date de fermeture indisponible"
        enriched.append(item)
    return enriched



def _row_display_name(row):
    return row.get("Enseigne / nom usuel") or row.get("Entreprise") or row.get("SIRET") or "Nom non renseigné"


def add_succession_links_to_chronology(api_key, closed_rows, active_rows, max_checks=8):
    """Enrich vacancy chronology with explicit SIRENE succession links.

    V5.8.1 uses a small, quota-aware scan budget and checks both directions:
    1) closed establishment -> successor(s), and
    2) active establishment -> predecessor(s).

    The second direction matters when a former occupant is missing from the
    closed-establishment stock returned for the zone, while the current
    occupant still exposes its predecessor through SIRENE succession links.

    Address chronology remains a fallback and never overrides an explicit
    succession link. This is still a signal, not proof of physical vacancy.
    """
    closed_by_siret = {r.get("SIRET"): r for r in (closed_rows or []) if r.get("SIRET")}
    active_by_siret = {r.get("SIRET"): r for r in (active_rows or []) if r.get("SIRET")}
    enriched = [dict(r) for r in (closed_rows or [])]
    by_closed_siret = {r.get("SIRET"): r for r in enriched if r.get("SIRET")}

    for item in enriched:
        item.setdefault("Succession SIRENE", "")
        item.setdefault("Successeur SIRET", "")
        item.setdefault("Date succession SIRENE", "")
        item.setdefault("Continuité économique", "")
        item.setdefault("Transfert de siège", "")
        item.setdefault("Source rapprochement", "Rapprochement par adresse")

    # Keep the total number of succession API calls deliberately bounded.
    budget = max(0, int(max_checks or 0))
    closed_budget = budget // 2
    active_budget = budget - closed_budget

    # Prioritise recent closures and short distances: these are the records
    # most useful for commercial vacancy analysis.
    closed_candidates = sorted(
        enriched,
        key=lambda r: (
            r.get("Date fermeture") or "0000-00-00",
            -float(r.get("Distance (m)") or 999999),
        ),
        reverse=True,
    )[:closed_budget]

    discoveries = []

    def apply_successors(item, successors, source_label):
        successors = [
            l for l in successors
            if l.get("siretEtablissementSuccesseur")
        ]
        if not successors:
            return
        successors = sorted(successors, key=lambda x: x.get("dateLienSuccession") or "9999-12-31")
        succ_sirets = list(dict.fromkeys(l.get("siretEtablissementSuccesseur") for l in successors if l.get("siretEtablissementSuccesseur")))
        names = []
        for succ_siret in succ_sirets:
            succ_row = active_by_siret.get(succ_siret)
            names.append(_row_display_name(succ_row) if succ_row else succ_siret)

        item["Succession SIRENE"] = "Oui"
        item["Successeur SIRET"] = "; ".join(succ_sirets)
        item["Date succession SIRENE"] = "; ".join(l.get("dateLienSuccession", "") for l in successors if l.get("dateLienSuccession"))
        econ = [l.get("continuiteEconomique") for l in successors if l.get("continuiteEconomique") is not None]
        item["Continuité économique"] = ("Oui" if any(econ) else "Non") if econ else ""
        transfer = [l.get("transfertSiege") for l in successors if l.get("transfertSiege") is not None]
        item["Transfert de siège"] = ("Oui" if any(transfer) else "Non") if transfer else ""
        item["Nouvel occupant détecté"] = "; ".join(dict.fromkeys(names))
        item["Source rapprochement"] = source_label
        item["Signal de vacance"] = "Successeur SIRENE identifié : vacance actuelle non démontrée"
        item["Niveau de signal"] = "Faible"

        closure = _parse_iso_date(item.get("Date fermeture"))
        succ_dates = [_parse_iso_date(l.get("dateLienSuccession")) for l in successors]
        succ_dates = [d for d in succ_dates if d]
        succ_date = min(succ_dates) if succ_dates else None
        if closure and succ_date and succ_date >= closure:
            months = round((succ_date - closure).days / 30.4375, 1)
            item["Durée intervalle (mois)"] = months
            item["Vacance historique"] = "Possible : intervalle entre fermeture et succession SIRENE"
            item["Chronologie"] = f"Fermeture {closure.isoformat()} → succession SIRENE {succ_date.isoformat()} → {', '.join(dict.fromkeys(names))}"
        else:
            item["Vacance historique"] = "À interpréter : succession SIRENE identifiée"
            item["Chronologie"] = f"Fermeture {item.get('Date fermeture') or 'date inconnue'} → succession SIRENE → {', '.join(dict.fromkeys(names))}"

    # Direction 1: closed predecessor -> successor.
    for row in closed_candidates:
        siret = row.get("SIRET")
        if not siret:
            continue
        try:
            links = search_succession_links(api_key, siret, direction="predecessor")
        except Exception:
            links = []
        successors = [l for l in links if l.get("siretEtablissementPredecesseur") == siret]
        if successors:
            apply_successors(by_closed_siret.get(siret, row), successors, "Lien de succession SIRENE")

    # Direction 2: active successor -> predecessor. This can discover a former
    # occupant even when that predecessor is not present in the closed stock.
    active_candidates = sorted(
        (r for r in (active_rows or []) if r.get("SIRET")),
        key=lambda r: (
            r.get("Date début actif") or r.get("Date création") or "0000-00-00",
            -float(r.get("Distance (m)") or 999999),
        ),
        reverse=True,
    )[:active_budget]

    for active in active_candidates:
        successor_siret = active.get("SIRET")
        try:
            links = search_succession_links(api_key, successor_siret, direction="successor")
        except Exception:
            links = []
        for link in links:
            predecessor = link.get("siretEtablissementPredecesseur")
            if not predecessor:
                continue
            predecessor_row = by_closed_siret.get(predecessor)
            record = {
                "Successeur SIRET": successor_siret,
                "Successeur": _row_display_name(active),
                "Prédécesseur SIRET": predecessor,
                "Date succession SIRENE": link.get("dateLienSuccession", ""),
                "Continuité économique": "Oui" if link.get("continuiteEconomique") is True else ("Non" if link.get("continuiteEconomique") is False else ""),
                "Transfert de siège": "Oui" if link.get("transfertSiege") is True else ("Non" if link.get("transfertSiege") is False else ""),
                "Source": "Lien de succession SIRENE — recherche depuis le successeur",
                "Distance (m)": active.get("Distance (m)", ""),
            }
            discoveries.append(record)
            if predecessor_row is not None and predecessor_row.get("Succession SIRENE") != "Oui":
                predecessor_row["Succession SIRENE"] = "Oui"
                predecessor_row["Successeur SIRET"] = successor_siret
                predecessor_row["Date succession SIRENE"] = link.get("dateLienSuccession", "")
                predecessor_row["Continuité économique"] = record["Continuité économique"]
                predecessor_row["Transfert de siège"] = record["Transfert de siège"]
                predecessor_row["Nouvel occupant détecté"] = _row_display_name(active)
                predecessor_row["Source rapprochement"] = "Lien de succession SIRENE — recherche depuis le successeur"
                predecessor_row["Signal de vacance"] = "Successeur SIRENE identifié : vacance actuelle non démontrée"
                predecessor_row["Niveau de signal"] = "Faible"

    return enriched, discoveries

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


def search_succession_links(api_key: str, siret: str, direction="predecessor"):
    """Return SIRENE succession links for one SIRET.

    The API documentation exposes predecessor and successor searches as
    separate queries. For vacancy analysis we normally start from a closed
    establishment and therefore query its successor links directly.
    """
    if direction not in {"predecessor", "successor"}:
        raise ValueError("Direction de succession invalide.")
    field = (
        "siretEtablissementPredecesseur"
        if direction == "predecessor"
        else "siretEtablissementSuccesseur"
    )
    try:
        r = requests.get(
            f"{BASE_URL}/siret/liensSuccession",
            params={"q": f"{field}:{siret}"},
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
        raise RuntimeError("Quota SIRENE atteint (30 requêtes/minute). Réduisez la recherche historique ou réessayez dans quelques secondes.")
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

