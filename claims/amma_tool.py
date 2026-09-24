"""MotherWell check_values tool. Plain Python. No AI, no Azure, no network.

Every clinical range lives in this file, so a model upgrade can never change a flag.
Teaching ranges only: they must be reviewed by a clinician before any real use.

Run it alone:  python amma_tool.py
"""
import json
import re

# ---------------------------------------------------------------------------
# Version of every clinical table in this file (RANGES, CONVERT, PLAUSIBLE, URGENT_BELOW).
# Change it whenever any number below changes. Every tool output carries it, so any flag
# can be traced back to the exact table that produced it. The table's history is the
# git history of this file.
# ---------------------------------------------------------------------------
RANGES_VERSION = "2026-09-24"

# ---------------------------------------------------------------------------
# Ranges, in one canonical unit per metric.
#   haemoglobin      WHO 2024 cutoffs by trimester (11.0 / 10.5 / 11.0 g/dL)
#   fasting_glucose  WHO 2013: 92 mg/dL or more meets the GDM threshold, so the
#                    upper bound is EXCLUSIVE (92 itself is flagged HIGH)
#   tsh, ferritin, vitamin_d  published clinical guidance, not WHO (WHO uses a
#                    ferritin cutoff of 15; 30 is the stricter clinical value)
# ---------------------------------------------------------------------------
RANGES = {
    "haemoglobin":     {"unit": "g/dL",  "min": {1: 11.0, 2: 10.5, 3: 11.0}, "max": 15.0,
                        "source": "WHO 2024 haemoglobin cutoffs"},
    "fasting_glucose": {"unit": "mg/dL", "min": 60, "max": 92, "max_exclusive": True,
                        "source": "WHO 2013 GDM criteria"},
    "tsh":             {"unit": "mIU/L", "min": 0.1, "max": {1: 2.5, 2: 3.0, 3: 3.0},
                        "source": "clinical guidance"},
    "ferritin":        {"unit": "ng/mL", "min": 30, "max": 300,
                        "source": "clinical guidance"},
    "vitamin_d":       {"unit": "ng/mL", "min": 20, "max": 100,
                        "source": "clinical guidance"},
}

# Unit conversion into the canonical unit above. Keys are normalised unit strings.
CONVERT = {
    "haemoglobin":     {"g/dl": 1.0, "g/l": 0.1, "mmol/l": 1.611},
    "fasting_glucose": {"mg/dl": 1.0, "mmol/l": 18.016},
    "tsh":             {"miu/l": 1.0, "uiu/ml": 1.0, "mu/l": 1.0},
    "ferritin":        {"ng/ml": 1.0, "ug/l": 1.0},
    "vitamin_d":       {"ng/ml": 1.0, "nmol/l": 1 / 2.496},
}

# Values that must not wait for the next appointment. Deliberately short: each entry is a
# clinical decision and needs the same clinician review as RANGES.
#   haemoglobin  WHO 2024: below 7.0 g/dL is severe anaemia, in every trimester
URGENT_BELOW = {"haemoglobin": 7.0}

# A number outside these limits (in the canonical unit) is almost certainly in a
# different unit. We never guess: it goes to "unchecked" and the mother is told.
PLAUSIBLE = {
    "haemoglobin": (3, 25), "fasting_glucose": (20, 700), "tsh": (0, 150),
    "ferritin": (0, 10000), "vitamin_d": (1, 200),
}

# Names people and models actually write. Ambiguous names such as "glucose",
# "blood_sugar" or "thyroid" are deliberately NOT mapped: a random or after-meal
# sugar must never be judged against the fasting range.
ALIASES = {
    "hemoglobin": "haemoglobin", "hb": "haemoglobin", "hgb": "haemoglobin",
    "haemoglobin_hb": "haemoglobin", "hemoglobin_hb": "haemoglobin",
    "fasting_blood_sugar": "fasting_glucose", "fbs": "fasting_glucose",
    "fasting_plasma_glucose": "fasting_glucose", "fpg": "fasting_glucose",
    "fasting_sugar": "fasting_glucose", "glucose_fasting": "fasting_glucose",
    "thyroid_stimulating_hormone": "tsh",
    "serum_ferritin": "ferritin", "s_ferritin": "ferritin",
    "vit_d": "vitamin_d", "vitamin_d3": "vitamin_d", "25_oh_vitamin_d": "vitamin_d",
    "25_oh_d": "vitamin_d", "25_hydroxy_vitamin_d": "vitamin_d",
}

WEEK_MIN, WEEK_MAX = 1, 42


def normalise(metric: str) -> str:
    """'Fasting Glucose' / 'fasting-glucose' / 'Haemoglobin (Hb)' -> canonical key."""
    key = "_".join(re.sub(r"[^a-z0-9]+", " ", str(metric).lower()).split())
    return ALIASES.get(key, key)


def _unit_key(unit: str) -> str:
    u = str(unit).strip().lower().replace(" ", "")
    return u.replace("\u00b5", "u").replace("\u03bc", "u").replace("mcg", "ug")


def trimester(week: int) -> int:
    if week <= 13:
        return 1
    if week <= 27:
        return 2
    return 3


def _bound(b, tri):
    return b[tri] if isinstance(b, dict) else b


