# AstrBot 网页探索器

为 AstrBot 提供模块化的只读网页探索能力。内置 GitHub、Arch Linux 官方仓库/AUR、MediaWiki、MC百科、Read the Docs、通用网页读取、可选 Qt WebEngine 截图，以及默认关闭的 Chawan 兜底。

## 工具

- `open_web_page`：读取 URL，返回正文、页面 ID 和编号链接。
- `search_web_site`：搜索 GitHub、Arch Linux 官方仓库/AUR、MediaWiki、MC百科或 Read the Docs；GitHub 支持 `repositories`、`issues`、`code`、`commits`。
- `follow_web_link`：通过页面 ID 和 `L1` 形式的链接编号继续访问。
- `find_in_web_page`：在会话缓存页面中查找文字。
- `capture_web_page`：截取固定 800×800 网页视图，以多模态工具结果交给视觉模型。

测试指令：

```text
/webopen https://github.com/openai/codex
/websearch https://github.com/openai/codex websocket error
/websearch https://wiki.archlinux.org systemd
/websearch https://www.mcmod.cn Applied Energistics
/websearch https://mcmod.readthedocs.io 模组添加
/websearch arch pacman
/websearch arch-official linux
/websearch aur paru
/webfollow p_ab12cd34 L1
/webfind p_ab12cd34 websocket
/webshot https://example.com
/webshot https://example.com --width 1280 --max-height 8000
```

## 网页截图

截图后端使用 Selenium 调用与 Qt WebEngine 版本匹配的 `webenginedriver`。Selenium 会通过插件依赖安装；系统还需要提供 Xvfb、Driver 和一个接受 WebEngineDriver 控制的 Qt WebEngine 浏览器。

核心配置示例：

```text
启用网页截图：是
WebEngineDriver：/usr/lib/qt6/webenginedriver
Qt WebEngine 浏览器：/path/to/simple-qt-webbrowser
截图浏览器 DISPLAY：留空
允许截图的域名：example.com、*.wikipedia.org
```

`DISPLAY` 留空时插件会为截图浏览器启动独立的 `1920×1080` Xvfb，并在插件退出时关闭。浏览器与 Driver 必须来自相互兼容的 Qt WebEngine/Chromium 版本。

### 模型截图

`capture_web_page` 始终生成一张 800×800 PNG，通过 AstrBot 的 `CallToolResult/ImageContent` 直接放入下一轮多模态上下文，不会自动发送给 QQ 用户。当前模型提供商必须支持图片输入。

### 用户截图指令

```text
/webshot <URL>
/webshot <URL> --width 1280
/webshot <URL> --max-height 8000
/webshot <URL> --width 1280 --max-height 8000
```

- 无需 @ 机器人。
- `--width` 是页面布局宽度，允许 `320～2560`。
- `--max-height` 是单张长图的最大高度，允许 `240～16000`。
- 实际高度取文档高度、最大高度和总像素限制中的最小值。
- 页面超过限制时会截断并在图片后提示。
- 未填写参数时使用 WebUI 默认值。

页面扩大后，插件最多等待配置的稳定时间，期间检查字体、当前视口图片和文档高度；高度最多修正两次。达到时间上限后直接截取当前状态，不会主动滚动触发懒加载。

### 截图白名单及重定向

截图功能使用独立域名白名单：

```text
example.com        只允许该域名
*.example.com      只允许其子域名，不包含 example.com 本身
```

空白名单禁止所有网页截图。插件会检查初始 URL、DNS 解析地址以及页面加载后的最终 URL；配套 Qt 浏览器还会在每次主框架导航发出前检查白名单，因此 HTTP 重定向、Meta Refresh 和 JavaScript 跳转后的域名也必须获得授权。

浏览器请求拦截器允许公网 HTTP/HTTPS 子资源，同时拒绝危险协议、`localhost` 和显式私有 IP。域名解析到私有地址的检查由插件完成；如需更强的子资源 DNS 隔离，应另外使用受限代理或网络命名空间。

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
| `arch_packages` | 可省略 | Arch 官方仓库与 AUR 软件包 API |
| `package_index` | `arch_packages` | 软件包索引分类写法 |
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

### Arch Linux 软件包搜索

无需填写站点规则或 Token，直接使用以下站点别名：

```text
arch           同时搜索 Arch 官方仓库和 AUR，官方结果排在前面
arch-official  只搜索 Arch 官方仓库
aur            只搜索 AUR
```

例如：

```text
/websearch arch paru
/websearch arch-official pacman
/websearch aur visual-studio-code-bin
```

也可以直接读取 `https://archlinux.org/packages/...` 和 `https://aur.archlinux.org/packages/...` 软件包详情页。插件只访问公开 API，不会调用本机的 `pacman`、`yay` 或其他 AUR 助手。官方包和 AUR 中的同名包会作为不同结果保留；AUR 是用户维护的软件包，安装前仍需人工检查 PKGBUILD 及相关文件。

### 处理顺序

```text
用户站点规则（priority 从高到低）
→ 内置 GitHub/Arch 软件包/MC百科/Read the Docs/MediaWiki 识别
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

Chawan 和网页截图默认关闭，并分别要求配置域名白名单。截图浏览器应使用专用 Profile 和独立运行用户，不应复用日常浏览器的登录数据。
