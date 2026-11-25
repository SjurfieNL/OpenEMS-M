# GEGENEREERD door tools/sync_ems.py uit de werkmap-tests. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
import asyncio
import importlib.util
from pathlib import Path


BRIDGE_PATH = (
    Path(__file__).resolve().parents[2]
    / "custom_components" / "openems_m" / "driver" / "atomic_bridge.py"
)
SPEC = importlib.util.spec_from_file_location(
    "openems_marstek_atomic_bridge", BRIDGE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
AtomicBridgeError = MODULE.AtomicBridgeError
MarstekAtomicBridge = MODULE.MarstekAtomicBridge


class DummyEntry:
    def __init__(self, title="", data=None, options=None):
        self.title = title
        self.data = dict(data or {})
        self.options = dict(options or {})


class DummyConfigEntries:
    def __init__(self):
        self.entries = {"entry": DummyEntry()}
        self.updates = []

    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)

    def async_update_entry(self, entry, data):
        entry.data = data
        self.updates.append(dict(data))


class DummyBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event, data):
        self.events.append((event, data))


class DummyCoordinator:
    def __init__(self, name, feedback=None):
        self.name = name
        self.is_available = True
        self._is_shutting_down = False
        self.rs485_user_disabled = False
        self.data = {"rs485_control_mode": 0x55AA}
        self.min_soc = 12
        self.feedback = list(feedback or [])
        self.writes = []
        self.register_writes = []

    async def write_power_atomic(
        self, discharge_power, charge_power, force_mode, read_back=True
    ):
        self.writes.append(
            (discharge_power, charge_power, force_mode, read_back)
        )
        if self.feedback:
            return self.feedback.pop(0)
        return {
            "force_mode": force_mode,
            "set_charge_power": charge_power,
            "set_discharge_power": discharge_power,
            "battery_power": 0,
        }

    def set_rs485_user_disabled(self, disabled):
        self.rs485_user_disabled = disabled

    def get_register(self, key):
        return {
            "rs485_control": 42000,
            "force_mode": 42010,
            "set_charge_power": 42020,
            "set_discharge_power": 42021,
        }.get(key)

    async def write_register(self, register, value, do_refresh=True):
        self.register_writes.append((register, value, do_refresh))
        return True


class DummyController:
    def __init__(self):
        self._control_lock = asyncio.Lock()
        self.manual_mode_enabled = False
        self.previous_power = 99
        self.previous_error = 99
        self._active_charge_batteries = ["old"]
        self._active_discharge_batteries = ["old"]


class DummyHass:
    def __init__(self, coordinators, controller):
        self.data = {
            "marstek_venus_energy_manager": {
                "entry": {
                    "coordinators": coordinators,
                    "controller": controller,
                }
            }
        }
        self.config_entries = DummyConfigEntries()
        self.bus = DummyBus()

    def async_create_task(self, coroutine):
        return asyncio.create_task(coroutine)


