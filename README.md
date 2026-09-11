# AstrBot 网页探索器

为 AstrBot 提供模块化的只读网页探索能力。内置 GitHub、MediaWiki、MC百科、Read the Docs、通用网页读取，以及默认关闭的 Chawan 兜底。

## 工具

- `open_web_page`：读取 URL，返回正文、页面 ID 和编号链接。
- `search_web_site`：搜索 GitHub、MediaWiki、MC百科或 Read the Docs；GitHub 支持 `repositories`、`issues`、`code`、`commits`。
- `follow_web_link`：通过页面 ID 和 `L1` 形式的链接编号继续访问。
- `find_in_web_page`：在会话缓存页面中查找文字。

测试指令：

```text
/webopen https://github.com/openai/codex
/websearch https://github.com/openai/codex websocket error
/websearch https://wiki.archlinux.org systemd
/websearch https://www.mcmod.cn Applied Energistics
/websearch https://mcmod.readthedocs.io 模组添加
/webfollow p_ab12cd34 L1
/webfind p_ab12cd34 websocket
```

## 站点规则

插件首次启动会创建：

```text
data/plugin_data/astrbot_web_explorer/site_rules.json
```

也可以在 WebUI 的“站点规则文件路径”填写其他绝对路径。规则修改后会在下一次网页请求时自动重载，不需要重启插件。

空配置格式：

```json
{
  "version": 1,
  "sites": []
}
```

完整规则格式：

```json
{
  "version": 1,
  "sites": [
    {
      "host": "wiki.example.com",
      "adapter": "wiki",
      "driver": "mediawiki",
      "api_base": "https://wiki.example.com/api.php",
      "priority": 100,
      "fallback": true,
      "enabled": true,
      "options": {}
    }
  ]
}
```

### 规则字段

- `host`：必填，只填写域名，不能包含协议、端口或路径。
- `adapter`：必填，指定分类或适配器。
- `driver`：分类的具体实现；直接填写具体适配器时可以省略。
- `api_base`：可选，用于覆盖自动发现的 API 地址。
- `priority`：可选整数，默认 `100`；数值较大的规则优先。
- `fallback`：可选布尔值，默认 `true`；失败后继续尝试下一规则或通用读取。设置为 `false` 时直接报错。
- `enabled`：可选布尔值，默认 `true`；设置为 `false` 可临时停用。
- `options`：可选对象，保留给对应适配器的站点级选项。

当前可填写的 `adapter`：

| adapter | driver | 用途 |
|---|---|---|
| `github` | 可省略 | GitHub REST API |
| `code_forge` | `github` | GitHub 分类写法 |
| `mediawiki` | 可省略 | MediaWiki Action API |
| `wiki` | `mediawiki` | Wiki 分类写法 |
| `mcmod` | 可省略 | MC百科模组、资料页面及站内搜索 |
| `game_wiki` | `mcmod` | MC百科分类写法 |
| `readthedocs` | 可省略 | Read the Docs 正文及服务端全文搜索 |
| `documentation` | `readthedocs` | 文档站分类写法 |
| `generic` | 可省略 | 通用网页读取 |
| `chawan` | 可省略 | Chawan 渲染，仍需在 WebUI 启用和加入白名单 |

### 域名匹配

精确域名：

```json
"host": "wiki.example.com"
```

所有子域名：

```json
"host": "*.example.com"
```

`*.example.com` 不会匹配根域名 `example.com`。需要匹配根域名时应另外增加一条规则。

### MediaWiki 示例

自动发现 API 地址：

```json
{
  "version": 1,
  "sites": [
    {
      "host": "wiki.example.com",
      "adapter": "wiki",
      "driver": "mediawiki",
      "priority": 100,
      "fallback": true,
      "enabled": true,
      "options": {}
    }
  ]
}
```

如果站点使用非标准路径，可以增加：

```json
"api_base": "https://wiki.example.com/custom/api.php"
```

### 多规则及优先级示例

先尝试 MediaWiki，失败后使用通用读取：

```json
{
  "version": 1,
  "sites": [
    {
      "host": "wiki.example.com",
      "adapter": "mediawiki",
      "priority": 200,
      "fallback": true
    },
    {
      "host": "wiki.example.com",
      "adapter": "generic",
      "priority": 100
    }
  ]
}
```

### Chawan 示例

```json
{
  "version": 1,
  "sites": [
    {
      "host": "dynamic.example.com",
      "adapter": "chawan",
      "priority": 200,
      "fallback": true,
      "enabled": true,
      "options": {}
    }
  ]
}
```

还需要在 WebUI 中启用 Chawan，并把 `dynamic.example.com` 加入 Chawan 域名白名单；需要执行脚本时再开启 JavaScript。仅填写 JSON 不会绕过白名单。

### MC百科示例

MC百科默认会自动使用专用适配器。如果曾为它配置 Chawan，应将对应规则改成：

```json
{
  "host": "www.mcmod.cn",
  "adapter": "mcmod",
  "priority": 200,
  "fallback": true,
  "enabled": true
}
```

站内搜索使用 `https://search.mcmod.cn`，无需执行 JavaScript。

### Read the Docs 示例

`*.readthedocs.io` 等托管域名会自动识别；带有 Read the Docs 元数据的自定义域名也会自动识别。需要显式配置时可以使用：

```json
{
  "host": "docs.example.com",
  "adapter": "readthedocs",
  "priority": 200,
  "fallback": true,
  "enabled": true
}
```

站内搜索使用 Read the Docs v3 服务端搜索 API，并自动限定到页面元数据声明的项目和版本。

### 处理顺序

```text
用户站点规则（priority 从高到低）
→ 内置 GitHub/MC百科/Read the Docs/MediaWiki 识别
→ MediaWiki 页面特征识别
→ 通用 HTTP 正文提取
→ 白名单中的 Chawan（检测到 JavaScript 空壳页面时）
```

可以使用下面的命令检查 JSON 语法：

```bash
python -m json.tool /path/to/site_rules.json
```

JSON 必须使用双引号，最后一个字段后不能带逗号。如果热重载的新文件无效，插件会继续使用上一次成功加载的规则，并在日志中写入警告。

## GitHub Token

公开内容可以匿名读取，但限额较低，代码搜索通常也需要认证。建议在 WebUI 配置最小读取权限的 fine-grained PAT。Token 只会发送给 `api.github.com`，不会随重定向发送到其他域名。

## 网络安全

默认拒绝本机、内网、链路本地及其他保留地址，并在每次 DNS 解析和重定向时重新检查。确实需要读取内网站点时，可以开启 `allow_private_network`，但这会扩大网页工具能够访问的范围。

Chawan 默认关闭，并要求单独配置域名白名单。第一版没有浏览器或 CDP 后端。
