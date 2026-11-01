import json
from unittest.mock import patch

from app.translate.openai import OpenAICompatibleTranslator


def test_russian_to_french_prompt_uses_language_names():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"choices":[{"message":{"content":"Bonjour"}}]}'

    translator = OpenAICompatibleTranslator("http://localhost/v1", "test-model")
    with patch("urllib.request.urlopen", return_value=Response()) as send:
        assert translator.translate("Привет", "ru", "fr") == "Bonjour"
    request = send.call_args.args[0]
    prompt = json.loads(request.data)["messages"][0]["content"]
    assert "from Russian to French" in prompt


def test_logs_never_include_model_response_or_http_exception(caplog):
    import logging

    secret = "private-transcript-secret"
    translator = OpenAICompatibleTranslator("http://localhost/v1", "test-model")
    with caplog.at_level(logging.DEBUG):
        with patch("urllib.request.urlopen", side_effect=RuntimeError(secret)):
            assert translator.translate("hello", "en", "fr") is None

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps({"choices": [], "private": secret}).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            assert translator.translate("hello", "en", "fr") is None

        class SuccessfulResponse(Response):
            def read(self):
                return json.dumps({"choices": [{"message": {"content": secret}}]}).encode()

        with patch("urllib.request.urlopen", return_value=SuccessfulResponse()):
            assert translator.translate("hello", "en", "fr") == secret
    assert secret not in caplog.text
    assert "request failed" in caplog.text
    assert "no choices" in caplog.text
