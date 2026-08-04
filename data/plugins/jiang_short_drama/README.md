# 姜小妹短剧搜索

严格匹配以下格式（`短剧` 后必须是 ASCII 空格或 Tab）：

```text
短剧 剧名
```

例如 `短剧 闪婚` 会并发查询多个苹果 CMS/VOD JSON 源，然后通过 OneBot `wechat_link_card` 消息段发送微信链接卡片。`短剧闪婚`、`/短剧 闪婚` 和使用全角空格的消息不会触发。

资源选择遵循以下顺序：

1. 有精准同名结果时，只在精准结果里选择，不混入续作或“第二季”。
2. 精准结果为空时，才启用达到最低分数的模糊匹配。
3. 对单线路 m3u8 解析实际总时长（包括 master playlist 的子清单），优先选择时长最长且达到 `full_version_min_minutes` 的完整版。
4. 没有完整版时回退到集数更完整的分集源。

模糊匹配不是只给首次结果打分：精准同名为空时，插件还会使用安全的缩短词和已知近形/别名做第二轮资源站查询，再用原始输入复核相似度。例如 `全球杀机` 会二次查询 `全球杀戮`。两字短词不会拆成单字扩搜，避免把 `腊肉` 错配成体育视频中的“老腊肉队”；能识别可能剧名但资源站尚未收录时，会明确提示“暂无可播放资源”。

需要指定分集时使用：

```text
短剧 剧名 第12集
```

插件同时支持 `第1-20集` 这类合并线路，会自动把第 12 集映射到对应播放段，不会一次发送几十张卡片。

## 播放器内选集

插件内置了分集播放器。配置公网入口后，分集卡片会打开同一个播放页，并提供：

- 竖屏页面在播放器下方以五列网格完整铺开全部剧集，不使用横向滑动；
- 自定义可关闭的底部选集面板，不调用 Android 原生全屏下拉框；
- 播放画面左右两侧的小型上一集、下一集图标；
- 播放画面左上角固定显示“选集”，顶部正中显示当前集数；
- 自定义播放器容器全屏按钮；进入全屏后仍保留上下集、集数、横屏选集面板和清晰度控件；
- 播放器右上角清晰度菜单：多码率 master playlist 可选择自动、480P、720P、1080P 等，单码率线路显示“原画”；
- 按视频真实宽高动态布局，横屏铺满可用宽度，竖屏居中并限制到视口高度；
- 播放失败时提供紧凑的当前集重试；
- 本集播完自动播放下一集；
- 记住当前集和各集播放进度；
- `短剧 剧名 第12集` 直接从播放器第 12 集打开。

播放列表使用短期随机令牌保存在机器人内存中，不会把几十个 m3u8 地址塞进微信卡片 URL。默认有效期为 6 小时，插件重载或 AstrBot 退出后旧令牌也会失效，重新发送搜索命令即可生成新卡片。

播放器默认只监听 `127.0.0.1:6197`。要让其他微信用户访问，需要把该端口通过 Nginx、Caddy、SSH 反向隧道等方式暴露为公网 HTTPS 地址，再配置：

```json
{
  "episode_player_public_base_url": "https://player.example",
  "episode_player_bind_host": "127.0.0.1",
  "episode_player_port": 6197
}
```

反向代理需要原样转发 `/short-drama/` 路径到 `http://127.0.0.1:6197`。例如 Nginx：

```nginx
location /short-drama/ {
    proxy_pass http://127.0.0.1:6197;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

没有配置 `episode_player_public_base_url`、监听失败或搜索到的资源只有一个播放线路时，插件自动回退到原有单集公共播放器；微信卡片不会因此失效。

当前项目已部署为 `https://player.xiuxianjyj.xin`：VPS 上的独立 Nginx 站点通过 SSH 反向 Unix Socket `/run/short-drama-player.sock` 连接机器人本机 `127.0.0.1:6197`，不开放额外公网 TCP 端口，也不会改动 `xiuxianjyj.xin` 的 NewAPI 根站点。相关文件位于 `deploy/nginx/short_drama_player.conf` 和 `scripts/start_short_drama_reverse_tunnel.ps1`；Windows 当前用户登录时会隐藏启动隧道，断线后每 10 秒自动重连。

卡片默认打开 `m3u8-player.cc` 的 HTTPS HLS 播放页，再由页面加载第一集 m3u8，避免微信把资源站域名当作顶层网页拦截。目标源仍需允许浏览器跨域访问；插件烟测所用源返回了 `Access-Control-Allow-Origin: *`。

若部署了自己的公网 HTTPS HLS 播放页，可在插件配置中替换 `player_url_template`，例如：

```text
https://player.example/watch?url={url}&title={title}&episode={episode}
```

三个占位符都会经过 URL 编码。单个资源站超时或返回异常时会自动忽略，不影响其余来源。

只有明确把模板设置为 `direct` 时，卡片才会重新直达 m3u8；该模式可能触发微信安全页拦截。