def _in_range(metric, value, tri):
    low = _bound(RANGES[metric]["min"], tri)
    high = _bound(RANGES[metric]["max"], tri)
    exclusive = RANGES[metric].get("max_exclusive", False)
    return low <= value and (value < high if exclusive else value <= high)


def _parse_week(week):
    """Accept 24, 24.0, '24' or '24 weeks'. Return an int in 1..42, or None."""
    if isinstance(week, bool):
        return None
    if isinstance(week, (int, float)):
        w = week
    else:
        m = re.match(r"\s*(\d+(?:\.\d+)?)", str(week))
        if not m:
            return None
        w = float(m.group(1))
    if float(w) != int(w):
        return None
    w = int(w)
    return w if WEEK_MIN <= w <= WEEK_MAX else None


def _parse_value(raw):
    """Return (number, unit or None) or (None, reason) for anything unusable."""
    if isinstance(raw, dict):
        number, unit = _parse_value(raw.get("value"))
        if number is None:
            return None, unit
        return number, (raw.get("unit") or unit)
    if isinstance(raw, bool):
        return None, "not a number"
    if isinstance(raw, (int, float)):
        return raw, None
    m = re.match(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*(.*?)\s*$", str(raw))
    if not m:
        return None, "not a number"
    return float(m.group(1).replace(",", ".")), (m.group(2) or None)


def check_values(values: dict, week) -> str:
    """Compare each blood value with the range for this trimester. Deterministic. No AI inside.

    values: {metric: number | "9.8 g/dL" | {"value": 98, "unit": "g/L"}}
    Anything that cannot be checked safely is returned in "unchecked", never dropped.
    """
    if not isinstance(values, dict):
        values = {}
    wk = _parse_week(week)
    if wk is None:
        return json.dumps({
            "week": week, "trimester": None, "flags": [], "all_values": {},
            "error": f"pregnancy week must be a whole number from {WEEK_MIN} to {WEEK_MAX}",
            "unchecked": [{"metric": m, "value": v,
                           "note": "NOT checked - the pregnancy week is missing or invalid"}
                          for m, v in values.items()],
            "week_sensitive": [], "ranges_version": RANGES_VERSION,
        }, indent=2)

    tri = trimester(wk)
    # Pregnancy dating is often a week out. Near a trimester change, a result can flip
    # between LOW and normal on the week alone, so the doctor must confirm the week.
    neighbours = sorted({trimester(w) for w in (wk - 1, wk + 1) if WEEK_MIN <= w <= WEEK_MAX} - {tri})
    result = {"week": wk, "trimester": tri, "flags": [], "unchecked": [], "week_sensitive": [],
              "all_values": {}, "ranges_version": RANGES_VERSION}

    def unchecked(raw_metric, raw_value, note):
        result["unchecked"].append({"metric": raw_metric, "value": raw_value, "note": note})

    for raw_metric, raw_value in values.items():
        metric = normalise(raw_metric)
        if metric not in RANGES:
            unchecked(raw_metric, raw_value, "NOT a known metric - this value was NOT checked")
            continue
        if metric in result["all_values"]:
            unchecked(raw_metric, raw_value, f"duplicate of {metric} - only the first value was checked")
            continue

        number, unit = _parse_value(raw_value)
        if number is None:
            unchecked(raw_metric, raw_value, f"{unit} - this value was NOT checked")
            continue

        canon = RANGES[metric]["unit"]
        if unit:
            factor = CONVERT[metric].get(_unit_key(unit))
            if factor is None:
                unchecked(raw_metric, raw_value, f"unit '{unit}' not recognised for {metric} - NOT checked")
                continue
            value = round(number * factor, 2)
        else:
            value = number
        lo_ok, hi_ok = PLAUSIBLE[metric]
        if not (lo_ok <= value <= hi_ok):
            unchecked(raw_metric, raw_value,
                      f"{value} is not plausible in {canon} - the unit may differ (for example g/L). NOT checked")
            continue

        low = _bound(RANGES[metric]["min"], tri)
        high = _bound(RANGES[metric]["max"], tri)
        in_range = _in_range(metric, value, tri)

        entry = {"value": value, "unit": canon, "min": low, "max": high, "in_range": in_range}
        if unit and _unit_key(unit) != _unit_key(canon):
            entry["reported_as"] = f"{number:g} {unit}"
        result["all_values"][metric] = entry

        if not in_range:
            flag = {"metric": metric, "value": value, "unit": canon, "min": low, "max": high,
                    "status": "LOW" if value < low else "HIGH"}
            if "reported_as" in entry:
                flag["reported_as"] = entry["reported_as"]
            if metric in URGENT_BELOW and value < URGENT_BELOW[metric]:
                flag["urgent"] = True
            result["flags"].append(flag)

        if any(_in_range(metric, value, other) != in_range for other in neighbours):
            result["week_sensitive"].append({
                "metric": metric,
                "note": "this result would differ if the pregnancy week is one week earlier or later "
                        "- ask the doctor to confirm the week"})

    return json.dumps(result, indent=2)


if __name__ == "__main__":
    # Messy names and units on purpose: normalisation, conversion, and the
    # guarantee that nothing is silently dropped.
    print(check_values({"haemoglobin": 9.8, "fasting glucose": 105,
                        "Ferritin": 18, "platelets": 150}, 24))
    print(check_values({"Hb": {"value": 98, "unit": "g/L"},
                        "FBS": "5.8 mmol/L", "haemoglobin": 98}, 24))
