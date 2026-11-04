import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import ModelSlotConfig, Settings
from app.core.errors import ChatError
from app.schemas.message import AgentConfigRequest
from app.services.ai_message_service import agent_status
from app.services.model_config_service import configured_slots, resolve_model

CONFIG_ID = "11111111-1111-4111-8111-111111111111"


class ModelResolutionTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings(_env_file=None)
        self.enterContext(
            patch("app.services.model_config_service.config", self.settings)
        )
        self.row = {
            "id": CONFIG_ID,
            "provider_name": "custom",
            "model_name": "stored-model",
            "base_url": "https://example.test/v1",
            "model_temperature": 0.4,
            "is_public": False,
            "credential_ref": None,
        }
        self.binding = self.enterContext(
            patch("app.services.model_config_service.select_model_binding")
        )
        self.binding.return_value = CONFIG_ID
        self.select = self.enterContext(
            patch("app.services.model_config_service.select_model_config_by_id")
        )
        self.select.return_value = self.row

    def test_explicit_configuration_uses_stored_connection_and_client_key(self):
        effective = resolve_model(
            "user-1",
            AgentConfigRequest(
                config_id=CONFIG_ID,
                api_key="client-key",
                model_name="forged-model",
                base_url="https://wrong.invalid",
            ),
        )
        self.assertEqual(effective.model_name, "stored-model")
        self.assertEqual(effective.base_url, self.row["base_url"])
        self.assertEqual(effective.model_temperature, 0.4)
        self.assertEqual(effective.api_key, "client-key")
        self.binding.assert_not_called()

    def test_unavailable_bound_model_is_an_error_and_does_not_fall_back(self):
        self.select.return_value = None
        with self.assertRaises(ChatError) as error:
            resolve_model("user-1", preflight=True)
        self.assertEqual(error.exception.code, "MODEL_CONFIG_NOT_FOUND")
        self.binding.assert_called_once_with(scope_key="user:user-1", slot="dialogue")

    def test_private_configuration_can_preflight_but_requires_key_to_send(self):
        effective = resolve_model("user-1", preflight=True)
        self.assertIsNone(effective.api_key)
        with self.assertRaises(ChatError) as error:
            resolve_model("user-1")
        self.assertEqual(error.exception.code, "MODEL_CREDENTIAL_REQUIRED")

    @patch.dict("os.environ", {"LUMINA_TEST_KEY": "public-key"})
    def test_public_default_can_be_resolved_without_a_user(self):
        self.row.update(is_public=True, credential_ref="env:LUMINA_TEST_KEY")
        effective = resolve_model(None, preflight=True)
        self.assertEqual(effective.api_key, "public-key")
        self.select.assert_called_once_with(config_id=CONFIG_ID, user_id=None)

    @patch.dict("os.environ", {"LUMINA_TEST_KEY": "public-key"})
    def test_public_configuration_ignores_client_key(self):
        self.row.update(is_public=True, credential_ref="env:LUMINA_TEST_KEY")
        effective = resolve_model(
            "user-1", AgentConfigRequest(config_id=CONFIG_ID, api_key="client-key")
        )
        self.assertEqual(effective.api_key, "public-key")

    @patch("app.integrations.database.user_space_repository.mysql_service.select")
    def test_status_preflights_model_without_querying_table_metadata(self, select):
        result = agent_status("user-1")
        self.assertTrue(result["available"])
        self.assertEqual(result["code"], "READY")
        self.assertTrue(result["requiresClientKey"])
        self.assertFalse(result["upstreamVerified"])
        select.assert_not_called()
        self.binding.assert_called_once_with(scope_key="user:user-1", slot="dialogue")

    def configure_environment(self):
        self.binding.return_value = None
        self.settings.models.dialogue = ModelSlotConfig(
            model="shared-chat",
            base_url="https://shared.test/v1",
            api_key="shared-secret",
        )

    def test_shared_environment_supports_all_four_agents_without_saved_configs(self):
        self.configure_environment()
        for slot in ("dialogue", "memory", "retrieval", "event"):
            with self.subTest(slot=slot):
                effective = resolve_model("user-1", slot=slot)
                self.assertEqual(effective.provider_name, "custom")
                self.assertEqual(effective.model_name, "shared-chat")
                self.assertIsNone(effective.config_id)
        self.select.assert_not_called()

    def test_specialist_environment_models_share_connection_but_keep_own_model(self):
        self.configure_environment()
        for slot in (
            "memory",
            "retrieval",
            "event",
            "vision",
            "image",
            "asr",
            "tts",
            "audio_emotion",
        ):
            setattr(
                self.settings.models, slot, ModelSlotConfig(model=slot, temperature=0.3)
            )
            effective = resolve_model("user-1", slot=slot)
            self.assertEqual(effective.provider_name, "custom")
            self.assertEqual(effective.model_name, slot)
            self.assertEqual(effective.base_url, "https://shared.test/v1")
            self.assertEqual(effective.api_key, "shared-secret")
            self.assertEqual(effective.model_temperature, 0.3)

    def test_slot_connection_can_be_overridden_and_reaches_chat_and_media_adapters(
        self,
    ):
        from app.llm.factory import get_llm_node
        from app.llm.media import media_connection

        self.configure_environment()
        self.settings.models.dialogue = ModelSlotConfig(
            model="other-model",
            base_url="https://other.test/v1",
            api_key="other-key",
            temperature=0.2,
        )
        effective = resolve_model("user-1")
        self.assertEqual(
            media_connection(effective),
            ("https://other.test/v1", "other-key", "other-model"),
        )
        with patch("app.llm.factory.get_openai_model") as create:
            get_llm_node(
                provider=effective.provider_name,
                model=effective.model_name,
                api_key=effective.api_key,
                config=effective,
            )
        self.assertEqual(
            create.call_args.args[:3],
            ("other-model", "other-key", "https://other.test/v1"),
        )
        self.assertNotIn("api_key", effective.model_dump())
        self.assertNotIn("other-key", repr(self.settings))

    def test_environment_slot_precedes_public_default(self):
        self.configure_environment()
        self.binding.side_effect = (
            lambda **kw: CONFIG_ID if kw["scope_key"] == "system" else None
        )
        self.settings.models.dialogue = self.settings.models.dialogue.model_copy(
            update={"model": "env-chat"}
        )
        self.assertEqual(resolve_model("user-1").model_name, "env-chat")
        self.select.assert_not_called()
        self.binding.assert_called_once_with(scope_key="user:user-1", slot="dialogue")

    def test_explicit_and_personal_choice_precede_environment(self):
        self.settings.models.dialogue = ModelSlotConfig(model="env-chat")
        explicit = resolve_model(
            "user-1", AgentConfigRequest(config_id=CONFIG_ID, api_key="client-key")
        )
        self.assertEqual(explicit.model_name, "stored-model")
        personal = resolve_model("user-1", preflight=True)
        self.assertEqual(personal.model_name, "stored-model")

    def test_fixed_slots_ignore_personal_bindings_and_request_credentials(self):
        self.configure_environment()
        self.binding.side_effect = (
            lambda **kw: CONFIG_ID if kw["scope_key"] == "user:user-1" else None
        )
        request = AgentConfigRequest(config_id=CONFIG_ID, api_key="client-key")
        self.settings.models.tts = ModelSlotConfig(model="fixed-voice")
        for slot in ("memory", "retrieval", "event", "tts"):
            effective = resolve_model("user-1", request, slot=slot)
            self.assertIsNone(effective.config_id)
            self.assertNotEqual(effective.api_key, "client-key")
        self.select.assert_not_called()
        self.assertTrue(
            all(c.kwargs["scope_key"] == "system" for c in self.binding.call_args_list)
        )

    def test_fixed_public_slot_can_only_read_a_public_model(self):
        self.row.update(is_public=True, credential_ref="env:FIXED_MODEL_TEST_KEY")
        with patch.dict(os.environ, {"FIXED_MODEL_TEST_KEY": "fixed-key"}):
            effective = resolve_model(
                "user-1", AgentConfigRequest(api_key="client-key"), slot="memory"
            )
        self.assertEqual(effective.api_key, "fixed-key")
        self.select.assert_called_once_with(config_id=CONFIG_ID, user_id=None)

    def test_database_default_uses_unified_environment_and_ignores_client_key(self):
        self.configure_environment()
        self.row["provider_name"] = "default"
        effective = resolve_model(
            "user-1", AgentConfigRequest(config_id=CONFIG_ID, api_key="client-key")
        )
        self.assertEqual(effective.model_name, "shared-chat")
        self.assertEqual(effective.api_key, "shared-secret")
        self.assertEqual(effective.config_id, CONFIG_ID)

    def test_default_factory_and_media_read_the_same_dialogue_slot(self):
        from app.llm.factory import get_llm_node
        from app.llm.media import media_connection

        self.configure_environment()
        with (
            patch("app.llm.factory.settings", self.settings),
            patch("app.llm.factory.get_openai_model") as create,
        ):
            get_llm_node(provider="default")
        self.assertEqual(
            create.call_args.args[:3],
            ("shared-chat", "shared-secret", "https://shared.test/v1"),
        )
        with patch("app.llm.media.config", self.settings):
            self.assertEqual(
                media_connection(AgentConfigRequest()),
                ("https://shared.test/v1", "shared-secret", "shared-chat"),
            )

    def test_user_dialogue_switch_does_not_change_fixed_agents_or_media(self):
        self.configure_environment()
        self.settings.models.tts = ModelSlotConfig(model="fixed-voice")
        self.binding.side_effect = (
            lambda **kw: CONFIG_ID
            if kw["scope_key"] == "user:user-1" and kw["slot"] == "dialogue"
            else None
        )
        self.row["credential_ref"] = "env:FIXED_MODEL_TEST_KEY"
        with patch.dict(os.environ, {"FIXED_MODEL_TEST_KEY": "personal-key"}):
            first = {
                slot: resolve_model("user-1", slot=slot)
                for slot in ("dialogue", "memory", "retrieval", "event", "tts")
            }
            self.row["model_name"] = "another-personal-chat"
            second = {slot: resolve_model("user-1", slot=slot) for slot in first}
        self.assertNotEqual(first["dialogue"].model_name, second["dialogue"].model_name)
        for slot in ("memory", "retrieval", "event", "tts"):
            self.assertEqual(first[slot], second[slot])

    def test_missing_multimodal_slot_never_uses_a_text_model(self):
        self.configure_environment()
        for slot in ("vision", "image", "asr", "tts", "audio_emotion"):
            with self.subTest(slot=slot), self.assertRaises(ChatError) as error:
                resolve_model("user-1", slot=slot)
            self.assertEqual(error.exception.code, "CAPABILITY_NOT_CONFIGURED")

    def test_invalid_or_incomplete_environment_slot_fails_without_fallback(self):
        self.binding.return_value = None
        self.settings.models.memory = ModelSlotConfig(
            model="memory-model", base_url="https://model.test/v1"
        )
        with self.assertRaises(ChatError) as error:
            resolve_model("user-1", slot="memory", preflight=True)
        self.assertEqual(error.exception.code, "MODEL_CREDENTIAL_REQUIRED")
        self.settings.models.memory.api_key = "key"
        self.settings.models.memory.base_url = "not-a-url"
        with self.assertRaises(ValueError):
            resolve_model("user-1", slot="memory")

    def test_local_ollama_environment_does_not_require_api_key(self):
        self.binding.return_value = None
        self.settings.models.memory = ModelSlotConfig(
            model="local-model", provider="ollama:local"
        )
        self.assertEqual(
            resolve_model("user-1", slot="memory").model_name, "local-model"
        )

    @patch("app.services.model_config_service.mysql_service.select")
    def test_capability_discovery_includes_environment_and_saved_bindings(self, select):
        select.return_value = [{"slot": "image"}]
        self.settings.models.tts = ModelSlotConfig(model="voice-model")
        self.assertEqual(configured_slots(), {"image", "tts"})
        self.assertNotIn("user:", select.call_args.args[0])

    def test_environment_media_and_background_entrypoints_use_the_same_resolver(self):
        from app.services.background_service import background_model
        from app.services.media_service import capability_config

        self.configure_environment()
        self.settings.models.tts = ModelSlotConfig(model="voice-model")
        self.settings.models.memory = ModelSlotConfig(model="memory-model")
        self.assertEqual(capability_config("user-1", "tts").model_name, "voice-model")
        with patch(
            "app.services.background_service.create_chat_model"
        ) as create:
            background_model("user-1", "memory")
        self.assertEqual(create.call_args.args[0].model_name, "memory-model")


