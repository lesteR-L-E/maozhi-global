"""根据数据组交付文件构建真实业务数据库。"""

from argparse import ArgumentParser
from pathlib import Path
from shutil import copy2

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DATABASE = PROJECT_ROOT / "data" / "maozhi-real.duckdb"

REQUIRED_SOURCE_FILES = (
    "trade.duckdb",
    "dim_country.csv",
    "dim_hs_code.csv",
    "dim_province_cn.csv",
    "fact_macro.csv",
)
REQUIRED_TARIFF_COLUMNS = (
    "reporter_m49",
    "reporter_iso3",
    "hs_code",
    "mfn_year",
    "mfn_rate",
    "mfn_type",
    "ahs_year",
    "ahs_rate",
    "ahs_type",
    "mfn_note",
    "ahs_note",
    "source",
)
EXPECTED_RCEP_REPORTERS = frozenset({
    "AUS",
    "BRN",
    "CHN",
    "IDN",
    "JPN",
    "KHM",
    "KOR",
    "LAO",
    "MMR",
    "MYS",
    "NZL",
    "PHL",
    "SGP",
    "THA",
    "VNM",
})
REQUIRED_COUNTRY_COLUMNS = (
    "m49_code",
    "iso3",
    "iso2",
    "name_cn",
    "name_en",
    "un_region",
    "un_subregion",
    "in_scope",
    "sovereignty",
    "aliases",
)
REQUIRED_HS_COLUMNS = (
    "hs6_std",
    "name_cn",
    "name_en",
    "hs2",
    "hs4",
    "chapter_name",
    "keywords",
)

REQUIRED_MACRO_COLUMNS = (
    "country_m49",
    "country_iso3",
    "year",
    "gdp",
    "gdp_growth",
    "population",
    "trade_gdp_pct",
    "source",
)

REQUIRED_DATABASE_COLUMNS = {
    "fact_trade_flow": {
        "year",
        "reporter_m49",
        "partner_m49",
        "reporter_iso3",
        "partner_iso3",
        "hs_code",
        "flow",
        "trade_value",
        "is_aggregate",
        "is_reported",
        "source",
    },
    "fact_trade_monthly": {
        "year",
        "month",
        "reporter_m49",
        "partner_m49",
        "reporter_iso3",
        "partner_iso3",
        "hs_code",
        "flow",
        "trade_value",
    },
    "preagg_market": {
        "hs_code",
        "reporter_m49",
        "year",
        "market_size",
        "china_share",
        "hhi",
        "top3_share",
    },
    "china_tc": {
        "hs_code",
        "year",
        "tc",
    },
    "dim_customer": {
        "customer_id",
        "country_iso3",
        "customer_type",
        "company_size",
        "segment",
        "data_note",
    },
    "fact_transaction": {
        "order_id",
        "line_id",
        "customer_id",
        "order_date",
        "hs_code",
        "net_sales_usd",
        "status",
        "country_iso3",
        "data_note",
    },
    "metadata": {
    "dataset_version",
    "source_type",
    "observation_date",
    "tariff_year",
    "target_hs6",
    "market_data_type",
    "customer_data_type",
    },
    "fact_tariff": set(REQUIRED_TARIFF_COLUMNS),
    "dim_country": set(REQUIRED_COUNTRY_COLUMNS),
    "dim_hs_code": set(REQUIRED_HS_COLUMNS),
    "fact_macro": set(REQUIRED_MACRO_COLUMNS),
}

DATASET_VERSION = "v1.0.0-integrated"
DATASET_OBSERVATION_DATE = "2026-09-21"
TARGET_HS6 = "851671"




def parse_args():
    """读取跨平台命令行参数。"""
    parser = ArgumentParser(
        description="根据数据组交付文件构建贸智全球真实数据库。"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="数据交付包解压目录，目录中应包含 trade.duckdb 和各 CSV。",
    )
    parser.add_argument(
        "--tariff-csv",
        type=Path,
        required=True,
        help="审计完成的 fact_tariff_final.csv 路径。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DATABASE,
        help="输出 DuckDB 路径。",
    )
    return parser.parse_args()

