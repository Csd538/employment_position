"""广东公共求职招聘服务平台的统一数据库操作。"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine
from tenacity import retry, stop_after_attempt, wait_fixed

from employment_position.util.config import (
    COMPANY_REFRESH_DAYS,
    COMPANY_TABLE_NAME,
    DB_POOL_RECYCLE_SECONDS,
    POSITION_ACTIVE_COLUMN,
    POSITION_COMPANY_DETAIL_COLUMN,
    POSITION_COMPANY_ID_COLUMN,
    POSITION_COMPANY_NAME_COLUMN,
    POSITION_ID_COLUMN,
    POSITION_INFORMATION_SOURCE_COLUMN,
    POSITION_SOURCE_NAME_COLUMN,
    POSITION_TABLE_NAME,
    RETRY_ATTEMPTS,
    RETRY_WAIT_SECONDS,
)

load_dotenv()

# ------------------------------ 数据库连接 ------------------------------

def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f".env中缺少数据库配置：{name}")
    return value


def create_database_engine() -> Engine:
    url = URL.create(
        drivername="mysql+pymysql",
        username=require_env("DB_MYSQL_TEST_11_USER"),
        password=require_env("DB_MYSQL_TEST_11_PASSWORD"),
        host=require_env("DB_MYSQL_TEST_11_HOST"),
        port=int(require_env("DB_MYSQL_TEST_11_PORT")),
        database=require_env("DB_MYSQL_TEST_11_DATABASE"),
        query={"charset": "utf8mb4"},
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=DB_POOL_RECYCLE_SECONDS,
    )


database_retry = retry(
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_fixed(RETRY_WAIT_SECONDS),
    reraise=True,
)


# ------------------------------ 数据结构 ------------------------------

@dataclass(frozen=True)
class PositionTarget:
    bcb009: str
    bbb911: str


POSITION_COLUMN_MAPPING = {
    "岗位名称": "position_name",
    "企业": "company_name",
    "行业": "industry",
    "地区": "region",
    "技能要求": "skill_requirements",
    "岗位要求": "position_requirements",
    "薪资范围": "salary_range",
    "发布时间": "publish_time",
    "职位ID": "position_id",
    "企业ID": "company_id",
    "企业详情ID": "company_detail_id",
    "link": "link",
    "学历要求": "education_requirement",
    "工作经验要求": "experience_requirement",
    "工作性质": "employment_type",
    "招聘人数": "recruitment_number",
    "福利待遇": "benefits",
    "信息来源": "information_source",
    "信息来源代码": "information_source_id",
    "是否在招": "is_active",
}



# ------------------------------ 岗位数据 ------------------------------

@database_retry
def ensure_position_link_column(engine: Engine) -> None:
    sql = text(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
          AND COLUMN_NAME = 'link'
        """
    )
    with engine.begin() as connection:
        exists = connection.execute(
            sql,
            {"table_name": POSITION_TABLE_NAME},
        ).scalar_one()
        if not exists:
            connection.execute(
                text(
                    f"""
                    ALTER TABLE `{POSITION_TABLE_NAME}`
                    ADD COLUMN link VARCHAR(1000) NULL COMMENT '岗位详情链接'
                    AFTER company_detail_id
                    """
                )
            )


def upsert_positions(
    engine: Engine,
    positions: list[dict[str, Any]],
    batch_size: int = 500,
) -> int:
    if not positions:
        return 0

    sql = text(
        f"""
        INSERT INTO `{POSITION_TABLE_NAME}` (
            position_name, company_name, industry, region, skill_requirements,
            position_requirements, salary_range, publish_time, position_id,
            company_id, company_detail_id, link, education_requirement,
            experience_requirement, employment_type, recruitment_number,
            benefits, information_source, information_source_id, is_active
        ) VALUES (
            :position_name, :company_name, :industry, :region,
            :skill_requirements, :position_requirements, :salary_range,
            :publish_time, :position_id, :company_id, :company_detail_id,
            :link, :education_requirement, :experience_requirement,
            :employment_type, :recruitment_number, :benefits,
            :information_source, :information_source_id, :is_active
        )
        ON DUPLICATE KEY UPDATE
            position_name = VALUES(position_name),
            company_name = VALUES(company_name),
            industry = VALUES(industry),
            region = VALUES(region),
            skill_requirements = VALUES(skill_requirements),
            position_requirements = VALUES(position_requirements),
            salary_range = VALUES(salary_range),
            publish_time = VALUES(publish_time),
            company_id = VALUES(company_id),
            company_detail_id = VALUES(company_detail_id),
            link = VALUES(link),
            education_requirement = VALUES(education_requirement),
            experience_requirement = VALUES(experience_requirement),
            employment_type = VALUES(employment_type),
            recruitment_number = VALUES(recruitment_number),
            benefits = VALUES(benefits),
            information_source = VALUES(information_source),
            information_source_id = VALUES(information_source_id),
            is_active = VALUES(is_active)
        """
    )
    records = [
        {
            database_name: position.get(output_name)
            for output_name, database_name in POSITION_COLUMN_MAPPING.items()
        }
        for position in positions
    ]

    for start in range(0, len(records), batch_size):
        _execute_batch(engine, sql, records[start : start + batch_size])
    return len(records)


