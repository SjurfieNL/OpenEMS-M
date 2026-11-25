# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Prijsvoorspelling (WattWanneer) als aanvulling op de Frank-prijzen.

De WattWanneer-integratie levert in het attribuut `forecast` per uur de
marktprijs (excl. btw), tot 7 dagen vooruit. Echte day-ahead uren hebben
`source: entsoe_day_ahead`, geschatte uren `source: model`. OpenEMS gebruikt
alleen uren NA de laatste bekende Frank-prijs: Frank blijft altijd leidend.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .persistence import JsonStore

from .ems_config import (
    PRICE_FORECAST_ATTRIBUTE,
    PRICE_FORECAST_DAYS_HELPER,
    PRICE_FORECAST_DEFAULT_DAYS,
    PRICE_FORECAST_DEFAULT_MARGIN_CT,
    PRICE_FORECAST_MARGIN_HELPER,
    PRICE_FORECAST_DEFAULT_ENTITY,
    PRICE_FORECAST_DEFAULT_FIXED_PER_KWH,
    PRICE_FORECAST_DEFAULT_FLOOR_SOC,
    PRICE_FORECAST_DEFAULT_MIN_DIFF_CT,
    PRICE_FORECAST_ENTITY_HELPER,
    PRICE_FORECAST_FLOOR_HELPER,
    PRICE_FORECAST_HELPER,
    PRICE_FORECAST_INITIALIZED_HELPER,
    PRICE_FORECAST_MAX_AGE_H,
    PRICE_FORECAST_MAX_MEAN_ERROR,
    PRICE_FORECAST_MIN_DIFF_HELPER,
    PRICE_FORECAST_REFRESH_S,
    PRICE_FORECAST_STATUS_SENSOR,
    PRICE_FORECAST_VAT,
)

MARKET_PRICE_RANGE = (-0.5, 1.5)  # EUR/kWh, ruime plausibiliteitsgrens
FIXED_RANGE = (0.0, 0.40)  # vaste opslag incl. btw in EUR/kWh

# Zelflerende correctie op de modelschatting (alleen geschatte uren).
BIAS_LEAD_S = 12 * 3600.0  # alleen schattingen van minstens 12 uur vooruit
BIAS_WINDOW_S = 7 * 86400.0  # leren over de laatste 7 dagen
BIAS_MIN_SAMPLES = 24  # pas meetellen vanaf 24 gemeten uren
BIAS_LIMIT = 0.05  # EUR/kWh, begrenzing op +/- 5 ct
BIAS_PENDING_MAX_AGE_S = 2 * 86400.0  # nooit afgerekende schattingen opruimen
EXPECTED_UNKNOWN = ("", "unknown", "unavailable", "none", "None")


class ForecastError(ValueError):
    """De voorspelling kon niet worden gebruikt."""


