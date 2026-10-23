import pytest
import pytest_socket
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cubematrix import protocol
from custom_components.cubematrix.const import CONF_MUSIC_PORT, DOMAIN

from .fake_lamp import FakeLamp, free_port


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    # Only tests that use hass need it, the pure core tests stay without HA
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture
def loopback(socket_enabled):
    """Allow sockets to the whole 127.0.0.0/8 range, the fake lamps live there"""
    pytest_socket.socket_allow_hosts(["127.0.0.0/8"], allow_unix_socket=True)


@pytest.fixture
async def fake_lamp(loopback, monkeypatch):
    """A fake lamp on 127.0.0.2, the integration talks to its LAN port"""
    fake = FakeLamp("127.0.0.2")
    await fake.start()
    monkeypatch.setattr(protocol, "LAN_PORT", fake.port)
    yield fake
    await fake.stop()


@pytest.fixture
async def entry(hass, fake_lamp):
    """A loaded entry for the fake lamp, with a free music port"""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cube Matrix (127.0.0.2)",
        data={"host": fake_lamp.ip},
        options={CONF_MUSIC_PORT: free_port("0.0.0.0")},
        unique_id=fake_lamp.ip,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