@database_retry
def _execute_batch(engine: Engine, sql, records: list[dict[str, Any]]) -> None:
    with engine.begin() as connection:
        connection.execute(sql, records)


# ------------------------------ 企业详情数据 ------------------------------

@database_retry
def ensure_company_table(engine: Engine) -> None:
    sql = f"""
    CREATE TABLE IF NOT EXISTS `{COMPANY_TABLE_NAME}` (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '数据库自增主键',
        company_detail_id VARCHAR(64) NOT NULL COMMENT '企业详情ID',
        company_id VARCHAR(64) NULL COMMENT '企业业务编号',
        information_source_id VARCHAR(20) NULL COMMENT '信息来源代码',
        source_name VARCHAR(255) NULL COMMENT '信息来源名称',
        company_name VARCHAR(255) NULL COMMENT '企业名称',
        company_intro_html LONGTEXT NULL COMMENT '企业介绍HTML，来源字段bab271',
        company_intro_text LONGTEXT NULL COMMENT '去除HTML后的企业介绍',
        contact_phone VARCHAR(255) NULL COMMENT '联系电话，来源字段aae005',
        detail_url VARCHAR(1000) NULL COMMENT '企业详情页地址',
        crawl_time DATETIME NULL COMMENT '最近成功抓取时间',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_company_detail_id (company_detail_id),
        KEY idx_company_id (company_id),
        KEY idx_company_name (company_name),
        KEY idx_information_source_id (information_source_id)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci
      COMMENT='招聘企业详情信息表'
    """
    with engine.begin() as connection:
        connection.execute(text(sql))