def validate_inputs(args):
    """检查构建真实数据库所需的输入文件。"""
    source_dir = args.source_dir.expanduser().resolve()
    tariff_csv = args.tariff_csv.expanduser().resolve()
    output_database = args.output.expanduser().resolve()

    if not source_dir.is_dir():
        raise FileNotFoundError(f"数据包目录不存在：{source_dir}")

    missing_files = [
        source_dir / filename
        for filename in REQUIRED_SOURCE_FILES
        if not (source_dir / filename).is_file()
    ]

    if missing_files:
        missing_text = "\n".join(str(path) for path in missing_files)
        raise FileNotFoundError(f"数据包缺少以下文件：\n{missing_text}")

    if not tariff_csv.is_file():
        raise FileNotFoundError(f"关税文件不存在：{tariff_csv}")

    if tariff_csv.suffix.lower() != ".csv":
        raise ValueError(f"关税文件必须是 CSV：{tariff_csv}")

    source_database = source_dir / "trade.duckdb"
    if output_database == source_database:
        raise ValueError("输出数据库不能覆盖原始 trade.duckdb。")

    return source_dir, tariff_csv, output_database


def load_validated_tariff(tariff_csv: Path) -> pd.DataFrame:
    """读取最终关税表并检查结构和数据覆盖。"""
    frame = pd.read_csv(
        tariff_csv,
        dtype={
            "reporter_m49": "string",
            "reporter_iso3": "string",
            "hs_code": "string",
        },
    )

    missing_columns = set(REQUIRED_TARIFF_COLUMNS) - set(frame.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"关税表缺少字段：{missing_text}")

    if len(frame) != 15:
        raise ValueError(f"关税表应有15行，实际为{len(frame)}行。")

    reporters = set(frame["reporter_iso3"].dropna())
    if reporters != EXPECTED_RCEP_REPORTERS:
        missing = sorted(EXPECTED_RCEP_REPORTERS - reporters)
        unexpected = sorted(reporters - EXPECTED_RCEP_REPORTERS)
        raise ValueError(
            f"Reporter覆盖不正确。缺少：{missing}；多出：{unexpected}"
        )

    hs_codes = set(frame["hs_code"].dropna())
    if hs_codes != {"851671"}:
        raise ValueError(f"关税表HS编码不正确：{sorted(hs_codes)}")

    if frame.duplicated(["reporter_iso3", "hs_code"]).any():
        raise ValueError("关税表存在重复的Reporter和HS编码组合。")

    frame["mfn_year"] = pd.to_numeric(
        frame["mfn_year"], errors="raise"
    ).astype("Int64")
    frame["ahs_year"] = pd.to_numeric(
        frame["ahs_year"], errors="raise"
    ).astype("Int64")
    frame["mfn_rate"] = pd.to_numeric(
        frame["mfn_rate"], errors="raise"
    )
    frame["ahs_rate"] = pd.to_numeric(
        frame["ahs_rate"], errors="raise"
    )

    if frame["mfn_rate"].notna().sum() != 15:
        raise ValueError("MFN必须15/15非空。")

    non_china = frame[frame["reporter_iso3"] != "CHN"]
    if len(non_china) != 14 or non_china["ahs_rate"].notna().sum() != 14:
        raise ValueError("非中国Reporter的AHS必须14/14非空。")

    china = frame[frame["reporter_iso3"] == "CHN"]
    china_ahs_columns = ["ahs_year", "ahs_rate", "ahs_type"]
    if len(china) != 1 or not china[china_ahs_columns].isna().all().all():
        raise ValueError("CHN的AHS年份、税率和类型必须保持为空。")

    mfn_years = set(frame["mfn_year"].dropna().astype(int))
    ahs_years = set(frame["ahs_year"].dropna().astype(int))
    if len(mfn_years) != 1 or mfn_years != ahs_years:
        raise ValueError(
            f"MFN与AHS年份不一致：MFN={mfn_years}，AHS={ahs_years}"
        )

    for column in ("mfn_rate", "ahs_rate"):
        available = frame[column].dropna()
        if not available.between(0, 100).all():
            raise ValueError(f"{column}存在超出0至100范围的值。")

    return frame


