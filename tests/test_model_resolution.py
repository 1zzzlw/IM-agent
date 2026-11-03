import unittest
from unittest.mock import patch

from app.core.errors import ChatError
from app.schemas.message import AgentConfigRequest
from app.services.ai_message_service import agent_status
from app.services.model_config_service import resolve_model


CONFIG_ID = "11111111-1111-4111-8111-111111111111"


class ModelResolutionTests(unittest.TestCase):
    def setUp(self):
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
            patch("app.services.model_config_service.select_dialogue_binding")
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
        self.binding.assert_called_once_with(user_id="user-1")

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

    @patch("app.services.ai_message_service.dialogue_schema_ready", return_value=False)
    def test_missing_tables_prevent_readiness_without_resolving_a_model(self, schema):
        result = agent_status("user-1")
        self.assertFalse(result["available"])
        self.assertEqual(result["code"], "DATABASE_SCHEMA_INCOMPLETE")
        self.binding.assert_not_called()

