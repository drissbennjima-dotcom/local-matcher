import streamlit as st
import pandas as pd
import re
import unicodedata

from engine.commercial import add_commercial_category, COMMERCIAL_CATEGORIES, COMMERCIAL_DETAIL_CATEGORIES
from engine.matching import score_activities, get_activity_profile, match_brands
from engine.sirene import (
    search_establishments, search_commune_active_closed, flatten_establishments, summarize_history,
    build_local_history, add_occupation_chronology,
)
from engine.geocoding import geocode_address
from engine.geo import haversine_m

st.set_page_config(page_title="Local Matcher V5.6", page_icon="🏬", layout="wide")
st.title("🏬 Local Matcher V5.6")
st.caption("Local cible → zone → actifs + fermés → chronologie d'occupation → signal de vacance → historique → matching")

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
    max_pages = st.slider("Pages SIRENE maximum (par statut)", 1, 20, 10, help="Une page SIRENE contient jusqu'à 1 000 établissements. V5.4 interroge séparément les actifs et les fermés : plus de pages = plus de couverture mais plus d'appels API.")

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

def _norm_address(value):
    value = (value or "").strip().upper()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def add_vacancy_signals(closed_rows, active_rows):
    """Compare closed and active establishments by normalized exact address."""
    active_by_address = {}
    for row in active_rows or []:
        key = (_norm_address(row.get("Adresse")), _norm_address(row.get("Code postal")), _norm_address(row.get("Commune")))
        if all(key):
            active_by_address.setdefault(key, []).append(row)

    enriched = []
    for row in closed_rows or []:
        item = dict(row)
        key = (_norm_address(row.get("Adresse")), _norm_address(row.get("Code postal")), _norm_address(row.get("Commune")))
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

# --- Geocoding + zone ---
st.subheader("🗺️ Zone commerciale")
if st.button("Analyser le local et son environnement", type="primary"):
    try:
        with st.spinner("Géolocalisation de l'adresse…"):
            geo = geocode_address(address)
        st.session_state["geo"] = geo
        st.session_state["zone_rows"] = []
        st.session_state["zone_closed_rows"] = []
        st.session_state["zone_error"] = ""
        st.success(f"Adresse géolocalisée : {geo['label']}")

        if use_zone:
            if not api_key:
                raise RuntimeError("Clé SIRENE introuvable. Vérifiez Streamlit → Manage app → Settings → Secrets.")
            with st.spinner("Recherche SIRENE des établissements actifs et fermés de la commune puis filtrage par rayon…"):
                raw_all, raw_active, raw_closed = search_commune_active_closed(api_key, geo["citycode"], max_pages=max_pages)
                active_rows = add_commercial_category(flatten_establishments(raw_active, forced_status="A"))
                closed_rows = add_commercial_category(flatten_establishments(raw_closed, forced_status="F"))

                def filter_by_radius(items):
                    filtered_items = []
                    for r in items:
                        if r.get("lat") is None or r.get("lon") is None:
                            continue
                        d = haversine_m(geo["lat"], geo["lon"], r["lat"], r["lon"])
                        if d <= radius:
                            r["Distance (m)"] = round(d)
                            filtered_items.append(r)
                    return filtered_items

                filtered_active = filter_by_radius(active_rows)
                filtered_closed = filter_by_radius(closed_rows)
                filtered_closed = add_vacancy_signals(filtered_closed, filtered_active)
                filtered_closed = add_occupation_chronology(filtered_closed, filtered_active)
                st.session_state["zone_coord_count"] = sum(1 for r in active_rows if r.get("lat") is not None and r.get("lon") is not None)
                st.session_state["zone_rows"] = filtered_active
                st.session_state["zone_closed_rows"] = filtered_closed
                st.session_state["zone_total_commune"] = len(active_rows)
                st.session_state["zone_closed_total_commune"] = len(closed_rows)
    except Exception as exc:
        st.session_state["zone_error"] = str(exc)
        st.error(str(exc))

