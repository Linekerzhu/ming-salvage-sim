# 安全策略

## 上报漏洞

发现安全漏洞请**不要**开公开 issue。请私下联系仓库所有者，附：
- 漏洞描述与影响
- 复现步骤（最小用例）
- 建议的修复方向

我们会在 72 小时内确认收到，并在修复后致谢。

## 已知安全态势（2026-06-27 审计后）

本项目经 `/ccgs-security-audit` 全量审计（6 类：SQL/LLM/输入/鉴权/密钥/依赖），结论：
**0 CRITICAL，2 HIGH（均已处置），3 MEDIUM（均已处置），5 LOW**。

代码基础扎实：所有 SQL 用 `?` 参数化（标识符插值有白名单守卫）、口令用 `pbkdf2_sha256`（20 万轮）、LLM 提示注入隔离好（玩家文本走独立 agent message 不插值进 instructions）、无 `eval`/`exec`/`pickle`/`os.system` 危险 sink。

### 已处置项

| ID | 严重度 | 问题 | 处置 |
|----|--------|------|------|
| SEC-001 | HIGH | 硬编码默认注册邀请码 `shdl95598` 散布源码 | 已移除默认值；未设 `MING_SIM_INVITE_CODE` 时注册以 bad_invite_code 拒绝 |
| SEC-002 | HIGH | `.env` 明文存 API key | `.gitignore` 覆盖 + 历史无泄露（已核验）；**key 轮换须运营者手动在 DeepSeek/302.ai 控制台做** |
| SEC-003 | MEDIUM | `data/runtime_llm.json` 明文存 api_key | 写盘后 `chmod 0600`（仅属主可读写） |
| SEC-004 | MEDIUM | 生产环境 `/docs` `/redoc` `/openapi.json` 暴露路由结构 | 多用户服务器模式（`MING_SIM_SERVER_USERS`）下自动关闭 |
| SEC-005 | MEDIUM | legacy 无盐 `sha256:` 口令哈希仍被接受 | 命中时记弃用警告日志；新凭证强制 pbkdf2 |

### 部署前清单

服务器部署（多用户）前**必须**：
1. 设置 `MING_SIM_INVITE_CODE`（否则注册关闭）
2. 轮换 `.env` 里的 API key（不要用开发期的 key）
3. 设置 `MING_SIM_SERVER_USERS`（启用鉴权 + 自动关闭 docs）
4. 确认 `MING_SIM_DB` 指向受限目录，`data/runtime_llm.json` 权限 0600
5. 确认 `MING_DEBUG` 未设（否则开 devtools / 详细日志）

## 单机桌面 vs 服务器

- **单机桌面**（`launcher.py`，loopback `127.0.0.1`）：默认配置即安全——无多用户、无外部暴露、docs 便于本地调试。上述 HIGH 处置对单机不构成风险。
- **服务器部署**：上述清单是硬性要求。

## 安全相关技能

项目内置 `.agents/skills/ccgs-security-audit`（Ming 适配的安全审计技能），覆盖 SQL 注入 / LLM 提示注入 / FastAPI 鉴权 / 密钥泄露 / 依赖供应链。建议每次大改后跑一次 `/ccgs-security-audit quick`。
