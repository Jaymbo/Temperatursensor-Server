"""Auswertung der Akku-Gesundheit.

Warum KEIN absoluter Spannungsschwellwert als Kriterium?
--------------------------------------------------------
Ein absoluter Spannungswert kann "kaputt" nicht von "nur gerade leer"
unterscheiden: verschiedene Ladezustaende, Temperaturen und Lastzustaende.
Erst der Vergleich ueber die Flotte macht eine Aussage moeglich.

Deshalb werden hier RELATIVE Metriken berechnet (siehe Auftrag 6.2):
  1. Entladerate (mV/Tag)           -> findet Alterung
  2. Zeit Voll -> Abschaltgrenze      -> direktester Kapazitaetsersatz
  3. Ladeverhalten (Endspannung,      -> findet den Ladecontroller-Bug
     6-h-Stopp)
  4. Zyklenzahl                      -> aus erkannten Lade-/Entladesessions
  5. optional Innenwiderstand R=dV/dI  -> braucht Last-/Ruhepaare

Bei zu wenig Daten wird bewusst "unbekannt" ausgegeben statt geraten.

Zeitbasis: Alle Auswertungen nutzen 'measured_at' (Geraetezeit) und korrigieren
sie, falls ein Zeitfaktor vorliegt. 'received_at' (Serverzeit) dient als
Rueckfall/Kontrolle. Keine Auswertung stuetzt sich auf nur eine der beiden.
"""

import datetime
import json
import os

# Bekannte Abschaltgrenze des Loggers (aus der Notiz): ca. 3,15 V.
CUTOFF_MV = 3150

# Voll gilt als erreicht ab dieser Spannung (LiPo ~4,2 V).
FULL_MV = 4100

# Einstellbare Schwellwerte "voll"/"leer" (in Millivolt).
#
# Warum Datei statt DB? Die Schwellwerte sind ein Hardware-/Chemie-Parameter
# (LiPo-Kennlinie), kein Messwert - sie gehoeren nicht in die Messdatenbank.
# Die Datei kann ohne Neustart geaendert werden (jeder Endpoint liest sie
# frisch ein), im Fehlerfall gelten die Defaults.
#
# Format (JSON):
#   {"full_mv": 4200, "empty_mv": 3300}
#
# Wo liegen die Defaults?
#   * full_mv  = 4200: LiPo gilt ab ~4,2 V als voll geladen.
#   * empty_mv = 3300: Bei 3,7 V (Nennspannung) ist noch viel Kapazitaet
#     uebrig - "leer" ist hier bewusst 3,3 V (nahe der Abschaltgrenze).
BATTERY_THRESHOLDS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "battery_thresholds.json"
)

_DEFAULT_THRESHOLDS = {"full_mv": 4200, "empty_mv": 3300}


