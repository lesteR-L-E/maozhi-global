"""创建贸智全球第一版固定演示数据库。

这个脚本只负责生成演示数据，不负责分析。真实数据到位后，数据组可以用同样的
表结构替换 CSV/数据库内容，而不需要改 Agent 工具的输入输出协议。
"""

from datetime import date
from pathlib import Path
import random

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "maozhi-demo.duckdb"


def make_dimensions() -> dict[str, pd.DataFrame]:
    """返回所有基础维表。"""
    country_rows = [
        (704, "VNM", "越南", "Asia", "东南亚"), (360, "IDN", "印度尼西亚", "Asia", "东南亚"),
        (608, "PHL", "菲律宾", "Asia", "东南亚"), (764, "THA", "泰国", "Asia", "东南亚"),
        (458, "MYS", "马来西亚", "Asia", "东南亚"), (702, "SGP", "新加坡", "Asia", "东南亚"),
        (104, "MMR", "缅甸", "Asia", "东南亚"), (116, "KHM", "柬埔寨", "Asia", "东南亚"),
        (418, "LAO", "老挝", "Asia", "东南亚"), (96, "BRN", "文莱", "Asia", "东南亚"),
        (156, "CHN", "中国", "Asia", "东亚"), (392, "JPN", "日本", "Asia", "东亚"),
        (410, "KOR", "韩国", "Asia", "东亚"), (36, "AUS", "澳大利亚", "Oceania", "大洋洲"),
        (554, "NZL", "新西兰", "Oceania", "大洋洲"), (840, "USA", "美国", "Americas", "北美洲"),
    ]
    country = pd.DataFrame(country_rows, columns=["m49", "iso3", "name_cn", "un_region", "un_subregion"])
    hs = pd.DataFrame([
        ("851671", "HS2022", "家用电热咖啡或茶饮机", "Electric coffee or tea makers"),
        ("851679", "HS2022", "其他电热器具", "Other electro-thermic appliances"),
    ], columns=["hs_code", "hs_version", "name_cn", "name_en"])
    province = pd.DataFrame([
        ("440000", "广东", 310_000_000), ("330000", "浙江", 240_000_000),
        ("320000", "江苏", 180_000_000), ("350000", "福建", 140_000_000),
        ("310000", "上海", 110_000_000), ("370000", "山东", 95_000_000),
        ("510000", "四川", 80_000_000), ("430000", "湖南", 66_000_000),
        ("420000", "湖北", 55_000_000), ("110000", "北京", 42_000_000),
    ], columns=["province_code", "province_name", "annual_export_value_usd"])
    return {"dim_country": country, "dim_hs_code": hs, "dim_province_cn": province}


