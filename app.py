import streamlit as st
import pandas as pd
from engine.matching import score_activities, get_activity_profile, match_brands

st.set_page_config(page_title="Local Matcher V2", page_icon="🏬", layout="wide")
st.title("🏬 Local Matcher V2")
st.caption("Matching immobilier : activité → technique → enseignes → propriétaire")

activities=pd.read_csv("data/activites.csv")
brands=pd.read_csv("data/enseignes.csv")
owners=pd.read_csv("data/proprietaires_demo.csv")

with st.sidebar:
    st.header("Local")
    address=st.text_input("Adresse","25 rue de la République, 75018 Paris")
    surface=st.number_input("Surface (m²)",1,10000,400,10)
    previous=st.selectbox("Ancienne activité",activities["activity_name"])
    st.header("Technique")
    extraction=st.selectbox("Extraction",["Inconnue","Oui","Non"])
    cold=st.selectbox("Froid / chambre froide",["Inconnu","Oui","Non"])
    delivery=st.selectbox("Livraison",["Inconnue","Oui","Non"])
    power=st.selectbox("Puissance électrique importante",["Inconnue","Oui","Non"])
    reserve=st.selectbox("Réserve importante",["Inconnue","Oui","Non"])
    st.header("Propriétaire")
    owner_name=st.text_input("Propriétaire personne morale (si connu)")
    owner_siren=st.text_input("SIREN propriétaire (si connu)")

profile=get_activity_profile(activities,previous)
st.subheader("📍 Profil immobilier estimé")
cols=st.columns(4)
for c,label,key in zip(cols,["Froid","Extraction","Livraison","Réserve"],["cold_need","extraction_need","delivery_need","reserve_need"]):
    c.metric(label,profile[key])
st.info("Ces caractéristiques sont des estimations par activité, pas un diagnostic technique.")

results=score_activities(activities,previous,surface,extraction,cold,delivery,power,reserve)
st.subheader("🎯 Activités compatibles")
d=results.head(15).copy()
d["score"]=d["score"].round().astype(int).astype(str)+"%"
st.dataframe(d[["activity_name","code_ape","category","score","surface_min","surface_max","cold_need","extraction_need","delivery_need","reserve_need"]],use_container_width=True,hide_index=True)

st.subheader("🏪 Enseignes compatibles")
br=match_brands(brands,results,surface).head(20).copy()
br["score"]=br["score"].round().astype(int).astype(str)+"%"
st.dataframe(br[["brand","category","surface_min","surface_max","presence","score","reason"]],use_container_width=True,hide_index=True)

st.subheader("🏢 Propriétaire")
if owner_name or owner_siren:
    st.success(f"{owner_name or 'Nom non renseigné'} — SIREN : {owner_siren or 'non renseigné'}")
else:
    st.warning("Aucun propriétaire renseigné. La V2 prépare cette brique sans prétendre identifier automatiquement un propriétaire.")
st.link_button("Rechercher un propriétaire personne morale avec TOISE","https://toiseai.com/")

st.caption("Les données foncières personnes morales sont millésimées ; elles ne garantissent pas la propriété au jour du test.")

st.subheader("🧪 Données propriétaires de démonstration")
st.dataframe(owners,use_container_width=True,hide_index=True)

st.subheader("⬇️ Export")
export=br.copy()
export["adresse_local"]=address
export["surface_local_m2"]=surface
export["ancienne_activite"]=previous
export["proprietaire"]=owner_name
export["siren_proprietaire"]=owner_siren
st.download_button("Télécharger les prospects CSV",export.to_csv(index=False).encode("utf-8-sig"),"local_matcher_prospects.csv","text/csv")

st.divider()
st.markdown("### Architecture cible\n`SIRENE → établissement fermé → adresse → parcelle → propriétaire personne morale → SIREN → matching enseignes`")
