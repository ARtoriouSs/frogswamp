from typing import Any

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import DEFAULT_URL, DOMAIN


class ClaudeBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    # 2: the bridge moved to a Unix socket
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(
                title="Claude bridge", data={CONF_URL: user_input[CONF_URL].strip().rstrip("/")}
            )
        schema = probatio.Schema(
            {
                probatio.Required(CONF_URL, default=DEFAULT_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
