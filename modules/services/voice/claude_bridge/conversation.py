import logging
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SESSION_TTL, TIMEOUT, endpoint, error_speech
from .history import Forwarded, pending_turns

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ClaudeBridgeEntity(entry)])


class ClaudeBridgeEntity(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    _attr_has_entity_name = True
    # No device, so the name gives the entity ID: conversation.claude
    _attr_name = "Claude"

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self.forwarded = Forwarded(SESSION_TTL)
        self.socket_session: aiohttp.ClientSession | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        if self.socket_session:
            await self.socket_session.close()
        await super().async_will_remove_from_hass()

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        conv_id = chat_log.conversation_id
        entries = [(c.role, getattr(c, "content", None)) for c in chat_log.content]
        speech = await self._ask(
            {
                "text": user_input.text,
                "language": user_input.language,
                "conversation_id": conv_id,
                # turns HA handled locally, so a follow-up like "and the right one too" has context
                "earlier": pending_turns(entries, self.forwarded.get(conv_id)),
            }
        )
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        self.forwarded.set(conv_id, len(chat_log.content))
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _ask(self, payload: dict) -> str:
        socket_path, base_url = endpoint(self.entry.data[CONF_URL])
        if socket_path is None:
            session = async_get_clientsession(self.hass)
        else:
            if self.socket_session is None:
                self.socket_session = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=socket_path))
            session = self.socket_session
        try:
            async with session.post(
                base_url + "/converse",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                return (await resp.json())["speech"]
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
            _LOGGER.warning("Voice bridge failed: %s", type(err).__name__)
            return error_speech(payload["language"])
