import io
import unittest
from unittest.mock import MagicMock, patch

from fastapi import UploadFile

from app.shared.utils.storage_service_client import (
    gateway_url_for_object_key,
    is_legacy_supabase_storage_path,
    upload_via_storage_service,
)


class TestLegacyPath(unittest.TestCase):
    def test_supabase_paths_contain_slash(self) -> None:
        self.assertTrue(is_legacy_supabase_storage_path("user-uuid/file.jpg"))

    def test_microservice_keys_are_single_segment(self) -> None:
        self.assertFalse(is_legacy_supabase_storage_path("889a5491-4de3-4ee8-8081-c5ac774d37e7.txt"))


class TestGatewayUrl(unittest.TestCase):
    @patch("app.shared.utils.storage_service_client.settings")
    def test_builds_https_gateway_url(self, mock_settings: MagicMock) -> None:
        mock_settings.STORAGE_SERVICE_BASE_URL = "https://storage.example"
        self.assertEqual(
            gateway_url_for_object_key("abc-def.txt"),
            "https://storage.example/files/abc-def.txt",
        )

    @patch("app.shared.utils.storage_service_client.settings")
    def test_strips_trailing_slash_on_base(self, mock_settings: MagicMock) -> None:
        mock_settings.STORAGE_SERVICE_BASE_URL = "https://storage.example/"
        self.assertEqual(
            gateway_url_for_object_key("k.bin"),
            "https://storage.example/files/k.bin",
        )


class TestUploadViaStorageService(unittest.TestCase):
    @patch("app.shared.utils.storage_service_client.httpx.Client")
    @patch("app.shared.utils.storage_service_client.settings")
    def test_returns_filename_from_json(
        self, mock_settings: MagicMock, mock_client_cls: MagicMock
    ) -> None:
        mock_settings.STORAGE_SERVICE_BASE_URL = "https://storage.example"
        mock_settings.STORAGE_SERVICE_UPLOAD_PATH = "/api/upload"
        mock_settings.STORAGE_SERVICE_API_KEY = ""

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "filename": "new-object-key.png",
            "url": "http://ignored",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        upload = io.BytesIO(b"x")
        file = UploadFile(filename="a.png", file=upload, headers={"content-type": "image/png"})

        key = upload_via_storage_service(file)
        self.assertEqual(key, "new-object-key.png")
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        self.assertTrue(call_url.startswith("https://storage.example/api/upload"))


class TestGenerateSignedUrlRouting(unittest.TestCase):
    """Integration of legacy detection with generate_signed_url (storage.py)."""

    @patch("app.shared.utils.storage.get_supabase")
    @patch("app.shared.utils.storage_service_client.settings")
    @patch("app.shared.utils.storage.settings")
    def test_legacy_path_uses_supabase(
        self,
        mock_storage_settings: MagicMock,
        mock_client_settings: MagicMock,
        mock_get_supabase: MagicMock,
    ) -> None:
        for m in (mock_storage_settings, mock_client_settings):
            m.STORAGE_SERVICE_BASE_URL = "https://storage.example"
            m.STORAGE_SERVICE_PRESIGNED_TTL_SECONDS = 600
            m.SUPABASE_URL = "https://proj.supabase.co"
            m.SUPABASE_SERVICE_KEY = "key"

        mock_sb = MagicMock()
        mock_sb.storage.from_.return_value.create_signed_url.return_value = {
            "signedURL": "https://signed.example/x"
        }
        mock_get_supabase.return_value = mock_sb

        from app.shared.utils.storage import generate_signed_url

        url, _ = generate_signed_url("bucket", "user/file.jpg", 3600)
        self.assertEqual(url, "https://signed.example/x")
        mock_sb.storage.from_.assert_called()

    @patch("app.shared.utils.storage_service_client.settings")
    @patch("app.shared.utils.storage.settings")
    def test_new_key_uses_gateway(
        self, mock_storage_settings: MagicMock, mock_client_settings: MagicMock
    ) -> None:
        for m in (mock_storage_settings, mock_client_settings):
            m.STORAGE_SERVICE_BASE_URL = "https://storage.example"
            m.STORAGE_SERVICE_PRESIGNED_TTL_SECONDS = 600
            m.SUPABASE_URL = ""
            m.SUPABASE_SERVICE_KEY = ""

        from app.shared.utils.storage import generate_signed_url

        url, expires = generate_signed_url("bucket", "flat-key.webm", 3600)
        self.assertEqual(url, "https://storage.example/files/flat-key.webm")
        self.assertIsNotNone(expires)
