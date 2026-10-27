# 广东公共求职招聘数据采集

从广东公共求职招聘服务平台采集岗位和企业信息，并写入 MySQL。项目支持异步并发、请求重试、代理访问、岗位新增或更新、企业详情补充和岗位上下架状态检查。

## 环境要求

- Python 3.12+
- MySQL
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

复制 `.env.example` 为 `.env`，然后配置：

- `DB_MYSQL_TEST_11_*`：MySQL 连接信息
- `TUNNEL_PROXY_*`：代理信息，不使用代理时可留空

抓取范围、并发数、批次大小和请求间隔在 `src/employment_position/util/config.py` 中配置。

## 创建虚拟环境

```
# 创建环境并安装锁定版本的依赖
uv sync --locked
```

# 激活虚拟环境

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux
source .venv/bin/activate
```

## 运行

Windows 和 Linux 使用相同命令，无需手动激活虚拟环境。建议按照以下顺序执行：

```bash
# ① 抓岗位
uv run python -m employment_position.server.guangdong_public_job_platform.get_all_positions

# ② 补企业详情（联系人 + 手机号）
uv run python -m employment_position.server.guangdong_public_job_platform.recruitment_company_detail

# ③ 填企业类型
uv run python -m employment_position.server.guangdong_public_job_platform.fill_company_type

# ④ 查岗位上下架
uv run python -m employment_position.server.guangdong_public_job_platform.recruitment_position_status
```
