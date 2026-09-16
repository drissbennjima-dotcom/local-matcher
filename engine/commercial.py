import re

# Catégories larges utilisées pour lire l'environnement économique.
# Elles sont analytiques et dérivées du code APE/NAF : elles ne prouvent pas
# l'activité réellement exercée dans le local.


def _ape_digits(ape):
    return re.sub(r"[^0-9]", "", str(ape or ""))


def _ape_prefix(ape):
    return _ape_digits(ape)[:2]


def classify_ape(ape):
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


# Sous-catégories destinées à l'analyse commerciale. Elles restent volontairement
# prudentes : lorsqu'un code ne permet pas une lecture assez précise, on conserve
# une catégorie "Commerce spécialisé / autre" plutôt que d'inventer une activité.

def classify_ape_detail(ape):
    d = _ape_digits(ape)
    p = d[:2]

    if p in {"46", "47"}:
        if d.startswith("4711"):
            return "Alimentaire général"
        if d.startswith("4719"):
            return "Commerce généraliste"
        if d.startswith("472"):
            return "Alimentaire spécialisé"
        if d.startswith("473"):
            return "Carburants"
        if d.startswith("474"):
            return "Informatique / télécoms"
        if d.startswith("475"):
            return "Équipement de la maison"
        if d.startswith("476"):
            return "Culture / sport / loisirs"
        if d.startswith("4771"):
            return "Mode / habillement"
        if d.startswith("4772"):
            return "Chaussures / maroquinerie"
        if d.startswith("4773"):
            return "Pharmacie / santé"
        if d.startswith("4774"):
            return "Médical / orthopédie"
        if d.startswith("4775"):
            return "Beauté / parfumerie"
        if d.startswith("4776"):
            return "Fleurs / animaux"
        if d.startswith("4777"):
            return "Bijouterie / horlogerie"
        if d.startswith("4778"):
            return "Commerce spécialisé / autre"
        if d.startswith("4779"):
            return "Commerce de détail hors magasin"
        return "Commerce spécialisé / autre"

    if p == "45":
        if d.startswith("451"):
            return "Vente automobile"
        if d.startswith("452"):
            return "Entretien / réparation automobile"
        if d.startswith("453"):
            return "Équipement automobile"
        if d.startswith("454"):
            return "Deux-roues"
        return "Automobile / autre"

    if p == "56":
        if d.startswith("561"):
            return "Restaurants"
        if d.startswith("562"):
            return "Restauration collective / événementielle"
        if d.startswith("563"):
            return "Débits de boissons"
        return "Restauration / autre"

    if p == "55":
        return "Hôtels / hébergement"
    if p in {"86", "87", "88"}:
        return "Santé / social"
    if p in {"90", "91", "92", "93"}:
        return "Culture / loisirs / sport"
    if p == "96":
        return "Services aux particuliers"
    if p == "95":
        return "Réparation"
    if p in {"49", "50", "51", "52", "53"}:
        return "Transport / logistique"
    if p == "68":
        return "Immobilier"
    if p in {"69", "70", "71", "72", "73", "74", "75", "77", "78", "79", "80", "81", "82"}:
        return "Services professionnels"
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

# Taxonomie volontairement large pour signaler les catégories non observées.
# "Non observé" signifie seulement qu'aucun établissement correspondant n'a été
# récupéré dans le périmètre analysé ; cela ne signifie pas qu'il existe une demande.
COMMERCIAL_DETAIL_CATEGORIES = [
    "Alimentaire général",
    "Alimentaire spécialisé",
    "Mode / habillement",
    "Chaussures / maroquinerie",
    "Beauté / parfumerie",
    "Équipement de la maison",
    "Culture / sport / loisirs",
    "Informatique / télécoms",
    "Fleurs / animaux",
    "Bijouterie / horlogerie",
    "Restaurants",
    "Débits de boissons",
    "Hôtels / hébergement",
    "Vente automobile",
    "Entretien / réparation automobile",
    "Équipement automobile",
    "Deux-roues",
    "Pharmacie / santé",
    "Médical / orthopédie",
    "Services aux particuliers",
    "Réparation",
]


def add_commercial_category(rows):
    for row in rows:
        row["Catégorie commerciale"] = classify_ape(row.get("APE"))
        row["Sous-catégorie commerciale"] = classify_ape_detail(row.get("APE"))
    return rows
