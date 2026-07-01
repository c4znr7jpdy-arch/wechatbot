# Local NeteaseCloudMusicApi

Local NetEase Cloud Music API service for `astrbot_plugin_music`.

`astrbot_plugin_music` auto-starts this service when `nodejs_base_url` points to
`127.0.0.1` / `localhost` and the service is not already responding.

Manual startup for troubleshooting:

```powershell
cd E:\Project\services\netease-cloud-music-api
$env:PORT = "3300"
npm start
```

AstrBot music plugin config:

```json
{
  "default_player_name": "nj点歌",
  "nodejs_base_url": "http://127.0.0.1:3300"
}
```
