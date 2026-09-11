# AstrBot 网页探索器

为 AstrBot 提供模块化的只读网页探索能力。第一版包含 GitHub、MediaWiki、通用网页读取，以及默认关闭的 Chawan 兜底。

## 工具

- `open_web_page`：读取 URL，返回正文、页面 ID 和编号链接。
- `search_web_site`：搜索 GitHub 或 MediaWiki；GitHub 支持 `repositories`、`issues`、`code`、`commits`。
- `follow_web_link`：通过页面 ID 和 `L1` 形式的链接编号继续访问。
- `find_in_web_page`：在会话缓存页面中查找文字。

测试指令：

```text
/webopen https://github.com/openai/codex
/websearch https://github.com/openai/codex websocket error
/websearch https://wiki.archlinux.org systemd
/webfollow p_ab12cd34 L1
/webfind p_ab12cd34 websocket
```

## 站点规则

插件首次启动会在自己的 AstrBot 数据目录创建 `site_rules.json`。也可以复制仓库中的 `site_rules.example.json`，然后在 WebUI 配置其绝对路径。

完整字段、匹配顺序和填写示例见 [站点规则 JSON 配置文档](docs/site-rules.md)。

规则修改后会在下一次网页请求时自动重载。支持精确域名和 `*.example.com`，匹配顺序由 `priority` 决定。分类及驱动别名：

```json
{
  "host": "wiki.example.com",
  "adapter": "wiki",
  "driver": "mediawiki",
  "api_base": "https://wiki.example.com/api.php"
}
```

`fallback=false` 表示指定适配器失败时直接报错；默认会继续尝试通用读取。

## GitHub Token

公开内容可以匿名读取，但限额较低，代码搜索通常也需要认证。建议在 WebUI 配置最小读取权限的 fine-grained PAT。Token 只会发送给 `api.github.com`，不会随重定向发送到其他域名。

## 网络安全

默认拒绝本机、内网、链路本地及其他保留地址，并在每次 DNS 解析和重定向时重新检查。确实需要读取内网站点时，可以开启 `allow_private_network`，但这会扩大网页工具能够访问的范围。

Chawan 默认关闭，并要求单独配置域名白名单。第一版没有浏览器或 CDP 后端。
