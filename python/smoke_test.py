"""四个业务工具的本地冒烟测试。

这个脚本不调用大模型，只验证：数据库存在、Worker能读取数据、四个方法能按
正确顺序返回成功结果，以及关键字段没有丢失。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "data" / "maozhi-real.duckdb"
WORKER = ROOT / "python" / "analysis_worker.py"
PYTHON = Path(sys.executable)


def main() -> None:
    if not DATABASE.exists():
        raise SystemExit(
            f"真实数据库不存在：{DATABASE}。"
            "请先运行 build_real_database.py。"
        )
    if not PYTHON.exists():
        raise SystemExit("当前Python解释器不存在，请检查虚拟环境。")

    requests = [
        {"method": "resolve_product", "params": {"product_name": "家用咖啡机"}},
        {"method": "resolve_product", "params": {"product_name": "纯电动乘用车"}},
        {
            "method": "resolve_product",
            "params": {
                "product_name": "纯电动乘用车",
                "search_terms_en": "vehicles with only electric motor for propulsion",
            },
        },
        {
            "method": "resolve_product",
            "params": {
                "product_name": "shavers with self-contained electric motor",
            },
        },
        {"method": "resolve_product", "params": {"product_name": "870380"}},
        {"method": "resolve_product", "params": {"product_name": "000000"}},
        {"method": "analyze_markets", "params": {"confirmed_hs6": "851671"}},
        {"method": "analyze_markets", "params": {"confirmed_hs6": "870380"}},
        {"method": "analyze_markets", "params": {"confirmed_hs6": "010619"}},
        {
            "method": "analyze_markets",
            "params": {
                "confirmed_hs6": "851671",
                "markets": ["AUS", "JPN"],
            },
        },
        {
            "method": "analyze_markets",
            "params": {
                "confirmed_hs6": "851671",
                "markets": ["USA"],
            },
        },
        {"method": "analyze_customers", "params": {}},
    ]
    environment = dict(os.environ)
    environment["MAOZHI_DATABASE"] = str(DATABASE)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        [str(PYTHON), str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    for request in requests:
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.write(json.dumps({"method": "recommend_operations", "params": {"target_market": "PHL"}, "_note": "先单独测试前置条件"}, ensure_ascii=False) + "\n")
    process.stdin.close()

    results = [json.loads(line) for line in process.stdout if line.strip()]
    process.wait(timeout=90)
    if len(results) != 13:
        raise SystemExit(f"Worker返回数量错误：{len(results)}")

    (
        coffee,
        untranslated_product,
        electric_vehicle,
        english_product,
        exact_hs,
        invalid_hs,
        markets,
        electric_markets,
        partial_markets,
        selected_markets,
        unsupported_market,
        customers,
        prerequisite,
    ) = results
    assert coffee["ok"] is True
    assert coffee["data"]["candidates"][0]["hs6"] == "851671"
    assert coffee["data"]["needs_confirmation"] is True
    assert untranslated_product["ok"] is True
    assert untranslated_product["data"]["candidates"] == []
    assert untranslated_product["data"]["needs_confirmation"] is True
    assert electric_vehicle["ok"] is True
    assert electric_vehicle["data"]["candidates"][0]["hs6"] == "870380"
    assert electric_vehicle["data"]["needs_confirmation"] is True
    assert english_product["ok"] is True
    assert english_product["data"]["candidates"][0]["hs6"] == "851010"
    assert exact_hs["ok"] is True
    assert exact_hs["data"]["candidates"][0]["hs6"] == "870380"
    assert exact_hs["data"]["needs_confirmation"] is False
    assert invalid_hs["ok"] is True
    assert invalid_hs["data"]["candidates"] == []
    assert invalid_hs["data"]["needs_confirmation"] is True
    assert markets["ok"] is True
    assert len(markets["data"]["markets"]) == 15
    assert markets["data"]["scope"]["analysis_year"] == 2023
    assert markets["data"]["scope"]["year_selection"] == "LATEST_COMMON"
    assert markets["data"]["scope"]["missing_markets"] == []
    assert len(markets["data"]["scope"]["supported_markets"]) == 15
    assert set(markets["data"]["scope"]["requested_markets"]) == set(
        markets["data"]["scope"]["supported_markets"]
    )
    assert electric_markets["ok"] is True
    assert electric_markets["dataQuality"]["status"] == "PARTIAL"
    assert "MONTHLY_TRADE" in electric_markets["dataQuality"]["missing"]
    assert "TARIFF" in electric_markets["dataQuality"]["missing"]
    assert electric_markets["warnings"]
    assert all(
        market["data_quality"]["monthly_points"] == 0
        and market["tariff_rate"] is None
        and market["data_quality"]["status"] == "PARTIAL"
        for market in electric_markets["data"]["markets"]
    )
    assert partial_markets["ok"] is True
    assert partial_markets["data"]["scope"]["year_selection"] == "MAX_COVERAGE_LATEST"
    assert len(partial_markets["data"]["scope"]["missing_markets"]) >= 1
    assert partial_markets["warnings"]
    assert selected_markets["ok"] is True
    assert selected_markets["data"]["scope"]["analysis_year"] == 2025
    assert selected_markets["data"]["scope"]["year_selection"] == "LATEST_COMMON"
    assert {
        market["market"]
        for market in selected_markets["data"]["markets"]
    } == {"AUS", "JPN"}
    assert unsupported_market["ok"] is False
    assert unsupported_market["error"]["code"] == "UNSUPPORTED_MARKET"
    forecast_methods = {
        market["forecast"]["method"]
        for market in markets["data"]["markets"]
    }
    assert forecast_methods <= {"ARIMA(1,1,0)", "CAGR_FALLBACK"}
    vietnam = next(
        market
        for market in markets["data"]["markets"]
        if market["market"] == "VNM"
    )
    assert vietnam["forecast"]["method"] == "CAGR_FALLBACK"
    assert customers["ok"] is True
    assert len(customers["data"]["customers"]) == 240
    assert customers["data"]["cluster_model"]["algorithm"] == "K-means"
    assert prerequisite["ok"] is False
    assert prerequisite["error"]["code"] == "ANALYSIS_PREREQUISITE_REQUIRED"

    # 用前两个分析结果重新调用策略工具，验证完整业务链路。
    full_process = subprocess.run(
        [str(PYTHON), str(WORKER)],
        input="\n".join([
            json.dumps({"method": "recommend_operations", "params": {"market_analysis": markets["data"], "customer_analysis": customers["data"], "target_market": "PHL"}}, ensure_ascii=False),
            "",
        ]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=True,
    )
    recommendation = json.loads(full_process.stdout.strip())
    assert recommendation["ok"] is True
    assert len(recommendation["data"]["cards"]) == len(
        customers["data"]["clusters"]
    )
    assert len(recommendation["data"]["cards"]) >= 1
    print("PASS resolve_product: generic text, exact HS, invalid HS")
    print("PASS analyze_markets: RCEP15, selected markets, scope guard")
    print("PASS analyze_customers: 240 customers, formal K-means")
    print("PASS prerequisite guard: strategy blocked without analyses")
    print("PASS recommend_operations: strategy cards generated")


if __name__ == "__main__":
    main()