def load_validated_country(source_dir: Path) -> pd.DataFrame:
    """读取国家维表并检查关键字段。"""
    frame = pd.read_csv(
        source_dir / "dim_country.csv",
        dtype={
            "m49_code": "string",
            "iso3": "string",
            "iso2": "string",
        },
    )

    missing_columns = set(REQUIRED_COUNTRY_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"国家维表缺少字段：{sorted(missing_columns)}"
        )

    if frame["m49_code"].isna().any():
        raise ValueError("国家维表存在空的M49代码。")

    if frame["m49_code"].duplicated().any():
        raise ValueError("国家维表存在重复的M49代码。")

    missing_reporters = (
        EXPECTED_RCEP_REPORTERS
        - set(frame["iso3"].dropna())
    )
    if missing_reporters:
        raise ValueError(
            f"国家维表缺少RCEP国家：{sorted(missing_reporters)}"
        )

    return frame[list(REQUIRED_COUNTRY_COLUMNS)].copy()


def load_validated_hs(source_dir: Path) -> pd.DataFrame:
    """读取HS维表并检查关键字段。"""
    frame = pd.read_csv(
        source_dir / "dim_hs_code.csv",
        dtype={
            "hs6_std": "string",
            "hs2": "string",
            "hs4": "string",
        },
    )

    missing_columns = set(REQUIRED_HS_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"HS维表缺少字段：{sorted(missing_columns)}"
        )

    if frame["hs6_std"].isna().any():
        raise ValueError("HS维表存在空的HS6编码。")

    if frame["hs6_std"].duplicated().any():
        raise ValueError("HS维表存在重复的HS6编码。")

    if "851671" not in set(frame["hs6_std"]):
        raise ValueError("HS维表中不存在目标商品851671。")

    return frame[list(REQUIRED_HS_COLUMNS)].copy()


def load_validated_macro(source_dir: Path) -> pd.DataFrame:
    """读取宏观事实表并检查关键字段。"""
    frame = pd.read_csv(
        source_dir / "fact_macro.csv",
        dtype={
            "country_m49": "string",
            "country_iso3": "string",
        },
    )

    missing_columns = set(REQUIRED_MACRO_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"宏观表缺少字段：{sorted(missing_columns)}"
        )

    key_columns = ["country_m49", "country_iso3", "year"]
    if frame[key_columns].isna().any().any():
        raise ValueError("宏观表存在空的国家代码或年份。")

    if frame.duplicated(["country_m49", "year"]).any():
        raise ValueError("宏观表存在重复的国家和年份组合。")

    reporters = set(frame["country_iso3"])
    if reporters != EXPECTED_RCEP_REPORTERS:
        raise ValueError(
            "宏观表的RCEP15国家覆盖不完整。"
        )

    frame["year"] = pd.to_numeric(
        frame["year"], errors="raise"
    ).astype("Int64")
    frame["gdp"] = pd.to_numeric(
        frame["gdp"], errors="raise"
    )
    frame["gdp_growth"] = pd.to_numeric(
        frame["gdp_growth"], errors="raise"
    )
    frame["population"] = pd.to_numeric(
        frame["population"], errors="raise"
    ).astype("Int64")
    frame["trade_gdp_pct"] = pd.to_numeric(
        frame["trade_gdp_pct"], errors="raise"
    )

    required_values = ["gdp", "gdp_growth", "population"]
    if frame[required_values].isna().any().any():
        raise ValueError("宏观表的GDP、增长率或人口存在缺失。")

    return frame[list(REQUIRED_MACRO_COLUMNS)].copy()


