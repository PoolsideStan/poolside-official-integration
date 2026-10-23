"""Sensor platform for Poolside site, body-of-water, and pool device telemetry."""

from collections.abc import Callable
import contextlib
from dataclasses import dataclass, replace
from datetime import datetime
import json
from typing import Any, override

from aiopoolside import (
    PoolsideClient,
    PoolsideControl,
    PoolsideDevice,
    PoolsideGroup,
    PoolsideSite,
)
from aiopoolside.const import (
    ACTUAL_POWER_STATE_FIELD,
    ACTUATOR_FRIENDLY_STATE_FIELD,
    ACTUATOR_POSITION_FIELD,
    BRIGHTNESS_FIELD,
    CALIBRATED_LEFT_TO_RIGHT_FIELD,
    CALIBRATED_RIGHT_TO_LEFT_FIELD,
    CURRENT_STATE_FIELD,
    CURRENT_TEMPERATURE_FIELD,
    FIELD_DISPLAY_NAME_KEY,
    FIELD_DISPLAY_ORDER_KEY,
    FIELD_NAME_KEY,
    FIELD_PROCESSING_LOGIC_KEY,
    FIELD_TYPES_KEY,
    FREEZE_PROTECT_REASON,
    INFORMATION_FIELD_TYPE,
    INFORMATION_FIELDS_FIELD,
    LIGHT_NAME_FIELD,
    POWER_STATE_FIELD,
    SPEED_FIELD,
    TEMPERATURE_RISE_EVENT_TYPE_KEY,
    TEMPERATURE_RISE_INFORMATION_FIELD,
    TEMPERATURE_RISE_NORMAL,
    TEMPERATURE_RISE_SAMPLED,
    TEMPERATURE_RISE_STATE_KEY,
    TEMPERATURE_RISE_TARGET_END_KEY,
    TWINKLE_FIELD,
    TWINKLE_INCREMENTS_FIELD,
    WINTERIZED_FIELD,
    WINTERIZED_REASON,
    ActuatorFriendlyState,
    BodyOfWaterState,
    ControlType,
    SiteMode,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import PoolsideConfigEntry
from .const import (
    ACID_MIX_EVENT_FIELD,
    DOSE_AMOUNT_KEY,
    DOSE_CUT_SHORT_KEY,
    DOSE_END_KEY,
    DOSE_INTERRUPTED_KEY,
    DOSE_MANUAL_KEY,
    DOSE_PROBE_BEFORE_KEY,
    DOSE_SECONDS_KEY,
    DOSE_START_KEY,
    DOSE_UNIT_KEY,
    DOMAIN,
    ENERGY_LIQUID_PROPANE_KEY,
    ENERGY_NATURAL_GAS_KEY,
    ENERGY_WATTS_KEY,
    LAST_MINUTE_OF_ENERGY_CONSUMPTION_FIELD,
    LIQUID_CHLORINE_MIX_EVENT_FIELD,
    LOGGER,
    MIX_MINUTES_KEY,
    ON_UNTIL_FIELD,
    SALT_CHLORINATOR_MIX_EVENT_FIELD,
    SITE_MODE_KEY,
    WATER_QUALITY_STATE_FIELD,
)
from .entity import (
    PoolsideBaseEntity,
    PoolsideDeviceEntity,
    PoolsideEntity,
    PoolsideGroupEntity,
    confirmed_status,
)
from .button import PoolsideDoseResetButton


@dataclass(frozen=True, kw_only=True)
class PoolsideBodySensorDescription(SensorEntityDescription):
    """Describes a numeric telemetry field pushed under a body of water's UUID."""

    field: str


TEMPERATURE_SENSOR = PoolsideBodySensorDescription(
    key="temperature",
    field=CURRENT_TEMPERATURE_FIELD,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    # All temperatures on this wire are degrees Fahrenheit.
    native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
)

# Water chemistry probes are optional equipment, so each of these is only
# added once its field is actually reported for the body of water.
CHEMISTRY_SENSORS = (
    PoolsideBodySensorDescription(
        key="orp",
        translation_key="orp",
        field="ORP",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
    ),
    PoolsideBodySensorDescription(
        key="ph",
        # Icon translation only; the name still comes from the device class.
        translation_key="ph",
        field="PH",
        device_class=SensorDeviceClass.PH,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    PoolsideBodySensorDescription(
        key="free_chlorine",
        translation_key="free_chlorine",
        field="FreeChlorine",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
    ),
    PoolsideBodySensorDescription(
        key="total_chlorine",
        translation_key="total_chlorine",
        field="TotalChlorine",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
    ),
    PoolsideBodySensorDescription(
        key="dissolved_oxygen_concentration",
        translation_key="dissolved_oxygen_concentration",
        field="DissolvedOxygenConcentration",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="mg/L",
    ),
    PoolsideBodySensorDescription(
        key="dissolved_oxygen_saturation",
        translation_key="dissolved_oxygen_saturation",
        field="DissolvedOxygenSaturation",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    PoolsideBodySensorDescription(
        key="salt_level",
        translation_key="salt_level",
        field="SaltLevel",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
    ),
)

# Dose-event sensors: one per chemical dosing system the body of water
# reports. The item's value is the dose document encoded as a JSON string
# while a dose is running, and null once the dose has finished - the
# controller keeps the last event under the same item name until the next
# dose starts. Acid and liquid chlorine are the chemical systems every
# dose-capable body of water carries; the salt chlorinator's item only
# exists on sites whose equipment chlorinates with salt, so like the
# optional chemistry probes above it's added only once reported.
DOSE_EVENT_SENSORS = (
    PoolsideBodySensorDescription(
        key="acid_dose",
        translation_key="acid_dose",
        field=ACID_MIX_EVENT_FIELD,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.FLUID_OUNCES,
    ),
    PoolsideBodySensorDescription(
        key="chlorine_dose",
        translation_key="chlorine_dose",
        field=LIQUID_CHLORINE_MIX_EVENT_FIELD,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.FLUID_OUNCES,
    ),
    PoolsideBodySensorDescription(
        key="salt_chlorinator_dose",
        translation_key="salt_chlorinator_dose",
        field=SALT_CHLORINATOR_MIX_EVENT_FIELD,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.FLUID_OUNCES,
    ),
)

# WaterQualityState values the controller has been observed to report, as
# sensor options (the raw wire values, lowercased). GOOD/OK/LOW are the
# everyday states; ALERT covers a chemistry problem and NONE a body of water
# with no chemistry management. Unrecognized values are dropped rather than
# crashing the state property.
WATER_QUALITY_OPTIONS = ["good", "ok", "low", "alert", "none"]

# Dose sensor keys mapped to the ChemicalType string the matching pump
# device's model carries ("Acid" Acid pump, "LiquidChlorine" chlorine pump).
DOSE_KEY_TO_CHEMICAL_TYPE = {
    "acid_dose": "ACID",
    "chlorine_dose": "CHLOR",
    "salt_chlorinator_dose": "SALT",
}


@dataclass(frozen=True, kw_only=True)
class PoolsideBodyEnergySensorDescription(SensorEntityDescription):
    """Describes one reading of a body of water's last-minute energy document.

    The `LastMinuteOfEnergyConsumption` item is a JSON document with the
    electric and fuel energy the body's equipment consumed over the last
    minute; each sensor renders one of its totals. Fuel energy is thermal
    (Btu), not metered fuel volume, so the gas reading deliberately carries
    no device class: HA's gas device class is for volume meters with
    totalizing state classes, and this is neither. `extra_attribute_key`
    names a document total rendered as an attribute instead of its own
    entity, keeping the entity count lean (propane rides along on the
    natural gas sensor).
    """

    document_key: str
    extra_attribute_key: str | None = None


ENERGY_SENSORS = (
    PoolsideBodyEnergySensorDescription(
        key="gas_consumption",
        translation_key="gas_consumption",
        document_key=ENERGY_NATURAL_GAS_KEY,
        extra_attribute_key=ENERGY_LIQUID_PROPANE_KEY,
        native_unit_of_measurement="Btu",
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


def _float_value(value: Any) -> float | None:
    """Coerce a telemetry value to a number, or None if it isn't one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_value(value: Any) -> str:
    """Pass a telemetry value through as text."""
    return str(value)


def _on_off_value(value: Any) -> str | None:
    """Map an ONOFF value (ON/OFF/UNKNOWN strings or booleans) to its option.

    UNKNOWN (or anything unrecognized) is a "can't confirm" sentinel, not a
    real state, so it maps to no data.
    """
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).strip().upper()
    if text == "ON":
        return "on"
    if text == "OFF":
        return "off"
    return None


def _bool_value(value: Any) -> bool | None:
    """Coerce a BOOLEAN value (booleans or true/false strings) to a bool."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes"):
        return True
    if text in ("false", "no"):
        return False
    return None


def _yes_no_value(value: Any) -> str | None:
    """Map a BOOLEAN value (booleans or true/false strings) to yes/no."""
    if (result := _bool_value(value)) is None:
        return None
    return "yes" if result else "no"


def _actuator_state_value(value: Any) -> str | None:
    """Map a FriendlyState value to its option, or None if unrecognized."""
    try:
        return ActuatorFriendlyState(str(value).strip().upper()).value.lower()
    except ValueError:
        return None


def _position_percent_value(value: Any) -> float | None:
    """Coerce a position percentage, treating out-of-range values as no data."""
    number = _float_value(value)
    if number is None or not 0 <= number <= 100:
        return None
    return number


def _datetime_value(value: Any) -> datetime | None:
    """Parse an ISO datetime, tolerating double-JSON-encoded values.

    Pump's PrimingUntil arrives as a JSON string encoded inside the string
    value ('"2026-..."'), so one layer of quoting is stripped first.
    """
    if isinstance(value, str) and value.startswith('"'):
        with contextlib.suppress(ValueError):
            value = json.loads(value)
    parsed = dt_util.parse_datetime(str(value))
    if parsed is not None and parsed.tzinfo is None:
        # Naive timestamps are in the controller's (= HA's) local time.
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _temperature_rise_document(
    client: PoolsideClient, body_of_water_uuid: str
) -> dict[str, Any] | None:
    """Return a body of water's TemperatureRiseInformation document, if any.

    Like other JSON-in-string fields it arrives encoded inside the string
    value in status pushes; a native document is accepted too.
    """
    value = client.get_status(body_of_water_uuid, TEMPERATURE_RISE_INFORMATION_FIELD)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            LOGGER.warning(
                "%s: unparsable %s: %r",
                body_of_water_uuid,
                TEMPERATURE_RISE_INFORMATION_FIELD,
                value,
            )
            return None
    return value if isinstance(value, dict) else None


def _json_document(
    status_key: str, field: str, value: Any
) -> dict[str, Any] | None:
    """Return a status item parsed as a JSON document, if it is one.

    Documents pushed via Device.setStatus arrive as JSON encoded inside the
    string value, like other capability documents; a native document is
    accepted too. Null, absent, non-JSON, and non-document values are all
    "no data", never a crash: several of these items simply don't exist on
    installs without the corresponding equipment.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            value = json.loads(value)
        except ValueError:
            LOGGER.warning("%s: unparsable %s: %r", status_key, field, value)
            return None
    return value if isinstance(value, dict) else None


def _dose_event_document(
    client: PoolsideClient, body_of_water_uuid: str, field: str
) -> dict[str, Any] | None:
    """Return the body of water's last dose event document for a chemical, if any."""
    return _json_document(
        body_of_water_uuid,
        field,
        client.get_status(body_of_water_uuid, field),
    )


def _dose_amount(document: dict[str, Any] | None) -> float | None:
    """Return a dose document's DoseAmount, in its own dosing unit."""
    if document is None:
        return None
    return _float_value(document.get(DOSE_AMOUNT_KEY))


def _dose_extra_attributes(document: dict[str, Any] | None) -> dict[str, Any]:
    """Return the supporting details of a dose event as attributes.

    Datetimes are kept as their raw controller strings - they're attributes
    for inspection, not state - and missing keys are simply omitted so an
    interrupted or in-progress dose doesn't render misleading values.
    """
    if document is None:
        return {}
    attributes: dict[str, Any] = {}
    for document_key, attribute_name in (
        (DOSE_SECONDS_KEY, "duration_seconds"),
        (DOSE_START_KEY, "started"),
        (DOSE_END_KEY, "ended"),
        (MIX_MINUTES_KEY, "mix_minutes"),
        (DOSE_PROBE_BEFORE_KEY, "probe_before"),
        (DOSE_INTERRUPTED_KEY, "interrupted"),
        (DOSE_CUT_SHORT_KEY, "cut_short"),
        (DOSE_MANUAL_KEY, "manually_initiated"),
        (DOSE_UNIT_KEY, "unit"),
    ):
        if (value := document.get(document_key)) is not None:
            attributes[attribute_name] = value
    return attributes


def _energy_document(
    client: PoolsideClient, body_of_water_uuid: str
) -> dict[str, Any] | None:
    """Return the body of water's last-minute energy document, if any.

    The controller reports the document under its site status; bodies of
    water are checked first all the same, so installs that stream the
    item per-body keep working.
    """
    for uuid in (body_of_water_uuid, client.site_uuid):
        if not uuid:
            continue
        document = _json_document(
            uuid,
            LAST_MINUTE_OF_ENERGY_CONSUMPTION_FIELD,
            client.get_status(uuid, LAST_MINUTE_OF_ENERGY_CONSUMPTION_FIELD),
        )
        if document is not None:
            return document
    return None


def _energy_total_reported(
    client: PoolsideClient,
    body_of_water_uuid: str,
    description: PoolsideBodyEnergySensorDescription,
) -> bool:
    """Return True once a body of water's energy document carries this total.

    A missing document or one without the total means the equipment
    behind it isn't reported (all-electric bodies carry no gas total, and
    vice versa), so the sensor for it is never added at all.
    """
    document = _energy_document(client, body_of_water_uuid)
    return document is not None and document.get(description.document_key) is not None


def _chemical_pump_device(
    pool_devices: list[PoolsideDevice], dose_key: str
) -> PoolsideDevice | None:
    """Return the chemical pump device a dose key's entities attach to.

    Dose items are keyed to a chemical (acid, liquid chlorine, salt), and
    its pump is identified by its vendor-assigned device name ("Acid
    Pump"); all chemical pumps share the generic ChemicalPump device
    type, so the name is the only per-chemical identifier installs
    carry. Unknown pairings return None and the caller falls back to the
    body group device.
    """
    chemical = DOSE_KEY_TO_CHEMICAL_TYPE.get(dose_key)
    if chemical is None:
        return None
    for device in pool_devices:
        name = str(getattr(device, "name", "") or "").upper()
        type_ = str(getattr(device, "device_type", "") or "").upper()
        if type_ != "CHEMICALPUMP":
            continue
        if chemical in name:
            return device
    return None


def _heater_device(
    pool_devices: list[PoolsideDevice],
) -> PoolsideDevice | None:
    """Return the pool heater device, if the site has one."""
    for device in pool_devices:
        type_ = str(getattr(device, "device_type", "") or "").upper()
        name = str(getattr(device, "name", "") or "").upper()
        if type_ == "HEATER" or "HEATER" in name:
            return device
    return None


def _water_quality_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in WATER_QUALITY_OPTIONS else None


def _predicted_ready_time(document: dict[str, Any] | None) -> datetime | None:
    """Return a heat-up's predicted completion time, as the vendor app shows it.

    The app only shows one for a sampled, normal heat-up: while the
    controller is still sampling it already fills in a preliminary estimate,
    and maintaining temperature or learning aren't heat-ups to wait for. An
    unset TargetEndDateTime arrives as a 0001-01-01 placeholder.
    """
    if (
        document is None
        or document.get(TEMPERATURE_RISE_STATE_KEY) != TEMPERATURE_RISE_SAMPLED
        or document.get(TEMPERATURE_RISE_EVENT_TYPE_KEY) != TEMPERATURE_RISE_NORMAL
    ):
        return None
    target = _datetime_value(document.get(TEMPERATURE_RISE_TARGET_END_KEY))
    if target is None or target.year <= 1:
        return None
    return target


@dataclass(frozen=True, kw_only=True)
class PoolsideFieldSensorDescription(SensorEntityDescription):
    """Describes how one DisplayProcessingLogic renders as a sensor."""

    value_fn: Callable[[Any], float | str | datetime | None] = _string_value


ON_OFF_OPTIONS = ["on", "off"]

# One template per DisplayProcessingLogic value, matching how the vendor UI
# renders each; the per-field description is the template re-keyed to the
# field's Name. Unlisted logics (STRING, LONG_STRING, and any new
# controller-side additions) render as plain text, so they degrade
# gracefully instead of being dropped.
PROCESSING_LOGIC_DESCRIPTIONS: dict[str, PoolsideFieldSensorDescription] = {
    "ONOFF": PoolsideFieldSensorDescription(
        key="ONOFF",
        device_class=SensorDeviceClass.ENUM,
        options=ON_OFF_OPTIONS,
        value_fn=_on_off_value,
    ),
    "TEMP_F": PoolsideFieldSensorDescription(
        key="TEMP_F",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "GPM": PoolsideFieldSensorDescription(
        key="GPM",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "PSI": PoolsideFieldSensorDescription(
        key="PSI",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "PERCENT": PoolsideFieldSensorDescription(
        key="PERCENT",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "MG_L": PoolsideFieldSensorDescription(
        key="MG_L",
        native_unit_of_measurement="mg/L",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_float_value,
    ),
    "PPM": PoolsideFieldSensorDescription(
        key="PPM",
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=_float_value,
    ),
    "WATTAGE": PoolsideFieldSensorDescription(
        key="WATTAGE",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "RPM": PoolsideFieldSensorDescription(
        key="RPM",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "AMP": PoolsideFieldSensorDescription(
        key="AMP",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "UA": PoolsideFieldSensorDescription(
        key="UA",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MICROAMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "MV": PoolsideFieldSensorDescription(
        key="MV",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "VOLT": PoolsideFieldSensorDescription(
        key="VOLT",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "MS_TO_S": PoolsideFieldSensorDescription(
        key="MS_TO_S",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "DATETIME": PoolsideFieldSensorDescription(
        key="DATETIME",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_datetime_value,
    ),
    "BOOLEAN": PoolsideFieldSensorDescription(
        key="BOOLEAN",
        device_class=SensorDeviceClass.ENUM,
        options=["yes", "no"],
        value_fn=_yes_no_value,
    ),
    "FLOAT": PoolsideFieldSensorDescription(
        key="FLOAT",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "INTEGER": PoolsideFieldSensorDescription(
        key="INTEGER",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_float_value,
    ),
    "X": PoolsideFieldSensorDescription(
        key="X",
        native_unit_of_measurement="x",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float_value,
    ),
    "ACTUATOR_FRIENDLY_STATE": PoolsideFieldSensorDescription(
        key="ACTUATOR_FRIENDLY_STATE",
        translation_key="actuator_state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value.lower() for state in ActuatorFriendlyState],
        value_fn=_actuator_state_value,
    ),
    "ACTUATOR_POSITION": PoolsideFieldSensorDescription(
        key="ACTUATOR_POSITION",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_position_percent_value,
    ),
}

DEFAULT_FIELD_DESCRIPTION = PoolsideFieldSensorDescription(key="STRING")

TWINKLE_PROCESSING_LOGIC = "TWINKLE"

# Supporting states referenced by other fields' rendering, never shown as
# sensors of their own even when a descriptor marks them INFORMATION.
HIDDEN_INFORMATION_FIELDS = {TWINKLE_INCREMENTS_FIELD}

# Light pool devices never publish an InformationFields document - the
# vendor app hardcodes their screen - so an equivalent fixed field set is
# synthesized for them instead.
LIGHT_DEVICE_FIELDS: list[dict[str, Any]] = [
    {
        FIELD_NAME_KEY: POWER_STATE_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Power",
        FIELD_PROCESSING_LOGIC_KEY: "ONOFF",
    },
    {
        FIELD_NAME_KEY: LIGHT_NAME_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Show",
        FIELD_PROCESSING_LOGIC_KEY: "LIGHT_NAME",
    },
    {
        FIELD_NAME_KEY: BRIGHTNESS_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Brightness",
        FIELD_PROCESSING_LOGIC_KEY: "PERCENT",
    },
    {
        FIELD_NAME_KEY: SPEED_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Speed",
        FIELD_PROCESSING_LOGIC_KEY: "X",
    },
    {
        FIELD_NAME_KEY: TWINKLE_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Twinkle",
        FIELD_PROCESSING_LOGIC_KEY: TWINKLE_PROCESSING_LOGIC,
    },
]

# Actuator pool devices always carry their headline state and position,
# whether or not they publish an InformationFields document.
ACTUATOR_DEVICE_FIELDS: list[dict[str, Any]] = [
    {
        FIELD_NAME_KEY: ACTUATOR_FRIENDLY_STATE_FIELD,
        FIELD_DISPLAY_NAME_KEY: "State",
        FIELD_PROCESSING_LOGIC_KEY: "ACTUATOR_FRIENDLY_STATE",
    },
    {
        FIELD_NAME_KEY: ACTUATOR_POSITION_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Position",
        FIELD_PROCESSING_LOGIC_KEY: "ACTUATOR_POSITION",
    },
]

# Shown for an actuator that publishes no InformationFields document; a
# published document takes its place, like the vendor screen's fallback list.
ACTUATOR_FALLBACK_FIELDS: list[dict[str, Any]] = [
    {
        FIELD_NAME_KEY: CALIBRATED_LEFT_TO_RIGHT_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Calibration left to right",
        FIELD_PROCESSING_LOGIC_KEY: "MS_TO_S",
    },
    {
        FIELD_NAME_KEY: CALIBRATED_RIGHT_TO_LEFT_FIELD,
        FIELD_DISPLAY_NAME_KEY: "Calibration right to left",
        FIELD_PROCESSING_LOGIC_KEY: "MS_TO_S",
    },
]


def _device_field_description(field: dict[str, Any]) -> PoolsideFieldSensorDescription:
    """Build the sensor description for one InformationFields entry."""
    logic = str(field.get(FIELD_PROCESSING_LOGIC_KEY) or "")
    template = PROCESSING_LOGIC_DESCRIPTIONS.get(logic, DEFAULT_FIELD_DESCRIPTION)
    return replace(template, key=field[FIELD_NAME_KEY])


def _information_fields(
    client: PoolsideClient, device: PoolsideDevice
) -> list[dict[str, Any]]:
    """Return a pool device's telemetry field descriptors, in display order.

    The InformationFields document is a JSON list (possibly encoded inside a
    string, like other capability documents) pushed under the device's UUID.
    Entries without the INFORMATION field type aren't telemetry and are
    skipped.
    """
    value = client.get_status(device.uuid, INFORMATION_FIELDS_FIELD)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            LOGGER.warning(
                "%s: unparsable %s: %r", device.uuid, INFORMATION_FIELDS_FIELD, value
            )
            return []
    if not isinstance(value, list):
        return []
    fields = [
        field
        for field in value
        if isinstance(field, dict)
        and field.get(FIELD_NAME_KEY)
        and field.get(FIELD_NAME_KEY) not in HIDDEN_INFORMATION_FIELDS
        and INFORMATION_FIELD_TYPE in (field.get(FIELD_TYPES_KEY) or [])
    ]
    fields.sort(key=lambda field: field.get(FIELD_DISPLAY_ORDER_KEY) or 0)
    return fields


def _device_fields(
    client: PoolsideClient, device: PoolsideDevice
) -> list[dict[str, Any]]:
    """Return a pool device's telemetry field descriptors.

    Light devices never publish InformationFields, so their fixed vendor
    screen layout is used instead. Actuators always carry their state and
    position fields, plus their published InformationFields document or the
    fixed fallback list when there is none. Every other device type is
    described by its InformationFields document alone.
    """
    if device.is_light:
        return LIGHT_DEVICE_FIELDS
    if device.is_actuator:
        return ACTUATOR_DEVICE_FIELDS + (
            _information_fields(client, device) or ACTUATOR_FALLBACK_FIELDS
        )
    return _information_fields(client, device)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolsideConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors per body of water, per pool device, and the site mode sensor.

    Every body gets a temperature sensor. Chemistry sensors, dose-event
    sensors, the water quality sensor, and the energy sensors are added
    the first time their item is reported - usually straight from the
    initial status snapshot, otherwise on a later push - since the
    equipment behind them is optional and whole items can be absent on
    installs without it. Heater "egg timer" sensors follow the same
    pattern per control, keyed on the control reporting OnUntil. Pool
    device sensors are keyed on the device's InformationFields document -
    except for light devices, whose fixed field set never waits on a
    descriptor.
    """
    data = entry.runtime_data
    client = data.client
    groups = {control.group.uuid: control.group for control in data.controls}
    bodies = [
        (group, body_of_water_uuid)
        for group in groups.values()
        if (body_of_water_uuid := group.body_of_water_uuid) is not None
    ]

    entities: list[SensorEntity] = []
    for group, body_of_water_uuid in bodies:
        entities.append(
            PoolsideBodySensor(client, group, body_of_water_uuid, TEMPERATURE_SENSOR)
        )
        entities.append(PoolsideBodyStateSensor(client, group, body_of_water_uuid))
    entities.extend(
        PoolsideControlDisabledReasonSensor(client, control)
        for control in data.controls
    )
    # ActualPowerState and Winterized are pushed for every pool device
    # regardless of what its InformationFields document lists, so their
    # sensors are created eagerly. Actuators are the exception: they are not
    # on/off-able and report no power state at all.
    entities.extend(
        PoolsideDevicePowerSensor(client, device)
        for device in data.pool_devices
        if not device.is_actuator
    )
    entities.extend(
        PoolsideDeviceWinterizedSensor(client, device) for device in data.pool_devices
    )
    if (site_uuid := data.site.uuid) is not None:
        entities.append(PoolsideSiteModeSensor(client, data.site, site_uuid))
    async_add_entities(entities)

    added: set[str] = set()
    # The heater pool device, if the site has one: gas consumption and the
    # heater egg-timer attach to it.
    heater_device = _heater_device(data.pool_devices)

    @callback
    def _async_add_reported_body_sensors() -> None:
        new_entities: list[SensorEntity] = []
        for group, body_of_water_uuid in bodies:
            # Only bodies with a heater report heat-ups.
            ready_key = f"{body_of_water_uuid}_estimated_ready_time"
            if ready_key not in added and (
                client.get_status(
                    body_of_water_uuid, TEMPERATURE_RISE_INFORMATION_FIELD
                )
                is not None
            ):
                added.add(ready_key)
                new_entities.append(
                    PoolsideBodyReadyTimeSensor(client, group, body_of_water_uuid)
                )
            for description in CHEMISTRY_SENSORS:
                added_key = f"{body_of_water_uuid}_{description.key}"
                if (
                    added_key in added
                    or client.get_status(body_of_water_uuid, description.field) is None
                ):
                    continue
                added.add(added_key)
                new_entities.append(
                    PoolsideBodySensor(client, group, body_of_water_uuid, description)
                )
            # Dose events are added eagerly (visible immediately as no data
            # until a dose runs); the salt chlorinator's item only exists on
            # salt sites, so that one waits for its first push. Each dose
            # sensor, its total, and its reset button attach to the chemical
            # pump device behind the chemical (matched by pump model, the
            # only per-chemical identifier every install carries), falling
            # back to the body group device on installs that report none.
            for description in DOSE_EVENT_SENSORS:
                added_key = f"{body_of_water_uuid}_{description.key}"
                if (
                    added_key in added
                    or (
                        description.key == "salt_chlorinator_dose"
                        and client.get_status(
                            body_of_water_uuid, description.field
                        )
                        is None
                    )
                ):
                    continue
                added.add(added_key)
                pump_device = _chemical_pump_device(
                    data.pool_devices, description.key
                )
                if pump_device is None:
                    dose_sensor = PoolsideBodyDoseSensor(
                        client, group, body_of_water_uuid, description
                    )
                    total_sensor = PoolsideDoseTotalSensor(
                        client, group, body_of_water_uuid, dose_sensor
                    )
                else:
                    dose_sensor = PoolsideBodyDoseSensor(
                        client, group, body_of_water_uuid, description,
                        device=pump_device,
                    )
                    total_sensor = PoolsideDoseTotalSensor(
                        client, group, body_of_water_uuid, dose_sensor,
                        device=pump_device,
                    )
                new_entities.append(dose_sensor)
                added.add(f"{added_key}_total")
                new_entities.append(total_sensor)
                # Registered for the button platform's reset buttons, which
                # set up after sensors (PLATFORMS order).
                data.dose_handlers[f"{body_of_water_uuid}_{description.key}"] = (
                    dose_sensor
                )
            water_quality_key = f"{body_of_water_uuid}_water_quality"
            if (
                water_quality_key not in added
                and client.get_status(
                    body_of_water_uuid, WATER_QUALITY_STATE_FIELD
                )
                is not None
            ):
                added.add(water_quality_key)
                new_entities.append(
                    PoolsideBodyWaterQualitySensor(
                        client, group, body_of_water_uuid
                    )
                )
            # Gas consumption attaches to the heater device (it's the
            # heater's fuel burn); electric watts is a whole-body figure
            # and stays on the body group device.
            for description in ENERGY_SENSORS:
                added_key = f"{body_of_water_uuid}_{description.key}"
                if added_key in added:
                    continue
                added.add(added_key)
                attach_device = (
                    heater_device
                    if description.key == "gas_consumption"
                    else None
                )
                new_entities.append(
                    PoolsideBodyEnergySensor(
                        client, group, body_of_water_uuid, description,
                        device=attach_device,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    _async_add_reported_body_sensors()
    for _group, body_of_water_uuid in bodies:
        entry.async_on_unload(
            client.subscribe_status(
                body_of_water_uuid, _async_add_reported_body_sensors
            )
        )

    # Heater "egg timer" sensors are added eagerly for every temperature
    # control; while no timed session runs the value is no data. A heater
    # that can never report the item simply stays at no data.
    heaters = [
        control
        for control in data.controls
        if control.control_type is ControlType.TEMPERATURE
    ]
    async_add_entities(
        PoolsideControlOnUntilSensor(
            client, control, device=heater_device
        )
        for control in heaters
    )

    # Pre-seeded so a descriptor that lists ActualPowerState or Winterized
    # itself doesn't collide with the dedicated sensors created above.
    added_device_fields: set[str] = {
        f"{device.uuid}_{dedicated_field}"
        for device in data.pool_devices
        for dedicated_field in (ACTUAL_POWER_STATE_FIELD, WINTERIZED_FIELD)
    }

    @callback
    def _async_add_described_device_sensors() -> None:
        new_entities: list[PoolsideDeviceSensor] = []
        for device in data.pool_devices:
            for field in _device_fields(client, device):
                added_key = f"{device.uuid}_{field[FIELD_NAME_KEY]}"
                if added_key in added_device_fields:
                    continue
                added_device_fields.add(added_key)
                sensor_cls = (
                    PoolsideDeviceTwinkleSensor
                    if field.get(FIELD_PROCESSING_LOGIC_KEY) == TWINKLE_PROCESSING_LOGIC
                    else PoolsideDeviceSensor
                )
                new_entities.append(sensor_cls(client, device, field))
        if new_entities:
            async_add_entities(new_entities)

    _async_add_described_device_sensors()
    for device in data.pool_devices:
        entry.async_on_unload(
            client.subscribe_status(device.uuid, _async_add_described_device_sensors)
        )


class PoolsideBodySensor(PoolsideGroupEntity, SensorEntity):
    """A numeric telemetry value for a body of water.

    Confirmed telemetry pushed by the controller, keyed by the group's
    BodyOfWaterUUID rather than any control's UUID.
    """

    entity_description: PoolsideBodySensorDescription

    def __init__(
        self,
        client: PoolsideClient,
        group: PoolsideGroup,
        body_of_water_uuid: str,
        description: PoolsideBodySensorDescription,
    ) -> None:
        """Set up one telemetry sensor for a given body of water."""
        super().__init__(client, group)
        self.entity_description = description
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}_{description.key}"
        )

    @override
    def _status_keys(self) -> set[str]:
        """Return the body-of-water key its telemetry arrives under."""
        return {self._body_of_water_uuid}

    @property
    @override
    def native_value(self) -> float | None:
        """Return the last reported value."""
        value = self._client.get_status(
            self._body_of_water_uuid, self.entity_description.field
        )
        return None if value is None else float(value)


class PoolsideBodyStateSensor(PoolsideGroupEntity, SensorEntity):
    """A body of water's overall FRIENDLY_STATE (its CurrentState field)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "current_state"
    _attr_options = [state.value.lower() for state in BodyOfWaterState]

    def __init__(
        self, client: PoolsideClient, group: PoolsideGroup, body_of_water_uuid: str
    ) -> None:
        """Set up the state sensor for a given body of water."""
        super().__init__(client, group)
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}_current_state"
        )

    @override
    def _status_keys(self) -> set[str]:
        """Return the body-of-water key its state arrives under."""
        return {self._body_of_water_uuid}

    @property
    @override
    def native_value(self) -> str | None:
        """Return the body of water's last reported state."""
        value = self._client.get_status(self._body_of_water_uuid, CURRENT_STATE_FIELD)
        if value is None:
            return None
        try:
            return BodyOfWaterState(value).value.lower()
        except ValueError:
            return None


class PoolsideBodyReadyTimeSensor(PoolsideGroupEntity, SensorEntity):
    """When a heating body of water is predicted to reach its set point.

    The controller's prediction from TemperatureRiseInformation, shown when
    the vendor app shows its "ready in" time. Unknown otherwise, including
    once the predicted time has passed without a new prediction.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "estimated_ready_time"

    def __init__(
        self, client: PoolsideClient, group: PoolsideGroup, body_of_water_uuid: str
    ) -> None:
        """Set up the ready-time sensor for a given body of water."""
        super().__init__(client, group)
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}_estimated_ready_time"
        )
        self._target: datetime | None = None
        self._cancel_expiry: CALLBACK_TYPE | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Track the prediction, and clear it once its time has passed."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.subscribe_status(
                self._body_of_water_uuid, self._async_prediction_updated
            )
        )
        self.async_on_remove(self._async_cancel_expiry)
        self._async_read_prediction()

    @callback
    def _async_read_prediction(self) -> None:
        """Read the current prediction and schedule a refresh when it passes."""
        self._async_cancel_expiry()
        self._target = _predicted_ready_time(
            _temperature_rise_document(self._client, self._body_of_water_uuid)
        )
        if self._target is not None and self._target > dt_util.utcnow():
            self._cancel_expiry = async_track_point_in_utc_time(
                self.hass, self._async_prediction_expired, self._target
            )

    @callback
    def _async_prediction_updated(self) -> None:
        """Handle a status push for the body of water."""
        self._async_read_prediction()
        self.async_write_ha_state()

    @callback
    def _async_prediction_expired(self, _now: datetime) -> None:
        """Drop a predicted time that has passed without a new prediction."""
        self._cancel_expiry = None
        self.async_write_ha_state()

    @callback
    def _async_cancel_expiry(self) -> None:
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the predicted completion time while it's still ahead."""
        if self._target is None or self._target <= dt_util.utcnow():
            return None
        return self._target


class PoolsideBodyDoseSensor(PoolsideGroupEntity, SensorEntity):
    """The most recent dose a body of water's chemical dosing system performed.

    The controller keeps one item per chemical (`CurrentAcidMixEvent`,
    `CurrentLiquidChlorineMixEvent`, ...): while a dose runs, its value is
    the dose document encoded as a JSON string, and it stays populated with
    the completed dose afterwards, going null only briefly on the next
    dose's start. A null therefore means "no new dose information", not
    "the last dose never happened", so the last known document is kept and
    the sensor keeps showing the last dose until the next one arrives.

    The sensor also feeds its chemical's `PoolsideDoseTotalSensor`: each
    newly completed dose (a document with a new UUID, once it carries an
    EndDateTime) adds its amount to that running total, which a paired
    reset button zeroes.
    """

    entity_description: PoolsideBodySensorDescription

    def __init__(
        self,
        client: PoolsideClient,
        group: PoolsideGroup,
        body_of_water_uuid: str,
        description: PoolsideBodySensorDescription,
        device: PoolsideDevice | None = None,
    ) -> None:
        """Set up the last-dose sensor for one chemical of a body of water."""
        super().__init__(client, group)
        self.entity_description = description
        self._group = group
        self._device = device
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}_{description.key}"
        )
        if device is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.uuid)}
            )
        self._last_document: dict[str, Any] | None = None
        self._seen_dose_uuids: set[str] | None = None
        self._total_sensor: PoolsideDoseTotalSensor | None = None

    @override
    def _status_keys(self) -> set[str]:
        """Return the body-of-water key its dose events arrive under."""
        return {self._body_of_water_uuid}

    @property
    @override
    def native_value(self) -> float | None:
        """Return the last dose's amount, in its own dosing unit."""
        return _dose_amount(self._last_known_document())

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the last dose's supporting details."""
        return _dose_extra_attributes(self._last_known_document())

    def _last_known_document(self) -> dict[str, Any] | None:
        """Return the newest dose document, keeping the last one on null."""
        document = _dose_event_document(
            self._client, self._body_of_water_uuid, self.entity_description.field
        )
        if document is not None:
            self._consider_document(document)
        return self._last_document

    def _consider_document(self, document: dict[str, Any]) -> None:
        """Record a dose document, feeding completed ones to the total."""
        self._last_document = document
        if self._total_sensor is None:
            return
        uuid = document.get("UUID")
        if not uuid or document.get("EndDateTime") is None:
            return
        if self._seen_dose_uuids is None:
            # The total sensor's restored state lists the dose UUIDs the
            # previous session already counted; trusting it fully (no
            # re-checking the current document) prevents a re-count when
            # the controller replays the last dose after a restart.
            self._seen_dose_uuids = set(self._total_sensor.restored_uuids)
        if uuid in self._seen_dose_uuids:
            return
        self._seen_dose_uuids.add(uuid)
        if (amount := _dose_amount(document)) is not None:
            self._total_sensor.add(amount)

    def reset(self) -> None:
        """Zero the running total and forget counted doses."""
        self._seen_dose_uuids = set()
        if self._total_sensor is not None:
            self._total_sensor.set_value(0.0)


class PoolsideDoseTotalSensor(
    PoolsideGroupEntity, RestoreEntity, SensorEntity
):
    """A running total of one chemical's dosed amounts since the last reset.

    The last-dose sensor reports each dose once (recognized by the dose
    document's UUID), and this sensor accumulates those amounts. The
    counter persists across HA restarts, so a tank's count-up continues
    until its reset button is pressed; an automation can trigger at a
    threshold to warn that a tank is nearly empty. Disabled by default:
    installs that prefer count-down from the vendor's tank tracking never
    see it.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        client: PoolsideClient,
        group: PoolsideGroup,
        body_of_water_uuid: str,
        dose_sensor: PoolsideBodyDoseSensor,
        device: PoolsideDevice | None = None,
    ) -> None:
        """Set up the dose-total sensor for one chemical of a body of water."""
        super().__init__(client, group)
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}"
            f"_{dose_sensor.entity_description.key}_total"
        )
        self._attr_translation_key = f"{dose_sensor.entity_description.key}_total"
        self._attr_native_unit_of_measurement = (
            dose_sensor.entity_description.native_unit_of_measurement
        )
        if device is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.uuid)}
            )
        self._dose_sensor = dose_sensor
        self._total: float = 0.0
        self._reset_at: str | None = None
        self._restored_uuids: set[str] = set()

    @override
    def _status_keys(self) -> set[str]:
        """Follow the last-dose sensor's pushes."""
        return {self._body_of_water_uuid}

    @property
    def restored_uuids(self) -> set[str]:
        """Dose UUIDs that the previous session had already counted."""
        return self._restored_uuids

    @property
    @override
    def native_value(self) -> float | None:
        """Return the dosed-since-reset total, in the chemical's unit."""
        return self._total

    def add(self, amount: float) -> None:
        """Add one dose's amount to the total."""
        self._total = round(self._total + amount, 3)
        self.async_write_ha_state()

    def set_value(self, value: float) -> None:
        """Set the total directly (the reset path)."""
        self._total = value
        self._reset_at = dt_util.now().isoformat(timespec="seconds")
        self.async_write_ha_state()

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return bookkeeping details for debugging the counter."""
        return {
            "last_reset": self._reset_at,
            "counted_dose_uuids": sorted(self._restored_uuids)
            if not self._dose_sensor._seen_dose_uuids
            else sorted(self._dose_sensor._seen_dose_uuids),
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the pre-restart total, then wire the feeding sensor."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            try:
                self._total = float(state.state)
            except (TypeError, ValueError):
                self._total = 0.0
            if (extra := state.attributes.get("counted_dose_uuids")) is not None:
                self._restored_uuids = set(extra)
            self._reset_at = state.attributes.get("last_reset")
        self._dose_sensor._total_sensor = self
        # Replay what's already known so totals survive restarts that
        # happened between the last dose and the next push.
        self._dose_sensor._consider_document(
            self._dose_sensor._last_document
        ) if self._dose_sensor._last_document else None


class PoolsideBodyWaterQualitySensor(PoolsideGroupEntity, SensorEntity):
    """A body of water's overall chemistry health (its WaterQualityState).

    Follows the body state sensor: an enum sensor over the raw FRIENDLY
    state values, with values the controller hasn't been observed to
    report mapped to no data instead of a state outside the options.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "water_quality_state"
    _attr_options = WATER_QUALITY_OPTIONS

    def __init__(
        self, client: PoolsideClient, group: PoolsideGroup, body_of_water_uuid: str
    ) -> None:
        """Set up the water quality sensor for a given body of water."""
        super().__init__(client, group)
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}_water_quality"
        )

    @override
    def _status_keys(self) -> set[str]:
        """Return the body-of-water key its quality arrives under."""
        return {self._body_of_water_uuid}

    @property
    @override
    def native_value(self) -> str | None:
        """Return the body of water's last reported water quality."""
        return _water_quality_value(
            self._client.get_status(
                self._body_of_water_uuid, WATER_QUALITY_STATE_FIELD
            )
        )


class PoolsideBodyEnergySensor(PoolsideGroupEntity, SensorEntity):
    """One total from a body of water's last-minute energy consumption.

    The document is absent on installs whose controller doesn't report it,
    and individual totals are absent when the corresponding fuel isn't in
    use; both render as no data rather than an error.
    """

    entity_description: PoolsideBodyEnergySensorDescription

    def __init__(
        self,
        client: PoolsideClient,
        group: PoolsideGroup,
        body_of_water_uuid: str,
        description: PoolsideBodyEnergySensorDescription,
        device: PoolsideDevice | None = None,
    ) -> None:
        """Set up one energy reading for a given body of water."""
        super().__init__(client, group)
        self.entity_description = description
        self._body_of_water_uuid = body_of_water_uuid
        self._attr_unique_id = (
            f"{client.controller_uuid}_{body_of_water_uuid}_{description.key}"
        )
        if device is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.uuid)}
            )

    @override
    def _status_keys(self) -> set[str]:
        """The body key and the site key: the item streams under the site.

        The controller carries `LastMinuteOfEnergyConsumption` on its site
        status (observed: the body-of-water item never carries it), but
        installs differ about where stream items live; the body key is
        kept first and the site covered with the same listener set.
        """
        keys = {self._body_of_water_uuid}
        if self._client.site_uuid:
            keys.add(self._client.site_uuid)
        return keys

    @property
    @override
    def native_value(self) -> float | None:
        """Return the last reported total, or no data if it isn't reported."""
        document = _energy_document(self._client, self._body_of_water_uuid)
        if document is None:
            return None
        return _float_value(document.get(self.entity_description.document_key))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the riding-along total, if this reading carries one."""
        if (key := self.entity_description.extra_attribute_key) is None:
            return None
        document = _energy_document(self._client, self._body_of_water_uuid)
        if document is None or (value := document.get(key)) is None:
            return None
        return {"liquid_propane_btu": value}


class PoolsideControlOnUntilSensor(PoolsideEntity, SensorEntity):
    """When a heater control's timed session ends (the "egg timer").

    OnUntil is the ISO end time of the timed session the control is
    running, null while none is. Added only for controls that actually
    report the item, so heaters without a timer never grow the entity.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "on_until"
    _use_translated_name = True

    def __init__(
        self,
        client: PoolsideClient,
        control: PoolsideControl,
        device: PoolsideDevice | None = None,
    ) -> None:
        """Set up the egg timer sensor for a heater control."""
        super().__init__(client, control)
        self._attr_unique_id = f"{client.controller_uuid}_{control.uuid}_on_until"
        self._attr_translation_placeholders = {"control_name": control.name}
        if device is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.uuid)}
            )

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the timed session's end time, or no data while none runs."""
        return _datetime_value(self._confirmed(ON_UNTIL_FIELD))


class PoolsideDeviceSensor(PoolsideDeviceEntity, SensorEntity):
    """One telemetry field of a physical pool device.

    Synthesized from the device's InformationFields descriptor rather than a
    fixed catalog: the descriptor names the status field and how to render
    it, and the value itself streams in under the device's UUID.
    """

    entity_description: PoolsideFieldSensorDescription

    def __init__(
        self, client: PoolsideClient, device: PoolsideDevice, field: dict[str, Any]
    ) -> None:
        """Set up one telemetry sensor from its InformationFields entry."""
        super().__init__(client, device)
        field_name: str = field[FIELD_NAME_KEY]
        self.entity_description = _device_field_description(field)
        self._attr_name = str(field.get(FIELD_DISPLAY_NAME_KEY) or field_name)
        self._attr_unique_id = f"{client.controller_uuid}_{device.uuid}_{field_name}"

    @property
    @override
    def native_value(self) -> float | str | datetime | None:
        """Return the last reported value, coerced per the field's processing logic."""
        value = self._client.get_status(self._device.uuid, self.entity_description.key)
        if value is None:
            return None
        return self.entity_description.value_fn(value)


class PoolsideDeviceTwinkleSensor(PoolsideDeviceSensor):
    """A TWINKLE field, translated through the device's TwinkleIncrements state.

    The raw Twinkle value is an increment the device's TwinkleIncrements
    document (a JSON array of {value, description} entries) gives a display
    name; a value the document doesn't list - or a missing/unparsable
    document - degrades to the raw value as text.
    """

    @property
    @override
    def native_value(self) -> str | None:
        """Return the increment's description, or the raw value as text."""
        value = self._client.get_status(self._device.uuid, self.entity_description.key)
        if value is None:
            return None
        return self._increment_description(value) or str(value)

    def _increment_description(self, value: Any) -> str | None:
        """Look the raw value up in the device's TwinkleIncrements document."""
        increments = self._client.get_status(
            self._device.uuid, TWINKLE_INCREMENTS_FIELD
        )
        if isinstance(increments, str):
            try:
                increments = json.loads(increments)
            except ValueError:
                LOGGER.warning(
                    "%s: unparsable %s: %r",
                    self._device.uuid,
                    TWINKLE_INCREMENTS_FIELD,
                    increments,
                )
                return None
        if not isinstance(increments, list):
            return None
        number = _float_value(value)
        for item in increments:
            if not isinstance(item, dict) or "description" not in item:
                continue
            candidate = item.get("value")
            if str(candidate) == str(value) or (
                number is not None and _float_value(candidate) == number
            ):
                return str(item["description"])
        return None


class PoolsideDevicePowerSensor(PoolsideDeviceEntity, SensorEntity):
    """Whether a physical pool device is actually running.

    ActualPowerState is ground truth from the hardware, pushed for every
    pool device independent of its InformationFields document, so this
    sensor exists from setup rather than waiting on the descriptor.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "actual_power_state"
    _attr_options = ON_OFF_OPTIONS

    def __init__(self, client: PoolsideClient, device: PoolsideDevice) -> None:
        """Set up the power state sensor for a pool device."""
        super().__init__(client, device)
        self._attr_unique_id = (
            f"{client.controller_uuid}_{device.uuid}_{ACTUAL_POWER_STATE_FIELD}"
        )

    @property
    @override
    def native_value(self) -> str | None:
        """Return on/off ground truth; UNKNOWN means the hardware can't confirm."""
        value = self._client.get_status(self._device.uuid, ACTUAL_POWER_STATE_FIELD)
        if value is None:
            return None
        return _on_off_value(value)


class PoolsideDeviceWinterizedSensor(PoolsideDeviceEntity, SensorEntity):
    """Whether a physical pool device has been taken offline for the season.

    Winterized is pushed for every pool device independent of its
    InformationFields document, so this sensor exists from setup rather
    than waiting on the descriptor.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "winterized"
    _attr_options = ["true", "false"]

    def __init__(self, client: PoolsideClient, device: PoolsideDevice) -> None:
        """Set up the winterized sensor for a pool device."""
        super().__init__(client, device)
        self._attr_unique_id = (
            f"{client.controller_uuid}_{device.uuid}_{WINTERIZED_FIELD}"
        )

    @property
    @override
    def native_value(self) -> str | None:
        """Return true/false, or no data if the field is absent or malformed."""
        value = self._client.get_status(self._device.uuid, WINTERIZED_FIELD)
        if (winterized := _bool_value(value)) is None:
            return None
        return "true" if winterized else "false"


class PoolsideControlDisabledReasonSensor(PoolsideEntity, SensorEntity):
    """Why a control is out of service, or none while it is operable.

    Deliberately NOT gated on the control's own availability - it exists to
    explain why the control's entity went unavailable, so it only follows
    the connection. Site-wide INSTALLER mode is explained by the site mode
    sensor instead, and covers every control at once, so it isn't repeated
    here.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "disabled_reason"
    _attr_options = ["none", "winterized", "freeze_protect", "pool_cover"]
    _use_translated_name = True

    def __init__(self, client: PoolsideClient, control: PoolsideControl) -> None:
        """Set up the disabled reason sensor for a control."""
        super().__init__(client, control)
        self._attr_unique_id = (
            f"{client.controller_uuid}_{control.uuid}_disabled_reason"
        )
        self._attr_translation_placeholders = {"control_name": control.name}

    @property
    @override
    def available(self) -> bool:
        """Return True while connected, regardless of the control's own state."""
        return self._client.available

    @property
    @override
    def native_value(self) -> str:
        """Return the strongest reason the control is out of service."""
        reasons = self._disabled_reasons()
        if self._control.winterized or WINTERIZED_REASON in reasons:
            return "winterized"
        if FREEZE_PROTECT_REASON in reasons:
            return "freeze_protect"
        if reasons:
            return "pool_cover"
        return "none"


class PoolsideSiteModeSensor(PoolsideBaseEntity, SensorEntity):
    """The controller's site-wide operating mode.

    Read-only: the mode is changed on the controller itself (or by an
    installer), never from Home Assistant. Deliberately NOT gated on the
    mode itself - it must stay readable in INSTALLER mode so the user can
    see why all their controls are unavailable.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "site_mode"
    _attr_options = [mode.value.lower() for mode in SiteMode]

    def __init__(
        self, client: PoolsideClient, site: PoolsideSite, site_uuid: str
    ) -> None:
        """Set up the mode sensor on its own controller-level device."""
        super().__init__(client)
        self._site_uuid = site_uuid
        self._attr_unique_id = f"{client.controller_uuid}_{SITE_MODE_KEY}"
        # Keyed by the stable controller UUID, not the site UUID, which
        # changes whenever the attendant edits the site's configuration.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.controller_uuid)},
            name=site.name,
            manufacturer="Poolside",
            model="Controller",
        )

    @override
    def _status_keys(self) -> set[str]:
        """Return the site key its Mode pushes arrive under."""
        return {self._site_uuid}

    @property
    @override
    def native_value(self) -> str | None:
        """Return the last reported site mode."""
        if (value := self._client.site_mode) is None:
            return None
        try:
            return SiteMode(value).value.lower()
        except ValueError:
            return None
