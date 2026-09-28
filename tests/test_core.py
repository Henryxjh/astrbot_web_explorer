from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web_explorer.adapters.arch_packages import ArchPackagesAdapter
from web_explorer.adapters.mcmod import McmodAdapter
from web_explorer.adapters.readthedocs import ReadTheDocsAdapter
from web_explorer.cache import PageCache
from web_explorer.chawan import clean_chawan_dump
from web_explorer.errors import UnsafeTargetError, WebExplorerError
from web_explorer.extract import extract_html_page
from web_explorer.http_client import SafeHttpClient
from web_explorer.models import WebPage
from web_explorer.screenshot import HostAllowlist, parse_webshot_arguments
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


class ChawanDumpTest(unittest.TestCase):
    def test_removes_terminal_and_layout_noise(self) -> None:
        dump = (
            "\x1b[31m标题\x1b[0m\r\n"
            "│  正文       内容  │\n"
            "[img] [12]链接\n"
            "[13]\n• [14]\n•\n\n\n\n下一段\x00"
        )
        self.assertEqual(
            clean_chawan_dump(dump),
            "标题\n正文 内容\n[12]链接\n\n下一段",
        )


class ScreenshotConfigurationTest(unittest.TestCase):
    def test_host_allowlist_exact_wildcard_and_idna(self) -> None:
        allowlist = HostAllowlist(("example.com", "*.wikipedia.org", "例子.测试"))
        self.assertTrue(allowlist.matches("EXAMPLE.COM."))
        self.assertTrue(allowlist.matches("zh.wikipedia.org"))
        self.assertFalse(allowlist.matches("wikipedia.org"))
        self.assertFalse(allowlist.matches("fakeexample.com"))
        self.assertTrue(allowlist.matches("xn--fsqu00a.xn--0zwm56d"))

    def test_parses_webshot_arguments(self) -> None:
        arguments = parse_webshot_arguments(
            "https://example.com/path?q=1 --width 1280 --max-height=8000"
        )
        self.assertEqual(arguments.url, "https://example.com/path?q=1")
        self.assertEqual(arguments.width, 1280)
        self.assertEqual(arguments.max_height, 8000)

    def test_rejects_duplicate_webshot_arguments(self) -> None:
        with self.assertRaises(WebExplorerError):
            parse_webshot_arguments("https://example.com --width 800 --width 1024")


class ArchPackagesAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = ExplorerSettings(search_limit=10)
        self.adapter = ArchPackagesAdapter(SafeHttpClient(settings), settings)

    def test_official_exact_match_is_ranked_first(self) -> None:
        results = self.adapter.parse_official_search(
            {
                "results": [
                    {
                        "pkgname": "pacman-contrib",
                        "repo": "extra",
                        "arch": "x86_64",
                        "pkgver": "1.13.0",
                        "pkgrel": "1",
                        "pkgdesc": "Contributed scripts",
                        "maintainers": ["alice"],
                    },
                    {
                        "pkgname": "pacman",
                        "repo": "core",
                        "arch": "x86_64",
                        "pkgver": "7.1.0",
                        "pkgrel": "2",
                        "pkgdesc": "Package manager",
                        "maintainers": ["bob"],
                    },
                ]
            },
            "pacman",
            "arch-official",
        )
        self.assertEqual(results.items[0].item_type, "arch_official")
        self.assertIn(" pacman 7.1.0-2", results.items[0].title)
        self.assertEqual(
            results.items[0].url,
            "https://archlinux.org/packages/core/x86_64/pacman/",
        )

    def test_aur_exact_match_precedes_popularity(self) -> None:
        results = self.adapter.parse_aur_search(
            {
                "type": "search",
                "results": [
                    {
                        "Name": "paru-bin",
                        "Version": "2.0-1",
                        "Popularity": 100,
                        "NumVotes": 1000,
                        "Maintainer": "alice",
                    },
                    {
                        "Name": "paru",
                        "Version": "2.1-1",
                        "Popularity": 5,
                        "NumVotes": 100,
                        "Maintainer": None,
                    },
                ],
            },
            "paru",
            "aur",
        )
        self.assertEqual(results.items[0].title, "[AUR] paru 2.1-1")
        self.assertIn("孤儿包", results.items[0].snippet)
        self.assertIn("检查 PKGBUILD", results.items[0].snippet)

    def test_aur_detail_contains_security_warning_and_links(self) -> None:
        page = self.adapter._aur_page(
            {
                "Name": "example-git",
                "PackageBase": "example-git",
                "Version": "1.0.r1-1",
                "Description": "Example package",
                "Maintainer": "alice",
                "URL": "https://example.com/project",
                "NumVotes": 3,
                "Popularity": 0.5,
            },
            "https://aur.archlinux.org/packages/example-git/",
        )
        self.assertIn("不属于 Arch Linux 官方仓库", page.content)
        self.assertIn("安装前应检查 PKGBUILD", page.content)
        self.assertEqual(page.metadata["source"], "aur")
        self.assertEqual(page.links[1].text, "AUR Git 仓库")

    def test_mode_aliases(self) -> None:
        self.assertEqual(self.adapter._mode("arch", "auto"), "all")
        self.assertEqual(self.adapter._mode("arch-official", "auto"), "official")
        self.assertEqual(self.adapter._mode("aur", "auto"), "aur")
        self.assertEqual(self.adapter._mode("https://aur.archlinux.org", "auto"), "aur")


class McmodAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = ExplorerSettings(search_limit=2)
        self.adapter = McmodAdapter(SafeHttpClient(settings), settings)

    def test_parses_class_page_without_site_chrome(self) -> None:
        page = self.adapter.parse_page(
            """
            <html><head><title>[AE2] 应用能源2 - MC百科</title></head><body>
            <div class="header-container">站点导航</div>
            <div class="class-info-left"><div>支持平台: JAVA版</div>
            <a href="https://github.com/AppliedEnergistics/Applied-Energistics-2">GitHub</a></div>
            <div class="class-menu-main"><li class="text-area common-text font14">
            <span class="common-text-title">概述</span><p>自动化与存储模组。</p>
            </li></div></body></html>
            """,
            "https://www.mcmod.cn/class/260.html",
        )
        self.assertEqual(page.adapter, "mcmod")
        self.assertEqual(page.title, "[AE2] 应用能源2")
        self.assertIn("支持平台", page.content)
        self.assertIn("自动化与存储模组", page.content)
        self.assertNotIn("站点导航", page.content)
        self.assertEqual(len(page.links), 1)

    def test_parses_item_page(self) -> None:
        page = self.adapter.parse_page(
            """
            <html><head><title>ME控制器 - [AE2] 应用能源2 - MC百科</title></head>
            <body><div class="item-give">物品命令：/give @p ae2:controller</div>
            <p class="item-dict">矿物词典: controller</p>
            <div class="item-info-table">最大堆叠：64</div>
            <div class="item-content common-text"><h2>介绍</h2><p>构成网络。</p></div>
            </body></html>
            """,
            "https://www.mcmod.cn/item/30659.html",
        )
        self.assertIn("/give @p ae2:controller", page.content)
        self.assertIn("构成网络", page.content)

    def test_parses_search_results_with_limit(self) -> None:
        html = """
        <div class="search-result-list">
          <div class="result-item"><div class="head"><a href="/class/category/1-1.html"></a><a href="https://www.mcmod.cn/class/260.html">AE2</a></div><div class="body">自动化与存储</div></div>
          <div class="result-item"><div class="head"><a href="https://www.mcmod.cn/item/1.html">物品一</a></div><div class="body">资料正文</div></div>
          <div class="result-item"><div class="head"><a href="https://www.mcmod.cn/item/2.html">物品二</a></div></div>
        </div>
        """
        results = self.adapter.parse_search(html, "AE2", "mcmod.cn")
        self.assertEqual(len(results.items), 2)
        self.assertEqual(results.items[0].item_type, "class")
        self.assertEqual(results.items[0].snippet, "自动化与存储")
        self.assertEqual(results.items[0].url, "https://www.mcmod.cn/class/260.html")


class ReadTheDocsAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = ExplorerSettings(search_limit=2)
        self.adapter = ReadTheDocsAdapter(SafeHttpClient(settings), settings)

    def test_detects_and_extracts_article(self) -> None:
        html = """
        <html lang="zh"><head><title>安装 — 示例文档</title>
        <meta name="readthedocs-project-slug" content="example">
        <meta name="readthedocs-version-slug" content="latest"></head>
        <body><nav>导航噪声</nav><article role="main" id="furo-main-content">
        <div class="wy-breadcrumbs">Docs » 安装</div>
        <h1>安装<a class="headerlink" href="#install">¶</a></h1>
        <p>运行安装命令，然后检查程序是否成功启动。</p><a href="next.html">下一步</a>
        </article></body></html>
        """
        self.assertTrue(self.adapter.detect_html(html))
        page = self.adapter.parse_page(
            html, "https://example.readthedocs.io/zh/latest/install.html"
        )
        self.assertEqual(page.title, "安装")
        self.assertEqual(page.metadata["project"], "example")
        self.assertEqual(page.metadata["version"], "latest")
        self.assertNotIn("导航噪声", page.content)
        self.assertNotIn("Docs »", page.content)
        self.assertNotIn("¶", page.content)
        self.assertEqual(
            page.links[0].url,
            "https://example.readthedocs.io/zh/latest/next.html",
        )

    def test_parses_search_api(self) -> None:
        results = self.adapter.parse_search(
            {
                "results": [
                    {
                        "title": "Installation",
                        "domain": "https://example.readthedocs.io",
                        "path": "/en/latest/install.html",
                        "project": {"slug": "example"},
                        "version": {"slug": "latest"},
                        "blocks": [{"content": "Install the package."}],
                    }
                ]
            },
            "install",
            "example.readthedocs.io",
        )
        self.assertEqual(results.items[0].title, "Installation")
        self.assertEqual(
            results.items[0].url,
            "https://example.readthedocs.io/en/latest/install.html",
        )
        self.assertEqual(results.items[0].snippet, "Install the package.")


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
