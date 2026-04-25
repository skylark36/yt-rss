# YT-RSS: YouTube 播放列表转播客 RSS

YT-RSS 是一个自动化工具，可以将 YouTube 播放列表转换为标准的 Podcast RSS 订阅源。它会自动下载播放列表中的视频音频，上传到云端存储（如 Cloudflare R2），并生成兼容 Apple Podcasts 的 RSS 文件。

## 功能特性

- **自动化同步**：自动检测 YouTube 播放列表更新。
- **音频提取**：使用 `yt-dlp` 提取最佳质量音频（默认转为 `.m4a` 格式）。
- **云端存储**：支持 Cloudflare R2 或任何兼容 S3 协议的存储服务。
- **状态管理**：内置状态追踪，避免重复下载和上传。
- **播客优化**：生成的 RSS 符合 Podcast 标准，支持封面图和作者设置。
- **反爬友善**：视频下载间隔随机暂停 10-60 秒，降低触发风控风险。
- **部署灵活**：支持一次性运行或作为守护进程（Docker 部署）。
- **隐私支持**：支持加载 YouTube Cookies 以访问受限内容。

## 快速开始

### 1. 准备工作

- **Cloudflare R2**：创建一个存储桶（Bucket），并获取 Access Key、Secret Key 和 Endpoint URL。
- **YouTube 播放列表**：获取你想要转换的播放列表 URL。
- **域名**：确保你的 R2 存储桶已绑定自定义域名或启用了公共访问，并记录下基础 URL。

### 2. 配置环境变量

在项目根目录下创建 `.env` 文件：

```env
# S3 / R2 配置
R2_ACCESS_KEY_ID=你的_ACCESS_KEY
R2_SECRET_ACCESS_KEY=你的_SECRET_KEY
R2_ENDPOINT_URL=你的_R2_ENDPOINT_URL
R2_BUCKET_NAME=你的_BUCKET_NAME

# 应用配置
PLAYLIST_URL=https://www.youtube.com/playlist?list=...
BASE_URL=https://your-r2-public-domain.com
MAX_NEW_VIDEOS=5

# 播客元数据 (可选)
ITUNES_IMAGE=https://example.com/cover.jpg
ITUNES_AUTHOR=Your Name

# 运行模式
SLEEP_INTERVAL=360    # 同步间隔（分钟），默认 360 分钟 (6 小时)
RUN_ONCE=false         # 设置为 true 则同步一次后退出
```

### 3. 使用 Docker 部署

使用 Docker Compose 可以快速启动：

```bash
docker-compose up -d
```

### 4. 本地开发运行

确保已安装 Python 3.10+ 和 `ffmpeg`。

#### 使用 uv (推荐)
```bash
# 同步环境
uv sync
# 运行
uv run main.py
```

#### 使用 pip
```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py
```

## 环境变量说明

| 变量名 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `R2_ACCESS_KEY_ID` | S3/R2 访问密钥 ID | **必填** |
| `R2_SECRET_ACCESS_KEY`| S3/R2 访问密钥 Secret | **必填** |
| `R2_ENDPOINT_URL` | S3/R2 Endpoint 地址 | **必填** |
| `R2_BUCKET_NAME` | 存储桶名称 | **必填** |
| `PLAYLIST_URL` | YouTube 播放列表 URL | **必填** |
| `BASE_URL` | 存储桶对应的公共访问基础 URL | **必填** |
| `PREFIX` | 自定义存储路径前缀（若不填则使用播放列表 ID） | 空 |
| `MAX_NEW_VIDEOS` | 每次运行最多处理的新视频数 | `5` |
| `RSS_FILENAME` | 生成的 RSS 文件名 | `rss.xml` |
| `STATE_FILENAME` | 状态记录文件名 | `state.json` |
| `ITUNES_IMAGE` | 播客封面图 URL | 空 |
| `ITUNES_AUTHOR` | 播客作者名称 | 空 |
| `COOKIES_FILE` | YouTube Cookies 文件路径 (`cookies.txt`) | 空 |
| `SLEEP_INTERVAL` | 服务模式下的同步间隔（分钟） | `360` |
| `RUN_ONCE` | 是否仅运行一次 | `true` |

## 目录结构说明

程序上传到 R2 后的目录结构如下：
```text
bucket-name/
  └── {playlist_id}/
      ├── state.json      # 记录已处理的视频
      ├── rss.xml         # 订阅源文件
      ├── video_id_1.m4a  # 音频文件
      └── video_id_2.m4a
```
你的播客订阅地址即为：`${BASE_URL}/${playlist_id}/rss.xml`

## 常见问题

**Q: 如何处理受限视频或私有播放列表？**
A: 在本地生成 `cookies.txt`（可以使用 Chrome 扩展如 "Get cookies.txt LOCALLY"），并在配置中通过 `COOKIES_FILE` 指定其路径。

**Q: 为什么每次只下载 5 个视频？**
A: 这是为了防止触发 YouTube 的频率限制或 R2 的大量上传。你可以通过 `MAX_NEW_VIDEOS` 调整此数值。

## 开源协议

MIT