def make_trade_data() -> dict[str, pd.DataFrame]:
    """生成五年年度、六十个月度和供应商拆分贸易事实。"""
    random.seed(20250915)
    market_info = {"VNM": 1.00, "IDN": 1.35, "PHL": 1.20, "THA": 1.10, "MYS": 0.90}
    market_m49 = {"VNM": 704, "IDN": 360, "PHL": 608, "THA": 764, "MYS": 458}
    partners = [(0, "WLD", 1.0), (156, "CHN", 0.22), (840, "USA", 0.18), (392, "JPN", 0.12), (702, "SGP", 0.10), (410, "KOR", 0.08), (999, "OTH", 0.30)]
    annual_rows = []
    monthly_rows = []
    growth = [0.86, 0.92, 1.00, 1.12, 1.24]
    for iso3, scale in market_info.items():
        reporter = market_m49[iso3]
        for year_index, year in enumerate(range(2021, 2026)):
            world_import = 820_000_000 * scale * growth[year_index]
            world_export = world_import * 0.42
            for flow, world_value in [("M", world_import), ("X", world_export)]:
                for partner_m49, partner_iso3, share in partners:
                    # partner=0 是 World 汇总，其他 partner 用于 HHI 和中国份额分析。
                    value = world_value if partner_m49 == 0 else world_value * share
                    annual_rows.append((str(year), "A", reporter, iso3, partner_m49, partner_iso3, "851671", "HS2022", flow, value, "UN Comtrade demo"))
        for month_index in range(60):
            year = 2021 + month_index // 12
            month = month_index % 12 + 1
            base = 820_000_000 * scale * growth[min(month_index // 12, 4)] / 12
            seasonal = 1 + 0.10 * __import__("math").sin(month_index % 12 / 12 * 2 * __import__("math").pi)
            value = base * seasonal * (0.82 + month_index * 0.0062) / growth[min(month_index // 12, 4)]
            monthly_rows.append((f"{year:04d}-{month:02d}", "M", reporter, iso3, 0, "WLD", "851671", "HS2022", "M", value, "UN Comtrade demo"))
    columns = ["period", "freq", "reporter_code", "reporter_iso3", "partner_code", "partner_iso3", "hs_code", "hs_version", "flow_code", "trade_value_usd", "source"]
    trade_annual = pd.DataFrame(annual_rows, columns=columns)
    trade_monthly = pd.DataFrame(monthly_rows, columns=columns)
    return {"fact_trade_flow_annual": trade_annual, "fact_trade_flow_monthly": trade_monthly}


def make_macro_and_tariff() -> dict[str, pd.DataFrame]:
    """生成市场评分使用的宏观和对华适用税率。"""
    gdp_base = {"VNM": 410e9, "IDN": 1_370e9, "PHL": 460e9, "THA": 515e9, "MYS": 430e9}
    macro = []
    tariff = []
    for index, iso3 in enumerate(gdp_base):
        for year in range(2021, 2026):
            macro.append((iso3, year, gdp_base[iso3] * (1 + 0.035 * (year - 2021)), 2.0 + index * 0.25, 20_000_000 + index * 5_000_000, gdp_base[iso3] / (20_000_000 + index * 5_000_000), "World Bank demo"))
        tariff.append((iso3, "CHN", "851671", 2025, 4.5 - index * 0.25, 3.0 - index * 0.2, 3.0 - index * 0.2, "RCEP demo", "Effectively Applied", "WITS demo"))
    return {
        "fact_macro": pd.DataFrame(macro, columns=["iso3", "year", "gdp_usd", "gdp_growth_rate", "population", "gdp_per_capita", "source"]),
        "fact_tariff": pd.DataFrame(tariff, columns=["reporter_market_code", "origin_country_code", "hs6", "year", "mfn_rate", "preferential_rate", "applied_rate", "agreement_name", "tax_type", "source"]),
    }


def make_customer_data() -> dict[str, pd.DataFrame]:
    """生成240个客户和两年交易事实，金额是模拟净销售额。"""
    random.seed(20250915)
    customers = []
    transactions = []
    markets = ["VNM", "IDN", "PHL", "THA", "MYS"]
    customer_types = ["END_USER", "DISTRIBUTOR", "WHOLESALER", "RETAILER"]
    observation = date(2025, 12, 31)
    for index in range(240):
        customer_id = f"CUST{index + 1:06d}"
        segment = index % 4
        market = markets[index % len(markets)]
        first_order = observation - pd.Timedelta(days=400 + index % 900)
        customers.append((customer_id, market, "SEA", "G47", customer_types[segment], ["SMALL", "MEDIUM", "MEDIUM", "LARGE"][segment], first_order.isoformat(), "ACTIVE", None))
        order_count = 3 + (index * 7) % 18
        last_gap = [18, 55, 145, 290][segment] + index % 20
        for order_index in range(order_count):
            order_date = observation - pd.Timedelta(days=last_gap + order_index * 40)
            amount = [18_000, 42_000, 85_000, 145_000][segment] * (0.75 + (index % 11) / 10) / order_count
            transactions.append((f"ORD{index + 1:06d}-{order_index + 1:03d}", 1, customer_id, market, order_date.isoformat(), "851671", round(amount, 2), round(amount, 2), "USD", "COMPLETED"))
    customer_columns = ["customer_id", "country_code", "region_code", "industry_code", "customer_type", "company_size", "first_order_date", "customer_status", "merged_into"]
    transaction_columns = ["order_id", "line_id", "customer_id", "country_code", "order_date", "hs6", "net_amount_usd", "original_amount", "original_currency", "order_status"]
    return {"dim_customer": pd.DataFrame(customers, columns=customer_columns), "fact_transaction": pd.DataFrame(transactions, columns=transaction_columns)}


def create_database() -> None:
    """创建数据库并写入所有演示表。"""
    DATA_DIR.mkdir(exist_ok=True)
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
    connection = duckdb.connect(str(DATABASE_PATH))
    all_tables = {}
    all_tables.update(make_dimensions())
    all_tables.update(make_trade_data())
    all_tables.update(make_macro_and_tariff())
    all_tables.update(make_customer_data())
    for table_name, frame in all_tables.items():
        connection.register("frame", frame)
        connection.execute(f"CREATE TABLE {table_name} AS SELECT * FROM frame")
        connection.unregister("frame")
    connection.execute("CREATE TABLE metadata AS SELECT 'v0.4.0-demo' AS dataset_version, 'SIMULATED_DEMO' AS source_type, '2025-12-31' AS observation_date")
    connection.close()
    print(f"created {DATABASE_PATH}")


if __name__ == "__main__":
    create_database()
