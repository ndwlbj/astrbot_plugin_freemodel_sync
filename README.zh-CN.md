# astrbot_plugin_freemodel_sync

[English](README.md)

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，把
[freemodel-sync](https://github.com/ndwlbj/freemodel-sync) 包装成几条**仅管理员可用**的聊天指令：
查看当前可用的免费大模型、把最好用的那个（加上备选）同步进你的工具配置，以及定时自动同步。

插件只是一个薄封装：每条指令都以独立进程运行 `python -m freemodel_sync`，行为与命令行完全一致
（文件锁、备份、退出码、日志脱敏）。同步哪些目标、怎么同步，仍然写在 freemodel-sync 自己的 YAML 配置里。

## 指令

所有指令都要求 AstrBot 管理员权限（`PermissionType.ADMIN`），非管理员在任何操作执行前就会被拒绝。

| 指令 | 作用 | 是否消耗免费额度 |
| --- | --- | --- |
| `/fms list [N]` | 过滤后的免费模型列表（`discover --json`） | 否（只拉模型列表） |
| `/fms rank [N]` | 基于缓存探测结果的当前选择与排名（`rank --json --no-probe`） | 否 |
| `/fms dryrun` | 预览同步会改什么（`sync --dry-run --no-notify`） | 否 |
| `/fms sync` | 立即发现、探测并写入所有目标（`sync`） | 是，少量探测请求 |
| `/fms status` | 自动同步开关、间隔、上次与下次运行时间 | 否 |
| `/fms auto on` / `off` | 开启/关闭定时同步；`on` 同时把当前会话设为结果通知会话 | – |

单独发送 `/fms` 会显示指令树。

## 安装

仓库地址：<https://github.com/ndwlbj/astrbot_plugin_freemodel_sync>

1. 在 AstrBot 中安装本插件，以下任选一种：
   - **从链接安装**（推荐）：在 WebUI 打开 **插件**，点击右下角的 **安装插件（+）**，选择 **从链接安装**，
     填入 `https://github.com/ndwlbj/astrbot_plugin_freemodel_sync`。
   - **从文件安装**：把仓库下载为 `.zip`，选择 **从文件安装** 上传。
   - 手动安装：把仓库克隆到 AstrBot 的 `data/plugins/` 目录，然后重启 AstrBot。

   AstrBot 会把 `requirements.txt`（`freemodel-sync>=0.2.0`）装进它自己的环境。
   `groq_api_key` 设置需要 freemodel-sync 0.3.0 及以上版本（在 AstrBot 的环境中执行 `pip install -U "freemodel-sync>=0.3.0"`）。
2. 按 freemodel-sync 的 README 创建配置，并先在命令行里试一次（`freemodel-sync init`，然后 `sync --dry-run`）。
3. 在插件设置里把 **freemodel-sync config file** 设为该路径（留空则使用 freemodel-sync 的默认值：
   `$FREEMODEL_SYNC_CONFIG` 或 `~/.config/freemodel-sync/config.yml`）。
4. API Key 可以作为 AstrBot 进程的环境变量提供（`OPENROUTER_API_KEY`、`GROQ_API_KEY`），也可以填在插件设置里。

如果 AstrBot 跑在 Docker 里，配置文件和所有目标路径都必须在**容器内**存在。

## 配置（`_conf_schema.json`）

| 键 | 默认值 | 含义 |
| --- | --- | --- |
| `fms_config_path` | `""` | freemodel-sync 的 YAML 配置 |
| `openrouter_api_key` | `""` | 可选；只作为 `OPENROUTER_API_KEY` 传给子进程 |
| `groq_api_key` | `""` | 可选；只作为 `GROQ_API_KEY` 传给子进程（需要带 Groq provider 的 freemodel-sync） |
| `auto_sync_enabled` | `false` | 定时同步 |
| `auto_sync_interval_minutes` | `360` | 间隔（分钟），最小 30 |
| `auto_sync_notify` | `true` | 把定时同步结果发到执行 `/fms auto on` 的会话 |
| `list_limit` | `15` | `list` / `rank` 显示的行数 |
| `timeout_seconds` | `600` | 超时后终止 freemodel-sync |

首次定时运行发生在插件加载（或 `/fms auto on`）之后一个间隔，而不是立即运行。
重叠运行会被拒绝（插件自身和 freemodel-sync 的文件锁都会拦截，退出码 4）。

## 密钥安全

- Key 字段标记了 `"secret": true`，WebUI 会遮挡显示；但 AstrBot 仍以明文保存在 `data/config/` 中，
  如果介意，请改用环境变量。
- 插件配置里的 Key 只传给 freemodel-sync 子进程，不会修改 AstrBot 自身的环境变量。
- 所有回复和日志都会经过脱敏：已配置的 Key 原值，以及任何形似 Key、Bearer token、`*_KEY=…`/`token: …`
  赋值、机器人 token 或 URL 中凭据的内容都会被替换为 `***`（包括 dry-run 中 `.env` 文件的 diff）。
  回复超过约 1800 字符会被截断。
- 插件本身不发起任何网络请求、没有遥测；只有 freemodel-sync 会访问你配置的模型服务商。

## AstrBot 自身的模型提供商

如果某个 freemodel-sync 目标是 AstrBot 的配置文件，AstrBot 通常需要重启或重载提供商后才会使用新模型。
本插件不会在内存中修改 AstrBot 的提供商设置。

## 开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest pyyaml ruff
ruff check . && ruff format --check .
pytest
```

测试会桩替 AstrBot API（`tests/conftest.py`），并用 `tests/fake_cli.py` 代替真实 CLI，
因此不需要安装 AstrBot、不需要联网、也不需要 API Key。如果同一解释器中装有 AstrBot，
`tests/test_real_astrbot.py` 还会用真实的 AstrBot 装饰器加载插件，检查每条指令都注册为仅管理员可用。

## 许可证

MIT
