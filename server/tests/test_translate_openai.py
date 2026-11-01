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
