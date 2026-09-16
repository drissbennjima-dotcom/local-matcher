import streamlit as st
import pandas as pd

from engine.matching import score_activities, get_activity_profile, match_brands
from engine.sirene import search_establishments, search_commune_active, flatten_establishments, summarize_history
from engine.geocoding import geocode_address
from engine.geo import haversine_m

st.set_page_config(page_title="Local Matcher V4", page_icon="🏬", layout="wide")
st.title("🏬 Local Matcher V4")
st.caption("Local cible → géolocalisation → zone commerciale → SIRENE → historique → matching")

activities = pd.read_csv("data/activites.csv")
brands = pd.read_csv("data/enseignes.csv")
owners = pd.read_csv("data/proprietaires_demo.csv")

with st.sidebar:
    st.header("📍 Local cible")
    address = st.text_input("Adresse complète", "16 rue Joseph Dijon, 75018 Paris")
    surface = st.number_input("Surface (m²)", 1, 10000, 250, 10)
    radius = st.selectbox("Rayon d'analyse", [250, 500, 1000, 2000], index=1, format_func=lambda x: f"{x} m")

    st.header("🏪 Analyse commerciale")
    use_zone = st.checkbox("Analyser les établissements dans le rayon", value=True)
    max_pages = st.slider("Pages SIRENE maximum", 1, 20, 10, help="Une page SIRENE contient jusqu'à 1 000 établissements. Plus de pages = plus de couverture mais plus d'appels API.")

    st.header("🏢 Ancien occupant")
    use_sirene = st.checkbox("Rechercher l'historique à l'adresse", value=True)
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

api_key = st.secrets.get("SIRENE_API_KEY") if hasattr(st, "secrets") else None

# --- Geocoding + zone ---
st.subheader("🗺️ Zone commerciale")
if st.button("Analyser le local et son environnement", type="primary"):
    try:
        with st.spinner("Géolocalisation de l'adresse…"):
            geo = geocode_address(address)
        st.session_state["geo"] = geo
        st.session_state["zone_rows"] = []
        st.session_state["zone_error"] = ""
        st.success(f"Adresse géolocalisée : {geo['label']}")

        if use_zone:
            if not api_key:
                raise RuntimeError("Clé SIRENE introuvable. Vérifiez Streamlit → Manage app → Settings → Secrets.")
            with st.spinner("Recherche SIRENE des établissements de la commune puis filtrage par rayon…"):
                raw_zone = search_commune_active(api_key, geo["citycode"], max_pages=max_pages)
                zone_rows = flatten_establishments(raw_zone)
                filtered = []
                for r in zone_rows:
                    if r.get("lat") is None or r.get("lon") is None:
                        continue
                    d = haversine_m(geo["lat"], geo["lon"], r["lat"], r["lon"])
                    if d <= radius:
                        r["Distance (m)"] = round(d)
                        filtered.append(r)
                st.session_state["zone_rows"] = filtered
                st.session_state["zone_total_commune"] = len(zone_rows)
    except Exception as exc:
        st.session_state["zone_error"] = str(exc)
        st.error(str(exc))

