"""Constants for the Poolside integration."""

import logging

from aiopoolside.const import ControlType

DOMAIN = "poolside"

LOGGER = logging.getLogger(__package__)

ZEROCONF_PROP_UUID = "uuid"
ZEROCONF_PROP_NAME = "name"

CONF_CLIENT_PRIVATE_KEY = "client_private_key"
CONF_CONTROLLER_PUBLIC_KEY = "controller_public_key"
CONF_CONTROLLER_UUID = "controller_uuid"
CONF_CLIENT_NAME = "client_name"

# Options
CONF_EXPOSE_POOL_DEVICES = "expose_pool_devices"
DEFAULT_EXPOSE_POOL_DEVICES = True

# Icon-only translation keys per control type, applied by both the fan and
# switch platforms (a control can render on either); the icons live in
# icons.json under both domains. Names still come from the control.
ICON_TRANSLATION_KEYS = {
    ControlType.WATER_FEATURE: "water_feature",
    ControlType.FILTER: "filter",
}

# unique_id suffix of the site mode sensor - the one entity not keyed to a
# control or body-of-water UUID from the layout.
SITE_MODE_KEY = "site_mode"

# Locally pushed status items (Device.setStatus) not yet named by
# aiopoolside. The first four are keyed by the body-of-water UUID; OnUntil
# by a TEMPERATURE control's own keys. Documents arrive as JSON encoded
# inside the string value, like other capability documents.

# The body of water's most recent chemical dose event, per chemical type.
# The salt-chlorinator item is only reported when the site's equipment
# actually chlorinates with salt.
ACID_MIX_EVENT_FIELD = "CurrentAcidMixEvent"
LIQUID_CHLORINE_MIX_EVENT_FIELD = "CurrentLiquidChlorineMixEvent"
SALT_CHLORINATOR_MIX_EVENT_FIELD = "CurrentSaltChlorinatorMixEvent"

# The body of water's overall chemistry health (GOOD/OK/LOW/...).
WATER_QUALITY_STATE_FIELD = "WaterQualityState"

# A JSON document with the energy the body of water's equipment consumed
# over the last minute (electric watts plus gas/propane Btu).
LAST_MINUTE_OF_ENERGY_CONSUMPTION_FIELD = "LastMinuteOfEnergyConsumption"

# A TEMPERATURE control's timed-session end time ("egg timer"), null while
# no timed session is running.
ON_UNTIL_FIELD = "OnUntil"

# Keys inside a Current*MixEvent document.
DOSE_AMOUNT_KEY = "DoseAmount"
DOSE_SECONDS_KEY = "DoseSeconds"
DOSE_UNIT_KEY = "DosingUnit"
MIX_MINUTES_KEY = "MixMinutes"
DOSE_START_KEY = "StartDateTime"
DOSE_END_KEY = "EndDateTime"
DOSE_INTERRUPTED_KEY = "InterruptedAt"
DOSE_PROBE_BEFORE_KEY = "Before"
DOSE_CUT_SHORT_KEY = "DosingCutShort"
DOSE_MANUAL_KEY = "ManuallyInitiatedEvent"

# Keys inside a LastMinuteOfEnergyConsumption document.
ENERGY_WATTS_KEY = "TotalWattsUsed"
ENERGY_NATURAL_GAS_KEY = "TotalNaturalGasBtuUsed"
ENERGY_LIQUID_PROPANE_KEY = "TotalLiquidPropaneBtuUsed"
