"""sensor.voice_jobs: the number of active voice jobs, with the list for the dashboard"""

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SIGNAL_JOBS


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([VoiceJobsSensor(entry, hass.data[DOMAIN]["scheduler"])])


class VoiceJobsSensor(SensorEntity):
    _attr_name = "Voice jobs"
    _attr_icon = "mdi:clipboard-clock-outline"
    _attr_should_poll = False
    # the lists change often and are kept in .storage anyway
    _unrecorded_attributes = frozenset({"jobs", "recent", "leases"})

    def __init__(self, entry: ConfigEntry, scheduler) -> None:
        self.scheduler = scheduler
        self._attr_unique_id = f"{entry.entry_id}_voice_jobs"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_JOBS, self.async_write_ha_state))

    @property
    def native_value(self) -> int:
        return len(self.scheduler.listing()["jobs"])

    @property
    def extra_state_attributes(self) -> dict:
        return self.scheduler.listing()
