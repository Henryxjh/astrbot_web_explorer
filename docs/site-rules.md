# 站点规则 JSON 配置

站点规则用于告诉网页探索器：某个域名应优先交给哪个分类适配器。没有匹配规则的网站仍会使用内置识别和通用网页读取。

## 文件位置

默认位置：

```text
data/plugin_data/astrbot_web_explorer/site_rules.json
```

插件首次启动时会自动创建一个空配置：

```json
{
  "version": 1,
  "sites": []
}
```

也可以在 AstrBot WebUI 的“站点规则文件路径”中填写其他绝对路径。保存 JSON 后无需重启插件，下一次网页请求会自动重新加载。

## 完整结构

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

## 顶层字段

### `version`

必填，当前只能填写数字 `1`。它用于以后区分不同版本的配置格式。

### `sites`

必填，JSON 数组。每个元素是一条站点规则。

## 站点规则字段

### `host`

必填，只填写域名，不要包含协议、端口、路径或查询参数。

精确匹配：

```json
"host": "wiki.example.com"
```

匹配所有子域名：

```json
"host": "*.example.com"
```

`*.example.com` 可以匹配 `wiki.example.com`，但不会匹配根域名 `example.com`。如果根域名也需要匹配，请另外增加一条规则。

### `adapter`

必填，指定适配器或分类。目前可填写：

| 值 | 用途 |
|---|---|
| `github` | GitHub REST API |
| `mediawiki` | MediaWiki Action API |
| `wiki` | Wiki 分类，需要配合 `driver` |
| `code_forge` | 代码托管分类，需要配合 `driver` |
| `generic` | 强制优先使用通用网页读取 |
| `chawan` | 使用 Chawan；还必须在 WebUI 启用并加入白名单 |

### `driver`

分类适配器的具体实现。当前支持的组合：

```json
{
  "adapter": "wiki",
  "driver": "mediawiki"
}
```

```json
{
  "adapter": "code_forge",
  "driver": "github"
}
```

直接填写 `adapter: "mediawiki"` 或 `adapter: "github"` 时可以省略 `driver`。

### `api_base`

可选，覆盖自动发现的 API 地址。主要用于非标准安装路径和反向代理后的站点。

MediaWiki 示例：

```json
"api_base": "https://wiki.example.com/custom/api.php"
```

留空字符串或省略时，适配器会从网页的 MediaWiki 元数据中自动发现。配置的 API 地址仍然受协议、DNS、内网地址、重定向和响应大小检查限制。

### `priority`

可选整数，默认 `100`。同一个域名匹配多条规则时，数值较大的规则先执行。

例如先尝试 MediaWiki，失败后再使用通用读取：

```json
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
```

### `fallback`

可选布尔值，默认 `true`。

- `true`：适配器失败或不支持该页面时，继续尝试下一条规则或通用读取。
- `false`：该规则失败后立即向模型返回错误。

如果 API 结果必须作为唯一可信来源，可以设置为 `false`；普通站点通常保留 `true`。

### `enabled`

可选布尔值，默认 `true`。设置为 `false` 可以临时停用规则而不必删除。

### `options`

可选对象，预留给分类适配器的站点级设置。第一版 MediaWiki 和 GitHub 驱动暂时没有必须填写的 `options`，建议保留空对象：

```json
"options": {}
```

`options` 中的字段会交给对应适配器，但只有适配器明确支持的选项才会生效。

## 常用示例

### 非标准路径的 MediaWiki

```json
{
  "version": 1,
  "sites": [
    {
      "host": "wiki.example.com",
      "adapter": "wiki",
      "driver": "mediawiki",
      "api_base": "https://wiki.example.com/media/api.php",
      "priority": 100,
      "fallback": true,
      "enabled": true,
      "options": {}
    }
  ]
}
```

### 让一组子域名使用通用读取

```json
{
  "version": 1,
  "sites": [
    {
      "host": "*.docs.example.com",
      "adapter": "generic",
      "priority": 50,
      "fallback": true,
      "enabled": true,
      "options": {}
    }
  ]
}
```

### 为特定网站启用 Chawan

站点规则：

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

同时需要在 WebUI：

1. 开启“实验性 Chawan 渲染后端”。
2. 将 `dynamic.example.com` 加入 Chawan 域名白名单。
3. 需要执行脚本时再开启“允许 Chawan 执行网页 JavaScript”。

仅写站点规则不会绕过 Chawan 白名单。

## 匹配顺序

插件按以下顺序选择处理方式：

```text
用户站点规则（priority 从高到低）
→ 内置 GitHub/MediaWiki 识别
→ MediaWiki 页面特征识别
→ 通用 HTTP 和正文提取
→ 白名单中的 Chawan（仅在检测到 JS 空壳页面时）
```

站点规则不会关闭公共安全检查，也不会让 GitHub Token 被发送给其他域名。

## 配置检查

JSON 必须使用双引号，最后一个字段后不能有逗号。可以在安装插件的机器上检查语法：

```bash
python -m json.tool /path/to/site_rules.json
```

如果热重载后的文件无效，插件会保留上一次成功加载的规则并写入警告日志。修正 JSON 后，下一次请求会再次加载。
