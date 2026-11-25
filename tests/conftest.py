"""Minimale nep-modules voor homeassistant en voluptuous.

Zo zijn de wizard en de validaties te testen zonder volledige HA-installatie.
De echte controle blijft: installeren op een HA en de wizard doorlopen.
"""

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))


def _install_stubs():
    if "homeassistant" in sys.modules:
        return
    vol = types.ModuleType("voluptuous")

    class _Key:
        def __init__(self, key, default=None):
            self.key, self.default = key, default
    vol.Required = lambda k, default=None: _Key(k, default)
    vol.Optional = lambda k, default=None: _Key(k, default)
    class _Schema(dict):
        def extend(self, other):
            merged = _Schema(self)
            merged.update(other)
            return merged
    vol.Schema = lambda d: _Schema(d)
    vol.All = lambda *a, **k: a
    vol.Coerce = lambda t: t
    vol.Range = lambda **k: k
    sys.modules["voluptuous"] = vol

    ha = types.ModuleType("homeassistant")
    ce = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kw):
            super().__init_subclass__(**kw)
            cls.DOMAIN = domain

        async def async_set_unique_id(self, uid):
            self.unique_id = uid

        def _abort_if_unique_id_configured(self):
            if getattr(self, "_configured", False):
                raise RuntimeError("already_configured")

        def async_show_form(self, step_id, data_schema=None, errors=None,
                            description_placeholders=None):
            return {"type": "form", "step_id": step_id, "errors": errors or {},
                    "schema": data_schema}

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

    class ConfigSubentryFlow:
        def _get_entry(self):
            return self.parent_entry

        def async_show_form(self, step_id, data_schema=None, errors=None,
                            description_placeholders=None):
            return {"type": "form", "step_id": step_id, "errors": errors or {},
                    "schema": data_schema, "placeholders": description_placeholders}

        def async_create_entry(self, title, data, unique_id=None):
            return {"type": "create_entry", "title": title, "data": data,
                    "unique_id": unique_id}

    _orig_create = ConfigFlow.async_create_entry

    def _create(self, title, data, subentries=None, **kw):
        result = _orig_create(self, title, data)
        result["subentries"] = list(subentries or [])
        return result
    ConfigFlow.async_create_entry = _create
    ce.ConfigFlow = ConfigFlow
    ce.ConfigSubentryFlow = ConfigSubentryFlow
    ce.ConfigFlowResult = dict
    ce.SubentryFlowResult = dict
    ce.ConfigEntry = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.ServiceCall = object
    core.callback = lambda func: func
    exc = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass
    exc.HomeAssistantError = HomeAssistantError
    sys.modules["homeassistant.exceptions"] = exc
    _install_entity_stubs()
    helpers = types.ModuleType("homeassistant.helpers")
    sel = types.ModuleType("homeassistant.helpers.selector")

    class _Any:
        def __init__(self, *a, **k):
            self.args, self.kwargs = a, k
    for name in ("EntitySelector", "EntitySelectorConfig", "NumberSelector",
                 "NumberSelectorConfig", "SelectSelector", "SelectSelectorConfig",
                 "TextSelector", "BooleanSelector"):
        setattr(sel, name, _Any)
    sel.NumberSelectorMode = types.SimpleNamespace(BOX="box")
    sel.SelectSelectorMode = types.SimpleNamespace(DROPDOWN="dropdown")
    helpers.selector = sel
    for mod in (("homeassistant", ha), ("homeassistant.config_entries", ce),
                ("homeassistant.core", core), ("homeassistant.helpers", helpers),
                ("homeassistant.helpers.selector", sel)):
        sys.modules[mod[0]] = mod[1]


def _install_entity_stubs():
    """Minimale entiteitsbasisklassen voor de helper-entiteiten."""
    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    class Entity:
        hass = None
        entity_id = None
        written = 0

        async def async_added_to_hass(self):
            return None

        def async_write_ha_state(self):
            self.written += 1

    class RestoreEntity(Entity):
        last_state = None

        async def async_get_last_state(self):
            return self.last_state

    class RestoreNumber(RestoreEntity):
        last_number = None

        async def async_get_last_number_data(self):
            return self.last_number

    class _Enum:
        def __init__(self, **values):
            self.__dict__.update(values)

    module("homeassistant.components")
    module("homeassistant.components.switch", SwitchEntity=type("SwitchEntity", (Entity,), {}))
    module("homeassistant.components.number", RestoreNumber=RestoreNumber,
           NumberMode=_Enum(BOX="box", SLIDER="slider"))
    module("homeassistant.components.select", SelectEntity=type("SelectEntity", (Entity,), {}))
    module("homeassistant.components.text", TextEntity=type("TextEntity", (Entity,), {}),
           TextMode=_Enum(TEXT="text", PASSWORD="password"))
    module("homeassistant.components.button", ButtonEntity=type("ButtonEntity", (Entity,), {}))
    module("homeassistant.const", EntityCategory=_Enum(CONFIG="config"))
    module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    module("homeassistant.helpers.restore_state", RestoreEntity=RestoreEntity)


_install_stubs()
