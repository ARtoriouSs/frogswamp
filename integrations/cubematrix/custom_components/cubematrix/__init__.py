"""The Yeelight Cube Matrix integration"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .connection import CubeConnection
from .const import (
    CONF_DIRECTION,
    CONF_MODULES,
    CONF_MUSIC_PORT,
    DEFAULT_DIRECTION,
    DEFAULT_MODULES,
    DEFAULT_MUSIC_PORT,
    DOMAIN,
)
from .data import DATA_KEY, CubeMatrixData, CubeRuntime
from .layout import Layout, parse_modules
from .player import Player
from .protocol import LampError, get_props

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The light, select and sensor platforms come with their modules in the next step
PLATFORMS: list[Platform] = []

type CubeMatrixConfigEntry = ConfigEntry[CubeRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data[DATA_KEY] = await CubeMatrixData.async_load(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CubeMatrixConfigEntry) -> bool:
    # Only check that the lamp answers. It is turned on and put in direct mode
    # when the light turns on, not when HA starts
    try:
        await get_props(entry.data[CONF_HOST], ["power"])
    except LampError as err:
        raise ConfigEntryNotReady(str(err)) from err
    options = entry.options
    layout = Layout(
        modules=parse_modules(options.get(CONF_MODULES, DEFAULT_MODULES)),
        direction=options.get(CONF_DIRECTION, DEFAULT_DIRECTION),
    )
    connection = CubeConnection(
        entry.data[CONF_HOST], options.get(CONF_MUSIC_PORT, DEFAULT_MUSIC_PORT)
    )
    player = Player(connection, layout, hass.data[DATA_KEY].library)
    entry.runtime_data = CubeRuntime(connection=connection, player=player)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CubeMatrixConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = entry.runtime_data
    # The player goes first, so nothing uses the connection after it closes
    await runtime.player.shutdown()
    await runtime.connection.close()
    return True
