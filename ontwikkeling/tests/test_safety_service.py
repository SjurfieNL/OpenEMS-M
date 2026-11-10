from safety_service import BatterySafetyService


class App:
    def __init__(self, states):
        self.states = states

    def get_state(self, entity):
        return self.states.get(entity)


def test_public_safety_service_blocks_only_affected_battery_alarm():
    batteries = [
        {"soc": "sensor.soc1", "alarm_status": "sensor.alarm1"},
        {"soc": "sensor.soc2", "alarm_status": "sensor.alarm2"},
    ]
    service = BatterySafetyService(App({
        "sensor.soc1": 80, "sensor.alarm1": "No alarm",
        "sensor.soc2": 80, "sensor.alarm2": "Over voltage",
        "input_number.marstek_minimum_soc": 20,
        "input_number.marstek_doel_soc": 95,
    }), batteries)
    assert service.is_battery_operation_safe(0, "export").safe
    result = service.is_battery_operation_safe(1, "export")
    assert not result.safe
    assert "alarm_status" in result.reason


def test_public_safety_service_applies_soc_limits_by_operation():
    service = BatterySafetyService(App({
        "sensor.soc": 20,
        "input_number.marstek_minimum_soc": 20,
        "input_number.marstek_doel_soc": 95,
    }), [{"soc": "sensor.soc"}])
    assert not service.is_battery_operation_safe(0, "export").safe
    assert service.is_battery_operation_safe(0, "charge").safe


def test_one_bms_alarm_blocks_combined_export_assessment():
    service = BatterySafetyService(App({
        "a1": "No alarm", "a2": "Cell over voltage", "a3": "No alarm",
    }), [
        {"alarm_status": "a1"},
        {"alarm_status": "a2"},
        {"alarm_status": "a3"},
    ])
    assert not service.all_safe("export")


def test_quarantined_battery_does_not_block_healthy_batteries():
    class Health:
        @staticmethod
        def is_available(index):
            return index != 1

        @staticmethod
        def status(index):
            return {"reason": "tijdelijke herstelpauze"}

    service = BatterySafetyService(App({
        "a1": "No alarm", "a2": "No alarm", "a3": "No alarm",
    }), [
        {"alarm_status": "a1"},
        {"alarm_status": "a2"},
        {"alarm_status": "a3"},
    ])
    service.bind_health_manager(Health())

    assert not service.is_battery_operation_safe(1, "export").safe
    assert service.all_safe("export")
    assert not service.all_safe("export", indexes=[1])


def test_combined_operation_is_unsafe_when_every_battery_is_quarantined():
    class Health:
        @staticmethod
        def is_available(index):
            return False

    service = BatterySafetyService(App({}), [{}, {}, {}])
    service.bind_health_manager(Health())

    assert not service.all_safe("export")
