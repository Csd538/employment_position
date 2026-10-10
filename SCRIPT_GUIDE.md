# 三个采集脚本说明

本项目从广东公共求职招聘服务平台采集岗位和企业信息，并将数据直接写入 MySQL。三个脚本分别负责岗位采集、企业详情补充和岗位上下架状态检查。

## 推荐执行顺序

```text
get_all_positions.py
        ↓
recruitment_company_detail.py
        ↓
recruitment_position_status.py
```

1. 先抓取岗位列表和岗位要求，写入 `employment_position`。
2. 再从岗位表提取企业详情 ID，补充企业信息到 `employment_recruitment_company`。
3. 最后检查当前非下架岗位，将已下架岗位的 `is_active` 更新为 `0`。

## 运行命令

在项目根目录执行：

```bash
uv sync --locked

uv run python -m employment_position.server.guangdong_public_job_platform.get_all_positions
uv run python -m employment_position.server.guangdong_public_job_platform.recruitment_company_detail
uv run python -m employment_position.server.guangdong_public_job_platform.recruitment_position_status
```

三个脚本相互独立，可以分别运行。首次使用前需要将 `.env.example` 复制为 `.env` 并填写 TEST_11 数据库配置。

## 公共机制

### 数据库

所有 SQL 和数据库连接统一放在 `src/employment_position/util/database.py`。

数据库配置从 `.env` 读取：

```dotenv
DB_MYSQL_TEST_11_HOST=
DB_MYSQL_TEST_11_PORT=3306
DB_MYSQL_TEST_11_USER=
DB_MYSQL_TEST_11_PASSWORD=
DB_MYSQL_TEST_11_DATABASE=
```

数据库操作默认最多尝试 5 次，每次间隔 1 秒。SQLAlchemy 开启 `pool_pre_ping`，连接池中的连接每 1800 秒回收一次。

### HTTP 请求

公共请求工具位于 `src/employment_position/util/http_util.py`：

- 单次请求默认超时 20 秒。
- 请求失败最多尝试 5 次，每次间隔 1 秒。
- 支持通过 `TUNNEL_PROXY_*` 配置隧道代理。
- 如果代理发生传输错误而直连成功，本次进程会停用该代理，后续请求直接访问网站。
- 为兼容目标网站的旧 TLS 配置，使用了兼容性 SSL 上下文。

代理为可选配置：

```dotenv
TUNNEL_PROXY_SERVER=
TUNNEL_PROXY_USERNAME=
TUNNEL_PROXY_PASSWORD=
```

### 日志

日志同时输出到控制台和项目根目录的 `logs/`：

```text
logs/get_all_positions.log
logs/recruitment_company_detail.log
logs/recruitment_position_status.log
```

日志使用 JSON Lines 格式并采用追加写入，因此历史运行记录不会自动清空。`logs/` 已加入 `.gitignore`。

### 并发配置

并发数、批次大小、请求间隔、抓取省份和刷新周期统一配置在：

```text
src/employment_position/util/config.py
```

并发数越大，请求越快，但也更容易触发网站限流、代理断开或本机连接数过多。调整后应观察对应日志中的失败率。

---

## 1. get_all_positions.py

### 功能

抓取各省份的在招岗位，补充岗位要求，并直接新增或更新到 `employment_position`。

主要数据包括：

- 岗位名称、企业名称、行业和地区
- 技能要求、岗位要求、学历和工作经验要求
- 薪资、招聘人数、福利和工作性质
- `position_id`、`company_id`、`company_detail_id`
- 信息来源及信息来源代码
- 岗位详情链接 `link`
- 在招状态 `is_active`

### 执行流程

1. 创建 TEST_11 数据库连接。
2. 检查岗位表是否存在 `link` 字段，缺少时自动添加。
3. 按 `PROVINCES` 中的顺序调度省份。
4. 每个省份分批并发抓取岗位列表页。
5. 使用接口字段 `bcb009`，即数据库字段 `position_id` 去重。
6. 遇到不足一页的数据时判定已经到达末页。
7. 根据每个岗位的 `position_id` 请求详情接口，补充字段 `acb22a` 作为岗位要求。
8. 一个省份的列表和详情全部处理完成后，才开始写入数据库。
9. 岗位按每批 500 条执行 UPSERT。
10. 所有省份结束后输出成功省份、失败省份和岗位数量统计。

