import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.schemas.model_config import (
    AddModelConfigRequest,
    AddModelConfigResponse,
    ModelConfigResponse,
    SwitchModelRequest,
    UpdateModelConfigRequest,
)

CONFIG_ID = "11111111-1111-4111-8111-111111111111"


class ModelConfigSchemaTests(unittest.TestCase):
    def test_uuid_round_trips_without_numeric_conversion(self):
        request = UpdateModelConfigRequest.model_validate(
            {
                "userId": "user-1",
                "configId": CONFIG_ID,
                "configName": " Chat ",
                "providerName": "custom",
                "modelName": "fixture",
                "baseUrl": "https://example.test/v1",
            }
        )
        self.assertEqual(request.config_id, CONFIG_ID)
        self.assertEqual(request.config_name, "Chat")
        self.assertEqual(
            AddModelConfigResponse(config_id=CONFIG_ID).model_dump(by_alias=True),
            {"configId": CONFIG_ID},
        )

    def test_inherit_default_accepts_null_and_uuid_binding_accepts_string(self):
        self.assertIsNone(
            SwitchModelRequest(user_id="user-1", config_id=None).config_id
        )
        self.assertEqual(
            SwitchModelRequest.model_validate(
                {"userId": "user-1", "configId": CONFIG_ID}
            ).config_id,
            CONFIG_ID,
        )
        with self.assertRaises(ValidationError):
            SwitchModelRequest(user_id="user-1", config_id=1)

    def test_blank_names_and_out_of_range_temperature_are_rejected(self):
        payload = {
            "userId": "user-1",
            "configName": "Chat",
            "providerName": "custom",
            "modelName": "fixture",
        }
        for change in (
            {"configName": "  "},
            {"modelTemperature": 3},
            {"userId": "x" * 65},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValidationError):
                    AddModelConfigRequest.model_validate({**payload, **change})

    def test_database_projection_returns_public_flag_and_no_secret(self):
        current = datetime.now(timezone.utc)
        response = ModelConfigResponse.model_validate(
            {
                "id": CONFIG_ID,
                "user_id": "user-1",
                "config_name": "Public",
                "provider_name": "default",
                "model_name": "fixture",
                "model_temperature": "0.7",
                "is_active": 1,
                "is_public": 1,
                "created_at": current,
                "updated_at": current,
                "credential_ref": "internal-reference",
                "api_key": "not-a-real-key",
            }
        ).model_dump(by_alias=True)
        self.assertTrue(response["isPublic"])
        self.assertTrue(response["isActive"])
        self.assertEqual(response["id"], CONFIG_ID)
        self.assertNotIn("credentialRef", response)
        self.assertNotIn("apiKey", response)


class ModelConnectionValidationTests(unittest.TestCase):
    def test_provider_and_url_validation(self):
        from app.services.model_config_service import validate_base_url
        from app.schemas.message import AgentConfigRequest

        for address in (
            "file:///tmp/model",
            "ftp://example.test",
            "https://name:password@example.test",
            "http://example.test?key=secret",
        ):
            with self.assertRaises(ValueError):
                validate_base_url(address)
        self.assertEqual(
            validate_base_url("http://127.0.0.1:11434/"), "http://127.0.0.1:11434"
        )
        with self.assertRaises(ValidationError):
            AddModelConfigRequest(
                user_id="u",
                config_name="x",
                provider_name="not-supported",
                model_name="m",
            )
        connection = AgentConfigRequest(api_key="test-secret")
        self.assertNotIn("test-secret", repr(connection))
        self.assertNotIn("apiKey", connection.model_dump(by_alias=True))


if __name__ == "__main__":
    unittest.main()
