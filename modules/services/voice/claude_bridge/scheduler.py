"""Runs voice jobs: keeps them in .storage, watches the states they need, runs their actions, sends pushes.
The rules are in jobs.py; this file only connects them to Home Assistant"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import jobs as J
from .const import NOTIFY_SERVICE, SIGNAL_JOBS, STORE_KEY, STORE_VERSION

_LOGGER = logging.getLogger(__name__)

# a lease that ended: check after 1 min, then try again every 5 min, 12 tries in all
LEASE_CHECK = timedelta(minutes=1)
LEASE_RETRY = timedelta(minutes=5)
LEASE_TRIES = 12


class HassHome:
    """What parse_job needs to know about the home"""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def exists(self, entity_id: str) -> bool:
        return self.hass.states.get(entity_id) is not None

    def exposed(self, entity_id: str) -> bool:
        return self.exists(entity_id) and async_should_expose(self.hass, "conversation", entity_id)

    def _area_ids(self, area: str) -> list[str]:
        wanted = area.strip().casefold()
        return [
            a.id
            for a in ar.async_get(self.hass).async_list_areas()
            if wanted in {a.id.casefold(), a.name.casefold(), *(alias.casefold() for alias in a.aliases)}
        ]

    def _entities_in(self, area_ids: list[str]) -> set[str]:
        entities = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        out = set()
        for area_id in area_ids:
            out.update(e.entity_id for e in er.async_entries_for_area(entities, area_id))
            for device in dr.async_entries_for_area(devices, area_id):
                out.update(
                    e.entity_id
                    for e in er.async_entries_for_device(entities, device.id)
                    # an entity with its own area belongs there, not to its device's area
                    if e.area_id is None
                )
        return out

    def presence_sensors(self, area: str) -> list[str]:
        out = []
        for entity_id in sorted(self._entities_in(self._area_ids(area))):
            state = self.hass.states.get(entity_id)
            if entity_id.startswith("binary_sensor.") and state is not None:
                if state.attributes.get("device_class") in ("occupancy", "presence"):
                    out.append(entity_id)
        return out

    def satellites(self, area: str | None) -> list[str]:
        everywhere = sorted(self.hass.states.async_entity_ids("assist_satellite"))
        if area is None:
            return everywhere
        here = self._entities_in(self._area_ids(area))
        return [e for e in everywhere if e in here]


def _clock(moment: datetime) -> str:
    return dt_util.as_local(moment).strftime("%H:%M")


class JobScheduler:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.home = HassHome(hass)
        self.store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self.jobs: list[J.Job] = []
        self.leases: list[J.Lease] = []
        self.started = False
        self.lock = asyncio.Lock()
        self._watch: dict[str, list] = {}
        self._lease_timer = None
        self._stop_start = None

    # --- life

    async def async_load(self) -> None:
        self.jobs, self.leases = J.load(await self.store.async_load() or {})
        # wait for the restored states, so a job never runs on the empty states of a starting HA
        self._stop_start = async_at_started(self.hass, self._on_started)

    async def _on_started(self, hass: HomeAssistant) -> None:
        self._stop_start = None
        self.started = True
        await self._lease_pass()
        for job in list(self.jobs):
            await self._evaluate(job.id)
        await self._save()

    @callback
    def async_stop(self) -> None:
        if self._stop_start:
            self._stop_start()
        for job_id in list(self._watch):
            self._unwatch(job_id)
        if self._lease_timer:
            self._lease_timer()
            self._lease_timer = None

    # --- services

    async def add(self, raw: str | dict) -> dict:
        if not self.started:
            return {"error": "Home Assistant is still starting, try again in a minute"}
        active = sum(1 for job in self.jobs if J.is_active(job))
        try:
            job = J.parse_job(raw, dt_util.now(), self.home, uuid.uuid4().hex[:8], active)
        except J.JobError as err:
            return {"error": str(err)}
        self.jobs.append(job)
        await self._save()
        await self._evaluate(job.id)
        summary = f"{job.title}: {J.step_label(job)}"
        if J.is_active(job):
            summary += f", истекает {dt_util.as_local(job.expires).strftime('%d.%m %H:%M')}"
        return {"id": job.id, "summary": summary}

    def listing(self) -> dict:
        def row(job):
            return {
                "id": job.id,
                "title": job.title,
                "step": J.step_label(job),
                "expires": dt_util.as_local(job.expires).strftime("%d.%m %H:%M"),
            }

        return {
            "jobs": [row(job) for job in self.jobs if J.is_active(job)],
            "recent": [row(job) for job in self.jobs if not J.is_active(job)],
            "leases": [
                {"entity_id": lease.entity_id, "title": lease.title, "ends": _clock(lease.ends)}
                for lease in self.leases
            ],
        }

    async def cancel(self, job_id: str) -> dict:
        async with self.lock:
            chosen = [
                job for job in self.jobs if J.is_active(job) and (job_id == "all" or job.id == job_id.strip())
            ]
            if not chosen:
                return {"error": f"no active job {job_id}, see script.voice_jobs for the ids"}
            now = dt_util.now()
            for job in chosen:
                job.status = "cancelled"
                job.finished_at = now
                self._unwatch(job.id)
        await self._save()
        return {"cancelled": [job.title for job in chosen]}

    # --- jobs

    def _state(self, entity_id: str) -> J.EntityState | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return J.EntityState(state.state, dict(state.attributes), state.last_changed)

    def _find(self, job_id: str) -> J.Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    def _unwatch(self, job_id: str) -> None:
        for stop in self._watch.pop(job_id, []):
            stop()

    async def _evaluate(self, job_id: str) -> None:
        async with self.lock:
            job = self._find(job_id)
            if job is None or not self.started:
                return
            status = job.status
            step = J.advance(job, dt_util.now(), self._state)
            self._unwatch(job_id)
            if step.run or step.expired or job.status != status:
                # saved before the actions run, so a crash can't run a job twice
                await self._save()
            if step.expired:
                self._push("Задание истекло", job.title, job.id)
                return
            if step.run:
                await self._run(job)
                return
            if J.is_active(job):
                self._watch_job(job, step.next_check)

    def _watch_job(self, job: J.Job, next_check: datetime | None) -> None:
        @callback
        def again(*_):
            self.hass.async_create_task(self._evaluate(job.id))

        stops = []
        entities = sorted(J.entities_of(job))
        if entities:
            stops.append(async_track_state_change_event(self.hass, entities, again))
        if next_check is not None:
            stops.append(async_track_point_in_time(self.hass, again, next_check))
        self._watch[job.id] = stops

    async def _run(self, job: J.Job) -> None:
        done, failed, lease_end = [], [], None
        for action in job.do:
            name = self._name(action.get("entity_id"))
            try:
                end = await self._act(job, action)
                done.append(J.describe_action(action, name))
                if end is not None:
                    lease_end = max(lease_end, end) if lease_end else end
            except (HomeAssistantError, ValueError, KeyError) as err:
                _LOGGER.warning("Voice job %s: %s failed: %s", job.id, action["type"], err)
                failed.append(f"{J.describe_action(action, name)} ({err})")
        if J.needs_summary(job) or failed:
            self._push(*J.push_text(job, done, failed, lease_end), job.id)

    def _name(self, entity_id: str | None) -> str | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.name if state else None

    async def _act(self, job: J.Job, action: dict) -> datetime | None:
        """Runs one action; returns when what it turned on will be turned off, if it has an end"""
        kind = action["type"]
        call = self.hass.services.async_call
        if kind == "heat":
            off_sensor = self.hass.states.get(f"binary_sensor.heat_off_{action['device']}")
            if off_sensor is None or off_sensor.state in J.UNKNOWN:
                raise HomeAssistantError("нет связи с устройством")
            minutes = int(action["minutes"])
            await call(
                "script",
                "heat_lease",
                {"device": action["device"], "duration": f"{minutes // 60:02d}:{minutes % 60:02d}:00", "force": True},
                blocking=True,
            )
            return dt_util.now() + timedelta(minutes=minutes)
        if kind == "turn_on":
            await call("homeassistant", "turn_on", {"entity_id": action["entity_id"]}, blocking=True)
            if "minutes" not in action:
                return None
            ends = dt_util.now() + timedelta(minutes=action["minutes"])
            self.leases = J.add_lease(self.leases, J.Lease(action["entity_id"], ends, job.title, job.id))
            await self._save()
            self._schedule_leases()
            return ends
        if kind == "turn_off":
            await call("homeassistant", "turn_off", {"entity_id": action["entity_id"]}, blocking=True)
        elif kind == "set_temperature":
            await call(
                "climate",
                "set_temperature",
                {"entity_id": action["entity_id"], "temperature": action["temperature"]},
                blocking=True,
            )
        elif kind == "notify":
            self._push("Таймер" if job.title.startswith("Таймер") else "Напоминание", action["message"], job.id)
        elif kind == "announce":
            await call("assist_satellite", "announce", {"entity_id": action["entities"], "message": action["message"]},
                       blocking=True)
        return None

    # --- leases

    def _schedule_leases(self) -> None:
        if self._lease_timer:
            self._lease_timer()
            self._lease_timer = None
        if self.leases:
            @callback
            def due(_now):
                self._lease_timer = None
                self.hass.async_create_task(self._lease_pass())

            self._lease_timer = async_track_point_in_time(self.hass, due, min(lease.ends for lease in self.leases))

    async def _lease_pass(self) -> None:
        now = dt_util.now()
        for lease in [lease for lease in self.leases if lease.ends <= now]:
            state = self.hass.states.get(lease.entity_id)
            if state is not None and state.state == "off":
                # turned off by someone, or our last try worked
                self.leases.remove(lease)
                continue
            if lease.tries >= LEASE_TRIES:
                self.leases.remove(lease)
                self._push("Не выключилось", f"{lease.title}: «{self._name(lease.entity_id) or lease.entity_id}» "
                           "не выключилось после часа попыток, проверь", lease.job_id)
                continue
            try:
                await self.hass.services.async_call(
                    "homeassistant", "turn_off", {"entity_id": lease.entity_id}, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Lease of %s: turn off failed: %s", lease.entity_id, err)
            lease.ends = now + (LEASE_CHECK if lease.tries == 0 else LEASE_RETRY)
            lease.tries += 1
        await self._save()
        self._schedule_leases()

    # --- storage and pushes

    async def _save(self) -> None:
        self.jobs = J.prune(self.jobs, dt_util.now())
        await self.store.async_save(J.dump(self.jobs, self.leases))
        async_dispatcher_send(self.hass, SIGNAL_JOBS)

    def _push(self, title: str, message: str, tag: str) -> None:
        self.hass.async_create_task(
            self.hass.services.async_call(
                "notify",
                NOTIFY_SERVICE,
                {"title": title, "message": message, "data": {"tag": f"voice-job-{tag}"}},
            )
        )
