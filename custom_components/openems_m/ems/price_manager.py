# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Haalt Frank-prijzen op en zet tijden om naar lokale tijd."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class PriceManager:
    def __init__(self, app, sensor, attribute, timezone):
        self.app = app
        self.sensor = sensor
        self.attribute = attribute
        self.timezone = ZoneInfo(timezone)
        self._last_quality_warning = None

    def get_planning_prices(self):
        price_data = self.app.get_state(
            self.sensor,
            attribute=self.attribute,
        )
        if not price_data:
            return []

        now_local = datetime.now(self.timezone)
        start_of_day = now_local.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        normalized = self._normalize(price_data)
        return [
            item for item in normalized
            if self.local_datetime(item["till"]) > start_of_day
        ]

    def get_current_price(self, prices):
        now_local = datetime.now(self.timezone)
        for item in prices:
            start = self.local_datetime(item["from"])
            end = self.local_datetime(item["till"])
            if start <= now_local < end:
                return float(item["price"])
        return None

    def get_actionable_prices(self, prices, now=None):
        """Geeft uitsluitend nog uitvoerbare prijsdelen terug.

        Het reeds verstreken deel van het huidige prijsinterval wordt
        afgeknipt. De oorspronkelijke lijst blijft daardoor bruikbaar voor
        historische grafieken.
        """
        now_local = now or datetime.now(self.timezone)
        if now_local.tzinfo is None:
            now_local = now_local.replace(tzinfo=self.timezone)
        now_local = now_local.astimezone(self.timezone)
        result = []
        for raw in prices:
            start = self.local_datetime(raw["from"])
            end = self.local_datetime(raw["till"])
            if end <= now_local:
                continue
            item = dict(raw)
            if start < now_local:
                item["from"] = now_local.isoformat()
            result.append(item)
        return result

    def get_remaining_prices(self, prices, now=None):
        """Behoudt het volledige begonnen prijsinterval tot zijn eindtijd.

        Een actief uur of kwartier mag bij een herberekening niet korter
        worden dan de ingestelde minimale blokduur. Alleen volledig verlopen
        intervallen worden daarom verwijderd.
        """
        now_local = now or datetime.now(self.timezone)
        if now_local.tzinfo is None:
            now_local = now_local.replace(tzinfo=self.timezone)
        now_local = now_local.astimezone(self.timezone)
        return [
            dict(item) for item in prices
            if self.local_datetime(item["till"]) > now_local
        ]

    def apply_resolution(self, prices, resolution):
        """Zet Frank-prijzen om naar de centrale OpenEMS-resolutie."""
        minutes = 15 if "15" in str(resolution) else 60
        normalized = self._normalize(prices)
        if not normalized:
            return []

        buckets = {}
        for item in normalized:
            start = self.local_datetime(item["from"])
            end = self.local_datetime(item["till"])
            cursor = start
            while cursor < end:
                if minutes == 60:
                    bucket_start = cursor.replace(
                        minute=0, second=0, microsecond=0
                    )
                else:
                    bucket_start = cursor.replace(
                        minute=(cursor.minute // 15) * 15,
                        second=0,
                        microsecond=0,
                    )
                bucket_end = bucket_start + timedelta(minutes=minutes)
                segment_end = min(end, bucket_end)
                seconds = max(0.0, (segment_end - cursor).total_seconds())
                if seconds:
                    key = (bucket_start.timestamp(), bucket_end.timestamp())
                    bucket = buckets.setdefault(key, {
                        "from": bucket_start.isoformat(),
                        "till": bucket_end.isoformat(),
                        "weighted": 0.0,
                        "seconds": 0.0,
                    })
                    bucket["weighted"] += float(item["price"]) * seconds
                    bucket["seconds"] += seconds
                cursor = segment_end

        return [
            {
                "from": item["from"],
                "till": item["till"],
                "price": item["weighted"] / item["seconds"],
            }
            for _, item in sorted(buckets.items())
            if item["seconds"] > 0
        ]

    @staticmethod
    def add_price_adjustment(prices, adjustment):
        """Voeg een vaste vergoeding toe aan iedere dynamische prijs."""
        value = float(adjustment)
        return [
            {**item, "price": float(item["price"]) + value}
            for item in prices
        ]

    def restrict_to_economic_slots(self, prices, economic_slots):
        """Laat alleen prijsdelen door die met een groen laadslot overlappen."""
        allowed = [
            (
                self.local_datetime(slot["from"]),
                self.local_datetime(slot["till"]),
            )
            for slot in economic_slots
        ]
        result = []
        for item in prices:
            start = self.local_datetime(item["from"])
            end = self.local_datetime(item["till"])
            if any(
                allowed_start < end and allowed_end > start
                for allowed_start, allowed_end in allowed
            ):
                result.append(item)
        return result

    def local_datetime(self, value):
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).astimezone(self.timezone)

    def _normalize(self, price_data):
        unique = {}
        invalid = 0
        for raw in price_data:
            try:
                start = self.local_datetime(raw["from"])
                end = self.local_datetime(raw["till"])
                price = float(raw["price"])
                if end <= start:
                    raise ValueError("einde ligt niet na start")
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            unique[(start.timestamp(), end.timestamp())] = {
                **raw,
                "from": start.isoformat(),
                "till": end.isoformat(),
                "price": price,
            }
        result = [
            item for _, item in sorted(unique.items())
        ]
        gaps = 0
        overlaps = 0
        for left, right in zip(result, result[1:]):
            left_end = self.local_datetime(left["till"])
            right_start = self.local_datetime(right["from"])
            if right_start.timestamp() > left_end.timestamp():
                gaps += 1
            elif right_start.timestamp() < left_end.timestamp():
                overlaps += 1
        duplicates = max(0, len(price_data) - invalid - len(unique))
        warning = (
            f"{invalid} ongeldig, {duplicates} dubbel, "
            f"{gaps} ontbrekend en {overlaps} overlappend prijsinterval"
            if invalid or duplicates or gaps or overlaps else None
        )
        if warning != self._last_quality_warning:
            if warning:
                self.app.log(
                    "[Prijzen] Genormaliseerd: " + warning,
                    level="WARNING",
                )
            elif self._last_quality_warning:
                self.app.log("[Prijzen] Prijsreeks is weer volledig")
            self._last_quality_warning = warning
        return result