geo = st.session_state.get("geo")
zone_rows = st.session_state.get("zone_rows", [])
zone_closed_rows = st.session_state.get("zone_closed_rows", [])
if geo:
    st.markdown(f"**Centre :** {geo['label']} · **Rayon :** {radius} m")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Actifs dans le rayon", len(zone_rows))
    c2.metric("Fermés dans le rayon", len(zone_closed_rows))
    c3.metric("Commune interrogée", geo.get("city", "NC"))
    c4.metric("Coordonnées", f"{geo['lat']:.5f}, {geo['lon']:.5f}")
    commune_total = st.session_state.get("zone_total_commune", 0)
    coord_total = st.session_state.get("zone_coord_count", 0)
    coverage = (coord_total / commune_total * 100) if commune_total else 0
    c5.metric("Couverture actifs", f"{coverage:.0f}%")

    if zone_rows:
        # Carte allégée : petits points pour éviter le nuage rouge illisible.
        map_df = pd.DataFrame([{"lat": r["lat"], "lon": r["lon"]} for r in zone_rows])
        try:
            import pydeck as pdk
            layers = [
                pdk.Layer(
                    "ScatterplotLayer",
                    data=map_df,
                    get_position="[lon, lat]",
                    get_radius=10,
                    get_fill_color=[220, 60, 45, 130],
                    pickable=True,
                ),
                pdk.Layer(
                    "ScatterplotLayer",
                    data=pd.DataFrame([{"lat": geo["lat"], "lon": geo["lon"]}]),
                    get_position="[lon, lat]",
                    get_radius=35,
                    get_fill_color=[30, 30, 30, 220],
                    pickable=False,
                ),
            ]
            view = pdk.ViewState(latitude=geo["lat"], longitude=geo["lon"], zoom=15)
            st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, tooltip={"text": "Établissement"}), use_container_width=True)
        except Exception:
            st.map(map_df, latitude="lat", longitude="lon", zoom=14)

        zone_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in zone_rows])
        if not zone_df.empty:
            st.markdown("### 📊 Composition de la zone")
            category_counts = (
                zone_df["Catégorie commerciale"]
                .value_counts()
                .rename_axis("Catégorie")
                .reset_index(name="Établissements")
            )
            category_counts["Part"] = (category_counts["Établissements"] / len(zone_df) * 100).round(1).astype(str) + "%"
            st.dataframe(category_counts, use_container_width=True, hide_index=True)

            commercial_df = zone_df[zone_df["Catégorie commerciale"].isin(COMMERCIAL_CATEGORIES)].copy()
            st.markdown("### 🏪 Lecture commerciale")
            if commercial_df.empty:
                st.info("Aucune catégorie commerciale sélectionnée n'a été détectée dans le rayon selon le regroupement APE utilisé.")
            else:
                # KPIs utiles à la commercialisation : on distingue le tissu commercial
                # du total SIRENE (qui contient aussi bureaux, holdings, sièges, etc.).
                k1, k2, k3 = st.columns(3)
                k1.metric("Établissements à vocation commerciale", len(commercial_df))
                area_km2 = 3.141592653589793 * (radius / 1000) ** 2
                density = len(commercial_df) / area_km2 if area_km2 else 0
                k2.metric("Densité commerciale théorique", f"{density:.0f} / km²")
                k3.metric("Part du total SIRENE", f"{len(commercial_df) / len(zone_df) * 100:.1f}%")

                cc = commercial_df["Catégorie commerciale"].value_counts().rename_axis("Catégorie").reset_index(name="Établissements")
                cc["Part du périmètre commercial"] = (cc["Établissements"] / len(commercial_df) * 100).round(1).astype(str) + "%"
                st.dataframe(cc, use_container_width=True, hide_index=True)

                st.markdown("#### 🔎 Sous-catégories commerciales")
                detail = commercial_df["Sous-catégorie commerciale"].value_counts().rename_axis("Sous-catégorie").reset_index(name="Établissements")
                detail["Part du périmètre commercial"] = (detail["Établissements"] / len(commercial_df) * 100).round(1).astype(str) + "%"
                st.dataframe(detail.head(20), use_container_width=True, hide_index=True)

                low = detail[detail["Établissements"] <= max(2, int(len(commercial_df) * 0.03))]
                if not low.empty:
                    st.markdown("**Présences commerciales peu représentées**")
                    st.write(" · ".join(low["Sous-catégorie"].head(10).tolist()))

                observed = set(detail["Sous-catégorie"].tolist())
                absent = [x for x in COMMERCIAL_DETAIL_CATEGORIES if x not in observed]
                if absent:
                    st.markdown("**Catégories non observées dans les données récupérées**")
                    st.write(" · ".join(absent))
                    st.caption("Une catégorie non observée ne signifie pas qu'elle est absente du terrain ni qu'elle représente une opportunité : le résultat dépend du périmètre, de la couverture SIRENE et du niveau de regroupement APE.")

            st.markdown("### 🏪 Établissements proches")
            cols = [c for c in ["Distance (m)", "Statut", "SIRET", "Enseigne / nom usuel", "Entreprise", "APE", "Catégorie commerciale", "Sous-catégorie commerciale", "Adresse"] if c in zone_df.columns]
            st.dataframe(zone_df.sort_values("Distance (m)")[cols].head(200), use_container_width=True, hide_index=True)
            st.caption("La zone est calculée à partir des coordonnées géographiques diffusées par Sirene et d'une distance à vol d'oiseau. La catégorie commerciale est un regroupement analytique de l'APE ; elle ne remplace pas une vérification terrain. Les statistiques commerciales ci-dessus utilisent uniquement les établissements actifs.")

    # --- Historical layer for vacancy detection ---
    st.markdown("### 🏚️ Anciens établissements dans le rayon")
    st.caption("Cette table contient uniquement les établissements retournés par la requête SIRENE au statut administratif fermé (F).")
    if zone_closed_rows:
        closed_zone_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in zone_closed_rows])
        preferred_cols = [
            "Distance (m)", "Signal de vacance", "Niveau de signal", "Vacance historique",
            "Date fermeture", "Nouvel occupant détecté", "Début nouvel occupant", "Durée intervalle (mois)",
            "Chronologie", "Occupant actif détecté", "Statut", "SIRET", "Enseigne / nom usuel",
            "Entreprise", "APE", "Date création", "Adresse", "Code postal", "Commune", "Nb périodes"
        ]
        closed_cols = [c for c in preferred_cols if c in closed_zone_df.columns] + [c for c in closed_zone_df.columns if c not in preferred_cols]
        st.dataframe(closed_zone_df.sort_values("Distance (m)")[closed_cols].head(300), use_container_width=True, hide_index=True)
        st.caption("V5.6 ajoute une chronologie prudente : fermeture d'un établissement → recherche d'un actif postérieur à la même adresse. Un intervalle détecté constitue un signal de vacance historique possible, pas une preuve de vacance physique ni une durée de bail.")
    else:
        st.info("Aucun établissement fermé géolocalisé n'a été trouvé dans le rayon avec la couverture SIRENE interrogée.")
