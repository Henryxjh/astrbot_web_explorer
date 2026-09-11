from __future__ import annotations

import base64
from urllib.parse import quote, urlencode, urlsplit

from ..errors import UnsupportedTargetError, WebExplorerError
from ..http_client import SafeHttpClient
from ..models import SearchItem, SearchResults, WebLink, WebPage
from ..settings import ExplorerSettings
from ..site_rules import SiteRule
from .base import WebAdapter


class GitHubAdapter(WebAdapter):
    name = "github"
    API_ORIGIN = "api.github.com"

    def __init__(self, client: SafeHttpClient, settings: ExplorerSettings) -> None:
        self.client = client
        self.settings = settings

    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        if rule and rule.adapter_name == self.name:
            return True
        return (urlsplit(url).hostname or "").lower() in {
            "github.com",
            "www.github.com",
            "api.github.com",
        }

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return headers

    async def _get_json(self, path: str, params: dict[str, str] | None = None):
        suffix = f"?{urlencode(params)}" if params else ""
        response = await self.client.get(
            f"https://api.github.com{path}{suffix}",
            headers=self._headers(),
            sensitive_origin=self.API_ORIGIN,
        )
        return response.json()

    async def _get_text(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> str:
        suffix = f"?{urlencode(params)}" if params else ""
        response = await self.client.get(
            f"https://api.github.com{path}{suffix}",
            headers=self._headers("application/vnd.github.raw+json"),
            sensitive_origin=self.API_ORIGIN,
        )
        return response.text()

    @staticmethod
    def _repo_parts(url: str) -> tuple[str, str, list[str]]:
        parsed = urlsplit(url)
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.hostname == "api.github.com" and parts[:1] == ["repos"]:
            parts = parts[1:]
        if len(parts) < 2:
            raise UnsupportedTargetError("GitHub URL 中缺少仓库所有者或仓库名。")
        return parts[0], parts[1].removesuffix(".git"), parts[2:]

    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        del rule
        owner, repo, tail = self._repo_parts(url)
        if not tail:
            return await self._open_repository(owner, repo)
        resource = tail[0]
        if resource == "issues" and len(tail) >= 2 and tail[1].isdigit():
            return await self._open_issue(owner, repo, tail[1], is_pull=False)
        if resource == "pull" and len(tail) >= 2 and tail[1].isdigit():
            return await self._open_issue(owner, repo, tail[1], is_pull=True)
        if resource == "commit" and len(tail) >= 2:
            return await self._open_commit(owner, repo, tail[1])
        if resource in {"blob", "tree"} and len(tail) >= 3:
            return await self._open_content(owner, repo, tail[1], "/".join(tail[2:]))
        if resource == "releases" and len(tail) >= 3 and tail[1] == "tag":
            return await self._open_release(owner, repo, "/".join(tail[2:]))
        raise UnsupportedTargetError("该 GitHub 页面暂未被专用适配器支持。")

    async def _open_repository(self, owner: str, repo: str) -> WebPage:
        data = await self._get_json(f"/repos/{quote(owner)}/{quote(repo)}")
        readme = ""
        try:
            readme = await self._get_text(f"/repos/{quote(owner)}/{quote(repo)}/readme")
        except WebExplorerError:
            pass
        topics = ", ".join(data.get("topics", []))
        content = "\n".join(
            part
            for part in (
                data.get("description") or "",
                f"默认分支：{data.get('default_branch', '')}",
                f"主要语言：{data.get('language') or '未知'}",
                f"Stars：{data.get('stargazers_count', 0)}；Forks：{data.get('forks_count', 0)}；Open issues：{data.get('open_issues_count', 0)}",
                f"Topics：{topics}" if topics else "",
                f"最后更新：{data.get('updated_at', '')}",
                "\nREADME\n" + readme if readme else "",
            )
            if part
        )
        return WebPage(
            title=data.get("full_name", f"{owner}/{repo}"),
            url=data.get("html_url", f"https://github.com/{owner}/{repo}"),
            content=content,
            links=[
                WebLink("Issues", f"https://github.com/{owner}/{repo}/issues"),
                WebLink("Pull requests", f"https://github.com/{owner}/{repo}/pulls"),
                WebLink("Commits", f"https://github.com/{owner}/{repo}/commits"),
                WebLink("Releases", f"https://github.com/{owner}/{repo}/releases"),
            ],
            adapter=self.name,
        )

    async def _open_issue(
        self,
        owner: str,
        repo: str,
        number: str,
        *,
        is_pull: bool,
    ) -> WebPage:
        issue = await self._get_json(
            f"/repos/{quote(owner)}/{quote(repo)}/issues/{number}"
        )
        comments = await self._get_json(
            f"/repos/{quote(owner)}/{quote(repo)}/issues/{number}/comments",
            {"per_page": "100"},
        )
        extra = {}
        review_comments = []
        if is_pull:
            extra = await self._get_json(
                f"/repos/{quote(owner)}/{quote(repo)}/pulls/{number}"
            )
            review_comments = await self._get_json(
                f"/repos/{quote(owner)}/{quote(repo)}/pulls/{number}/comments",
                {"per_page": "100"},
            )
        lines = [
            f"类型：{'Pull Request' if is_pull else 'Issue'}",
            f"状态：{issue.get('state', '')}",
            f"作者：{(issue.get('user') or {}).get('login', '')}",
            f"创建：{issue.get('created_at', '')}；更新：{issue.get('updated_at', '')}",
        ]
        if is_pull:
            lines.append(
                f"合并状态：merged={extra.get('merged', False)}；mergeable={extra.get('mergeable')}"
            )
        labels = [label.get("name", "") for label in issue.get("labels", [])]
        if labels:
            lines.append("标签：" + ", ".join(labels))
        lines.extend(["", issue.get("body") or "[无正文]"])
        if comments:
            lines.append("\n对话评论")
            for comment in comments:
                user = (comment.get("user") or {}).get("login", "unknown")
                lines.append(
                    f"\n[{user} · {comment.get('created_at', '')}]\n{comment.get('body') or ''}"
                )
        if int(issue.get("comments", 0)) > len(comments):
            lines.append(
                f"\n[普通评论共有 {issue.get('comments')} 条，本次仅返回前 {len(comments)} 条]"
            )
        if review_comments:
            lines.append("\n代码审查评论")
            for comment in review_comments:
                user = (comment.get("user") or {}).get("login", "unknown")
                path = comment.get("path", "")
                line = comment.get("line") or comment.get("original_line") or ""
                lines.append(
                    f"\n[{user} · {path}:{line} · {comment.get('created_at', '')}]\n{comment.get('body') or ''}"
                )
        if is_pull and int(extra.get("review_comments", 0)) > len(review_comments):
            lines.append(
                f"\n[代码审查评论共有 {extra.get('review_comments')} 条，本次仅返回前 {len(review_comments)} 条]"
            )
        return WebPage(
            title=f"{owner}/{repo} #{number}: {issue.get('title', '')}",
            url=issue.get(
                "html_url", f"https://github.com/{owner}/{repo}/issues/{number}"
            ),
            content="\n".join(lines),
            links=[],
            adapter=self.name,
            metadata={
                "comments_returned": len(comments),
                "review_comments_returned": len(review_comments),
            },
        )

    async def _open_commit(self, owner: str, repo: str, ref: str) -> WebPage:
        data = await self._get_json(
            f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(ref, safe='')}"
        )
        commit = data.get("commit") or {}
        author = commit.get("author") or {}
        lines = [
            f"SHA：{data.get('sha', '')}",
            f"作者：{author.get('name', '')}",
            f"时间：{author.get('date', '')}",
            f"统计：+{(data.get('stats') or {}).get('additions', 0)} -{(data.get('stats') or {}).get('deletions', 0)}",
            "",
            commit.get("message", ""),
        ]
        for file in data.get("files", []):
            lines.append(
                f"\n文件：{file.get('filename', '')} ({file.get('status', '')}, +{file.get('additions', 0)} -{file.get('deletions', 0)})"
            )
            if file.get("patch"):
                lines.append(file["patch"])
        return WebPage(
            title=f"{owner}/{repo} commit {str(data.get('sha', ref))[:12]}",
            url=data.get("html_url", f"https://github.com/{owner}/{repo}/commit/{ref}"),
            content="\n".join(lines),
            adapter=self.name,
        )

    async def _open_content(
        self,
        owner: str,
        repo: str,
        ref: str,
        path: str,
    ) -> WebPage:
        data = await self._get_json(
            f"/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}",
            {"ref": ref},
        )
        if isinstance(data, list):
            links = [
                WebLink(
                    item.get("name", ""),
                    item.get("html_url", ""),
                )
                for item in data[: self.settings.max_links]
                if item.get("html_url")
            ]
            content = "\n".join(
                f"{item.get('type', '?'):>4}  {item.get('path', '')}" for item in data
            )
            return WebPage(
                title=f"{owner}/{repo}/{path}",
                url=f"https://github.com/{owner}/{repo}/tree/{ref}/{path}",
                content=content,
                links=links,
                adapter=self.name,
            )
        encoded = data.get("content", "")
        if data.get("encoding") == "base64" and encoded:
            try:
                content = base64.b64decode(encoded).decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                content = "[文件内容无法解码]"
        else:
            content = await self._get_text(
                f"/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}",
                {"ref": ref},
            )
        return WebPage(
            title=f"{owner}/{repo}/{path}",
            url=data.get(
                "html_url", f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"
            ),
            content=content,
            adapter=self.name,
        )

    async def _open_release(self, owner: str, repo: str, tag: str) -> WebPage:
        data = await self._get_json(
            f"/repos/{quote(owner)}/{quote(repo)}/releases/tags/{quote(tag, safe='')}"
        )
        links = [
            WebLink(asset.get("name", "asset"), asset.get("browser_download_url", ""))
            for asset in data.get("assets", [])[: self.settings.max_links]
            if asset.get("browser_download_url")
        ]
        return WebPage(
            title=data.get("name") or data.get("tag_name") or tag,
            url=data.get(
                "html_url", f"https://github.com/{owner}/{repo}/releases/tag/{tag}"
            ),
            content="\n".join(
                [
                    f"标签：{data.get('tag_name', '')}",
                    f"发布：{data.get('published_at', '')}",
                    f"预发布：{data.get('prerelease', False)}",
                    "",
                    data.get("body") or "[无发布说明]",
                ]
            ),
            links=links,
            adapter=self.name,
        )

    async def search(
        self,
        query: str,
        site: str,
        kind: str = "auto",
        rule: SiteRule | None = None,
    ) -> SearchResults:
        del rule
        kind = kind.strip().lower() or "auto"
        allowed = {"auto", "repositories", "issues", "code", "commits"}
        if kind not in allowed:
            raise WebExplorerError(
                "GitHub 搜索类型必须是 auto、repositories、issues、code 或 commits。"
            )
        repo_filter = ""
        try:
            owner, repo, _ = self._repo_parts(site)
            repo_filter = f" repo:{owner}/{repo}"
        except UnsupportedTargetError:
            pass
        if kind == "auto":
            kind = "issues" if repo_filter else "repositories"
        search_query = query.strip() + repo_filter
        data = await self._get_json(
            f"/search/{kind}",
            {"q": search_query, "per_page": str(self.settings.search_limit)},
        )
        items = []
        for item in data.get("items", []):
            repository = item.get("repository") or {}
            if kind == "repositories":
                title = item.get("full_name", "")
                snippet = item.get("description") or ""
            elif kind == "issues":
                title = item.get("title", "")
                snippet = item.get("body") or ""
            elif kind == "commits":
                title = (item.get("commit") or {}).get("message", "").split("\n", 1)[0]
                snippet = (item.get("commit") or {}).get("message", "")
            else:
                title = item.get("path", "")
                snippet = f"仓库：{repository.get('full_name', '')}"
            items.append(
                SearchItem(
                    title=title[:500],
                    url=item.get("html_url", ""),
                    snippet=snippet[:1000],
                    item_type=kind,
                )
            )
        return SearchResults(
            query=query,
            site=site or "https://github.com",
            items=items,
            adapter=self.name,
        )
