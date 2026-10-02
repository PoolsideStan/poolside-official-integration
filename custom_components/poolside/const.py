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

# Body-of-water field describing an ongoing heat-up (or cool-down) toward the
# set point, including the controller's predicted completion time. Not in
# aiopoolside.const as of 0.1.0; import from there once it is.
TEMPERATURE_RISE_INFORMATION_FIELD = "TemperatureRiseInformation"
TEMPERATURE_RISE_TARGET_END_KEY = "TargetEndDateTime"
TEMPERATURE_RISE_STATE_KEY = "TemperatureState"
TEMPERATURE_RISE_EVENT_TYPE_KEY = "HeatingEventType"
# The vendor app only shows a "ready in" time for a sampled, normal heat-up.
TEMPERATURE_RISE_SAMPLED = "SAMPLED"
TEMPERATURE_RISE_NORMAL = "NORMAL"
