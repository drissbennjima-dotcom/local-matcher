import streamlit as st
import pandas as pd
import re
import unicodedata

from engine.commercial import add_commercial_category, COMMERCIAL_CATEGORIES, COMMERCIAL_DETAIL_CATEGORIES
from engine.matching import score_activities, get_activity_profile, match_brands
from engine.sirene import (
    search_establishments, search_commune_active_closed, flatten_establishments, summarize_history,
    build_local_history, add_occupation_chronology, add_succession_links_to_chronology,
)
from engine.geocoding import geocode_address
from engine.geo import haversine_m

st.set_page_config(page_title="Local Matcher V6.1", page_icon="🏬", layout="wide")
st.title("🏬 Local Matcher V6.1")
st.caption("Local cible → zone → actifs + fermés → chronologie → succession SIRENE → opportunités de vacance → matching")

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

def build_opportunities(closed_rows):
    """Build a conservative shortlist of currently plausible vacancy signals.

    V6.1 deliberately excludes old closures, unknown dates, and addresses where
    an active establishment is already detected. The result is a prioritisation
    aid, not proof of physical vacancy.
    """
    from datetime import date
    opportunities = []
    historical = []
    today = date.today()

    for r in closed_rows or []:
        item = dict(r)
        closure = _parse_date_safe(item.get("Date fermeture"))
        has_active = bool((item.get("Occupant actif détecté") or "").strip())
        name = (item.get("Enseigne / nom usuel") or item.get("Entreprise") or "").strip()
        gap = item.get("Durée intervalle (mois)")
        try:
            gap = float(gap)
        except Exception:
            gap = None

        # Current active occupant or explicit SIRENE successor means the former
        # establishment is not a current vacancy candidate.
        if has_active or item.get("Succession SIRENE") == "Oui":
            continue

        # No reliable closure date: keep it out of the commercial shortlist.
        if not closure:
            item["Classement vacance"] = "Donnée insuffisante"
            historical.append(item)
            continue

        age_months = max(0, (today - closure).days / 30.4375)

        # Closures older than 36 months are historical, not current opportunity
        # candidates. This prevents 1980s/1990s records from dominating the list.
        if age_months > 36:
            item["Classement vacance"] = "Historique ancien"
            historical.append(item)
            continue

        score = 50
        reasons = ["aucun actif SIRENE détecté à l'adresse"]

        if age_months <= 3:
            score += 35
            reasons.append("fermeture très récente")
        elif age_months <= 12:
            score += 25
            reasons.append("fermeture récente")
        elif age_months <= 24:
            score += 15
            reasons.append("fermeture de moins de 2 ans")
        else:
            score += 5
            reasons.append("fermeture de moins de 3 ans")

        if gap is not None and gap >= 6:
            score += 10
            reasons.append(f"intervalle historique de {gap:g} mois")
        elif gap is not None and gap > 0:
            score += 5
            reasons.append("intervalle historique détecté")

        if name:
            score += 5
            reasons.append("ancien occupant identifié")
        else:
            score -= 20
            reasons.append("ancien occupant non identifié")

        try:
            dist = float(item.get("Distance (m)"))
            if dist <= 250:
                score += 5
                reasons.append("dans les 250 m")
            elif dist <= 500:
                score += 3
                reasons.append("dans les 500 m")
        except Exception:
            pass

        score = max(0, min(100, score))
        if score >= 80:
            niveau = "🔴 Priorité à vérifier"
        elif score >= 65:
            niveau = "🟠 À qualifier"
        else:
            niveau = "🟡 Signal faible"

        item["Score opportunité"] = score
        item["Niveau opportunité"] = niveau
        item["Pourquoi"] = " · ".join(reasons)
        item["Classement vacance"] = "Opportunité récente à vérifier"
        opportunities.append(item)

    opportunities = sorted(
        opportunities,
        key=lambda r: (-int(r.get("Score opportunité", 0)), float(r.get("Distance (m)") or 999999))
    )
    historical = sorted(
        historical,
        key=lambda r: (_parse_date_safe(r.get("Date fermeture")) or date.min),
        reverse=True,
    )
    return opportunities, historical