def test_bridge_prefers_new_modbus_domain_and_sorts_explicit_numbers():
    accu3 = DummyCoordinator("Accu 3")
    accu3.openems_battery_number = 3
    accu1 = DummyCoordinator("Accu 1")
    accu1.openems_battery_number = 1
    accu2 = DummyCoordinator("Accu 2")
    accu2.openems_battery_number = 2
    hass = DummyHass([DummyCoordinator("Oude Accu 1")], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = {
        "entry-3": accu3,
        "entry-1": accu1,
        "entry-2": accu2,
    }
    bridge = MarstekAtomicBridge(hass)

    result = asyncio.run(bridge.set_power(3, 0, 700))

    assert result["battery"] == "Accu 3"
    assert accu3.writes == [(700, 0, 2, True)]
    assert accu1.writes == []
    assert accu2.writes == []


def test_new_modbus_domain_migrates_existing_named_entries():
    accu3 = DummyCoordinator("Berging Accu3 (192.0.2.3)")
    accu1 = DummyCoordinator("Berging Accu1 (192.0.2.1)")
    accu2 = DummyCoordinator("Berging Accu2 (192.0.2.2)")
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = {
        "entry-3": accu3,
        "entry-1": accu1,
        "entry-2": accu2,
    }
    hass.config_entries.entries.update({
        "entry-3": DummyEntry("Berging Accu3"),
        "entry-1": DummyEntry("Berging Accu1"),
        "entry-2": DummyEntry("Berging Accu2"),
    })
    bridge = MarstekAtomicBridge(hass)

    result = asyncio.run(bridge.set_power(3, 1249, 0))

    assert result["battery"] == "Berging Accu3 (192.0.2.3)"
    assert accu3.openems_battery_number == 3
    assert accu3.writes == [(0, 1249, 1, True)]
    assert accu1.writes == []
    assert accu2.writes == []


def test_new_modbus_domain_uses_entity_registry_for_opaque_entry_names():
    accu3 = DummyCoordinator("Venus (192.0.2.3)")
    accu1 = DummyCoordinator("Venus (192.0.2.1)")
    accu2 = DummyCoordinator("Venus (192.0.2.2)")
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = {
        "opaque-c": accu3,
        "opaque-a": accu1,
        "opaque-b": accu2,
    }
    hass.config_entries.entries.update({
        "opaque-c": DummyEntry("Venus"),
        "opaque-a": DummyEntry("Venus"),
        "opaque-b": DummyEntry("Venus"),
    })
    entity_ids = {
        "opaque-a": ["number.berging_accu1_forceer_laadvermogen"],
        "opaque-b": ["number.berging_accu2_forceer_laadvermogen"],
        "opaque-c": ["number.berging_accu3_forceer_laadvermogen"],
    }
    bridge = MarstekAtomicBridge(hass)
    bridge._config_entry_entity_ids = lambda entry_id: entity_ids[entry_id]

    result = asyncio.run(bridge.set_power(3, 1249, 0))

    assert result["battery"] == "Venus (192.0.2.3)"
    assert accu3.openems_battery_number == 3
    assert accu3.writes == [(0, 1249, 1, True)]
    assert accu1.writes == []
    assert accu2.writes == []


def test_batch_uses_direct_entity_target_without_name_discovery():
    coordinator = DummyCoordinator("Volledig ondoorzichtige naam")
    hass = DummyHass([], DummyController())
    bridge = MarstekAtomicBridge(hass)
    bridge._target_from_entity_id = lambda entity_id, battery: (
        "opaque-entry", None, coordinator
    )

    result = asyncio.run(bridge.set_powers([{
        "battery": 3,
        "charge_power": 1249,
        "discharge_power": 0,
        "entity_id": "select.berging_accu3_geforceerde_modus",
    }]))[0]

    assert result["battery"] == "Volledig ondoorzichtige naam"
    assert coordinator.writes == [(0, 1249, 1, True)]


def test_new_modbus_domain_requires_unique_explicit_battery_numbers():
    accu1 = DummyCoordinator("Accu 1")
    accu1.openems_battery_number = 1
    duplicate = DummyCoordinator("Nog een Accu 1")
    duplicate.openems_battery_number = 1
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = {
        "first": accu1,
        "duplicate": duplicate,
    }

    try:
        MarstekAtomicBridge(hass).targets()
    except AtomicBridgeError as error:
        assert "dubbel" in str(error)
    else:
        raise AssertionError("Dubbel accunummer werd toegestaan")


def _numbered_hass(*numbers):
    coordinators = {}
    for number in numbers:
        coordinator = DummyCoordinator(f"Accu {number}")
        coordinator.openems_battery_number = number
        coordinators[f"entry-{number}"] = coordinator
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = coordinators
    return hass, coordinators


def test_single_battery_installation_is_accepted():
    hass, coordinators = _numbered_hass(1)

    targets = MarstekAtomicBridge(hass).targets()

    assert [item[0] for item in targets] == ["entry-1"]
    result = asyncio.run(MarstekAtomicBridge(hass).set_power(1, 0, 600))
    assert result["battery"] == "Accu 1"
    assert coordinators["entry-1"].writes == [(600, 0, 2, True)]


def test_two_battery_installation_is_accepted():
    hass, _coordinators = _numbered_hass(2, 1)

    targets = MarstekAtomicBridge(hass).targets()

    assert [item[0] for item in targets] == ["entry-1", "entry-2"]


def test_no_battery_gives_clear_error():
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = {"leeg": None}

    try:
        MarstekAtomicBridge(hass).targets()
    except AtomicBridgeError as error:
        assert "nog geen" in str(error)
    else:
        raise AssertionError("Installatie zonder accu werd toegestaan")


def test_gap_in_battery_numbers_is_refused():
    hass, _coordinators = _numbered_hass(1, 3)

    try:
        MarstekAtomicBridge(hass).targets()
    except AtomicBridgeError as error:
        assert "ontbrekend: 2" in str(error)
    else:
        raise AssertionError("Gat in accunummers werd toegestaan")


def test_fourth_battery_is_accepted_and_sorted_after_the_first_three():
    coordinators = {}
    for number in (4, 2, 1, 3):
        coordinator = DummyCoordinator(f"Accu {number}")
        coordinator.openems_battery_number = number
        coordinators[f"entry-{number}"] = coordinator
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = coordinators

    targets = MarstekAtomicBridge(hass).targets()

    assert [item[0] for item in targets] == [
        "entry-1", "entry-2", "entry-3", "entry-4"
    ]
    result = asyncio.run(MarstekAtomicBridge(hass).set_power(4, 0, 600))
    assert result["battery"] == "Accu 4"
    assert coordinators["entry-4"].writes == [(600, 0, 2, True)]
    assert coordinators["entry-1"].writes == []


def test_fourth_battery_without_third_is_refused():
    coordinators = {}
    for number in (1, 2, 4):
        coordinator = DummyCoordinator(f"Accu {number}")
        coordinator.openems_battery_number = number
        coordinators[f"entry-{number}"] = coordinator
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = coordinators

    try:
        MarstekAtomicBridge(hass).targets()
    except AtomicBridgeError as error:
        assert "ontbrekend: 3" in str(error)
    else:
        raise AssertionError("Accu 4 zonder accu 3 werd toegestaan")


def test_battery_number_five_is_rejected():
    coordinators = {}
    for number in (1, 2, 3, 5):
        coordinator = DummyCoordinator(f"Accu {number}")
        coordinator.openems_battery_number = number
        coordinators[f"entry-{number}"] = coordinator
    hass = DummyHass([], DummyController())
    hass.data[MODULE.MARSTEK_MODBUS_DOMAIN] = coordinators

    try:
        MarstekAtomicBridge(hass).targets()
    except AtomicBridgeError as error:
        assert "Ongeldig" in str(error)
    else:
        raise AssertionError("Accunummer 5 werd toegestaan")


def test_bridge_uses_natural_battery_order_and_claims_control():
    accu2 = DummyCoordinator("Accu 2")
    accu1 = DummyCoordinator("Accu 1")
    controller = DummyController()
    hass = DummyHass([accu2, accu1], controller)
    bridge = MarstekAtomicBridge(hass)

    result = asyncio.run(bridge.set_power(1, 1200, 0))

    assert result["ok"] is True
    assert result["battery"] == "Accu 1"
    assert accu1.writes == [(0, 1200, 1, True)]
    assert accu2.writes == []
    assert controller.manual_mode_enabled is True
    assert hass.config_entries.entries["entry"].data[
        "manual_mode_enabled"
    ] is True
    assert controller.previous_power == 1200


def test_bridge_retries_one_ack_mismatch_with_complete_atomic_write():
    coordinator = DummyCoordinator(
        "Accu 1",
        feedback=[
            {
                "force_mode": 0,
                "set_charge_power": 0,
                "set_discharge_power": 0,
                "battery_power": 0,
            },
            {
                "force_mode": 2,
                "set_charge_power": 0,
                "set_discharge_power": 900,
                "battery_power": 850,
            },
        ],
    )
    bridge = MarstekAtomicBridge(
        DummyHass([coordinator], DummyController())
    )

    result = asyncio.run(bridge.set_power(1, 0, 900))

    assert result["ok"] is True
    assert coordinator.writes == [
        (900, 0, 2, True),
        (900, 0, 2, True),
    ]


def test_bridge_times_out_once_then_uses_per_battery_backoff():
    class SlowCoordinator(DummyCoordinator):
        def __init__(self, name):
            super().__init__(name)
            self.block = True

        async def write_power_atomic(
            self, discharge_power, charge_power, force_mode, read_back=True
        ):
            self.writes.append(
                (discharge_power, charge_power, force_mode, read_back)
            )
            if self.block:
                await asyncio.Event().wait()
            return {
                "force_mode": force_mode,
                "set_charge_power": charge_power,
                "set_discharge_power": discharge_power,
                "battery_power": 0,
            }

    now = [1000.0]
    coordinator = SlowCoordinator("Accu 3")
    hass = DummyHass([coordinator], DummyController())
    bridge = MarstekAtomicBridge(
        hass, monotonic=lambda: now[0], write_timeout=0.01
    )

    try:
        asyncio.run(bridge.set_power(1, 0, 900, source="test"))
    except AtomicBridgeError as error:
        assert "back-off" in str(error)
    else:
        raise AssertionError("Vastgelopen Modbus-write kreeg geen timeout")

    assert len(coordinator.writes) == 2  # time-out + 1 nieuwe poging
    try:
        asyncio.run(bridge.set_power(1, 0, 900, source="test"))
    except AtomicBridgeError as error:
        assert "communicatie-back-off" in str(error)
    else:
        raise AssertionError("Communicatie-back-off werd niet toegepast")
    assert len(coordinator.writes) == 2  # time-out + 1 nieuwe poging

    now[0] += MODULE.COMMUNICATION_BACKOFF_BASE_SECONDS + 1
    coordinator.block = False
    result = asyncio.run(bridge.set_power(1, 0, 900, source="test"))
    assert result["ok"] is True
    assert len(coordinator.writes) == 3
    assert bridge._communication_failures == {}
    statuses = [
        payload["status"] for event, payload in hass.bus.events
        if event == "openems_modbus_write_audit"
    ]
    assert "afgebroken_communicatietimeout" in statuses
    assert "overgeslagen_communicatie_backoff" in statuses


def test_slow_battery_does_not_prevent_other_battery_write():
    class SlowCoordinator(DummyCoordinator):
        async def write_power_atomic(
            self, discharge_power, charge_power, force_mode, read_back=True
        ):
            self.writes.append(
                (discharge_power, charge_power, force_mode, read_back)
            )
            await asyncio.Event().wait()

    healthy = DummyCoordinator("Accu 1")
    slow = SlowCoordinator("Accu 3")
    bridge = MarstekAtomicBridge(
        DummyHass([healthy, slow], DummyController()),
        write_timeout=0.01,
    )
    commands = [
        {"battery": 1, "charge_power": 500, "discharge_power": 0},
        {"battery": 2, "charge_power": 500, "discharge_power": 0},
    ]

    try:
        asyncio.run(bridge.set_powers(commands))
    except AtomicBridgeError:
        pass
    else:
        raise AssertionError("Trage accu had de batch moeten afkeuren")

    assert healthy.writes == [(0, 500, 1, True)]
    assert slow.writes == [(0, 500, 1, True)] * 2  # incl. nieuwe poging


def test_bridge_rejects_simultaneous_charge_and_discharge():
    bridge = MarstekAtomicBridge(
        DummyHass([DummyCoordinator("Accu 1")], DummyController())
    )

    try:
        asyncio.run(bridge.set_power(1, 500, 400))
    except AtomicBridgeError as error:
        assert "niet tegelijk" in str(error)
    else:
        raise AssertionError("Gelijktijdig laden en ontladen werd toegestaan")


def test_bridge_reenables_rs485_before_atomic_power_write():
    coordinator = DummyCoordinator("Accu 1")
    coordinator.rs485_user_disabled = True
    coordinator.data["rs485_control_mode"] = 0x55BB
    bridge = MarstekAtomicBridge(
        DummyHass([coordinator], DummyController())
    )

    result = asyncio.run(bridge.set_power(1, 500, 0))

    assert result["ok"] is True
    assert coordinator.rs485_user_disabled is False
    assert coordinator.register_writes == [(42000, 0x55AA, False)]
    assert coordinator.writes == [(0, 500, 1, True)]


def test_all_three_batteries_receive_equal_register_sequence_and_audit():
    coordinators = [
        DummyCoordinator(f"Accu {number}") for number in (1, 2, 3)
    ]
    hass = DummyHass(coordinators, DummyController())
    bridge = MarstekAtomicBridge(hass)
    commands = [
        {
            "battery": number,
            "charge_power": 750,
            "discharge_power": 0,
            "source": "forensische_test",
            "reason": "gelijke opdracht",
        }
        for number in (1, 2, 3)
    ]

    asyncio.run(bridge.set_powers(commands, request_id="parity-1"))

    assert all(
        coordinator.writes == [(0, 750, 1, True)]
        for coordinator in coordinators
    )
    audits = [
        payload for event, payload in hass.bus.events
        if event == "openems_modbus_write_audit"
    ]
    by_battery = {
        name: sorted(
            (
                item["register"], item["nieuwe_waarde"],
                item["bron"], item["reden"], item["request_id"],
            )
            for item in audits if item["batterij"] == name
        )
        for name in ("Accu 1", "Accu 2", "Accu 3")
    }
    assert by_battery["Accu 1"] == by_battery["Accu 2"]
    assert by_battery["Accu 2"] == by_battery["Accu 3"]
    assert {item[0] for item in by_battery["Accu 3"]} == {
        42010, 42020, 42021,
    }
    assert 41200 not in {item[0] for item in by_battery["Accu 3"]}


def test_raw_client_audit_catches_backup_write_from_another_writer():
    class Client:
        def __init__(self):
            self.writes = []

        async def async_write_register(self, register, value, **kwargs):
            self.writes.append((register, value, kwargs))
            return True

    coordinator = DummyCoordinator("Accu 3")
    coordinator.client = Client()
    coordinator.data["backup_function"] = 0
    original_get_register = coordinator.get_register

    def get_register(key):
        if key == "backup_function":
            return 41200
        return original_get_register(key)

    coordinator.get_register = get_register
    hass = DummyHass([coordinator], DummyController())
    bridge = MarstekAtomicBridge(hass)
    bridge.targets()

    asyncio.run(coordinator.client.async_write_register(41200, 1))

    audits = [
        payload for event, payload in hass.bus.events
        if event == "openems_modbus_write_audit"
    ]
    assert coordinator.client.writes == [(41200, 1, {})]
    assert audits[-1]["register"] == 41200
    assert audits[-1]["sleutel"] == "backup_function"
    assert audits[-1]["oude_waarde"] == 0
    assert audits[-1]["nieuwe_waarde"] == 1
    assert audits[-1]["status"] == "raw_write_ok"
    assert "test_marstek_atomic_bridge.py" in audits[-1]["schrijver"]


def test_bridge_never_changes_rs485_after_non_delivery_commands():
    coordinator = DummyCoordinator(
        "Accu 1",
        feedback=[
            {
                "force_mode": 2,
                "set_charge_power": 0,
                "set_discharge_power": 900,
                "battery_power": 0,
            }
            for _ in range(3)
        ] + [
            {
                "force_mode": 2,
                "set_charge_power": 0,
                "set_discharge_power": 900,
                "battery_power": 850,
            }
        ],
    )

    async def no_wait(_seconds):
        return None

    bridge = MarstekAtomicBridge(
        DummyHass([coordinator], DummyController()),
        sleep=no_wait,
    )

    async def scenario():
        return [
            await bridge.set_power(1, 0, 900),
            await bridge.set_power(1, 0, 900),
            await bridge.set_power(1, 0, 900),
        ]

    first, second, third = asyncio.run(scenario())

    assert first["delivery_ok"] is False
    assert second["delivery_ok"] is False
    assert first["wake_attempted"] is False
    assert second["wake_attempted"] is False
    assert third["delivery_ok"] is False
    assert third["wake_attempted"] is False
    assert coordinator.register_writes == []


def test_bridge_does_not_wake_when_bms_may_protect_low_soc():
    coordinator = DummyCoordinator(
        "Accu 1",
        feedback=[
            {
                "force_mode": 2,
                "set_charge_power": 0,
                "set_discharge_power": 900,
                "battery_power": 0,
            }
            for _ in range(3)
        ],
    )
    coordinator.data["battery_soc"] = 14
    bridge = MarstekAtomicBridge(
        DummyHass([coordinator], DummyController())
    )

    async def scenario():
        return [
            await bridge.set_power(1, 0, 900)
            for _ in range(3)
        ]

    results = asyncio.run(scenario())

    assert all(result["delivery_ok"] for result in results)
    assert all(not result["wake_attempted"] for result in results)
    assert coordinator.register_writes == []


def test_bridge_skips_write_when_registers_and_delivery_already_match():
    coordinator = DummyCoordinator("Accu 1")
    coordinator.data.update({
        "force_mode": 2,
        "set_charge_power": 0,
        "set_discharge_power": 900,
        "battery_power": 850,
        "battery_soc": 50,
    })
    bridge = MarstekAtomicBridge(
        DummyHass([coordinator], DummyController())
    )

    result = asyncio.run(bridge.set_power(1, 0, 900))

    assert result["ok"] is True
    assert result["write_skipped"] is True
    assert coordinator.writes == []
    audit = [
        payload for event, payload in bridge.hass.bus.events
        if event == "openems_modbus_write_audit"
    ]
    assert {item["register"] for item in audit} == {42010, 42020, 42021}
    assert all(
        item["status"] == "overgeslagen_ongewijzigd"
        for item in audit
    )


def test_latest_only_batch_drops_intermediate_pending_setpoint():
    class BlockingCoordinator(DummyCoordinator):
        def __init__(self):
            super().__init__("Accu 1")
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.block_once = True

        async def write_power_atomic(
            self,
            discharge_power,
            charge_power,
            force_mode,
            read_back=True,
        ):
            if self.block_once:
                self.block_once = False
                self.started.set()
                await self.release.wait()
            return await super().write_power_atomic(
                discharge_power,
                charge_power,
                force_mode,
                read_back=read_back,
            )

    async def scenario():
        coordinator = BlockingCoordinator()
        hass = DummyHass([coordinator], DummyController())
        bridge = MarstekAtomicBridge(hass)
        bridge.enqueue_powers([{
            "battery": 1,
            "charge_power": 500,
            "discharge_power": 0,
        }], request_id="first")
        await coordinator.started.wait()
        bridge.enqueue_powers([{
            "battery": 1,
            "charge_power": 700,
            "discharge_power": 0,
        }], request_id="discarded")
        bridge.enqueue_powers([{
            "battery": 1,
            "charge_power": 900,
            "discharge_power": 0,
        }], request_id="latest")
        coordinator.release.set()
        await bridge._worker_task
        return coordinator, hass

    coordinator, hass = asyncio.run(scenario())

    assert coordinator.writes == [
        (0, 500, 1, True),
        (0, 900, 1, True),
    ]
    results = [
        payload for event, payload in hass.bus.events
        if event == "openems_marstek_control_result"
    ]
    assert len(results) == 2
    assert [
        payload["request_id"] for payload in results
    ] == ["first", "latest"]


def test_write_audit_log_level_only_warns_on_deviations():
    import logging

    level = MarstekAtomicBridge._audit_log_level
    # Ongewijzigde opdrachten vullen het HA-log niet meer.
    assert level("overgeslagen_ongewijzigd") == logging.DEBUG
    # Normale, geslaagde schrijfopdrachten zijn info.
    for status in ("geschreven_bevestigd", "geschreven_rs485_aan", "raw_write_ok"):
        assert level(status) == logging.INFO
    # Afwijkingen blijven zichtbaar als waarschuwing.
    for status in (
        "geschreven_niet_bevestigd",
        "raw_write_mislukt",
        "afgebroken_communicatietimeout",
        "overgeslagen_communicatie_backoff",
        "iets_onbekends",
    ):
        assert level(status) == logging.WARNING
