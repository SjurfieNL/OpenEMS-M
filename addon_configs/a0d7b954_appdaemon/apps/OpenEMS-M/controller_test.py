"""Tijdelijke, veilige handmatige functietest voor de Marstek-controller."""


class ControllerTestMode:
    OPTIONS = {
        "Uit": "off",
        "Self Consumption": "self_consumption",
        "Charge 300 W": "charge",
        "Discharge 300 W": "discharge",
        "Stop": "stop",
    }

    def __init__(
        self,
        app,
        controller,
        helper_entity,
        battery_count,
        charge_power,
        discharge_power,
    ):
        self.app = app
        self.controller = controller
        self.helper_entity = helper_entity
        self.battery_count = battery_count
        self.charge_power = float(charge_power)
        self.discharge_power = float(discharge_power)
        self._busy = False

    def start(self):
        self.app.listen_state(self._state_changed, self.helper_entity)
        self.app.log(
            f"Controller Test Mode actief via {self.helper_entity}"
        )

    def apply_current(self):
        """Past de blijvend gekozen teststand na een herstart opnieuw toe."""
        value = self.app.get_state(self.helper_entity)
        self._state_changed(
            self.helper_entity, "state", None, value, {}
        )

    def _state_changed(self, entity, attribute, old, new, kwargs):
        if self._busy or new == old:
            return

        action = self.OPTIONS.get(new)
        if action is None:
            self.app.log(
                f"Onbekende controller-teststand: {new}",
                level="WARNING",
            )
            return

        if action == "off":
            self.app.log("Controller Test Mode: Uit")
            return

        self._busy = True
        try:
            self.app.log(
                "------------------------------------------------------------"
            )
            self.app.log(f"CONTROLLER TEST: {new}")
            self.app.log(
                "------------------------------------------------------------"
            )

            if action == "self_consumption":
                self.controller.set_self_consumption(source="test")
            elif action == "charge":
                self.controller.set_charge(
                    [self.charge_power] * self.battery_count,
                    source="test",
                )
            elif action == "discharge":
                self.controller.set_discharge(
                    [self.discharge_power] * self.battery_count,
                    source="test",
                )
            elif action == "stop":
                self.controller.set_stop(source="test")
        except (TypeError, ValueError, KeyError) as error:
            self.app.log(
                f"Controller-test mislukt: {error}",
                level="ERROR",
            )
            # Lees na enkele seconden de relevante toestanden terug.
            self.app.run_in(self._verify_states, 3, action=action)
        finally:
            self._busy = False

    def _verify_states(self, kwargs):
        action = kwargs.get("action", "onbekend")
        self.app.log(f"CONTROLLER CONTROLE NA TEST: {action}")
        for battery in self.controller.batteries:
            values = {
                "RS485": self.app.get_state(battery["rs485_control"]),
                "modus": self.app.get_state(battery["work_mode"]),
                "force": self.app.get_state(battery["force_mode"]),
                "laden": self.app.get_state(battery["charge_power"]),
                "ontladen": self.app.get_state(battery["discharge_power"]),
            }
            self.app.log(
                f"{battery['name']}: "
                f"RS485={values['RS485']} | "
                f"modus={values['modus']} | "
                f"force={values['force']} | "
                f"laden={values['laden']} W | "
                f"ontladen={values['ontladen']} W"
            )