class EnvironmentSettingsTests(unittest.TestCase):
    def test_dotenv_slots_and_process_override(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text(
                "MODELS__DIALOGUE__MODEL=chat\nMODELS__DIALOGUE__BASE_URL=https://shared.test/v1\n"
                "MODELS__DIALOGUE__API_KEY=secret\nMODELS__MEMORY__MODEL=memory\n"
                "MODELS__MEMORY__TEMPERATURE=0.4\nMODELS__TTS__MODEL=speech\n"
                "MODELS__TTS__BASE_URL=https://voice.test/v1\nMODELS__TTS__API_KEY=voice-key\n",
                encoding="utf-8",
            )
            os.environ["MODELS__MEMORY__MODEL"] = "process-memory"
            settings = Settings(_env_file=path)
            self.assertEqual(settings.models.memory.model, "process-memory")
            self.assertEqual(settings.models.memory.temperature, 0.4)
            self.assertEqual(settings.models.tts.base_url, "https://voice.test/v1")
            self.assertEqual(settings.models.tts.api_key, "voice-key")
            self.assertEqual(settings.models.dialogue.model, "chat")

    def test_invalid_slot_temperature_and_provider_rejected(self):
        with self.assertRaises(ValidationError):
            ModelSlotConfig(temperature=3)
        with self.assertRaises(ValidationError):
            ModelSlotConfig(provider="unsupported")