### 详情链接生成规则

岗位详情链接由 `company_detail_id` 和 `position_id` 组成：

```text
https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/#/positionDetail?bbb911={company_detail_id}&bcb009={position_id}
```

### 去重与入库规则

抓取阶段按照 `position_id` 去重。数据库中 `position_id` 应具有唯一索引：

- 新 `position_id`：插入新岗位。
- 已存在的 `position_id`：更新该岗位字段。
- `company_id` 不是岗位唯一键，同一企业可以有多个岗位。
- 从在招列表重新抓到的岗位会写入 `is_active=1`，可以重新激活此前标记为下架、但再次出现在在招列表中的岗位。

日志中的“数据库已处理（新增或更新）”表示 UPSERT 处理数量，不等于纯新增数量。

### 当前并发参数

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `POSITION_PAGE_SIZE` | 200 | 岗位列表每页数量 |
| `POSITION_BATCH_SIZE` | 10 | 每轮并发请求的页数 |
| `POSITION_PROVINCE_CONCURRENCY` | 1 | 同时处理的省份数 |
| `POSITION_DETAIL_CONCURRENCY` | 10 | 岗位详情最大并发数 |
| `POSITION_DETAIL_BATCH_SIZE` | 100 | 每轮详情处理数量 |
| `POSITION_MAX_PAGES` | 10000 | 单省最大安全页数 |

### 注意点

- 当前省份并发数为 1，因此按照 `PROVINCES` 顺序逐省执行。
- 列表批次中部分页面失败时会记录警告并跳过；整批页面全部失败时，该省份停止处理。
- 某个岗位详情请求失败时，该岗位仍会入库，但岗位要求可能为空。
- 入库不是逐页执行，而是在一个省份的岗位列表和详情全部完成后执行。
- 每 500 条岗位为一个独立事务。后续批次失败时，已经提交的前置批次不会自动回滚。
- 达到 `POSITION_MAX_PAGES` 仍未遇到末页时会主动报错，防止无限翻页。

---

## 2. recruitment_company_detail.py

### 功能

从岗位表读取企业详情 ID，请求企业详情接口，并写入 `employment_recruitment_company`。

主要数据包括：

- `company_detail_id`：企业详情唯一 ID，对应接口字段 `bbb911`
- `company_id`：企业业务 ID，对应接口字段 `aab001`
- `information_source_id`：信息来源代码，对应接口字段 `bze433`
- 企业名称、信息来源名称
- 企业介绍 HTML 和去除 HTML 后的纯文本
- 联系电话、企业详情链接和最近抓取时间

### 执行流程

1. 创建 TEST_11 数据库连接。
2. 不存在企业详情表时自动创建。
3. 从岗位表查询需要抓取的企业。
4. 按 `company_detail_id` 去重，并使用其他岗位记录补齐缺失的企业基础字段。
5. 分批并发请求企业详情接口。
6. 将企业介绍字段 `bab271` 同时保存为 HTML 和纯文本。
7. 每批请求完成后，将成功结果 UPSERT 到企业详情表。
8. 输出累计成功数和失败数。

### 企业筛选规则

以下企业会进入待抓取列表：

- 企业详情表中不存在该 `company_detail_id`。
- 企业介绍为空。
- `crawl_time` 为空。
- 距离上次成功抓取已经超过 `COMPANY_REFRESH_DAYS`。

默认刷新周期为 30 天。配置小于等于 0 时，每次运行都会刷新全部企业。

### 去重与入库规则

企业详情表使用 `company_detail_id` 作为唯一键：

- 新企业详情 ID：插入。
- 已存在的企业详情 ID：更新企业详情。
- 新响应中的空字符串不会覆盖数据库中已有的企业 ID、名称、介绍和联系电话。
- `detail_url` 和 `crawl_time` 在成功抓取后更新。

### 当前并发参数

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `COMPANY_CONCURRENCY` | 10 | 企业详情最大并发数 |
| `COMPANY_BATCH_SIZE` | 100 | 每批企业数量 |
| `COMPANY_REQUEST_INTERVAL_SECONDS` | 0.3 秒 | 每次请求结束后的等待时间 |
| `COMPANY_REFRESH_DAYS` | 30 天 | 企业详情刷新周期 |

