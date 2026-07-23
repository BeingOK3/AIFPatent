from __future__ import annotations

import unittest

from idea.config import load_config
from idea.runtime import _build_search_providers


class RuntimeProviderTests(unittest.TestCase):
    def test_default_runtime_uses_serpapi_instead_of_empty_provider_list(self) -> None:
        config = load_config()
        providers, timeouts = _build_search_providers(config, cache=None)
        self.assertEqual(
            [provider.name for provider in providers], ["serpapi_google_patents"]
        )
        self.assertEqual(
            timeouts,
            {
                "serpapi_google_patents": (
                    config.search.providers.serpapi_google_patents.timeout_seconds
                )
            },
        )

    def test_all_enabled_providers_have_stable_priority_and_timeout(self) -> None:
        config = load_config()
        configured = config.search.providers
        providers = configured.model_copy(
            update={
                "exa_mcp": configured.exa_mcp.model_copy(update={"enabled": True}),
                "google_patents_local": configured.google_patents_local.model_copy(
                    update={"enabled": True}
                ),
            }
        )
        config = config.model_copy(
            update={"search": config.search.model_copy(update={"providers": providers})}
        )
        runtime_providers, timeouts = _build_search_providers(config, cache=None)
        self.assertEqual(
            [provider.name for provider in runtime_providers],
            ["serpapi_google_patents", "google_patents_local", "exa_mcp"],
        )
        self.assertEqual(set(timeouts), {provider.name for provider in runtime_providers})


if __name__ == "__main__":
    unittest.main()