else:
    st.warning("Aucun établissement géolocalisé actif n'a été trouvé dans ce rayon. Essayez un rayon supérieur.")

st.divider()

# --- Exact address history ---
st.subheader("🔎 Historique du local")
st.caption("V5.6 impose une recherche à l'adresse exacte : le numéro, la voie, le code postal et la commune sont vérifiés. Aucun établissement d'une autre adresse n'est conservé.")
if use_sirene:
    if not api_key:
        st.error("Clé SIRENE introuvable. Vérifiez Streamlit → Manage app → Settings → Secrets.")
    elif st.button("🔎 Rechercher l'historique du local"):
        with st.spinner("Recherche stricte des établissements actifs et fermés à l'adresse exacte…"):
            try:
                raw, query_used = search_establishments(api_key, address, include_closed=True, max_results=100)
                rows = flatten_establishments(raw)
                st.session_state["sirene_rows"] = rows
                st.session_state["sirene_query"] = query_used
                st.session_state["sirene_scope"] = (f"Adresse exacte — {len(rows)} résultat(s) vérifié(s)" if rows else "Aucun résultat exact")
                st.session_state.pop("local_timeline", None)
                st.session_state.pop("local_succession", None)
                if rows:
                    st.success(f"{len(rows)} établissement(s) trouvé(s) à l'adresse.")
                else:
                    st.warning("Aucun établissement trouvé à cette adresse avec SIRENE.")
            except Exception as exc:
                st.error(str(exc))

