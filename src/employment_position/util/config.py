"""招聘岗位、企业详情与岗位状态脚本的非敏感配置。"""

# ------------------------------ HTTP 通用配置 ------------------------------

# 请求超时（秒）
DEFAULT_TIMEOUT = 20

# 重试次数
RETRY_ATTEMPTS = 5

# 重试间隔（秒）
RETRY_WAIT_SECONDS = 1

# ------------------------------ 数据库配置 ------------------------------

DB_POOL_RECYCLE_SECONDS = 1800

POSITION_TABLE_NAME = "employment_position"
COMPANY_TABLE_NAME = "employment_recruitment_company"

POSITION_ID_COLUMN = "position_id"
POSITION_COMPANY_ID_COLUMN = "company_id"
POSITION_COMPANY_DETAIL_COLUMN = "company_detail_id"
POSITION_COMPANY_NAME_COLUMN = "company_name"
POSITION_INFORMATION_SOURCE_COLUMN = "information_source_id"
POSITION_SOURCE_NAME_COLUMN = "information_source"
POSITION_ACTIVE_COLUMN = "is_active"

# ------------------------------ 岗位列表抓取 ------------------------------

# 招聘岗位接口每页数量
POSITION_PAGE_SIZE = 200

# 每批并发抓取页数
POSITION_BATCH_SIZE = 10

# 完成一批岗位列表页面后的等待时间（秒）
POSITION_BATCH_INTERVAL_SECONDS = 0.5

# 岗位列表抓完后，访问公开详情接口补充 acb22a 岗位要求
POSITION_DETAIL_CONCURRENCY = 10
POSITION_DETAIL_BATCH_SIZE = 100
POSITION_DETAIL_REQUEST_INTERVAL_SECONDS = 0.3

# 安全页数上限，正常情况下会在遇到最后一页时提前停止
POSITION_MAX_PAGES = 10000

# Excel 输出目录
POSITION_OUTPUT_DIR = "output"

# ------------------------------ 企业详情抓取 ------------------------------

COMPANY_CONCURRENCY = 10
COMPANY_BATCH_SIZE = 100
COMPANY_REQUEST_INTERVAL_SECONDS = 0.3
COMPANY_REFRESH_DAYS = 30
COMPANY_ACCESS_PREFLIGHT_LIMIT = 5

# ------------------------------ 岗位状态检查 ------------------------------

POSITION_CHECK_CONCURRENCY = 10
POSITION_CHECK_BATCH_SIZE = 100
POSITION_CHECK_REQUEST_INTERVAL_SECONDS = 0.3
POSITION_ACCESS_PREFLIGHT_LIMIT = 5

# 省级统计用区划代码（12位）。
# 程序会按列表顺序逐省抓取；如只想抓部分省份，可删除或注释其他项。
PROVINCES = [
    ("北京市", "110000000000"),
    ("天津市", "120000000000"),
    ("河北省", "130000000000"),
    ("山西省", "140000000000"),
    ("内蒙古自治区", "150000000000"),
    ("辽宁省", "210000000000"),
    ("吉林省", "220000000000"),
    ("黑龙江省", "230000000000"),
    ("上海市", "310000000000"),
    ("江苏省", "320000000000"),
    ("浙江省", "330000000000"),
    ("安徽省", "340000000000"),
    ("福建省", "350000000000"),
    ("江西省", "360000000000"),
    ("山东省", "370000000000"),
    ("河南省", "410000000000"),
    ("湖北省", "420000000000"),
    ("湖南省", "430000000000"),
    ("广东省", "440000000000"),
    ("广西壮族自治区", "450000000000"),
    ("海南省", "460000000000"),
    ("重庆市", "500000000000"),
    ("四川省", "510000000000"),
    ("贵州省", "520000000000"),
    ("云南省", "530000000000"),
    ("西藏自治区", "540000000000"),
    ("陕西省", "610000000000"),
    ("甘肃省", "620000000000"),
    ("青海省", "630000000000"),
    ("宁夏回族自治区", "640000000000"),
    ("新疆维吾尔自治区", "650000000000"),
]
