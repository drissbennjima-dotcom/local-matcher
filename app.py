import streamlit as st
import pandas as pd

from engine.matching import score_activities, get_activity_profile, match_brands
from engine.sirene import search_establishments, flatten_establishments, summarize_history

st.set_page_config(page_title="Local Matcher V3.2", page_icon="🏬", layout="wide")
st.title("🏬 Local Matcher V3.2")
st.caption("SIRENE réel → ancien occupant → activité → profil technique → enseignes compatibles")

activities = pd.read_csv("data/activites.csv")
brands = pd.read_csv("data/enseignes.csv")
owners = pd.read_csv("data/proprietaires_demo.csv")

with st.sidebar:
    st.header("📍 Local")
    address = st.text_input("Adresse complète", "16 rue Joseph Dijon, 75018 Paris")
    surface = st.number_input("Surface (m²)", 1, 10000, 250, 10)

    st.header("🏢 Ancien occupant")
    use_sirene = st.checkbox("Rechercher dans SIRENE", value=True)
    previous = st.selectbox("Ancienne activité utilisée pour le matching", activities["activity_name"])

    st.header("🔧 Technique")
    extraction = st.selectbox("Extraction", ["Inconnue", "Oui", "Non"])
    cold = st.selectbox("Froid / chambre froide", ["Inconnu", "Oui", "Non"])
    delivery = st.selectbox("Livraison", ["Inconnue", "Oui", "Non"])
    power = st.selectbox("Puissance électrique importante", ["Inconnue", "Oui", "Non"])
    reserve = st.selectbox("Réserve importante", ["Inconnue", "Oui", "Non"])

    st.header("🏢 Propriétaire")
    owner_name = st.text_input("Propriétaire personne morale (si connu)")
    owner_siren = st.text_input("SIREN propriétaire (si connu)")

# --- SIRENE ---
st.subheader("🔎 Recherche SIRENE")
api_key = None
try:
    api_key = st.secrets.get("SIRENE_API_KEY")
except Exception:
    api_key = None

if use_sirene:
    if not api_key:
        st.error("Clé SIRENE introuvable. Vérifiez Streamlit → Manage app → Settings → Secrets.")
    else:
        if st.button("Rechercher actifs + anciens occupants à cette adresse", type="primary"):
            with st.spinner("Interrogation de SIRENE…"):
                try:
                    raw, query_used = search_establishments(api_key, address, max_results=100)
                    rows = flatten_establishments(raw)
                    if rows:
                        st.session_state["sirene_rows"] = rows
                        st.session_state["sirene_query"] = query_used
                        st.success(f"{len(rows)} établissement(s) trouvé(s).")
                    else:
                        st.session_state["sirene_rows"] = []
                        st.warning("Aucun établissement trouvé à cette adresse avec SIRENE.")
                except Exception as exc:
                    st.error(str(exc))

rows = st.session_state.get("sirene_rows", [])
if rows:
    sirene_df = pd.DataFrame([{k:v for k,v in r.items() if k != "Historique périodes"} for r in rows])
    st.caption(f"Requête SIRENE utilisée : `{st.session_state.get('sirene_query','')}`")
    st.dataframe(sirene_df, use_container_width=True, hide_index=True)

    closed = [r for r in rows if r["Statut"] == "Fermé"]
    active = [r for r in rows if r["Statut"] == "Actif"]
    st.info(f"**{len(closed)} fermé(s)** · **{len(active)} actif(s)**. V3.2 interroge explicitement les deux statuts ; un établissement fermé peut servir d'ancien occupant et son historique SIRENE peut préciser l'activité ou l'enseigne.")

    labels = []
    for i, r in enumerate(closed):
        label = f"{r['SIRET']} — {r['Enseigne / nom usuel'] or r['Entreprise'] or 'Nom non renseigné'} — APE {r['APE'] or 'NC'}"
        labels.append((i, label))
    if closed:
        chosen = st.selectbox("Ancien occupant à utiliser pour le matching", labels, format_func=lambda x: x[1])
        chosen_row = closed[chosen[0]]
        st.session_state["chosen_sirene"] = chosen_row
        history = summarize_history(chosen_row["Historique périodes"])
        if history:
            st.markdown("**Historique SIRENE de l'établissement sélectionné**")
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
    elif active:
        st.warning("Aucun établissement fermé trouvé. Vous pouvez néanmoins utiliser l'activité manuellement ci-dessous.")

st.divider()

profile = get_activity_profile(activities, previous)
st.subheader("📍 Profil immobilier estimé")
cols = st.columns(4)
for c, label, key in zip(cols, ["Froid", "Extraction", "Livraison", "Réserve"], ["cold_need", "extraction_need", "delivery_need", "reserve_need"]):
    c.metric(label, profile[key])
st.info("Ces caractéristiques sont des estimations par activité, pas un diagnostic technique. SIRENE ne prouve pas la présence d'une installation technique dans le local.")

results = score_activities(activities, previous, surface, extraction, cold, delivery, power, reserve)
st.subheader("🎯 Activités compatibles")
d = results.head(15).copy()
d["score"] = d["score"].round().astype(int).astype(str) + "%"
st.dataframe(d[["activity_name","code_ape","category","score","surface_min","surface_max","cold_need","extraction_need","delivery_need","reserve_need"]], use_container_width=True, hide_index=True)

st.subheader("🏪 Enseignes compatibles")
br = match_brands(brands, results, surface).head(20).copy()
br["score"] = br["score"].round().astype(int).astype(str) + "%"
st.dataframe(br[["brand","category","surface_min","surface_max","presence","score","reason"]], use_container_width=True, hide_index=True)

st.subheader("🏢 Propriétaire")
if owner_name or owner_siren:
    st.success(f"{owner_name or 'Nom non renseigné'} — SIREN : {owner_siren or 'non renseigné'}")
else:
    st.warning("Aucun propriétaire renseigné. Cette brique sera connectée ensuite aux données foncières personnes morales.")
st.link_button("Rechercher un propriétaire personne morale avec TOISE", "https://toiseai.com/")
st.caption("Les données foncières personnes morales sont millésimées ; elles ne garantissent pas la propriété au jour du test.")

st.subheader("⬇️ Export")
export = br.copy()
export["adresse_local"] = address
export["surface_local_m2"] = surface
export["ancienne_activite"] = previous
export["proprietaire"] = owner_name
export["siren_proprietaire"] = owner_siren
if st.session_state.get("chosen_sirene"):
    c = st.session_state["chosen_sirene"]
    export["ancien_siret_sirene"] = c.get("SIRET", "")
    export["ancien_siren_sirene"] = c.get("SIREN", "")
    export["ancien_occupant_sirene"] = c.get("Enseigne / nom usuel", "") or c.get("Entreprise", "")
    export["ancien_ape_sirene"] = c.get("APE", "")

st.download_button("Télécharger les prospects CSV", export.to_csv(index=False).encode("utf-8-sig"), "local_matcher_prospects_v3_2.csv", "text/csv")

st.divider()
st.markdown("### Architecture actuelle\n`SIRENE → établissement à l'adresse → actif/fermé → ancien occupant → APE/historique → matching enseignes`\n\n### Architecture cible\n`SIRENE → établissement fermé → adresse → parcelle → propriétaire personne morale → SIREN → matching enseignes`")