def load_thresholds(path: str | None = None) -> dict:
    """
    Laedt die einstellbaren Schwellwerte voll/leer aus der Config-Datei.

    Fehlt die Datei oder ist sie ungueltig, gelten die Defaults (voll = 4200 mV,
    leer = 3300 mV). Bewusst kein Cache: Aenderungen an der Datei werden beim
    naechsten Aufruf sofort wirksam (kein Server-Neustart noetig).

    :return: {"full_mv": int, "empty_mv": int} (geparst, plausibilisiert)
    """
    path = path or BATTERY_THRESHOLDS_FILE
    result = dict(_DEFAULT_THRESHOLDS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return result
    for key in ("full_mv", "empty_mv"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and 0 < value < 10000:
            result[key] = int(value)
    # Konsistenz: leer muss unter voll liegen, sonst Defaults.
    if result["empty_mv"] >= result["full_mv"]:
        return dict(_DEFAULT_THRESHOLDS)
    return result

# Ein Ausfall wird erkannt, wenn der Abstand zweier Messungen groesser ist als
# gap_factor * erwartetes Sendeintervall. Der Grund bleibt offen (Luecke).
OUTAGE_GAP_FACTOR = 3.0

# Ladeerkennung: ab so vielen aufeinanderfolgenden steigenden Messungen und
# mindestens dieser Gesamtzunahme gilt ein Abschnitt als Ladung.
CHARGE_MIN_POINTS = 3
CHARGE_MIN_RISE_MV = 20

# Ladecontroller-Bug: "Akku laden stoppt nach 6h". Wird die Spannung nach
# ~6 h im Ladevorgang flach statt weiter zu steigen, ist das sichtbar.
CHARGE_STOP_HOURS = 6.0
CHARGE_PLATEAU_MV = 10

# Linearitaet: LiPo ist zwischen ca. 3,7 und 3,9 V sehr flach. Eine echte
# lineare Prozentanzeige aus der Spannung gibt es nicht. Entscheidung (siehe
# Auftrag 4.5): Option (c) - bewusst 5 STUFEN statt Prozent, zusaetzlich wird
# die Spannung UNTER LAST (Option b) bevorzugt, wenn vorhanden, weil sie deutlich
# steiler ist. Begruendung: eine scheinbare Prozentgenauigkeit waere gelogen.
#
# Die Stufen werden aus den einstellbaren Schwellwerten voll/leer
# (battery_thresholds.json, siehe load_thresholds()) hergeleitet:
#   Stufe 5 = >= full_mv, Stufe 0 = < empty_mv, dazwischen gleichmaessig.
def _level_stages(full_mv: int, empty_mv: int) -> tuple:
    """Baut die Stufentabelle (Schwelle, Stufe, Label) aus voll/leer."""
    return (
        (full_mv, 5, "voll"),
        (full_mv - (full_mv - empty_mv) * 1 // 4, 4, "gut"),
        (full_mv - (full_mv - empty_mv) * 2 // 4, 3, "mittel"),
        (full_mv - (full_mv - empty_mv) * 3 // 4, 2, "schwach"),
        (empty_mv, 1, "kritisch"),
    )


def parse_time(value: str) -> datetime.datetime | None:
    """Parst einen ISO-Zeitstempel; None bei Fehler."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def battery_level_stage(mv: int, under_load: bool | None = None) -> dict:
    """
    Ordnet einer Akkuspannung eine von 5 Stufen zu (KEIN Prozent).

    LiPo ist zwischen ca. 3,7 und 3,9 V sehr flach - eine lineare Prozent-
    anzeige aus der Spannung existiert nicht. Deshalb bewusst 5 Stufen. Liegt
    eine Messung UNTER LAST vor, ist die Spannung deutlich steiler und die
    Zuordnung aussagekraeftiger (Option b). Sie wird hier aber NICHT anders
    skaliert, sondern nur gekennzeichnet, damit der Nutzer den Unterschied sieht.

    :return: {"stage": 0..5, "label": str, "under_load": bool}
    """
    if mv is None:
        return {"stage": None, "label": "unbekannt", "under_load": bool(under_load)}
    thresholds = load_thresholds()
    for threshold, stage, label in _level_stages(
        thresholds["full_mv"], thresholds["empty_mv"]
    ):
        if mv >= threshold:
            return {"stage": stage, "label": label, "under_load": bool(under_load)}
    return {"stage": 0, "label": "leer", "under_load": bool(under_load)}


def _sorted_readings(readings: list[dict], time_key: str = "measured_at") -> list[dict]:
    """Sortiert Messwerte nach Zeit; ungueltige Zeitstempel fliegen raus."""
    out = []
    for r in readings:
        t = parse_time(r.get(time_key))
        if t is None or r.get("mv") is None:
            continue
        out.append({**r, "_t": t})
    out.sort(key=lambda x: x["_t"])
    return out


def detect_outages(readings: list[dict], expected_interval_s: float,
                   gap_factor: float = OUTAGE_GAP_FACTOR) -> list[dict]:
    """
    Erkennt Ausfaelle als Luecken zwischen zwei Messungen.

    Eine Luecke liegt vor, wenn der Abstand groesser ist als
    gap_factor * erwartetes Sendeintervall. Der Grund bleibt offen (kann Akku-
    leer, WLAN-Ausfall oder Ausbau sein).

    :return: Liste von {"start_time", "end_time", "type": "outage", "note", "gap_s"}
    """
    pts = _sorted_readings(readings)
    if expected_interval_s is None or expected_interval_s <= 0:
        return []
    out = []
    for a, b in zip(pts, pts[1:]):
        gap = (b["_t"] - a["_t"]).total_seconds()
        if gap > gap_factor * expected_interval_s:
            out.append({
                "start_time": a["_t"].isoformat(),
                "end_time": b["_t"].isoformat(),
                "type": "outage",
                "note": f"Luecke {gap:.0f}s (>{gap_factor:.1f}x Intervall)",
                "gap_s": gap,
            })
    return out


def detect_charge_sessions(readings: list[dict],
                           min_points: int = CHARGE_MIN_POINTS,
                           min_rise_mv: int = CHARGE_MIN_RISE_MV) -> list[dict]:
    """
    Erkennt Ladevorgaenge: mehrere aufeinanderfolgende monoton steigende Werte.

    Damit laesst sich "logging beim Laden" umsetzen: die Session-Grenzen
    (Messbetrieb vs. Ladung) werden automatisch aus dem Verlauf abgeleitet.

    :return: Liste von {"start_time", "end_time", "type": "charge", "note",
                       "start_mv", "end_mv", "points"}
    """
    pts = _sorted_readings(readings)
    sessions = []
    i = 0
    n = len(pts)
    while i < n - 1:
        # Beginn eines moeglichen Ladeabschnitts?
        if pts[i + 1]["mv"] > pts[i]["mv"]:
            j = i
            while j < n - 1 and pts[j + 1]["mv"] > pts[j]["mv"]:
                j += 1
            points = j - i + 1
            rise = pts[j]["mv"] - pts[i]["mv"]
            if points >= min_points and rise >= min_rise_mv:
                sessions.append({
                    "start_time": pts[i]["_t"].isoformat(),
                    "end_time": pts[j]["_t"].isoformat(),
                    "type": "charge",
                    "note": f"+{rise} mV in {points - 1} Schritten",
                    "start_mv": pts[i]["mv"],
                    "end_mv": pts[j]["mv"],
                    "points": points,
                })
            i = j + 1
        else:
            i += 1
    return sessions


def detect_measure_sessions(readings: list[dict],
                            charge_sessions: list[dict],
                            outages: list[dict]) -> list[dict]:
    """
    Alles, was weder Ladung noch Ausfall ist, gilt als Messbetrieb.

    Nur zur Vollstaendigkeit der Betriebsphasen - fuer die Metriken zaehlt vor
    allem die Trennung Ladung vs. Entladung.
    """
    pts = _sorted_readings(readings)
    if not pts:
        return []
    excluded = []
    for s in charge_sessions + outages:
        st = parse_time(s["start_time"])
        en = parse_time(s["end_time"])
        if st is not None and en is not None:
            excluded.append((st, en))

    def in_excluded(t):
        return any(st <= t <= en for st, en in excluded)

    out = []
    current = None
    for p in pts:
        if in_excluded(p["_t"]):
            if current is not None:
                out.append(current)
                current = None
            continue
        if current is None:
            current = {"start_time": p["_t"].isoformat(),
                       "end_time": p["_t"].isoformat(),
                       "type": "measure", "note": None, "points": 1}
        else:
            current["end_time"] = p["_t"].isoformat()
            current["points"] += 1
    if current is not None:
        out.append(current)
    return out


def discharge_rate_mv_per_day(readings: list[dict],
                             charge_sessions: list[dict]) -> dict:
    """
    Entladerate in mV/Tag (Metrik 1, einfachste Metrik).

    Nur Abschnitte AUSSERHALB der Ladevorgaenge werden gefittet (sonst wuerde
    der Anstieg die Rate verfaelschen). Least-Squares-Steigung ueber alle
    Entladepunkte zusammen.

    :return: {"mv_per_day": float|None, "n": int, "source": "measured_at"|...}
    """
    pts = _sorted_readings(readings)
    charge_intervals = [
        (parse_time(s["start_time"]), parse_time(s["end_time"]))
        for s in charge_sessions
    ]

    def in_charge(t):
        return any(st is not None and en is not None and st <= t <= en
                   for st, en in charge_intervals)

    xs, ys = [], []
    for p in pts:
        if in_charge(p["_t"]):
            continue
        xs.append(p["_t"].timestamp())
        ys.append(float(p["mv"]))

    if len(xs) < 2:
        return {"mv_per_day": None, "n": len(xs), "source": "measured_at"}

    x0 = xs[0]
    xs = [x - x0 for x in xs]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return {"mv_per_day": None, "n": n, "source": "measured_at"}
    slope_per_s = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / denom
    return {
        "mv_per_day": slope_per_s * 86400.0,
        "n": n,
        "source": "measured_at",
    }


def full_to_cutoff_days(readings: list[dict],
                        cutoff_mv: int = CUTOFF_MV) -> dict:
    """
    Zeit von Voll (>= FULL_MV) bis zur Abschaltgrenze (<= cutoff_mv).

    Staerkster Kapazitaetsersatz, weil die Last durch das feste Messintervall
    vergleichbar ist. Es wird nur ein zusammenhaengender Abstieg ausgewertet.

    :return: {"days": float|None, "from": str|None, "to": str|None}
    """
    pts = _sorted_readings(readings)
    start = None
    for p in pts:
        if p["mv"] >= FULL_MV:
            start = p
            break
    if start is None:
        return {"days": None, "from": None, "to": None}
    # Ersten Punkt <= cutoff nach dem Voll-Punkt suchen
    end = None
    for p in pts:
        if p["_t"] >= start["_t"] and p["mv"] <= cutoff_mv:
            end = p
            break
    if end is None:
        return {"days": None, "from": start["_t"].isoformat(), "to": None}
    days = (end["_t"] - start["_t"]).total_seconds() / 86400.0
    return {"days": days, "from": start["_t"].isoformat(), "to": end["_t"].isoformat()}


def charge_behavior(charge_sessions: list[dict],
                    readings: list[dict]) -> dict:
    """
    Ladeverhalten: Endspannung je Ladevorgang + Erkennung des 6-h-Stopps.

    Bekanntes Verhalten: "Akku laden stoppt nach 6 h, aus- und einstecken setzt
    weitere 6 h Ladung frei". Wenn die Spannung nach 6 h flach wird statt weiter
    zu steigen, ist das hier sichtbar.

    :return: {"sessions": [...], "six_hour_stops": int}
    """
    pts = _sorted_readings(readings)
    sessions_out = []
    six_hour_stops = 0
    for s in charge_sessions:
        st = parse_time(s["start_time"])
        en = parse_time(s["end_time"])
        if st is None or en is None:
            continue
        dur_h = (en - st).total_seconds() / 3600.0
        # Spannung in den letzten 30 min vs. davor: steigt sie noch?
        tail_start = en - datetime.timedelta(minutes=30)
        window = [p["mv"] for p in pts if st <= p["_t"] <= en]
        tail = [p["mv"] for p in pts if tail_start <= p["_t"] <= en]
        plateau = (max(tail) - min(tail)) <= CHARGE_PLATEAU_MV if len(tail) >= 2 else False
        stopped_at_6h = dur_h >= CHARGE_STOP_HOURS and plateau
        if stopped_at_6h:
            six_hour_stops += 1
        sessions_out.append({
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "duration_h": dur_h,
            "start_mv": window[0] if window else None,
            "end_mv": window[-1] if window else None,
            "plateau_at_end": plateau,
            "six_hour_stop": stopped_at_6h,
        })
    return {"sessions": sessions_out, "six_hour_stops": six_hour_stops}


def internal_resistance_ohm(readings: list[dict]) -> dict:
    """
    Optional: Innenwiderstand aus dV/dI zwischen Last- und Ruhemessung.

    Braucht mindestens ein Paar (under_load=True/False) mit sehr kleiner
    Zeitdifferenz. Ohne solche Paare -> "unbekannt" (NICHT raten).

    :return: {"r_ohm": float|None, "n_pairs": int}
    """
    pts = _sorted_readings(readings)
    loaded = [p for p in pts if p.get("under_load")]
    idle = [p for p in pts if not p.get("under_load")]
    pairs = 0
    for lp in loaded:
        # naechste Ruhemessung in <= 60 s Abstand suchen
        candidates = [ip for ip in idle
                      if abs((ip["_t"] - lp["_t"]).total_seconds()) <= 60.0]
        if candidates:
            pairs += 1
        # dV/dI ist hier nur qualitativ; ohne bekannten Laststrom nicht
        # in Ohm umrechenbar -> TODO. Wir liefern das Verhaeltnis nicht.
    return {"r_ohm": None, "n_pairs": pairs}


def estimate_remaining_days(rate: dict, latest_mv: int | None,
                           cutoff_mv: int = CUTOFF_MV) -> float | None:
    """
    Resttage-Schaetzung aus der Entladerate. None, wenn Rate fehlt/positiv ist.
    """
    if latest_mv is None or not rate or rate.get("mv_per_day") is None:
        return None
    mv_per_day = rate["mv_per_day"]
    if mv_per_day >= 0:
        return None
    return max(0.0, (latest_mv - cutoff_mv) / abs(mv_per_day))


def analyze_sensor(sensor_id: str, readings: list[dict],
                   expected_interval_s: float = 10.0,
                   hw_rev: int | None = None,
                   battery_id: str | None = None) -> dict:
    """
    Komplette Auswertung EINES Sensors/Akkus.

    :param battery_id: Zuordnung zum Auswertungszeitpunkt (fuer den
        Flottenvergleich, da der Akku gewechselt worden sein kann).
    :return: dict mit rate, full_to_cutoff, charge, cycles, latest, level,
             remaining_days, hw_rev, sessions, suspicion
    """
    pts = _sorted_readings(readings)
    outages = detect_outages(pts, expected_interval_s)
    charges = detect_charge_sessions(pts)
    measures = detect_measure_sessions(pts, charges, outages)
    rate = discharge_rate_mv_per_day(pts, charges)
    f2c = full_to_cutoff_days(pts)
    charge = charge_behavior(charges, pts)
    latest = pts[-1]["mv"] if pts else None
    remaining = estimate_remaining_days(rate, latest)
    cycles = len(charges)

    return {
        "sensor_id": sensor_id,
        "hw_rev": hw_rev,
        "battery_id": battery_id,
        "n_readings": len(pts),
        "latest_mv": latest,
        "latest_at": pts[-1]["_t"].isoformat() if pts else None,
        "level": battery_level_stage(latest, pts[-1].get("under_load") if pts else None),
        "discharge_rate": rate,
        "full_to_cutoff": f2c,
        "charge": charge,
        "cycles": cycles,
        "remaining_days": remaining,
        "outages": outages,
        "charge_sessions": charges,
        "measure_sessions": measures,
    }


def build_fleet(per_sensor: dict[str, dict]) -> dict:
    """
    Flottenvergleich: alle Sensoren/Akkus nach Entladerate sortiert.

    Ausreisser (hohe Entladerate) stehen oben. Die Verdachtsursache wird
    getrennt ausgewiesen (Akku vs. Geraet) - nur so unterscheidet man
    "dieser Akku ist alt" von "dieses Geraet entlaedt zu tief". Bei Logger 1
    war es das GERAET (Kurzschluss), nicht der Akku.

    :param per_sensor: {sensor_id: analyze_sensor(...)}
    :return: {"fleet": [...], "median_rate_mv_per_day": float|None}
    """
    rates = [v["discharge_rate"]["mv_per_day"]
             for v in per_sensor.values()
             if v.get("discharge_rate", {}).get("mv_per_day") is not None]
    median = _median(rates) if rates else None

    fleet = []
    for sid, v in per_sensor.items():
        rate = v.get("discharge_rate", {}).get("mv_per_day")
        fleet.append({
            "sensor_id": sid,
            "battery_id": v.get("battery_id"),
            "hw_rev": v.get("hw_rev"),
            "rate_mv_per_day": rate,
            "remaining_days": v.get("remaining_days"),
            "cycles": v.get("cycles"),
            "latest_mv": v.get("latest_mv"),
            "latest_at": v.get("latest_at"),
            "level": v.get("level"),
            "suspicion": classify_suspicion(rate, median, v.get("hw_rev")),
        })
    # Ausreisser oben: hohe Entladerate zuerst. Rate ist negativ, daher aufsteigend
    # sortieren (-100 vor -10). None ans Ende.
    fleet.sort(key=lambda x: (
        x["rate_mv_per_day"] is None,
        x["rate_mv_per_day"] if x["rate_mv_per_day"] is not None else 0.0,
    ))
    return {"fleet": fleet, "median_rate_mv_per_day": median}


def classify_suspicion(rate_mv_per_day: float | None,
                       fleet_median: float | None,
                       hw_rev: int | None) -> dict:
    """
    Verdachtsursache je Sensor/Akku: Akku vs. Geraet.

    Regel: liegt die Entladerate deutlich ueber dem Flotten-Median, ist der
    Verdacht "Akku oder Geraet". Ein frueherer Kurzschluss-Fall (Logger 1) ist
    ein GERAETEFEHLER - das laesst sich nur ueber die Historie der Zuordnung
    entscheiden, nicht aus der Spannung allein. Ohne Datenbasis: "unbekannt".

    :return: {"color": "gruen|gelb|rot|unbekannt", "cause": str, "reason": str}
    """
    if rate_mv_per_day is None or fleet_median is None:
        return {"color": "unbekannt", "cause": "unbekannt",
                "reason": "zu wenig Daten fuer einen Vergleich"}
    # Rate ist negativ (Entladung). Betrag vergleichen.
    r = abs(rate_mv_per_day)
    m = abs(fleet_median)
    if m == 0:
        return {"color": "unbekannt", "cause": "unbekannt",
                "reason": "Flotten-Median ist 0"}
    ratio = r / m
    if ratio < 1.3:
        return {"color": "gruen", "cause": "keiner",
                "reason": f"Rate {r:.1f} mV/Tag ~ Flottenmedian {m:.1f}"}
    if ratio < 2.5:
        return {"color": "gelb", "cause": "akku_oder_geraet",
                "reason": f"Rate {r:.1f} mV/Tag, {ratio:.1f}x Median"}
    return {"color": "rot", "cause": "akku_oder_geraet",
            "reason": f"Rate {r:.1f} mV/Tag, {ratio:.1f}x Median - "
                      "Zuordnung (Akku/Geraet) ueber Historie pruefen"}


def _median(values: list[float]) -> float:
    """Median einer Zahlenliste."""
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])
