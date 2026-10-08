import os
import pandas as pd
import time
from sqlalchemy import text
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus


# ==========================
# 1. MySQL 配置
# ==========================
DB_MYSQL_96_HOST="172.22.121.11"
DB_MYSQL_96_PORT="43200"
DB_MYSQL_96_USER="ETL_test_user"
DB_MYSQL_96_PASSWORD="0893ce91-cf0c-4e7b-b824-eea8939ae917"
DB_MYSQL_96_DATABASE="personnel-matching-ETL-test"

TABLE_NAME = "employment_position"


# ==========================
# 2. Excel 文件夹路径
# ==========================
EXCEL_DIR = "/root/.cache/huggingface/csd/work/15、东西部协作/output"


# ==========================
# 3. 中文字段 -> 数据库字段
# ==========================
COLUMN_MAPPING = {
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
    "学历要求": "education_requirement",
    "工作经验要求": "experience_requirement",
    "工作性质": "employment_type",
    "招聘人数": "recruitment_number",
    "福利待遇": "benefits",
    "信息来源": "information_source",
    "信息来源代码": "information_source_id",
    "是否在招": "is_active",
}


# ==========================
# 4. 创建数据库连接
# ==========================
password = quote_plus(DB_MYSQL_96_PASSWORD)

engine = create_engine(
    f"mysql+pymysql://{DB_MYSQL_96_USER}:{password}@{DB_MYSQL_96_HOST}:{DB_MYSQL_96_PORT}/{DB_MYSQL_96_DATABASE}"
    f"?charset=utf8mb4",
    pool_pre_ping=True
)


def clean_dataframe(df):
    """
    清洗 Excel 数据
    """

    # 去除表头前后空格
    df.columns = df.columns.astype(str).str.strip()

    # 检查字段
    missing_columns = [
        col for col in COLUMN_MAPPING
        if col not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Excel 缺少字段: {missing_columns}"
        )

    # 只保留我们需要的字段
    df = df[list(COLUMN_MAPPING.keys())].copy()

    # 重命名为数据库字段
    df.rename(columns=COLUMN_MAPPING, inplace=True)

    # --------------------------
    # 职位ID、企业ID强制转字符串
    # 避免 Excel 把 ID 当数字处理
    # --------------------------
    for col in ["position_id", "company_id"]:
        df[col] = df[col].apply(
            lambda x: None
            if pd.isna(x)
            else str(x).strip()
        )

    # --------------------------
    # 发布时间处理
    # --------------------------
    df["publish_time"] = pd.to_datetime(
        df["publish_time"],
        errors="coerce"
    )

    # NaT 转 None
    df["publish_time"] = df["publish_time"].apply(
        lambda x: None if pd.isna(x) else x
    )

    # --------------------------
    # 招聘人数处理
    # --------------------------
    df["recruitment_number"] = pd.to_numeric(
        df["recruitment_number"],
        errors="coerce"
    )

    df["recruitment_number"] = df["recruitment_number"].apply(
        lambda x: None if pd.isna(x) else int(x)
    )

    # --------------------------
    # 其他 NaN -> None
    # --------------------------
    df = df.astype(object).where(
        pd.notnull(df),
        None
    )

    return df


def insert_data(df):
    """
    分批写入 MySQL
    每个批次单独一个事务
    某个批次失败时自动重试
    """

    sql = """
    INSERT INTO employment_position (
        position_name,
        company_name,
        industry,
        region,
        skill_requirements,
        position_requirements,
        salary_range,
        publish_time,
        position_id,
        company_id,
        company_detail_id,
        education_requirement,
        experience_requirement,
        employment_type,
        recruitment_number,
        benefits,
        information_source,
        information_source_id,
        is_active
    )
    VALUES (
        :position_name,
        :company_name,
        :industry,
        :region,
        :skill_requirements,
        :position_requirements,
        :salary_range,
        :publish_time,
        :position_id,
        :company_id,
        :company_detail_id,
        :education_requirement,
        :experience_requirement,
        :employment_type,
        :recruitment_number,
        :benefits,
        :information_source,
        :information_source_id,
        :is_active
    )
    """

    records = df.to_dict(orient="records")

    batch_size = 500

    total = len(records)
    success_count = 0

    for start in range(0, total, batch_size):

        batch = records[start:start + batch_size]

        batch_no = start // batch_size + 1
        total_batch = (total + batch_size - 1) // batch_size

        print(
            f"正在写入批次 {batch_no}/{total_batch}，"
            f"数据 {start + 1} ~ {min(start + batch_size, total)}"
        )

        # 每个批次最多重试3次
        for retry in range(3):

            try:

                # 注意：
                # 每一个批次重新开启一个事务
                with engine.begin() as conn:
                    result = conn.execute(
                        text(sql),
                        batch
                    )

                success_count += len(batch)

                print(
                    f"批次 {batch_no} 写入成功，"
                    f"当前处理：{success_count}/{total}"
                )

                break

            except Exception as e:

                print(
                    f"批次 {batch_no} 写入失败，"
                    f"第 {retry + 1}/3 次尝试"
                )

                print(f"错误：{e}")

                # 清除连接池中的失效连接
                engine.dispose()

                if retry < 2:
                    print("等待5秒后重新连接数据库...")
                    time.sleep(5)

                else:
                    raise

    print(
        f"写入完成，共处理 {success_count} 条数据"
    )


def import_excel_file(file_path):
    """
    导入一个 Excel
    """

    filename = os.path.basename(file_path)

    print("=" * 80)
    print(f"开始处理：{filename}")

    try:
        df = pd.read_excel(
            file_path,
            sheet_name="岗位数据",
            dtype={
                "职位ID": str,
                "企业ID": str
            }
        )

        print(f"读取到 {len(df)} 条数据")

        df = clean_dataframe(df)

        # 删除职位ID为空的数据
        before_count = len(df)

        df = df[
            df["position_id"].notna()
            & (df["position_id"] != "")
        ]

        removed_count = before_count - len(df)

        if removed_count:
            print(
                f"职位ID为空，跳过 {removed_count} 条"
            )

        if df.empty:
            print("没有可写入的数据")
            return

        insert_data(df)

        print(
            f"成功写入 {len(df)} 条数据"
        )

    except Exception as e:
        print(
            f"处理失败：{filename}"
        )
        print(
            f"错误：{e}"
        )


def main():

    files = [
        filename
        for filename in os.listdir(EXCEL_DIR)
        if filename.lower().endswith(".xlsx")
        and not filename.startswith("~$")
    ]

    print(
        f"共发现 {len(files)} 个 Excel 文件"
    )

    total = len(files)

    for index, filename in enumerate(files, start=1):

        file_path = os.path.join(
            EXCEL_DIR,
            filename
        )

        print(
            f"\n[{index}/{total}]"
        )

        import_excel_file(file_path)

    print("\n" + "=" * 80)
    print("全部 Excel 处理完成")


if __name__ == "__main__":
    main()