def _parse_date_safe(value):
    if not value: return None
    try:
        from datetime import date
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None

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
        st.session_state["succession_discoveries"] = []
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
                filtered_closed, succession_discoveries = add_succession_links_to_chronology(
                    api_key, filtered_closed, filtered_active, max_checks=8
                )
                st.session_state["succession_discoveries"] = succession_discoveries
                st.session_state["zone_coord_count"] = sum(1 for r in active_rows if r.get("lat") is not None and r.get("lon") is not None)
                st.session_state["zone_rows"] = filtered_active
                st.session_state["zone_closed_rows"] = filtered_closed
                opportunities, historical_vacancy = build_opportunities(filtered_closed)
                st.session_state["opportunities"] = opportunities
                st.session_state["historical_vacancy"] = historical_vacancy
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

    # --- Opportunity shortlist ---
    opportunities = st.session_state.get("opportunities", [])
    historical_vacancy = st.session_state.get("historical_vacancy", [])
    st.markdown("### 🎯 Opportunités de locaux à qualifier")
    st.caption("V6.1 ne retient ici que les fermetures datées de moins de 36 mois, sans occupant actif détecté à la même adresse. Ce classement est une aide à la prospection, pas une preuve de vacance physique.")
    if opportunities:
        opp_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in opportunities])
        cols = [c for c in ["Score opportunité", "Niveau opportunité", "Distance (m)", "Enseigne / nom usuel", "Entreprise", "Date fermeture", "Vacance historique", "Durée intervalle (mois)", "Pourquoi", "Adresse", "APE", "Catégorie commerciale"] if c in opp_df.columns]
        st.dataframe(opp_df[cols].head(50), use_container_width=True, hide_index=True)
    else:
        st.info("Aucune opportunité récente ne peut être priorisée avec les données récupérées. Les établissements réoccupés ou trop anciens sont volontairement exclus de cette shortlist.")

    if historical_vacancy:
        st.markdown("### 📚 Historique ancien / données à faible intérêt immédiat")
        st.caption("Fermetures de plus de 36 mois ou dates insuffisantes : conservées pour l'historique, mais exclues du classement des opportunités actuelles.")
        hist_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in historical_vacancy])
        cols = [c for c in ["Distance (m)", "Enseigne / nom usuel", "Entreprise", "Date fermeture", "Classement vacance", "Vacance historique", "Adresse", "APE"] if c in hist_df.columns]
        st.dataframe(hist_df[cols].head(100), use_container_width=True, hide_index=True)

    # --- Historical layer for vacancy detection ---
    st.markdown("### 🏚️ Anciens établissements dans le rayon")
    st.caption("Cette table contient uniquement les établissements retournés par la requête SIRENE au statut administratif fermé (F).")
    if zone_closed_rows:
        closed_zone_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ("Historique périodes", "lat", "lon")} for r in zone_closed_rows])
        preferred_cols = [
            "Distance (m)", "Signal de vacance", "Niveau de signal", "Vacance historique",
            "Date fermeture", "Nouvel occupant détecté", "Début nouvel occupant", "Durée intervalle (mois)",
            "Chronologie", "Succession SIRENE", "Successeur SIRET", "Date succession SIRENE", "Continuité économique", "Occupant actif détecté", "Statut", "SIRET", "Enseigne / nom usuel",
            "Entreprise", "APE", "Date création", "Adresse", "Code postal", "Commune", "Nb périodes"
        ]
        closed_cols = [c for c in preferred_cols if c in closed_zone_df.columns] + [c for c in closed_zone_df.columns if c not in preferred_cols]
        st.dataframe(closed_zone_df.sort_values("Distance (m)")[closed_cols].head(300), use_container_width=True, hide_index=True)
        st.caption("V6 distingue les signaux de vacance potentielle, les locaux déjà réoccupés et les cas indéterminés. La shortlist est une aide à la prospection, pas une preuve de vacance physique.")

        succession_discoveries = st.session_state.get("succession_discoveries", [])
        if succession_discoveries:
            st.markdown("### 🔄 Successions détectées depuis les établissements actifs")
            st.caption("Cette vue permet de retrouver un ancien occupant lorsque le prédécesseur n'est pas présent dans le stock des établissements fermés affiché dans le rayon.")
            st.dataframe(pd.DataFrame(succession_discoveries), use_container_width=True, hide_index=True)
    else:
        st.info("Aucun établissement fermé géolocalisé n'a été trouvé dans le rayon avec la couverture SIRENE interrogée.")
else:
    st.warning("Aucun établissement géolocalisé actif n'a été trouvé dans ce rayon. Essayez un rayon supérieur.")

st.divider()

# --- Exact address history ---
st.subheader("🔎 Historique du local")
st.caption("V6.1 conserve une recherche à l'adresse exacte : le numéro, la voie, le code postal et la commune sont vérifiés. Aucun établissement d'une autre adresse n'est conservé.")
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
st.markdown("### Architecture V6.1\n`Adresse exacte → géocodage → commune → SIRENE actifs + fermés → rayon → chronologie → succession bidirectionnelle → signal de vacance → matching`\n\n### Architecture cible\n`Local → zone → vacance potentielle → ancienne activité → profil technique → enseigne → propriétaire → prospection`")
