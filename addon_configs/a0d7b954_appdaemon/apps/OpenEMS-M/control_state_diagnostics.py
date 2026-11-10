"""Diagnose van onverwachte wijzigingen in de Marstek-besturing."""


class ControlStateDiagnostics:
    CONTROL_FIELDS = ("rs485_control", "work_mode", "force_mode")

    def __init__(self, app, batteries):
        self.app = app
        self.batteries = batteries
        self._entities = {
            battery[field]: (index, field)
            for index, battery in enumerate(batteries)
            for field in self.CONTROL_FIELDS
            if battery.get(field)
        }

    def start(self):
        listen_event = getattr(self.app, "listen_event", None)
        if callable(listen_event):
            # Filter al vóór de callbackwachtrij. Een algemene state_changed-
            # listener ontvangt iedere HA-mutatie en kan bij een trage
            # Modbus-servicecall de AppDaemon-thread onnodig laten vollopen.
            for entity_id in self._entities:
                listen_event(
                    self._state_changed,
                    "state_changed",
                    entity_id=entity_id,
                )

    def _state_changed(self, event_name, data, kwargs):
        entity_id = data.get("entity_id")
        selected = self._entities.get(entity_id)
        if selected is None:
            return

        old_state = data.get("old_state") or {}
        new_state = data.get("new_state") or {}
        old = old_state.get("state")
        new = new_state.get("state")
        if old == new:
            return

        index, field = selected
        battery = self.batteries[index]
        context = new_state.get("context") or data.get("context") or {}
        snapshot = self._snapshot(battery)
        self.app.log(
            "[Besturingsdiagnose] {} {}: {} -> {} | "
            "context={} user={} parent={} | gewijzigd={} bijgewerkt={} | "
            "modus={} force={} rs485={} laden={} W ontladen={} W "
            "werkelijk={} W".format(
                battery.get("name", f"Accu {index + 1}"),
                field,
                old,
                new,
                context.get("id") or "-",
                context.get("user_id") or "-",
                context.get("parent_id") or "-",
                new_state.get("last_changed") or "-",
                new_state.get("last_updated") or "-",
                snapshot["work_mode"],
                snapshot["force_mode"],
                snapshot["rs485_control"],
                snapshot["charge_power"],
                snapshot["discharge_power"],
                snapshot["actual_power"],
            ),
            level="WARNING" if field == "rs485_control" and new == "off"
            else "INFO",
        )

    def _snapshot(self, battery):
        return {
            field: self._state(battery.get(field))
            for field in (
                "work_mode",
                "force_mode",
                "rs485_control",
                "charge_power",
                "discharge_power",
                "actual_power",
            )
        }

    def _state(self, entity_id):
        if not entity_id:
            return "-"
        value = self.app.get_state(entity_id)
        return "-" if value is None else value
