"""Smoke tests for updater module: version comparison and mocked API."""

import json
import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO

from updater import _parse_version, check_for_update, is_frozen


class TestParseVersion(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_parse_version("1.2.3"), (1, 2, 3))

    def test_v_prefix(self):
        self.assertEqual(_parse_version("v2.0.1"), (2, 0, 1))

    def test_comparison(self):
        self.assertGreater(_parse_version("1.1.0"), _parse_version("1.0.9"))
        self.assertGreater(_parse_version("2.0.0"), _parse_version("1.99.99"))
        self.assertEqual(_parse_version("1.0.0"), _parse_version("v1.0.0"))


MOCK_RELEASE = {
    "tag_name": "v2.0.0",
    "body": "## Changes\n- Faster video\n- Bug fixes",
    "assets": [
        {"name": "app.exe", "browser_download_url": "https://example.com/app.exe"},
        {"name": "checksums.txt", "browser_download_url": "https://example.com/checksums.txt"},
    ],
}


class TestCheckForUpdate(unittest.TestCase):
    @patch("updater.__version__", "1.0.0")
    @patch("updater.urllib.request.urlopen")
    def test_update_available(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps(MOCK_RELEASE).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        has_update, ver, url, changelog = check_for_update()
        self.assertTrue(has_update)
        self.assertEqual(ver, "2.0.0")
        self.assertIn("app.exe", url)
        self.assertIn("Faster video", changelog)

    @patch("updater.__version__", "3.0.0")
    @patch("updater.urllib.request.urlopen")
    def test_no_update(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps(MOCK_RELEASE).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        has_update, ver, url, changelog = check_for_update()
        self.assertFalse(has_update)

    @patch("updater.urllib.request.urlopen", side_effect=Exception("network error"))
    def test_network_error(self, _):
        has_update, ver, url, changelog = check_for_update()
        self.assertFalse(has_update)


class TestIsFrozen(unittest.TestCase):
    def test_not_frozen(self):
        self.assertFalse(is_frozen())


if __name__ == "__main__":
    unittest.main()
