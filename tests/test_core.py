from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web_explorer.cache import PageCache
from web_explorer.errors import UnsafeTargetError, WebExplorerError
from web_explorer.extract import extract_html_page
from web_explorer.http_client import SafeHttpClient
from web_explorer.models import WebPage
from web_explorer.settings import ExplorerSettings
from web_explorer.site_rules import SiteRuleStore


class SiteRulesTest(unittest.TestCase):
    def test_exact_and_wildcard_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rules.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "sites": [
                            {
                                "host": "wiki.example.com",
                                "adapter": "wiki",
                                "driver": "mediawiki",
                                "priority": 200,
                            },
                            {
                                "host": "*.example.com",
                                "adapter": "generic",
                                "priority": 100,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = SiteRuleStore(path)
            store.load()
            rules = store.match("wiki.example.com")
            self.assertEqual(
                [rule.adapter_name for rule in rules], ["mediawiki", "generic"]
            )
            self.assertEqual(len(store.match("example.com")), 0)

    def test_invalid_rule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rules.json"
            path.write_text(
                '{"version": 1, "sites": [{"host": "https://bad/", "adapter": "generic"}]}',
                encoding="utf-8",
            )
            with self.assertRaises(WebExplorerError):
                SiteRuleStore(path).load()


class ExtractorTest(unittest.TestCase):
    def test_extracts_main_content_and_links(self) -> None:
        page = extract_html_page(
            """
            <html><head><title>Example</title><script>secret()</script></head>
            <body><nav>navigation</nav><main><h1>Hello</h1>
            <p>This is the main article with enough useful text for extraction.</p>
            <a href="/next#part">Next page</a></main></body></html>
            """,
            "https://example.com/start",
            max_content_chars=10_000,
            max_links=10,
        )
        self.assertEqual(page.title, "Example")
        self.assertIn("Hello", page.content)
        self.assertNotIn("navigation", page.content)
        self.assertNotIn("secret", page.content)
        self.assertEqual(page.links[0].url, "https://example.com/next")


class CacheTest(unittest.TestCase):
    def test_cache_is_scoped(self) -> None:
        cache = PageCache(ttl_seconds=60, max_pages=10)
        page = cache.put("group:1", WebPage("Title", "https://example.com", "Body"))
        self.assertIs(cache.get("group:1", page.page_id), page)
        self.assertIsNone(cache.get("group:2", page.page_id))


class NetworkPolicyTest(unittest.TestCase):
    def test_blocks_literal_private_addresses(self) -> None:
        client = SafeHttpClient(ExplorerSettings())
        for url in (
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://[::1]/",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeTargetError):
                client.validate_url(url)

    def test_private_network_can_be_enabled(self) -> None:
        client = SafeHttpClient(ExplorerSettings(allow_private_network=True))
        self.assertEqual(client.validate_url("http://127.0.0.1/"), "http://127.0.0.1/")


if __name__ == "__main__":
    unittest.main()
