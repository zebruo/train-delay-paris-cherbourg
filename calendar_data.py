"""
Jours fériés et vacances scolaires (zone B — Normandie/une bonne partie de la
ligne Paris-Cherbourg), via les APIs publiques data.gouv.fr / education.gouv.fr.

Mis en cache localement (calendar_cache.json) et re-téléchargé seulement si le
cache a plus de 7 jours, pour ne pas interroger ces APIs à chaque relevé de 5
minutes alors que ces données changent rarement.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

CACHE_FILE = "calendar_cache.json"
CACHE_MAX_AGE_S = 7 * 24 * 3600
ZONE = "Zone B"


def _fetch_jours_feries(year):
    url = f"https://calendrier.api.gouv.fr/jours-feries/metropole/{year}.json"
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.loads(response.read())


def _fetch_vacances(zone):
    today = date.today()
    date_range = f"[{today.year}-01-01 TO {today.year + 1}-12-31]"
    query = f'zones="{zone}" AND start_date:{date_range}'
    params = urllib.parse.urlencode({
        "dataset": "fr-en-calendrier-scolaire",
        "q": query,
        "rows": 100,
    })
    url = f"https://data.education.gouv.fr/api/records/1.0/search/?{params}"
    with urllib.request.urlopen(url, timeout=20) as response:
        data = json.loads(response.read())
    periodes = set()
    for record in data.get("records", []):
        fields = record["fields"]
        periodes.add((fields.get("description", ""), fields["start_date"][:10], fields["end_date"][:10]))
    return [{"description": d, "start_date": s, "end_date": e} for d, s, e in sorted(periodes, key=lambda p: p[1])]


def _read_cache():
    """Cache existant, ou None s'il est absent/corrompu (ex: écriture
    interrompue par une coupure du Pi) — dans ce cas on retélécharge comme si
    le cache n'existait pas, plutôt que de planter."""
    if not os.path.isfile(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(cache):
    # Écriture atomique (fichier temporaire + renommage) pour ne jamais
    # laisser un calendar_cache.json à moitié écrit si le process est
    # interrompu en cours de route.
    tmp_path = f"{CACHE_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp_path, CACHE_FILE)


def _load_or_refresh_cache():
    cache = _read_cache()
    if cache is not None:
        age = time.time() - os.path.getmtime(CACHE_FILE)
        if age < CACHE_MAX_AGE_S:
            return cache

    # En repli si une des deux API échoue : garder les anciennes données
    # plutôt que les écraser par du vide, pour qu'une panne réseau passagère
    # ne corrompe pas silencieusement les résultats pendant 7 jours.
    ancien_jours_feries = cache["jours_feries"] if cache else {}
    ancien_vacances = cache["vacances"] if cache else []

    today = date.today()
    jours_feries = dict(ancien_jours_feries)
    for year in (today.year, today.year + 1):
        try:
            jours_feries.update(_fetch_jours_feries(year))
        except Exception:
            pass

    try:
        vacances = _fetch_vacances(ZONE)
    except Exception:
        vacances = ancien_vacances

    nouveau_cache = {"jours_feries": jours_feries, "vacances": vacances}
    _write_cache(nouveau_cache)
    return nouveau_cache


class Calendrier:
    def __init__(self):
        cache = _load_or_refresh_cache()
        self.jours_feries = cache["jours_feries"]
        self.vacances = cache["vacances"]

    def type_jour(self, yyyymmdd):
        """'férié', 'weekend' ou 'ouvré' pour une date au format GTFS YYYYMMDD."""
        d = datetime.strptime(yyyymmdd, "%Y%m%d").date()
        iso = d.isoformat()
        if iso in self.jours_feries:
            return "férié"
        if d.weekday() >= 5:
            return "weekend"
        return "ouvré"

    def en_vacances(self, yyyymmdd):
        """True si la date tombe dans une période de vacances scolaires (zone B)."""
        d = datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat()
        for periode in self.vacances:
            if periode["start_date"] <= d <= periode["end_date"]:
                return True
        return False
