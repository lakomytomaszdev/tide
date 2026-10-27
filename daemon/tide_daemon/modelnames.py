"""Nazwy modeli bez zależności (moduł models i agents importują to samo, bez wczytywania pakietów).

Ollama Cloud: modele z etykietą kończącą się na `cloud` po `:` albo `-` (`gpt-oss:cloud`,
`gpt-oss:120b-cloud`, `qwen3-coder:480b-cloud`). Po `ollama pull` widać je w `/api/tags` jak modele
pobrane, ale lokalna Ollama tylko przekazuje rozmowę na ollama.com — to chmura, nigdy model lokalny
(znacznik „lokalny” w widżecie agentów, rola `agent` w oknie „Nowe zadanie”).
"""
import re

CLOUD_TAG = re.compile(r"[:-]cloud$", re.IGNORECASE)


def is_cloud_model(name) -> bool:
    """Czy nazwa modelu oznacza model w chmurze Ollamy (`…:cloud`, `…:120b-cloud`)."""
    if not isinstance(name, str) or ":" not in name:
        return False
    tag = name.rsplit(":", 1)[1]
    return bool(CLOUD_TAG.search(":" + tag))