@database_retry
def load_company_targets(engine: Engine) -> list[dict[str, str]]:
    """读取岗位表中的企业，只抓缺失、字段不全或已到刷新时间的企业。"""
    cutoff = datetime.now() - timedelta(days=COMPANY_REFRESH_DAYS)
    parameters: dict[str, Any] = {}

    if COMPANY_REFRESH_DAYS > 0:
        refresh_condition = " OR c.crawl_time IS NULL OR c.crawl_time < :cutoff"
        parameters["cutoff"] = cutoff
    else:
        refresh_condition = " OR c.company_detail_id IS NOT NULL"

    sql = text(
        f"""
        SELECT DISTINCT
            CAST(p.`{POSITION_COMPANY_DETAIL_COLUMN}` AS CHAR) AS company_detail_id,
            CAST(p.`{POSITION_INFORMATION_SOURCE_COLUMN}` AS CHAR)
                AS information_source_id,
            CAST(p.`{POSITION_COMPANY_ID_COLUMN}` AS CHAR) AS company_id,
            p.`{POSITION_COMPANY_NAME_COLUMN}` AS company_name,
            p.`{POSITION_SOURCE_NAME_COLUMN}` AS source_name
        FROM `{POSITION_TABLE_NAME}` AS p
        LEFT JOIN `{COMPANY_TABLE_NAME}` AS c
            ON c.company_detail_id = p.`{POSITION_COMPANY_DETAIL_COLUMN}`
                COLLATE utf8mb4_general_ci
        WHERE p.`{POSITION_COMPANY_DETAIL_COLUMN}` IS NOT NULL
          AND TRIM(CAST(p.`{POSITION_COMPANY_DETAIL_COLUMN}` AS CHAR)) <> ''
          AND (
              c.company_detail_id IS NULL
              OR c.company_intro_html IS NULL
              OR c.company_intro_html = ''
              {refresh_condition}
          )
        ORDER BY company_detail_id
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(sql, parameters).mappings().all()

    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        company_detail_id = str(row["company_detail_id"]).strip()
        candidate = {
            "company_detail_id": company_detail_id,
            "information_source_id": str(
                row.get("information_source_id") or ""
            ).strip(),
            "company_id": str(row.get("company_id") or "").strip(),
            "company_name": str(row.get("company_name") or "").strip(),
            "source_name": str(row.get("source_name") or "").strip(),
        }
        target = unique.setdefault(company_detail_id, candidate.copy())
        for field in (
            "information_source_id",
            "company_id",
            "company_name",
            "source_name",
        ):
            if not target[field] and candidate[field]:
                target[field] = candidate[field]

    return list(unique.values())


@database_retry
def upsert_companies(
    engine: Engine,
    companies: list[dict[str, Any]],
) -> None:
    if not companies:
        return

    sql = text(
        f"""
        INSERT INTO `{COMPANY_TABLE_NAME}` (
            company_detail_id,
            company_id,
            information_source_id,
            source_name,
            company_name,
            company_intro_html,
            company_intro_text,
            contact_phone,
            detail_url,
            crawl_time
        ) VALUES (
            :company_detail_id,
            :company_id,
            :information_source_id,
            :source_name,
            :company_name,
            :company_intro_html,
            :company_intro_text,
            :contact_phone,
            :detail_url,
            :crawl_time
        )
        ON DUPLICATE KEY UPDATE
            company_id = COALESCE(NULLIF(VALUES(company_id), ''), company_id),
            information_source_id = COALESCE(
                NULLIF(VALUES(information_source_id), ''),
                information_source_id
            ),
            source_name = COALESCE(NULLIF(VALUES(source_name), ''), source_name),
            company_name = COALESCE(NULLIF(VALUES(company_name), ''), company_name),
            company_intro_html = COALESCE(
                NULLIF(VALUES(company_intro_html), ''),
                company_intro_html
            ),
            company_intro_text = COALESCE(
                NULLIF(VALUES(company_intro_text), ''),
                company_intro_text
            ),
            contact_phone = COALESCE(
                NULLIF(VALUES(contact_phone), ''),
                contact_phone
            ),
            detail_url = VALUES(detail_url),
            crawl_time = VALUES(crawl_time)
        """
    )

    with engine.begin() as connection:
        connection.execute(sql, companies)


# ------------------------------ 岗位状态数据 ------------------------------

@database_retry
def ensure_active_column(engine: Engine) -> None:
    """不存在时添加在招状态列，并为定时扫描添加索引。"""
    check_sql = text(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
          AND COLUMN_NAME = :column_name
        """
    )

    with engine.begin() as connection:
        exists = connection.execute(
            check_sql,
            {
                "table_name": POSITION_TABLE_NAME,
                "column_name": POSITION_ACTIVE_COLUMN,
            },
        ).scalar_one()

        if not exists:
            connection.execute(
                text(
                    f"""
                    ALTER TABLE `{POSITION_TABLE_NAME}`
                    ADD COLUMN `{POSITION_ACTIVE_COLUMN}` TINYINT(1)
                    NOT NULL DEFAULT 1 COMMENT '岗位状态：1在招，0下架'
                    """
                )
            )

        index_exists = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table_name
                  AND INDEX_NAME = 'idx_is_active'
                """
            ),
            {
                "table_name": POSITION_TABLE_NAME,
            },
        ).scalar_one()
        if not index_exists:
            connection.execute(
                text(
                    f"""
                    ALTER TABLE `{POSITION_TABLE_NAME}`
                    ADD KEY `idx_is_active` (`{POSITION_ACTIVE_COLUMN}`)
                    """
                )
            )


@database_retry
def load_active_positions(engine: Engine) -> list[PositionTarget]:
    sql = text(
        f"""
        SELECT
            CAST(`{POSITION_ID_COLUMN}` AS CHAR) AS bcb009,
            CAST(`{POSITION_COMPANY_DETAIL_COLUMN}` AS CHAR) AS bbb911
        FROM `{POSITION_TABLE_NAME}`
        WHERE `{POSITION_ACTIVE_COLUMN}` != 0
          AND `{POSITION_ID_COLUMN}` IS NOT NULL
          AND TRIM(CAST(`{POSITION_ID_COLUMN}` AS CHAR)) <> ''
        ORDER BY id ASC
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(sql).mappings().all()

    unique: dict[str, PositionTarget] = {}
    for row in rows:
        bcb009 = str(row["bcb009"]).strip()
        bbb911 = str(row.get("bbb911") or "").strip()
        unique.setdefault(
            bcb009,
            PositionTarget(bcb009=bcb009, bbb911=bbb911),
        )
    return list(unique.values())


@database_retry
def mark_positions_offline(
    engine: Engine,
    position_ids: list[str],
) -> None:
    if not position_ids:
        return

    sql = text(
        f"""
        UPDATE `{POSITION_TABLE_NAME}`
        SET `{POSITION_ACTIVE_COLUMN}` = 0
        WHERE `{POSITION_ID_COLUMN}` = :bcb009
          AND `{POSITION_ACTIVE_COLUMN}` != 0
        """
    )
    with engine.begin() as connection:
        connection.execute(
            sql,
            [{"bcb009": position_id} for position_id in position_ids],
        )
