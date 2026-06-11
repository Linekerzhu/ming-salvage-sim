# 生产部署运行手册

本文用于把网页模式从本地试玩提升到小规模服务器运营。

## 推荐部署形态

- 使用 `docker compose up --build -d` 启动服务。
- 将 `/app/data` 挂载为持久卷；容器内默认 `MING_SIM_DATA_DIR=/app/data`。
- 由 Nginx、Caddy、Traefik 或云负载均衡终止 HTTPS，再转发到容器 `8010`。
- 公网服务必须开启账号模式：配置 `MING_SIM_SERVER_USERS` 和 `MING_SIM_ADMIN_USERS`。

## 必要环境变量

| 变量 | 建议 |
| --- | --- |
| `OPENAI_API_KEY` | 服务端统一配置，不暴露给浏览器用户 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 按供应商与成本策略配置 |
| `MING_SIM_SERVER_USERS` | 使用 `pbkdf2_sha256$<iterations>$<salt>$<hex>` 密码 |
| `MING_SIM_ADMIN_USERS` | 明确列出管理员，不依赖默认首用户 |
| `MING_SIM_COOKIE_SECURE` | HTTPS 后设置为 `1` |
| `MING_SIM_SESSION_TTL_SECONDS` | 公网建议 86400 到 604800 |
| `MING_SIM_LOGIN_RATE_LIMIT_ATTEMPTS` | 默认 8 |
| `MING_SIM_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | 默认 300 |
| `MING_SIM_TRUST_PROXY_HEADERS` | 仅在可信反向代理后设置为 `1` |
| `MING_SIM_MAX_RUNNING_GAMES` | 默认 64；按机器内存和管理员能力调低 |
| `MING_SIM_MAX_CONCURRENT_TURNS` | 默认 2；限制同时颁诏结算的 LLM 重任务 |
| `MING_SIM_JSON_LOGS` | 容器/云日志建议 `1` |

## 健康检查

- `GET /healthz`：适合作为容器 liveness probe。
- `GET /readyz`：适合作为 readiness probe；会检查服务状态库、前端构建产物和内容目录。

`docker-compose.yml` 与 `Dockerfile` 都已配置 `/healthz` 健康检查。

## 状态与数据

- 主进度、存档、自定义立绘和服务状态库都在 `MING_SIM_DATA_DIR` 下。
- 多用户模式下，每个用户的数据位于 `users/<safe_user_id>/`。
- 登录 session 持久化在 `server_state.sqlite3`，服务重启后未过期 session 可恢复。
- 游戏进程内的运行中对局对象仍是单进程态；重启后用户需要从主进度或存档继续。
- 运行中对局和同时结算任务都有容量保护，超过上限时 API 返回 `503`，管理员可在 `/server-admin` 查看运行情况并关闭空闲对局。

## 备份

至少备份整个 `MING_SIM_DATA_DIR`：

```bash
tar -czf ming-data-$(date +%Y%m%d-%H%M%S).tgz data/
```

容器部署时可备份 Docker 卷：

```bash
docker run --rm -v ming_ming-data:/data -v "$PWD":/backup alpine \
  tar -czf /backup/ming-data-$(date +%Y%m%d-%H%M%S).tgz -C /data .
```

## 运营边界

当前服务器能力适合小规模私服、内测服或低并发付费社群。若要开放注册或承诺 SLA，还需要继续补：

- 独立用户系统与更细 RBAC。
- Redis/数据库中的运行任务状态。
- LLM 调用队列、取消、重试、成本配额。
- 结构化指标采集与告警。
- 数据库迁移体系和自动备份恢复演练。
