#!/usr/bin/env python3
"""Deterministic unit tests for request_pov.py; never contacts OpenRouter."""

from __future__ import annotations

import importlib.util
import http.client
import io
import json
import os
import signal
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


sys.dont_write_bytecode = True
SCRIPT_PATH = Path(__file__).with_name("request_pov.py")
SPEC = importlib.util.spec_from_file_location("request_pov", SCRIPT_PATH)
assert SPEC and SPEC.loader
request_pov = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = request_pov
SPEC.loader.exec_module(request_pov)


SUCCESS_RESPONSE = {
    "id": "generation-123",
    "model": "anthropic/resolved",
    "provider": "Example-Provider",
    "choices": [
        {
            "finish_reason": "stop",
            "native_finish_reason": "end_turn",
            "message": {"content": "Useful POV"},
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


class FakeHttpResponse:
    def __init__(self, payload: object = None, *, raw: bytes | None = None) -> None:
        self.body = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if not self.body:
            return b""
        chunk = self.body if size < 0 else self.body[:size]
        self.body = self.body[len(chunk):]
        return chunk


class ChunkedHttpResponse(FakeHttpResponse):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    def read(self, _size: int = -1) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class FailingHttpResponse(FakeHttpResponse):
    def read(self, _size: int = -1) -> bytes:
        raise http.client.IncompleteRead(b"private-partial-body", 100)


class TimingOutHttpResponse(FakeHttpResponse):
    def read(self, _size: int = -1) -> bytes:
        raise socket.timeout("private timeout detail")


class PovFixture(unittest.TestCase):
    """Shared fixture: isolated user config, fake transport, common argv."""

    def setUp(self) -> None:
        # main() appends a history line on every real attempt, so an unisolated suite
        # writes synthetic latency into the developer's own history file -- and could
        # read their real OPENROUTER_API_KEY out of the user config while doing it.
        # Point the whole user-config directory at a throwaway for every test.
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        self.config_home = Path(config_home.name)
        patcher = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.config_home)}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def history_lines(self) -> list[dict[str, object]]:
        path = self.config_home / "request-pov" / "history.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def invoke(
        self,
        argv: list[str],
        *,
        response: object = SUCCESS_RESPONSE,
        delay: float = 0.0,
        api_key: str = "test-api-key-never-print",
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()

        def fake_call(_key: str, _payload: dict[str, object], _timeout: float) -> object:
            if delay:
                time.sleep(delay)
            if isinstance(response, BaseException):
                raise response
            return response

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": api_key}, clear=False), mock.patch.object(
            request_pov, "call_openrouter", side_effect=fake_call
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                request_pov.main(argv)
        return int(raised.exception.code), stdout.getvalue(), stderr.getvalue()

    def base_args(self, *extra: str) -> list[str]:
        return ["--lineage", "anthropic", "--prompt", "Review this harmless plan", *extra]


class RequestPovTests(PovFixture):
    def test_successful_human_readable_response_and_default_progress(self) -> None:
        status, stdout, stderr = self.invoke(self.base_args())
        self.assertEqual(0, status)
        self.assertIn("Resolved model: anthropic/resolved", stdout)
        self.assertIn("--- External POV ---", stdout)
        self.assertIn("Useful POV", stdout)
        self.assertIn("request started", stderr)
        self.assertIn("request completed", stderr)

    def test_successful_json_response(self) -> None:
        status, stdout, stderr = self.invoke(self.base_args("--json"))
        result = json.loads(stdout)
        self.assertEqual(0, status)
        self.assertEqual("success", result["status"])
        self.assertEqual("ok", result["diagnostic_code"])
        self.assertFalse(result["retryable"])
        self.assertEqual("Useful POV", result["pov"])
        self.assertEqual("", stderr)

    def test_json_argument_error_is_one_json_document(self) -> None:
        status, stdout, stderr = self.invoke(["--json", "--lineage", "anthropic"])
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        self.assertEqual("invalid_arguments", result["diagnostic_code"])
        self.assertFalse(result["retryable"])
        self.assertEqual("", stderr)

    def test_json_stdout_parseable_while_progress_only_uses_stderr(self) -> None:
        status, stdout, stderr = self.invoke(
            self.base_args("--json", "--progress", "--progress-interval", "0.01"),
            delay=0.025,
        )
        self.assertEqual(0, status)
        self.assertEqual("success", json.loads(stdout)["status"])
        self.assertNotIn("request-pov:", stdout)
        self.assertIn("request-pov: request started", stderr)
        self.assertIn("request-pov: still running", stderr)

    def test_quiet_mode_suppresses_progress(self) -> None:
        status, _stdout, stderr = self.invoke(self.base_args("--quiet"), delay=0.02)
        self.assertEqual(0, status)
        self.assertEqual("", stderr)

    def test_multiple_heartbeat_intervals_during_delayed_response(self) -> None:
        status, _stdout, stderr = self.invoke(
            self.base_args("--progress-interval", "0.01"), delay=0.045
        )
        self.assertEqual(0, status)
        self.assertGreaterEqual(stderr.count("still running"), 3)

    def test_http_error_classification(self) -> None:
        error = request_pov.PovError(
            "OpenRouter request failed with HTTP 503.",
            "http_error",
            request_pov.EXIT_NETWORK_API,
            True,
            {"http_status": 503},
        )
        status, stdout, _stderr = self.invoke(self.base_args("--json", "--progress"), response=error)
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_NETWORK_API, status)
        self.assertEqual("http_error", result["diagnostic_code"])
        self.assertTrue(result["retryable"])

    def test_call_openrouter_classifies_http_errors_without_body(self) -> None:
        error = urllib.error.HTTPError(request_pov.API_URL, 429, "secret body", {}, io.BytesIO(b"private"))
        with mock.patch.object(request_pov.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        error.close()
        self.assertEqual("http_error", raised.exception.diagnostic_code)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private", str(raised.exception))

    def test_dns_network_failure_classification(self) -> None:
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError(socket.gaierror(-2, "host not found")),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual(request_pov.EXIT_NETWORK_API, raised.exception.exit_code)
        self.assertEqual("network_error", raised.exception.diagnostic_code)
        self.assertTrue(raised.exception.retryable)

    def test_socket_timeout_classification(self) -> None:
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError(socket.timeout("slow")),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual(request_pov.EXIT_TIMEOUT, raised.exception.exit_code)
        self.assertEqual("request_timeout_state_unknown", raised.exception.diagnostic_code)
        self.assertFalse(raised.exception.retryable)

    def test_overall_timeout_classification(self) -> None:
        status, stdout, _stderr = self.invoke(
            self.base_args("--json", "--timeout", "0.02"), delay=0.08
        )
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_TIMEOUT, status)
        self.assertEqual("request_timeout_state_unknown", result["diagnostic_code"])
        self.assertFalse(result["retryable"])

    def test_nonfinite_float_options_are_classified_validation_errors(self) -> None:
        for option, value in (
            ("--timeout", "nan"),
            ("--timeout", "inf"),
            ("--progress-interval", "nan"),
            ("--progress-interval", "-inf"),
        ):
            with self.subTest(option=option, value=value):
                option_args = [f"{option}={value}"] if value.startswith("-") else [option, value]
                status, stdout, _stderr = self.invoke(
                    self.base_args("--json", *option_args)
                )
                result = json.loads(stdout)
                self.assertEqual(request_pov.EXIT_VALIDATION, status)
                self.assertEqual("validation_error", result["diagnostic_code"])
                self.assertFalse(result["retryable"])

    def test_empty_assistant_content(self) -> None:
        response = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
        status, stdout, _stderr = self.invoke(self.base_args("--json"), response=response)
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, status)
        self.assertEqual("empty_assistant_content", result["diagnostic_code"])
        self.assertTrue(result["retryable"])

    def test_missing_choices_message_and_content(self) -> None:
        cases = (
            ({}, "missing_choices"),
            ({"choices": [{}]}, "missing_message"),
            ({"choices": [{"message": {}}]}, "missing_assistant_content"),
        )
        for response, code in cases:
            with self.subTest(code=code, response=response):
                status, stdout, _stderr = self.invoke(self.base_args("--json"), response=response)
                result = json.loads(stdout)
                self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, status)
                self.assertEqual(code, result["diagnostic_code"])
                self.assertFalse(result["retryable"])

    def test_non_string_content_part_is_classified_empty_content(self) -> None:
        response = {
            "choices": [
                {"finish_reason": "stop", "message": {"content": [{"type": "text", "text": 123}]}}
            ]
        }
        status, stdout, _stderr = self.invoke(self.base_args("--json"), response=response)
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, status)
        self.assertEqual("empty_assistant_content", result["diagnostic_code"])

    def test_reasoning_exhausted_token_budget(self) -> None:
        response = {
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 5000,
                "total_tokens": 5011,
                "reasoning_tokens": 4990,
                "private_detail": "must-not-surface",
            },
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning": "private reasoning"},
                }
            ]
        }
        status, stdout, _stderr = self.invoke(self.base_args("--json"), response=response)
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, status)
        self.assertEqual("token_budget_exhausted_by_reasoning", result["diagnostic_code"])
        self.assertTrue(result["retryable"])
        self.assertEqual("length", result["metadata"]["finish_reason"])
        self.assertEqual(
            request_pov.REASONING_BUDGET_REMEDIATION,
            result["metadata"]["remediation_code"],
        )
        self.assertEqual(
            {
                "prompt_tokens": 11,
                "completion_tokens": 5000,
                "total_tokens": 5011,
                "reasoning_tokens": 4990,
            },
            result["metadata"]["usage"],
        )
        self.assertEqual(6000, result["metadata"]["configured_max_completion_tokens"])
        self.assertEqual("medium", result["metadata"]["configured_reasoning_effort"])
        self.assertNotIn("private reasoning", stdout)
        self.assertNotIn("must-not-surface", stdout)

    def test_partial_content_still_reports_reasoning_budget_exhaustion(self) -> None:
        response = {
            "choices": [
                {
                    "finish_reason": "length",
                    "native_finish_reason": "max_tokens",
                    "message": {
                        "content": "Truncated private opening",
                        "reasoning": "private reasoning",
                    },
                }
            ]
        }
        status, stdout, _stderr = self.invoke(self.base_args("--json"), response=response)
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, status)
        self.assertEqual("token_budget_exhausted_by_reasoning", result["diagnostic_code"])
        self.assertTrue(result["retryable"])
        self.assertTrue(result["metadata"]["partial_assistant_content_present"])
        self.assertEqual(
            request_pov.REASONING_BUDGET_REMEDIATION,
            result["metadata"]["remediation_code"],
        )
        self.assertNotIn("Truncated private opening", stdout)
        self.assertNotIn("private reasoning", stdout)

    def test_provider_error_in_http_success_response(self) -> None:
        response = {
            "model": "anthropic/resolved",
            "provider": "Example-Provider",
            "error": {"code": 503, "type": "provider_unavailable", "message": "private response"},
        }
        with mock.patch.object(
            request_pov.urllib.request, "urlopen", return_value=FakeHttpResponse(response)
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("provider_error", raised.exception.diagnostic_code)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private response", str(raised.exception))

    def test_oversized_http_success_response_is_rejected_before_parsing(self) -> None:
        raw = b'x' * (request_pov.MAX_RESPONSE_BYTES + 1)
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(raw=raw),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("response_too_large", raised.exception.diagnostic_code)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, raised.exception.exit_code)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(
            request_pov.MAX_RESPONSE_BYTES,
            raised.exception.metadata["max_response_bytes"],
        )

    def test_short_reads_are_accumulated_until_valid_json_eof(self) -> None:
        response = ChunkedHttpResponse(
            [b'{"choices":[{"message":', b'{"content":"Useful POV"}}]}']
        )
        decoded = request_pov.read_response_json(response)
        self.assertEqual("Useful POV", request_pov.extract_content(decoded))

    def test_short_valid_prefix_cannot_hide_oversized_trailing_body(self) -> None:
        response = ChunkedHttpResponse(
            [b"{}", b"x" * request_pov.MAX_RESPONSE_BYTES]
        )
        with self.assertRaises(request_pov.PovError) as raised:
            request_pov.read_response_json(response)
        self.assertEqual("response_too_large", raised.exception.diagnostic_code)

    def test_response_read_error_is_classified_without_partial_body(self) -> None:
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            return_value=FailingHttpResponse(raw=b""),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("response_read_error", raised.exception.diagnostic_code)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, raised.exception.exit_code)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual("IncompleteRead", raised.exception.metadata["read_error_type"])
        self.assertNotIn("private-partial-body", str(raised.exception))

    def test_response_body_timeout_preserves_state_unknown_timeout_contract(self) -> None:
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            return_value=TimingOutHttpResponse(raw=b""),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("request_timeout_state_unknown", raised.exception.diagnostic_code)
        self.assertEqual(request_pov.EXIT_TIMEOUT, raised.exception.exit_code)
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("private timeout detail", str(raised.exception))

    def test_invalid_json_response_is_classified_without_raw_body(self) -> None:
        private_raw = b'{"private":"customer-marker"'
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(raw=private_raw),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("unreadable_response", raised.exception.diagnostic_code)
        self.assertEqual(request_pov.EXIT_UNUSABLE_RESPONSE, raised.exception.exit_code)
        self.assertNotIn("customer-marker", str(raised.exception))

    def test_invalid_utf8_response_is_classified_without_raw_body(self) -> None:
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(raw=b'\xff\xfeprivate-marker'),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("unreadable_response", raised.exception.diagnostic_code)
        self.assertNotIn("private-marker", str(raised.exception))

    def test_non_object_json_response_is_classified_safely(self) -> None:
        with mock.patch.object(
            request_pov.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(["private-marker"]),
        ):
            with self.assertRaises(request_pov.PovError) as raised:
                request_pov.call_openrouter("api-secret", {"model": "test"}, 1)
        self.assertEqual("malformed_response", raised.exception.diagnostic_code)
        self.assertEqual("list", raised.exception.metadata["response_type"])
        self.assertNotIn("private-marker", str(raised.exception))

    def test_simulated_sigint_has_stable_nonretryable_contract(self) -> None:
        error = request_pov.RequestInterrupted(signal.SIGINT)
        status, stdout, _stderr = self.invoke(self.base_args("--json"), response=error)
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_INTERRUPTED_SIGINT, status)
        self.assertEqual("request_interrupted_state_unknown", result["diagnostic_code"])
        self.assertFalse(result["retryable"])
        self.assertEqual("SIGINT", result["metadata"]["signal"])

    def test_real_sigint_interrupts_wait_and_restores_handler(self) -> None:
        previous_handler = signal.getsignal(signal.SIGINT)
        timer = threading.Timer(0.02, os.kill, args=(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            status, stdout, _stderr = self.invoke(
                self.base_args("--json", "--progress"), delay=0.2
            )
        finally:
            timer.join()
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_INTERRUPTED_SIGINT, status)
        self.assertEqual("request_interrupted_state_unknown", result["diagnostic_code"])
        self.assertFalse(result["retryable"])
        self.assertEqual(previous_handler, signal.getsignal(signal.SIGINT))

    def test_api_key_prompt_and_context_absent_from_diagnostics(self) -> None:
        secret_key = "sk-or-v1-never-print-this-value"
        sensitive_prompt = "private-customer-evidence-marker"
        error = request_pov.PovError(
            "Could not reach OpenRouter due to a network error.",
            "network_error",
            request_pov.EXIT_NETWORK_API,
            True,
        )
        status, stdout, stderr = self.invoke(
            ["--lineage", "anthropic", "--prompt", sensitive_prompt, "--json", "--progress"],
            response=error,
            api_key=secret_key,
        )
        self.assertEqual(request_pov.EXIT_NETWORK_API, status)
        combined = stdout + stderr
        self.assertNotIn(secret_key, combined)
        self.assertNotIn(sensitive_prompt, combined)

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

    def test_model_env_file_resolution_remains_intact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("POV_ANTHROPIC_MODEL=anthropic/from-env\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
                status = request_pov.run(
                    self.base_args("--env-file", str(env_path), "--dry-run", "--json")
                )
        self.assertEqual(0, status)

    def test_user_config_fallback_is_independent_of_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "unrelated-project" / "nested"
            project.mkdir(parents=True)
            project_env = project.parent / ".env"
            project_env.write_text(
                "POV_ANTHROPIC_MODEL=anthropic/project-model\n",
                encoding="utf-8",
            )
            config_home = root / "config"
            user_env = config_home / "request-pov" / ".env"
            user_env.parent.mkdir(parents=True)
            user_env.write_text(
                "OPENROUTER_API_KEY=user-config-key\n"
                "POV_ANTHROPIC_MODEL=anthropic/user-model\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True
            ):
                env_files = request_pov.resolve_env_files(None, project)
                model = request_pov.get_setting("POV_ANTHROPIC_MODEL", env_files)
                api_key = request_pov.get_setting("OPENROUTER_API_KEY", env_files)
        self.assertEqual([project_env.resolve(), user_env.resolve()], env_files)
        self.assertEqual("anthropic/project-model", model)
        self.assertEqual("user-config-key", api_key)

    def test_explicit_env_file_does_not_fall_through_to_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "explicit.env"
            explicit.write_text("POV_ANTHROPIC_MODEL=anthropic/explicit\n", encoding="utf-8")
            config_home = root / "config"
            user_env = config_home / "request-pov" / ".env"
            user_env.parent.mkdir(parents=True)
            user_env.write_text("OPENROUTER_API_KEY=user-config-key\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True
            ):
                env_files = request_pov.resolve_env_files(explicit, root)
                api_key = request_pov.get_setting("OPENROUTER_API_KEY", env_files)
        self.assertEqual([explicit.resolve()], env_files)
        self.assertIsNone(api_key)

    def test_process_environment_precedes_all_dotenv_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("OPENROUTER_API_KEY=file-key\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"OPENROUTER_API_KEY": "process-key"}, clear=True
            ):
                value = request_pov.get_setting("OPENROUTER_API_KEY", [env_path])
        self.assertEqual("process-key", value)

    def test_relative_xdg_config_home_cannot_make_fallback_cwd_dependent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            with mock.patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": "relative-config", "HOME": str(root / "home")},
                clear=True,
            ):
                with mock.patch.object(Path, "home", return_value=root / "home"):
                    first_path = request_pov.user_config_env()
                    second_path = request_pov.user_config_env()
        expected = root / "home" / ".config" / "request-pov" / ".env"
        self.assertEqual(expected, first_path)
        self.assertEqual(expected, second_path)
        self.assertTrue(first_path.is_absolute())

    def test_known_cross_lineage_model_conflict_is_rejected(self) -> None:
        status, stdout, _stderr = self.invoke(
            self.base_args("--model", "openai/gpt-5.6-terra", "--dry-run", "--json")
        )
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        self.assertEqual("model_lineage_mismatch", result["diagnostic_code"])
        self.assertFalse(result["retryable"])

    def test_model_class_implies_lineage_without_naming_it(self) -> None:
        """`--model-class sol` alone must resolve; the nickname names the lineage."""
        for name, entry in request_pov.MODEL_CLASSES.items():
            with self.subTest(model_class=name):
                args = request_pov.parse_args(
                    ["--model-class", name, "--prompt", "review this"]
                )
                self.assertIsNone(args.lineage)
                self.assertEqual(entry["lineage"], request_pov.MODEL_CLASSES[name]["lineage"])

    def test_every_model_class_slug_matches_its_declared_lineage(self) -> None:
        """A nickname whose slug prefix disagrees with its lineage would route wrong."""
        for name, entry in request_pov.MODEL_CLASSES.items():
            with self.subTest(model_class=name):
                prefix = entry["slug"].split("/", 1)[0].lower()
                self.assertEqual(
                    entry["lineage"], request_pov.MODEL_PREFIX_LINEAGES.get(prefix)
                )

    def test_completion_budget_scales_with_reasoning_effort(self) -> None:
        """High effort must not inherit a budget that reasoning alone can exhaust."""
        budgets = {}
        for effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
            args = request_pov.parse_args(
                ["--lineage", "openai", "--prompt", "x", "--reasoning-effort", effort]
            )
            budgets[effort] = args.max_completion_tokens
        # Unchanged where the trap was never demonstrated.
        for effort in ("none", "minimal", "low", "medium"):
            self.assertEqual(
                request_pov.DEFAULT_MAX_COMPLETION_TOKENS, budgets[effort]
            )
        # Raised where it was: a high-effort call spent all 6000 on reasoning.
        self.assertGreaterEqual(budgets["high"], 24000)
        self.assertGreaterEqual(budgets["xhigh"], budgets["high"])
        self.assertGreaterEqual(budgets["max"], budgets["high"])

    def test_explicit_budget_still_wins_over_the_effort_default(self) -> None:
        args = request_pov.parse_args(
            [
                "--lineage",
                "openai",
                "--prompt",
                "x",
                "--reasoning-effort",
                "high",
                "--max-completion-tokens",
                "1000",
            ]
        )
        self.assertEqual(1000, args.max_completion_tokens)

    def test_same_lineage_request_is_refused(self) -> None:
        """The tool's whole point is cross-lineage; a self-review must fail closed."""
        status, stdout, _ = self.invoke(
            [
                "--json",
                "--caller-lineage",
                "anthropic",
                "--model-class",
                "opus",
                "--prompt",
                "review this",
                "--dry-run",
            ]
        )
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        result = json.loads(stdout)
        self.assertEqual("same_lineage_refused", result["diagnostic_code"])
        self.assertFalse(result["retryable"])

    def test_same_lineage_allowed_only_with_explicit_override(self) -> None:
        status, stdout, _ = self.invoke(
            [
                "--json",
                "--caller-lineage",
                "anthropic",
                "--model-class",
                "opus",
                "--allow-same-lineage",
                "--prompt",
                "review this",
                "--dry-run",
            ]
        )
        self.assertEqual(0, status)
        self.assertEqual("dry-run", json.loads(stdout)["status"])

    def test_cross_lineage_request_is_unaffected_by_the_guard(self) -> None:
        status, stdout, _ = self.invoke(
            [
                "--json",
                "--caller-lineage",
                "anthropic",
                "--model-class",
                "sol",
                "--prompt",
                "review this",
                "--dry-run",
            ]
        )
        self.assertEqual(0, status)
        self.assertEqual("openai", json.loads(stdout)["requested_lineage"])

    def test_missing_lineage_and_model_class_is_rejected(self) -> None:
        status, stdout, _ = self.invoke(["--json", "--prompt", "x", "--dry-run"])
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        self.assertEqual("validation_error", json.loads(stdout)["diagnostic_code"])

    def test_fable_class_preserves_tilde_and_precedes_environment_override(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"POV_ANTHROPIC_MODEL": "anthropic/claude-sonnet-5"},
            clear=True,
        ):
            status, stdout, _ = self.invoke(
                ["--model-class", "fable", "--caller-lineage", "openai",
                 "--prompt", "review this", "--dry-run", "--json"]
            )
        result = json.loads(stdout)
        self.assertEqual(0, status)
        self.assertEqual("anthropic", result["requested_lineage"])
        self.assertEqual("~anthropic/claude-fable-latest", result["requested_model"])

    def test_fable_class_refuses_same_lineage_and_conflicting_lineage(self) -> None:
        for extra, diagnostic in (
            (["--caller-lineage", "anthropic"], "same_lineage_refused"),
            (["--lineage", "xai"], "model_lineage_mismatch"),
        ):
            with self.subTest(extra=extra):
                status, stdout, _ = self.invoke(
                    ["--model-class", "fable", "--prompt", "review this",
                     "--dry-run", "--json", *extra]
                )
                self.assertEqual(request_pov.EXIT_VALIDATION, status)
                self.assertEqual(diagnostic, json.loads(stdout)["diagnostic_code"])

    def test_explicit_tilde_anthropic_model_rejects_conflicting_lineage(self) -> None:
        status, stdout, _ = self.invoke(
            ["--lineage", "openai", "--model", "~anthropic/claude-fable-latest",
             "--prompt", "review this", "--dry-run", "--json"]
        )
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        self.assertEqual("model_lineage_mismatch", json.loads(stdout)["diagnostic_code"])

    def test_opus_model_class_resolves_to_opus_5(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout):
            status = request_pov.run(
                self.base_args("--model-class", "opus", "--dry-run", "--json")
            )
        result = json.loads(stdout.getvalue())
        self.assertEqual(0, status)
        self.assertEqual("anthropic/claude-opus-5", result["requested_model"])

    def test_explicit_opus_class_precedes_stale_environment_override(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"POV_ANTHROPIC_MODEL": "anthropic/claude-opus-4.6"},
            clear=True,
        ), redirect_stdout(stdout):
            status = request_pov.run(
                self.base_args("--model-class", "opus", "--dry-run", "--json")
            )
        result = json.loads(stdout.getvalue())
        self.assertEqual(0, status)
        self.assertEqual("anthropic/claude-opus-5", result["requested_model"])

    def test_opus_model_class_rejects_non_anthropic_lineage(self) -> None:
        status, stdout, _stderr = self.invoke(
            [
                "--lineage",
                "xai",
                "--model-class",
                "opus",
                "--prompt",
                "Review this harmless plan",
                "--dry-run",
                "--json",
            ]
        )
        result = json.loads(stdout)
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        self.assertEqual("model_lineage_mismatch", result["diagnostic_code"])

    def test_xai_lineage_defaults_to_current_grok_model(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout):
            status = request_pov.run(
                [
                    "--lineage",
                    "xai",
                    "--prompt",
                    "Review this harmless plan",
                    "--dry-run",
                    "--json",
                ]
            )
        result = json.loads(stdout.getvalue())
        self.assertEqual(0, status)
        self.assertEqual("xai", result["requested_lineage"])
        self.assertEqual("x-ai/grok-4.6", result["requested_model"])
        self.assertNotIn("grok-latest", result["requested_model"])
        self.assertNotIn("grok-4.20", result["requested_model"])

    def test_xai_model_env_override_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("POV_XAI_MODEL=x-ai/grok-custom\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout):
                status = request_pov.run(
                    [
                        "--lineage",
                        "xai",
                        "--prompt",
                        "Review this harmless plan",
                        "--env-file",
                        str(env_path),
                        "--dry-run",
                        "--json",
                    ]
                )
        self.assertEqual(0, status)
        self.assertEqual("x-ai/grok-custom", json.loads(stdout.getvalue())["requested_model"])

    def test_xai_prefix_participates_in_lineage_validation(self) -> None:
        request_pov.validate_model_lineage("xai", "x-ai/grok-4.6")
        with self.assertRaises(request_pov.PovError) as raised:
            request_pov.validate_model_lineage("anthropic", "x-ai/grok-4.6")
        self.assertEqual("model_lineage_mismatch", raised.exception.diagnostic_code)

    def test_custom_model_alias_remains_compatible(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = request_pov.run(
                self.base_args("--model", "custom/model-alias", "--dry-run", "--json")
            )
        self.assertEqual(0, status)
        self.assertEqual("custom/model-alias", json.loads(stdout.getvalue())["requested_model"])

    def test_secret_and_oversized_context_protections(self) -> None:
        with self.assertRaises(request_pov.PovError) as secret_error:
            request_pov.run(
                ["--lineage", "anthropic", "--prompt", "api_key=abcdefghijklmnop", "--dry-run"]
            )
        self.assertEqual("likely_secret_detected", secret_error.exception.diagnostic_code)
        with self.assertRaises(request_pov.PovError) as size_error:
            request_pov.run(
                [
                    "--lineage",
                    "anthropic",
                    "--prompt",
                    "long prompt",
                    "--max-context-bytes",
                    "4",
                    "--dry-run",
                ]
            )
        self.assertEqual("context_too_large", size_error.exception.diagnostic_code)

    def test_hard_credentials_cannot_be_overridden(self) -> None:
        credentials = (
            "Authorization: Bearer abcdefghijklmnop",
            "Cookie: sessionid=abcdefghijklmnop",
            "+Authorization: Bearer abcdefghijklmnop",
            "+Cookie: sessionid=abcdefghijklmnop",
            '"Authorization": "Bearer abcdefghijklmnop"',
            '"Cookie": "sessionid=abcdefghijklmnop"',
            "ghp_abcdefghijklmnopqrstuvwxyz123456",
            "AKIAIOSFODNN7EXAMPLE",
        )
        for credential in credentials:
            with self.subTest(credential=credential[:8]):
                with self.assertRaises(request_pov.PovError) as raised:
                    request_pov.run(
                        [
                            "--lineage",
                            "anthropic",
                            "--prompt",
                            credential,
                            "--allow-sensitive-context",
                            "--dry-run",
                        ]
                    )
                self.assertEqual("credential_detected", raised.exception.diagnostic_code)

    def test_credential_like_filenames_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for filename in (".env.local", ".env.production", ".npmrc", "credentials.json"):
                path = Path(directory) / filename
                path.write_text("harmless-placeholder", encoding="utf-8")
                with self.subTest(filename=filename), self.assertRaises(request_pov.PovError):
                    request_pov.read_text_file(path, "context file")

    def test_outbound_prompt_does_not_include_local_path_or_filename(self) -> None:
        path = Path("/Users/private-name/customer-123-evidence.txt")
        prompt = request_pov.assemble_prompt("Question", [(path, "Evidence body")])
        self.assertNotIn("/Users/private-name", prompt)
        self.assertNotIn("customer-123-evidence.txt", prompt)
        self.assertIn("--- Evidence 1 ---", prompt)

    def test_payload_enforces_provider_privacy_and_zdr(self) -> None:
        payload = request_pov.build_payload(
            "anthropic/test", "Question:\nReview this", 500, "medium", True
        )
        self.assertEqual("deny", payload["provider"]["data_collection"])
        self.assertTrue(payload["provider"]["zdr"])
        self.assertEqual("anthropic/test", payload["model"])

    def test_response_diagnostic_never_echoes_content_or_error_message(self) -> None:
        response = {
            "error": {"code": 503, "message": "private provider detail"},
            "choices": [{"finish_reason": "stop", "message": {"content": "private output"}}],
        }
        diagnostic = request_pov.safe_response_diagnostic(response)
        self.assertNotIn("private output", diagnostic)
        self.assertNotIn("private provider detail", diagnostic)
        self.assertIn("choice_count=1", diagnostic)

    def test_missing_response_model_is_not_claimed_as_resolved(self) -> None:
        response = {
            "provider": "Example Provider",
            "choices": [{"finish_reason": "stop", "message": {"content": "Useful POV"}}],
        }
        status, stdout, _stderr = self.invoke(self.base_args("--json"), response=response)
        result = json.loads(stdout)
        self.assertEqual(0, status)
        self.assertIsNone(result["resolved_model"])

    def test_usage_metadata_is_numeric_allowlisted_and_finite(self) -> None:
        huge_integer = 10**10000
        usage = request_pov.safe_usage_metadata(
            {
                "usage": {
                    "prompt_tokens": 10,
                    "reasoning_tokens": huge_integer,
                    "completion_tokens": float("nan"),
                    "total_tokens": float("inf"),
                    "raw_private_field": "private",
                }
            }
        )
        self.assertEqual({"prompt_tokens": 10}, usage)
        self.assertNotIn("NaN", json.dumps(usage, allow_nan=False))

    def test_default_completion_budget_and_progress_settings(self) -> None:
        args = request_pov.parse_args(self.base_args())
        self.assertGreaterEqual(args.max_completion_tokens, 6000)
        self.assertEqual(15.0, args.progress_interval)
        self.assertFalse(args.progress)
        self.assertFalse(args.quiet)

    def test_build_ssl_context(self) -> None:
        self.assertIsInstance(request_pov.build_ssl_context(), ssl.SSLContext)

    def test_dry_run_does_not_require_api_key(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout):
            status = request_pov.run(self.base_args("--dry-run", "--json"))
        self.assertEqual(0, status)
        self.assertEqual("dry-run", json.loads(stdout.getvalue())["status"])
        self.assertEqual("ok", json.loads(stdout.getvalue())["diagnostic_code"])


class HistoryTests(PovFixture):
    """The local latency record exists to tune the timeout from evidence."""

    def test_success_records_one_line_with_latency_and_usage(self) -> None:
        status, _stdout, _stderr = self.invoke(self.base_args("--json"))
        self.assertEqual(0, status)
        lines = self.history_lines()
        self.assertEqual(1, len(lines))
        entry = lines[0]
        self.assertEqual("success", entry["status"])
        self.assertEqual("ok", entry["diagnostic_code"])
        self.assertEqual(0, entry["exit_code"])
        self.assertEqual("medium", entry["reasoning_effort"])
        self.assertEqual(120.0, entry["timeout"])
        self.assertEqual(6000, entry["max_completion_tokens"])
        self.assertIsInstance(entry["elapsed_seconds"], float)
        self.assertEqual(20, entry["usage"]["completion_tokens"])

    def test_timeout_is_recorded_because_failures_are_the_point(self) -> None:
        # A history of only successes would show a comfortable latency distribution
        # and argue the 120s default is fine -- the exact wrong conclusion.
        status, _stdout, _stderr = self.invoke(
            self.base_args("--json", "--timeout", "0.02"), delay=0.08
        )
        self.assertEqual(request_pov.EXIT_TIMEOUT, status)
        lines = self.history_lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("error", lines[0]["status"])
        self.assertEqual("request_timeout_state_unknown", lines[0]["diagnostic_code"])
        self.assertEqual(request_pov.EXIT_TIMEOUT, lines[0]["exit_code"])
        self.assertEqual(0.02, lines[0]["timeout"])

    def test_validation_error_before_the_request_is_not_recorded(self) -> None:
        status, _stdout, _stderr = self.invoke(["--json", "--lineage", "anthropic"])
        self.assertEqual(request_pov.EXIT_VALIDATION, status)
        self.assertEqual([], self.history_lines())

    def test_dry_run_is_not_recorded(self) -> None:
        status, _stdout, _stderr = self.invoke(self.base_args("--json", "--dry-run"))
        self.assertEqual(0, status)
        self.assertEqual([], self.history_lines())

    def test_no_history_opts_out(self) -> None:
        status, _stdout, _stderr = self.invoke(self.base_args("--json", "--no-history"))
        self.assertEqual(0, status)
        self.assertEqual([], self.history_lines())

    def test_history_never_records_question_response_or_context_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "customer-123-evidence.txt"
            evidence.write_text("Confidential evidence body", encoding="utf-8")
            status, _stdout, _stderr = self.invoke(
                self.base_args("--json", "--context-file", str(evidence))
            )
        self.assertEqual(0, status)
        raw = (self.config_home / "request-pov" / "history.jsonl").read_text()
        self.assertNotIn("Review this harmless plan", raw)
        self.assertNotIn("Useful POV", raw)
        self.assertNotIn("Confidential evidence body", raw)
        self.assertNotIn("customer-123-evidence", raw)
        # Sizes and counts answer the latency question without carrying the content.
        self.assertEqual(1, self.history_lines()[0]["context_files"])
        self.assertGreater(self.history_lines()[0]["prompt_bytes"], 0)

    def test_history_file_is_owner_only(self) -> None:
        self.invoke(self.base_args("--json"))
        path = self.config_home / "request-pov" / "history.jsonl"
        self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_appends_rather_than_truncating_across_calls(self) -> None:
        self.invoke(self.base_args("--json"))
        self.invoke(self.base_args("--json"))
        self.assertEqual(2, len(self.history_lines()))

    def test_unwritable_history_never_fails_the_request(self) -> None:
        # Bookkeeping must not add a failure mode to a request that already succeeded.
        with mock.patch.object(
            request_pov, "history_path", side_effect=OSError("read-only filesystem")
        ):
            status, stdout, _stderr = self.invoke(self.base_args("--json"))
        self.assertEqual(0, status)
        self.assertEqual("success", json.loads(stdout)["status"])

    def test_oversized_history_rotates_once(self) -> None:
        path = self.config_home / "request-pov" / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * (request_pov.MAX_HISTORY_BYTES + 1), encoding="utf-8")
        self.invoke(self.base_args("--json"))
        self.assertTrue(path.with_name(path.name + ".1").exists())
        self.assertEqual(1, len(self.history_lines()))


if __name__ == "__main__":
    unittest.main()
