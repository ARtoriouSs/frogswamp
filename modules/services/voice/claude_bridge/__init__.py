"""Claude bridge: a conversation agent that passes Assist requests to the local voice bridge, and the
voice jobs Claude schedules (jobs.py, scheduler.py)"""

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import DEFAULT_URL, DOMAIN, OLD_DEFAULT_URL
from .scheduler import JobScheduler

PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    scheduler = JobScheduler(hass)
    await scheduler.async_load()
    hass.data.setdefault(DOMAIN, {})["scheduler"] = scheduler

    # errors come back as {"error": ...}, so the scripts pass them to Claude instead of failing
    async def add_job(call: ServiceCall) -> ServiceResponse:
        return await scheduler.add(call.data["job"])

    async def list_jobs(call: ServiceCall) -> ServiceResponse:
        return scheduler.listing()

    async def cancel_job(call: ServiceCall) -> ServiceResponse:
        return await scheduler.cancel(call.data["job_id"])

    hass.services.async_register(
        DOMAIN,
        "add_job",
        add_job,
        # a script's "{{ job }}" template turns the JSON text into a dict
        vol.Schema({vol.Required("job"): vol.Any(dict, cv.string)}),
        SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, "list_jobs", list_jobs, vol.Schema({}), SupportsResponse.ONLY)
    hass.services.async_register(
        DOMAIN, "cancel_job", cancel_job, vol.Schema({vol.Required("job_id"): cv.string}), SupportsResponse.ONLY
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        data = dict(entry.data)
        if data.get(CONF_URL) == OLD_DEFAULT_URL:
            data[CONF_URL] = DEFAULT_URL
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop("scheduler").async_stop()
        for service in ("add_job", "list_jobs", "cancel_job"):
            hass.services.async_remove(DOMAIN, service)
    return ok
