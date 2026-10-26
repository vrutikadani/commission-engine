"""Generate a synthetic quarter of deals and a rep roster (fixed seed, reproducible)."""
import csv, random
from pathlib import Path

random.seed(7)
HERE = Path(__file__).parent
reps = [
    ("Avery Brooks", "AE", "Morgan Hale"), ("Riley Chen", "AE", "Morgan Hale"),
    ("Jamie Patel", "AE", "Morgan Hale"), ("Quinn Foster", "AE_RAMP", "Morgan Hale"),
    ("Taylor Reyes", "INSIDE", "Morgan Hale"), ("Casey Kim", "INSIDE", "Morgan Hale"),
    ("Drew Santos", "ACCOUNT_MGR", "Morgan Hale"), ("Skyler Ward", "ACCOUNT_MGR", "Morgan Hale"),
    ("Morgan Hale", "DIRECTOR", ""),
]
with open(HERE / "reps.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["name", "role", "manager"]); w.writerows(reps)

months = ["2026-07", "2026-08", "2026-09"]
rows, n = [], 1000
for name, role, _ in reps:
    if role == "DIRECTOR":
        continue
    k = {"AE": 9, "AE_RAMP": 3, "INSIDE": 8, "ACCOUNT_MGR": 22}[role]
    for _ in range(k):
        n += 1
        m = random.choice(months)
        if role == "ACCOUNT_MGR":
            amt = round(random.uniform(4000, 16000), 2)
            rows.append([f"D-{n}", name, m, amt, round(amt * random.uniform(0, .08), 2), "consumables", 0, 0])
            continue
        typ = random.choices(["capital", "ifs"], [0.8, 0.2])[0]
        dep = random.choice([1500, 5000, 9000]) if typ == "ifs" else 0
        rows.append([f"D-{n}", name, m, random.choice([24500, 27500, 29900, 32000]), 0, typ, dep,
                     int(role == "INSIDE" and random.random() < .4)])
with open(HERE / "deals_q3.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["deal_id", "owner", "month", "amount", "discount", "deal_type", "deposit", "fast_mql"])
    w.writerows(rows)
print(f"{len(rows)} deals, {len(reps)} reps")