def replace_tariff_table(
    working_database: Path,
    tariff_frame: pd.DataFrame,
) -> None:
    """用审计后的最终关税数据替换工作数据库中的旧关税表。"""
    connection = duckdb.connect(str(working_database))
    frame_registered = False

    try:
        connection.execute("BEGIN TRANSACTION")

        connection.register("tariff_frame", tariff_frame)
        frame_registered = True

        connection.execute("DROP TABLE IF EXISTS fact_tariff")
        connection.execute(
            """
            CREATE TABLE fact_tariff AS
            SELECT
                CAST(reporter_m49 AS VARCHAR) AS reporter_m49,
                CAST(reporter_iso3 AS VARCHAR) AS reporter_iso3,
                CAST(hs_code AS VARCHAR) AS hs_code,
                CAST(mfn_year AS INTEGER) AS mfn_year,
                CAST(mfn_rate AS DOUBLE) AS mfn_rate,
                CAST(mfn_type AS VARCHAR) AS mfn_type,
                CAST(ahs_year AS INTEGER) AS ahs_year,
                CAST(ahs_rate AS DOUBLE) AS ahs_rate,
                CAST(ahs_type AS VARCHAR) AS ahs_type,
                CAST(mfn_note AS VARCHAR) AS mfn_note,
                CAST(ahs_note AS VARCHAR) AS ahs_note,
                CAST(source AS VARCHAR) AS source
            FROM tariff_frame
            """
        )

        result = connection.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT reporter_iso3) AS unique_reporters,
                COUNT(mfn_rate) AS mfn_non_missing,
                COUNT(ahs_rate) AS ahs_non_missing
            FROM fact_tariff
            """
        ).fetchone()

        expected = (15, 15, 15, 14)
        if result != expected:
            raise ValueError(
                f"写入后的关税表检查失败：实际={result}，预期={expected}"
            )

        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        if frame_registered:
            connection.unregister("tariff_frame")
        connection.close()


def replace_dimension_table(
    working_database: Path,
    table_name: str,
    frame: pd.DataFrame,
    key_column: str,
) -> None:
    """将已验证的维表写入工作数据库。"""
    allowed_tables = {
        "dim_country": "m49_code",
        "dim_hs_code": "hs6_std",
    }

    if allowed_tables.get(table_name) != key_column:
        raise ValueError(f"不允许写入未知维表：{table_name}")

    connection = duckdb.connect(str(working_database))
    frame_registered = False

    try:
        connection.execute("BEGIN TRANSACTION")
        connection.register("dimension_frame", frame)
        frame_registered = True

        connection.execute(f"DROP TABLE IF EXISTS {table_name}")
        connection.execute(
            f"CREATE TABLE {table_name} AS "
            "SELECT * FROM dimension_frame"
        )

        result = connection.execute(
            f"""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT {key_column}) AS unique_keys
            FROM {table_name}
            """
        ).fetchone()

        expected = (len(frame), len(frame))
        if result != expected:
            raise ValueError(
                f"{table_name}写入检查失败："
                f"实际={result}，预期={expected}"
            )

        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        if frame_registered:
            connection.unregister("dimension_frame")
        connection.close()


def replace_macro_table(
    working_database: Path,
    macro_frame: pd.DataFrame,
) -> None:
    """将宏观事实表写入工作数据库。"""
    connection = duckdb.connect(str(working_database))
    frame_registered = False

    try:
        connection.execute("BEGIN TRANSACTION")
        connection.register("macro_frame", macro_frame)
        frame_registered = True

        connection.execute("DROP TABLE IF EXISTS fact_macro")
        connection.execute(
            """
            CREATE TABLE fact_macro AS
            SELECT * FROM macro_frame
            """
        )

        written_rows = connection.execute(
            "SELECT COUNT(*) FROM fact_macro"
        ).fetchone()[0]

        if written_rows != len(macro_frame):
            raise ValueError(
                "宏观表写入行数不一致："
                f"实际={written_rows}，预期={len(macro_frame)}"
            )

        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        if frame_registered:
            connection.unregister("macro_frame")
        connection.close()


def replace_metadata_table(
    working_database: Path,
    tariff_year: int,
) -> None:
    """记录数据库版本和数据真实性边界。"""
    connection = duckdb.connect(str(working_database))

    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute("DROP TABLE IF EXISTS metadata")
        connection.execute(
            """
            CREATE TABLE metadata (
                dataset_version VARCHAR,
                source_type VARCHAR,
                observation_date DATE,
                tariff_year INTEGER,
                target_hs6 VARCHAR,
                market_data_type VARCHAR,
                customer_data_type VARCHAR
            )
            """
        )
        connection.execute(
            """
            INSERT INTO metadata
            VALUES (?, ?, CAST(? AS DATE), ?, ?, ?, ?)
            """,
            [
                DATASET_VERSION,
                "MIXED",
                DATASET_OBSERVATION_DATE,
                tariff_year,
                TARGET_HS6,
                "PUBLIC_DATA",
                "SIMULATED_DEMO",
            ],
        )

        row_count = connection.execute(
            "SELECT COUNT(*) FROM metadata"
        ).fetchone()[0]

        if row_count != 1:
            raise ValueError("metadata表必须有且只有一行。")

        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def validate_working_database(
    working_database: Path,
) -> dict[str, int]:
    """统一验收工具运行需要的数据库表。"""
    connection = duckdb.connect(
        str(working_database),
        read_only=True,
    )

    try:
        available_tables = {
            row[0]
            for row in connection.execute("SHOW TABLES").fetchall()
        }

        missing_tables = (
            set(REQUIRED_DATABASE_COLUMNS) - available_tables
        )
        if missing_tables:
            raise ValueError(
                f"数据库缺少表：{sorted(missing_tables)}"
            )

        for table_name, required_columns in (
            REQUIRED_DATABASE_COLUMNS.items()
        ):
            actual_columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = ?
                    """,
                    [table_name],
                ).fetchall()
            }

            missing_columns = required_columns - actual_columns
            if missing_columns:
                raise ValueError(
                    f"{table_name}缺少字段："
                    f"{sorted(missing_columns)}"
                )

        stats = {
            "annual_trade_rows": connection.execute(
                "SELECT COUNT(*) FROM fact_trade_flow"
            ).fetchone()[0],
            "monthly_trade_rows": connection.execute(
                "SELECT COUNT(*) FROM fact_trade_monthly"
            ).fetchone()[0],
            "market_rows": connection.execute(
                "SELECT COUNT(*) FROM preagg_market"
            ).fetchone()[0],
            "customer_rows": connection.execute(
                "SELECT COUNT(*) FROM dim_customer"
            ).fetchone()[0],
            "transaction_rows": connection.execute(
                "SELECT COUNT(*) FROM fact_transaction"
            ).fetchone()[0],
        }

        if any(value <= 0 for value in stats.values()):
            raise ValueError("数据库中存在空的核心业务表。")

        target_reporters = connection.execute(
            """
            SELECT COUNT(DISTINCT reporter_iso3)
            FROM fact_trade_flow
            WHERE hs_code = '851671'
              AND flow = 'Import'
            """
        ).fetchone()[0]

        if target_reporters != 15:
            raise ValueError(
                "851671年度贸易数据未覆盖RCEP15国："
                f"实际覆盖{target_reporters}国。"
            )

        thai_china_years = connection.execute(
            """
            SELECT COUNT(DISTINCT year)
            FROM fact_trade_flow
            WHERE reporter_iso3 = 'THA'
              AND partner_iso3 = 'CHN'
              AND hs_code = '851671'
              AND flow = 'Import'
              AND year IN (2020, 2023)
            """
        ).fetchone()[0]

        if thai_china_years != 2:
            raise ValueError(
                "泰国851671在2020或2023年缺少中国伙伴记录。"
            )

        hhi_range = connection.execute(
            """
            SELECT MIN(hhi), MAX(hhi)
            FROM preagg_market
            WHERE hs_code = '851671'
            """
        ).fetchone()

        if (
            hhi_range[0] is None
            or hhi_range[1] is None
            or hhi_range[0] < 0
            or hhi_range[1] > 10000
        ):
            raise ValueError(
                f"preagg_market的HHI范围异常：{hhi_range}"
            )

        invalid_customers = connection.execute(
            """
            SELECT COUNT(*)
            FROM dim_customer
            WHERE data_note IS NULL
               OR data_note <> '比赛演示模拟数据'
            """
        ).fetchone()[0]

        invalid_transactions = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_transaction
            WHERE data_note IS NULL
               OR data_note <> '比赛演示模拟数据'
            """
        ).fetchone()[0]

        if invalid_customers or invalid_transactions:
            raise ValueError(
                "客户或交易表中存在未明确标记为模拟的数据。"
            )

        return stats
    finally:
        connection.close()


def create_working_database(source_dir: Path, output_database: Path) -> Path:
    """复制原始数据库，后续只修改副本。"""
    source_database = source_dir / "trade.duckdb"

    output_database.parent.mkdir(parents=True, exist_ok=True)

    working_database = output_database.with_name(
        f"{output_database.stem}.building{output_database.suffix}"
    )

    if working_database.exists():
        working_database.unlink()

    copy2(source_database, working_database)
    return working_database


def finalize_database(
    working_database: Path,
    output_database: Path,
) -> Path:
    """将验收通过的工作数据库发布为正式数据库。"""
    if not working_database.is_file():
        raise FileNotFoundError(
            f"工作数据库不存在：{working_database}"
        )

    working_database.replace(output_database)
    return output_database

def main():
    args = parse_args()
    source_dir, tariff_csv, output_database = validate_inputs(args)

    tariff_frame = load_validated_tariff(tariff_csv)
    tariff_year = int(
    tariff_frame["mfn_year"].dropna().iloc[0]
    )
    country_frame = load_validated_country(source_dir)
    hs_frame = load_validated_hs(source_dir)
    macro_frame = load_validated_macro(source_dir)

    working_database = create_working_database(
        source_dir,
        output_database,
    )

    replace_tariff_table(
        working_database,
        tariff_frame,
    )

    replace_dimension_table(
    working_database,
    "dim_country",
    country_frame,
    "m49_code",
    )

    replace_dimension_table(
        working_database,
        "dim_hs_code",
        hs_frame,
        "hs6_std",
    )

    replace_macro_table(
        working_database,
        macro_frame,
    )

    replace_metadata_table(
    working_database,
    tariff_year,
    )

    database_stats = validate_working_database(
    working_database
    )

    final_database = finalize_database(
    working_database,
    output_database,
    )

    tariff_year = int(tariff_frame["mfn_year"].dropna().iloc[0])

    print("关税数据写入完成")
    print(f"正式数据库：{final_database}")
    print("关税记录：15")
    print(f"关税年份：{tariff_year}")
    print("MFN覆盖：15/15")
    print("适用AHS覆盖：14/14")
    print(f"国家维表：{len(country_frame)}行")
    print(f"HS维表：{len(hs_frame)}行")
    print(f"宏观事实表：{len(macro_frame)}行")
    print(
        "贸易占GDP缺失："
        f"{int(macro_frame['trade_gdp_pct'].isna().sum())}行"
    )
    print("数据库统一验收：通过")
    print(
        "年度贸易事实表："
        f"{database_stats['annual_trade_rows']:,}行"
    )
    print(
        "月度贸易事实表："
        f"{database_stats['monthly_trade_rows']:,}行"
    )
    print(
        "市场预聚合表："
        f"{database_stats['market_rows']:,}行"
    )
    print(
        "模拟客户表："
        f"{database_stats['customer_rows']:,}行"
    )
    print(
        "模拟交易表："
        f"{database_stats['transaction_rows']:,}行"
    )
if __name__ == "__main__":
    main()