### 注意点

- 该脚本依赖岗位表中的 `company_detail_id`，应在岗位脚本之后运行。
- 单个企业请求失败时会跳过该企业，不影响同批其他企业入库。
- 企业介绍纯文本使用 Python 标准库解析 HTML，会移除 `script` 和 `style` 内容，并合并多余空白。
- 企业详情接口成功但关键字段为空时，会优先保留岗位表或数据库中的已有基础字段。

---

## 3. recruitment_position_status.py

### 功能

检查数据库中尚未标记下架的岗位，并将确认下架的岗位更新为 `is_active=0`。

### 待检查岗位范围和顺序

查询条件为：

```sql
WHERE is_active != 0
  AND position_id IS NOT NULL
  AND TRIM(position_id) != ''
ORDER BY id ASC
```

因此：

- `is_active=0` 的岗位不会再次检查。
- `is_active=1` 或其他非零状态会检查。
- `is_active IS NULL` 不会进入检查列表。
- 按数据库自增主键 `id` 从小到大检查。
- 每批日志会记录序号范围和首尾职位 ID。

### 执行流程

1. 创建 TEST_11 数据库连接。
2. 检查岗位表是否存在 `is_active` 字段和 `idx_is_active` 索引，缺少时自动创建。
3. 按 `id ASC` 读取所有 `is_active != 0` 的岗位。
4. 每批取 100 条，并以最大并发数 10 请求岗位详情接口。
5. 将接口响应分类为 `online`、`offline` 或 `unknown`。
6. 只把 `offline` 岗位批量更新为 `is_active=0`。
7. 在线和未知状态不修改数据库。
8. 每批输出累计在线、下架和未知数量。

### 状态判断规则

| 接口响应 | 判定 | 数据库操作 |
|---|---|---|
| `appcode="200"`，返回的 `bcb009` 与请求职位 ID 一致 | `online` | 不更新 |
| `appcode="200"`，但 `bcb009` 为空 | `offline` | 更新 `is_active=0` |
| `appcode="10"` 且没有详情数据 | `offline` | 更新 `is_active=0` |
| 返回了其他职位 ID 或未知响应结构 | `unknown` | 不更新 |
| 请求异常或网络失败 | `unknown` | 不更新 |

下架接口可能仍返回 `appcode="200"` 和一个非空字典，但其中 `bcb009` 为 `null`，因此不能只根据 `data` 是否存在判断在线。

### 当前并发参数

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `POSITION_CHECK_CONCURRENCY` | 10 | 状态请求最大并发数 |
| `POSITION_CHECK_BATCH_SIZE` | 100 | 每批检查数量 |
| `POSITION_CHECK_REQUEST_INTERVAL_SECONDS` | 0.3 秒 | 每次请求结束后的等待时间 |

### 注意点

- 数据库中只有确认下架的岗位会发生 UPDATE。在线岗位不更新 `update_time`，所以观察数据库更新时间时会看到零散记录。
- 当前没有保存“最后检查到哪个岗位”的断点。任务中断后重新运行，会从仍满足 `is_active != 0` 的最小 `id` 开始；已经下架为 `0` 的岗位会被排除。
- 每批 100 条全部请求完成后，才统一更新其中的下架岗位。
- 请求失败和未知响应不会被误标为下架，可以在后续运行中再次检查。
- 日志文件采用追加模式，修复前或历史运行产生的错误记录仍会保留，应以日志时间和文件末尾的新记录为准。

## 运维建议

- 日常顺序建议：岗位抓取 → 企业详情 → 岗位状态检查。
- 岗位抓取可以按业务更新频率定时执行；企业详情可使用较低频率；岗位状态检查可独立定时执行。
- 首次全量运行前先使用较低并发观察网站和代理稳定性。
- 运行期间重点查看每批完成日志、失败数量、unknown 数量和数据库连接错误。
- `.env` 包含数据库和代理凭据，已经加入 `.gitignore`，不要提交到仓库。
- `.env.example` 只保留变量名和示例端口，不应填写真实密码。