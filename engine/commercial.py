import re


def _ape_prefix(ape):
    return re.sub(r"[^0-9]", "", str(ape or ""))[:2]


def classify_ape(ape):
    """Broad commercial/environment category derived from the APE/NAF code.
    This is an analytical grouping, not a statement about the actual activity on site.
    """
    p = _ape_prefix(ape)
    if p == "45":
        return "Automobile"
    if p in {"46", "47"}:
        return "Commerce"
    if p == "55":
        return "Hébergement"
    if p == "56":
        return "Restauration"
    if p == "68":
        return "Immobilier"
    if p in {"69", "70", "71", "72", "73", "74", "75", "77", "78", "79", "80", "81", "82"}:
        return "Services professionnels"
    if p in {"86", "87", "88"}:
        return "Santé / social"
    if p in {"90", "91", "92", "93"}:
        return "Culture / loisirs / sport"
    if p == "95":
        return "Réparation"
    if p == "96":
        return "Services aux particuliers"
    if p in {"49", "50", "51", "52", "53"}:
        return "Transport / logistique"
    return "Autres"


COMMERCIAL_CATEGORIES = [
    "Commerce",
    "Restauration",
    "Hébergement",
    "Automobile",
    "Culture / loisirs / sport",
    "Services aux particuliers",
    "Santé / social",
    "Réparation",
]


def add_commercial_category(rows):
    for row in rows:
        row["Catégorie commerciale"] = classify_ape(row.get("APE"))
    return rows