def parse_forecast(items):
    """Zet het `forecast`-attribuut om naar uurpunten.

    Geeft [{"start": epoch, "market": prijs, "real": bool}] gesorteerd en
    gemiddeld per heel uur (kwartierdata wordt dus ook ondersteund).
    """
    if not isinstance(items, (list, tuple)) or len(items) < 4:
        raise ForecastError("attribuut `forecast` ontbreekt of is te kort")
    low, high = MARKET_PRICE_RANGE
    buckets = {}
    for item in items:
        try:
            start = datetime.fromisoformat(
                str(item["start"]).replace("Z", "+00:00")
            )
            price = float(item["price"])
        except (KeyError, TypeError, ValueError):
            raise ForecastError("ongeldig item in `forecast`")
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if not math.isfinite(price) or not low <= price <= high:
            raise ForecastError(
                f"prijs {price} buiten het plausibele bereik; eenheid onjuist?"
            )
        hour = int(start.timestamp() // 3600)
        bucket = buckets.setdefault(hour, {"prices": [], "real": True})
        bucket["prices"].append(price)
        if str(item.get("source", "")) != "entsoe_day_ahead":
            bucket["real"] = False
    now = datetime.now(timezone.utc).timestamp()
    result = [
        {
            "start": hour * 3600.0,
            "market": sum(bucket["prices"]) / len(bucket["prices"]),
            "real": bucket["real"],
        }
        for hour, bucket in sorted(buckets.items())
    ]
    if not now - 400 * 86400 < result[0]["start"] < now + 40 * 86400:
        raise ForecastError("tijdstempels buiten het verwachte bereik")
    return result


class PriceForecast:
    def __init__(
        self, app, timezone_name, market_provider=None,
        current_provider=None, clock=None, data_file=None,
    ):
        self.app = app
        self.timezone = ZoneInfo(timezone_name)
        self.market_provider = market_provider
        self.current_provider = current_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.hours = []
        self.fixed = PRICE_FORECAST_DEFAULT_FIXED_PER_KWH
        self.fetched_at = None
        self.error = None
        self.mean_error = None
        self._failed = False
        # Zonder data_file (tests) leert de correctie alleen in het geheugen.
        self.store = (
            JsonStore(
                data_file, app=app, timezone=self.timezone,
                label="Correctie prijsvoorspelling",
            )
            if data_file is not None else None
        )
        self.bias_data = self._load_bias()

    # ---- instellingen ---------------------------------------------------
    def enabled(self):
        return str(self.app.get_state(PRICE_FORECAST_HELPER)).lower() == "on"

    def entity(self):
        value = self.app.get_state(PRICE_FORECAST_ENTITY_HELPER)
        if value in (None, *EXPECTED_UNKNOWN):
            return PRICE_FORECAST_DEFAULT_ENTITY
        return str(value).strip()

    def _number(self, entity, default):
        try:
            return float(self.app.get_state(entity))
        except (TypeError, ValueError):
            return float(default)

    def params(self):
        initialized = (
            str(self.app.get_state(PRICE_FORECAST_INITIALIZED_HELPER)) == "on"
        )
        if not initialized:
            return {
                "min_diff": PRICE_FORECAST_DEFAULT_MIN_DIFF_CT / 100.0,
                "floor_soc": PRICE_FORECAST_DEFAULT_FLOOR_SOC,
                "days": PRICE_FORECAST_DEFAULT_DAYS,
                "margin": PRICE_FORECAST_DEFAULT_MARGIN_CT / 100.0,
            }
        return {
            "min_diff": self._number(
                PRICE_FORECAST_MIN_DIFF_HELPER,
                PRICE_FORECAST_DEFAULT_MIN_DIFF_CT,
            ) / 100.0,
            "floor_soc": self._number(
                PRICE_FORECAST_FLOOR_HELPER, PRICE_FORECAST_DEFAULT_FLOOR_SOC
            ),
            "days": int(self._number(
                PRICE_FORECAST_DAYS_HELPER, PRICE_FORECAST_DEFAULT_DAYS
            )),
            "margin": max(0.0, self._number(
                PRICE_FORECAST_MARGIN_HELPER,
                PRICE_FORECAST_DEFAULT_MARGIN_CT,
            )) / 100.0,
        }

    # ---- levenscyclus ----------------------------------------------------
    def start(self):
        self.app.run_every(self.refresh, "now+20", PRICE_FORECAST_REFRESH_S)
        self.publish()

    def age_hours(self):
        if self.fetched_at is None:
            return None
        return (self.clock() - self.fetched_at).total_seconds() / 3600.0

    def usable(self):
        age = self.age_hours()
        return (
            self.enabled() and bool(self.hours) and age is not None
            and age <= PRICE_FORECAST_MAX_AGE_H
        )

    def refresh(self, kwargs=None):
        """Leest de sensor; bij een fout blijft de laatste goede data staan."""
        if not self.enabled():
            self.publish()
            return
        try:
            self._read()
            if self._failed:
                self.app.log("[Prijsvoorspelling] Weer beschikbaar")
            self._failed = False
            self.error = None
        except Exception as error:  # nooit de app laten crashen
            self.error = str(error)
            if not self._failed:
                self.app.log(
                    f"[Prijsvoorspelling] {self.entity()} niet bruikbaar: "
                    f"{error}; de laatste voorspelling blijft (tot 24 uur) "
                    "bruikbaar",
                    level="WARNING",
                )
            self._failed = True
        self.publish()

    def _read(self):
        entity = self.entity()
        items = self.app.get_state(entity, attribute=PRICE_FORECAST_ATTRIBUTE)
        hours = parse_forecast(items)
        frank = self._frank_market_map()
        mean_error = self._compare_with_frank(hours, frank)
        if mean_error is not None and mean_error > PRICE_FORECAST_MAX_MEAN_ERROR:
            raise ForecastError(
                f"echte uren wijken gemiddeld {mean_error * 100:.1f} ct af van "
                "de Frank-marktprijzen; genegeerd (eenheid of tijdzone?)"
            )
        first_time = self.fetched_at is None
        try:
            self._learn_bias(hours, frank)
        except Exception as error:  # leren mag de voorspelling nooit blokkeren
            self.app.log(
                f"[Prijsvoorspelling] Correctie niet bijgewerkt: {error}",
                level="WARNING",
            )
        self.hours = hours
        self.fixed = self._current_fixed()
        self.mean_error = mean_error
        self.fetched_at = self.clock()
        if first_time:
            first = datetime.fromtimestamp(hours[0]["start"], self.timezone)
            last = datetime.fromtimestamp(hours[-1]["start"], self.timezone)
            self.app.log(
                f"[Prijsvoorspelling] {len(hours)} uur uit {entity} "
                f"({first:%d-%m %H:%M} t/m {last:%d-%m %H:%M})"
                + (
                    f"; verschil met Frank-markt {mean_error * 100:.2f} ct"
                    if mean_error is not None else ""
                )
            )

    # ---- planning --------------------------------------------------------
    def all_in(self, market):
        return market * (1.0 + PRICE_FORECAST_VAT) + self.fixed

    def market(self, item):
        """Marktprijs van een uur; geschatte uren krijgen de correctie."""
        if item.get("real"):
            return item["market"]
        return item["market"] + self.bias()

    def prices(self, after=None):
        """Voorspelde uurprijzen (all-in) vanaf `after`, in Frank-formaat."""
        if not self.usable():
            return []
        now = self.clock().timestamp()
        floor = after.timestamp() if after is not None else 0.0
        result = []
        for item in self.hours:
            start = item["start"]
            end = start + 3600.0
            if start < floor - 1e-6 or end <= now:
                continue
            result.append({
                "from": datetime.fromtimestamp(
                    start, self.timezone).isoformat(),
                "till": datetime.fromtimestamp(
                    end, self.timezone).isoformat(),
                "price": round(self.all_in(self.market(item)), 5),
                "voorspeld": True,
            })
        return result

    def planner_kwargs(self, known_prices, price_manager, resolution):
        """Extra planner-argumenten, of {} als de voorspelling niet telt."""
        if not self.enabled():
            return {}
        if not self.hours and self.fetched_at is None:
            self.refresh()
        if not self.usable() or not known_prices:
            return {}
        last_end = max(
            price_manager.local_datetime(item["till"])
            for item in known_prices
        )
        forecast = self.prices(after=last_end)
        if not forecast:
            return {}
        real_days = self._real_days(last_end)
        forecast = price_manager.apply_resolution(forecast, resolution)
        params = self.params()
        return {
            "forecast_prices": forecast,
            "forecast_min_difference": params["min_diff"],
            "forecast_floor_soc": params["floor_soc"],
            "forecast_days": params["days"],
            "forecast_extra_margin": params["margin"],
            "forecast_real_days": real_days,
        }

    def _real_days(self, after):
        """Dagen waarvan alle uren vaste day-ahead prijzen zijn."""
        flags = {}
        for item in self.hours:
            moment = datetime.fromtimestamp(item["start"], self.timezone)
            if moment < after:
                continue
            day = moment.date()
            flags[day] = flags.get(day, True) and bool(item["real"])
        return {day for day, real in flags.items() if real}

    # ---- hulpfuncties ----------------------------------------------------
    def _current_fixed(self):
        """Vaste opslag (energiebelasting + inkoopvergoeding incl. btw)."""
        if self.current_provider is None:
            return self.fixed
        try:
            all_in, market = self.current_provider()
        except Exception:
            return self.fixed
        if all_in is None or market is None:
            return self.fixed
        fixed = float(all_in) - float(market) * (1.0 + PRICE_FORECAST_VAT)
        if not FIXED_RANGE[0] <= fixed <= FIXED_RANGE[1]:
            return self.fixed
        return fixed

    def _frank_market_map(self):
        """Bekende Frank-marktprijzen als {uurstart: [prijzen]}."""
        if self.market_provider is None:
            return {}
        try:
            known = self.market_provider() or []
        except Exception:
            return {}
        frank = {}
        for item in known:
            try:
                start = datetime.fromisoformat(
                    item["from"].replace("Z", "+00:00")
                ).timestamp()
                end = datetime.fromisoformat(
                    item["till"].replace("Z", "+00:00")
                ).timestamp()
                price = float(item["price"])
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            moment = math.floor(start / 3600.0) * 3600.0
            while moment < end:
                frank.setdefault(moment, []).append(price)
                moment += 3600.0
        return frank

    def _compare_with_frank(self, hours, frank=None):
        """Gemiddeld verschil tussen echte uren en de Frank-marktprijzen."""
        if frank is None:
            frank = self._frank_market_map()
        if not frank:
            return None
        differences = [
            abs(item["market"] - sum(frank[item["start"]])
                / len(frank[item["start"]]))
            for item in hours if item["real"] and item["start"] in frank
        ]
        if len(differences) < 3:
            return None
        return sum(differences) / len(differences)

    # ---- zelflerende correctie -------------------------------------------
    @staticmethod
    def _empty_bias():
        return {"versie": 1, "schattingen": {}, "fouten": []}

    def _load_bias(self):
        data = (
            self.store.load(self._empty_bias)
            if self.store is not None else self._empty_bias()
        )
        if not isinstance(data.get("schattingen"), dict):
            data["schattingen"] = {}
        if not isinstance(data.get("fouten"), list):
            data["fouten"] = []
        return data

    def _learn_bias(self, hours, frank):
        """Onthoud modelschattingen en reken ze af zodra de echte prijs er is.

        Fout = echte marktprijs - geschatte marktprijs. Een positief gemiddelde
        betekent dat het model te laag schat.
        """
        now = self.clock().timestamp()
        pending = self.bias_data["schattingen"]
        errors = self.bias_data["fouten"]
        changed = False
        actual = {
            start: sum(prices) / len(prices)
            for start, prices in frank.items() if prices
        }
        for item in hours:
            if item["real"]:
                actual.setdefault(item["start"], item["market"])
        for item in hours:
            if item["real"] or item["start"] - now < BIAS_LEAD_S:
                continue
            key = str(int(item["start"]))
            value = round(item["market"], 5)
            if pending.get(key) != value:
                pending[key] = value
                changed = True
        for key in list(pending):
            try:
                start = float(key)
                predicted = float(pending[key])
            except (TypeError, ValueError):
                del pending[key]
                changed = True
                continue
            if start in actual:
                error = actual[start] - predicted
                if math.isfinite(error):
                    errors.append({"start": int(start), "fout": round(error, 5)})
                del pending[key]
                changed = True
            elif start < now - BIAS_PENDING_MAX_AGE_S:
                del pending[key]
                changed = True
        kept = [
            entry for entry in errors
            if isinstance(entry, dict)
            and isinstance(entry.get("fout"), (int, float))
            and float(entry.get("start", 0)) >= now - BIAS_WINDOW_S
        ]
        if len(kept) != len(errors):
            self.bias_data["fouten"] = kept
            changed = True
        if changed and self.store is not None:
            self.store.save(self.bias_data)

    def bias_samples(self):
        return len(self.bias_data["fouten"])

    def bias(self):
        """Correctie op de geschatte marktprijs (EUR/kWh), 0 tot er genoeg is."""
        errors = [float(entry["fout"]) for entry in self.bias_data["fouten"]]
        if len(errors) < BIAS_MIN_SAMPLES:
            return 0.0
        mean = sum(errors) / len(errors)
        return max(-BIAS_LIMIT, min(BIAS_LIMIT, mean))

    # ---- status ----------------------------------------------------------
    def publish(self):
        age = self.age_hours()
        if not self.enabled():
            state = "uit"
        elif self.error and not self.usable():
            state = "fout"
        elif not self.hours:
            state = "wachten"
        elif not self.usable():
            state = "verouderd"
        else:
            state = "actief"
        uren = []
        if self.hours:
            now = self.clock().timestamp()
            horizon = now + 72 * 3600.0
            uren = [
                {
                    "van": datetime.fromtimestamp(
                        item["start"], self.timezone).isoformat(),
                    "prijs": round(self.all_in(self.market(item)), 4),
                    "markt": round(self.market(item), 5),
                    "echt": item["real"],
                }
                for item in self.hours
                if item["start"] + 3600.0 > now and item["start"] < horizon
            ]
        self.app.set_state(
            PRICE_FORECAST_STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "OpenEMS prijsvoorspelling",
                "icon": "mdi:chart-line-variant",
                "bron": self.entity(),
                "bijgewerkt": (
                    self.fetched_at.astimezone(self.timezone).isoformat()
                    if self.fetched_at else None
                ),
                "leeftijd_uur": round(age, 1) if age is not None else None,
                "fout": self.error,
                "gemiddeld_verschil_ct": (
                    round(self.mean_error * 100, 2)
                    if self.mean_error is not None else None
                ),
                "vaste_opslag": round(self.fixed, 5),
                "correctie_ct": round(self.bias() * 100, 2),
                "correctie_metingen": self.bias_samples(),
                "correctie_actief": self.bias_samples() >= BIAS_MIN_SAMPLES,
                "uren": uren,
            },
        )
