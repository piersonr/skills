#!/usr/bin/env python3
"""Unit tests for request_pov.py; no network calls or API key required."""

from __future__ import annotations

import importlib.util
import ssl
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPT_PATH = Path(__file__).with_name("request_pov.py")
SPEC = importlib.util.spec_from_file_location("request_pov", SCRIPT_PATH)
assert SPEC and SPEC.loader
request_pov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(request_pov)


class RequestPovTests(unittest.TestCase):
    def test_parse_dotenv_quotes_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "OPENROUTER_API_KEY='secret-value'\n"
                "POV_ANTHROPIC_MODEL=anthropic/test # local override\n",
                encoding="utf-8",
            )
            values = request_pov.read_dotenv(path)
        self.assertEqual("secret-value", values["OPENROUTER_API_KEY"])
        self.assertEqual("anthropic/test", values["POV_ANTHROPIC_MODEL"])

    def test_secret_detection(self) -> None:
        self.assertTrue(request_pov.contains_likely_secret("api_key=abcdefghijklmnop"))
        self.assertTrue(
            request_pov.contains_likely_secret(
                "-----BEGIN PRIVATE KEY-----\nnot-a-real-key"
            )
        )
        self.assertFalse(
            request_pov.contains_likely_secret(
                "The OPENROUTER_API_KEY environment variable must be configured."
            )
        )

    def test_payload_enforces_data_collection_policy(self) -> None:
        payload = request_pov.build_payload(
            "anthropic/test", "Question:\nReview this", 500, "medium", True
        )
        self.assertEqual("deny", payload["provider"]["data_collection"])
        self.assertTrue(payload["provider"]["zdr"])
        self.assertEqual("anthropic/test", payload["model"])

    def test_extract_content(self) -> None:
        response = {"choices": [{"message": {"content": "Useful POV"}}]}
        self.assertEqual("Useful POV", request_pov.extract_content(response))

    def test_build_ssl_context(self) -> None:
        self.assertIsInstance(request_pov.build_ssl_context(), ssl.SSLContext)

    def test_dry_run_does_not_require_api_key(self) -> None:
        status = request_pov.run(
            [
                "--lineage",
                "anthropic",
                "--prompt",
                "Review this harmless plan",
                "--dry-run",
                "--json",
            ]
        )
        self.assertEqual(0, status)


if __name__ == "__main__":
    unittest.main()