geo = st.session_state.get("geo")
zone_rows = st.session_state.get("zone_rows", [])
if geo:
    st.markdown(f"**Centre :** {geo['label']} · **Rayon :** {radius} m")
    c1, c2, c3 = st.columns(3)
    c1.metric("Établissements dans le rayon", len(zone_rows))
    c2.metric("Commune interrogée", geo.get("city", "NC"))
    c3.metric("Coordonnées", f"{geo['lat']:.5f}, {geo['lon']:.5f}")

    if zone_rows:
        map_df = pd.DataFrame([{"lat": r["lat"], "lon": r["lon"]} for r in zone_rows])
        st.map(map_df, latitude="lat", longitude="lon", zoom=14)

        zone_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in zone_rows])
        if not zone_df.empty:
            zone_df["Catégorie APE"] = zone_df["APE"].fillna("").astype(str).str[:2]
            st.markdown("### 📊 Composition de la zone")
            counts = zone_df["Catégorie APE"].replace("", "NC").value_counts().head(15).rename_axis("APE").reset_index(name="Établissements")
            st.dataframe(counts, use_container_width=True, hide_index=True)
            st.markdown("### 🏪 Établissements proches")
            cols = [c for c in ["Distance (m)", "Statut", "SIRET", "Enseigne / nom usuel", "Entreprise", "APE", "Adresse"] if c in zone_df.columns]
            st.dataframe(zone_df.sort_values("Distance (m)")[cols].head(200), use_container_width=True, hide_index=True)
            st.caption("La zone est calculée à partir des coordonnées géographiques diffusées par Sirene et d'une distance à vol d'oiseau. Les résultats dépendent de la couverture géographique disponible pour les établissements.")
    else:
        st.warning("Aucun établissement géolocalisé n'a été trouvé dans ce rayon. Essayez un rayon supérieur.")

st.divider()

# --- Exact address history ---
st.subheader("🔎 Historique du local")
if use_sirene:
    if not api_key:
        st.error("Clé SIRENE introuvable. Vérifiez Streamlit → Manage app → Settings → Secrets.")
    elif st.button("Rechercher actifs + anciens occupants à cette adresse"):
        with st.spinner("Interrogation SIRENE à l'adresse exacte…"):
            try:
                raw, query_used = search_establishments(api_key, address, include_closed=True, max_results=100)
                rows = flatten_establishments(raw)
                st.session_state["sirene_rows"] = rows
                st.session_state["sirene_query"] = query_used
                if rows:
                    st.success(f"{len(rows)} établissement(s) trouvé(s).")
                else:
                    st.warning("Aucun établissement trouvé à cette adresse avec SIRENE.")
            except Exception as exc:
                st.error(str(exc))

rows = st.session_state.get("sirene_rows", [])
if rows:
    sirene_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in rows])
    st.caption(f"Requêtes SIRENE : `{st.session_state.get('sirene_query','')}`")
    st.dataframe(sirene_df, use_container_width=True, hide_index=True)
    closed = [r for r in rows if r["Statut"] == "Fermé"]
    active = [r for r in rows if r["Statut"] == "Actif"]
    st.info(f"**{len(closed)} fermé(s)** · **{len(active)} actif(s)**.")
    labels = []
    for i, r in enumerate(closed):
        labels.append((i, f"{r['SIRET']} — {r['Enseigne / nom usuel'] or r['Entreprise'] or 'Nom non renseigné'} — APE {r['APE'] or 'NC'}"))
    if closed:
        chosen = st.selectbox("Ancien occupant à utiliser pour le matching", labels, format_func=lambda x: x[1])
        chosen_row = closed[chosen[0]]
        st.session_state["chosen_sirene"] = chosen_row
        history = summarize_history(chosen_row["Historique périodes"])
        if history:
            st.markdown("**Historique SIRENE de l'établissement sélectionné**")
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)

# --- Matching ---
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
export["rayon_analyse_m"] = radius
export["ancienne_activite"] = previous
export["proprietaire"] = owner_name
export["siren_proprietaire"] = owner_siren
if st.session_state.get("chosen_sirene"):
    c = st.session_state["chosen_sirene"]
    export["ancien_siret_sirene"] = c.get("SIRET", "")
    export["ancien_siren_sirene"] = c.get("SIREN", "")
    export["ancien_occupant_sirene"] = c.get("Enseigne / nom usuel", "") or c.get("Entreprise", "")
    export["ancien_ape_sirene"] = c.get("APE", "")
st.download_button("Télécharger les prospects CSV", export.to_csv(index=False).encode("utf-8-sig"), "local_matcher_prospects_v4.csv", "text/csv")

st.divider()
st.markdown("### Architecture V4\n`Adresse → géocodage → commune → SIRENE géolocalisé → rayon → environnement commercial → historique local → matching`\n\n### Architecture cible\n`Local → zone → historique → parcelle → propriétaire → prospects → enseignes`")