rows = st.session_state.get("sirene_rows", [])
if rows:
    sirene_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in rows])
    st.caption(f"Périmètre : **{st.session_state.get('sirene_scope', 'Adresse exacte')}**")
    st.caption(f"Requêtes SIRENE : `{st.session_state.get('sirene_query','')}`")
    st.dataframe(sirene_df, use_container_width=True, hide_index=True)
    closed = [r for r in rows if r["Statut"] == "Fermé"]
    active = [r for r in rows if r["Statut"] == "Actif"]
    st.info(f"**{len(closed)} fermé(s)** · **{len(active)} actif(s)**.")

    if st.button("📚 Construire la chronologie du local", disabled=not rows):
        with st.spinner("Reconstitution de l'historique et vérification des liens de succession…"):
            try:
                timeline, succession_rows, calls = build_local_history(api_key, rows, address, max_seeds=10)
                st.session_state["local_timeline"] = timeline
                st.session_state["local_succession"] = succession_rows
                st.session_state["local_history_calls"] = calls
            except Exception as exc:
                st.error(str(exc))

    timeline = st.session_state.get("local_timeline", [])
    succession_rows = st.session_state.get("local_succession", [])
    if timeline:
        st.markdown("### 🧭 Chronologie détectée du local")
        st.dataframe(pd.DataFrame(timeline), use_container_width=True, hide_index=True)
        st.caption(f"Chronologie construite à partir des périodes historisées SIRENE du périmètre **{st.session_state.get('sirene_scope', 'adresse exacte')}**. {st.session_state.get('local_history_calls', 0)} interrogation(s) SIRENE détaillée(s) supplémentaire(s). Les périodes SIRENE décrivent l'établissement ; elles ne constituent pas à elles seules une preuve juridique d'occupation physique du local.")
    else:
        st.warning("Aucune donnée de période exploitable n'a pu être reconstituée pour les établissements sélectionnés. Cela ne signifie pas qu'il n'existe aucun historique à cette adresse.")

    if succession_rows:
        st.markdown("### 🔄 Liens de succession détectés")
        st.dataframe(pd.DataFrame(succession_rows), use_container_width=True, hide_index=True)
        st.caption("Un lien de succession signale une relation enregistrée par SIRENE entre établissements ; la continuité économique et le transfert de siège sont des informations déclarées dans le répertoire et doivent être interprétés avec prudence.")

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
st.download_button("Télécharger les prospects CSV", export.to_csv(index=False).encode("utf-8-sig"), "local_matcher_prospects_v5_5.csv", "text/csv")

st.divider()
st.markdown("### Architecture V5.6\n`Adresse exacte → géocodage → commune → SIRENE actifs + fermés → rayon → chronologie d’occupation → signal de vacance → historique → succession → matching`\n\n### Architecture cible\n`Local → zone → vacance potentielle → ancienne activité → profil technique → enseigne → propriétaire → prospection`")
