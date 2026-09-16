import pandas as pd

LEVELS = {"Faible": 1, "Moyen": 2, "Élevé": 3, "Très élevé": 4, "Inconnu": 0}

def get_activity_profile(df, activity_name):
    return df.loc[df["activity_name"] == activity_name].iloc[0].to_dict()

def _need_match(user_value, expected):
    if user_value in ("Inconnu", "Inconnue"):
        return 0.5
    n = LEVELS.get(expected, 0)
    if not n:
        return 0.5
    return min(n/4, 1) if user_value == "Oui" else 1-min(n/4, 1)

def score_activities(activities, previous_activity, surface, extraction, cold, delivery, power, reserve):
    previous = activities.loc[activities["activity_name"] == previous_activity].iloc[0]
    rows = []
    for _, row in activities.iterrows():
        family = 1.0 if row["category"] == previous["category"] else 0.35
        if row["surface_min"] <= surface <= row["surface_max"]:
            surface_score = 1.0
        elif surface < row["surface_min"]:
            surface_score = max(0, 1-(row["surface_min"]-surface)/row["surface_min"])
        else:
            surface_score = max(0, 1-(surface-row["surface_max"])/row["surface_max"])
        technical = sum([
            _need_match(cold,row["cold_need"]),
            _need_match(extraction,row["extraction_need"]),
            _need_match(delivery,row["delivery_need"]),
            _need_match(power,row["power_need"]),
            _need_match(reserve,row["reserve_need"])
        ])/5
        similarity = 1.0 if row["activity_name"] == previous_activity else (0.8 if row["category"] == previous["category"] else 0.45)
        score = (family*.30 + surface_score*.20 + technical*.25 + similarity*.25)*100
        rows.append({**row.to_dict(),"score":score})
    return pd.DataFrame(rows).sort_values("score", ascending=False)

def match_brands(brands, activity_results, surface):
    top = activity_results.head(10)
    cats = set(top["category"])
    rows=[]
    for _, b in brands.iterrows():
        family = 1.0 if b["category"] in cats else 0.35
        if b["surface_min"] <= surface <= b["surface_max"]:
            ss=1.0
        else:
            ss=max(0,1-min(abs(surface-b["surface_min"])/max(b["surface_min"],1),
                            abs(surface-b["surface_max"])/max(b["surface_max"],1)))
        score=(family*.45+ss*.35+float(b["presence_score"])*.20)*100
        rows.append({**b.to_dict(),"score":score,
                     "reason":f"Famille {b['category']} ; surface cible {b['surface_min']}-{b['surface_max']} m²"})
    return pd.DataFrame(rows).sort_values("score",ascending=False)
