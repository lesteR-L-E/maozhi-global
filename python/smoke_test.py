"""四个业务工具的本地冒烟测试。

这个脚本不调用大模型，只验证：数据库存在、Worker能读取数据、四个方法能按
正确顺序返回成功结果，以及关键字段没有丢失。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "data" / "maozhi-demo.duckdb"
WORKER = ROOT / "python" / "analysis_worker.py"
PYTHON = ROOT / ".venv" / "bin" / "python"


def main() -> None:
    if not DATABASE.exists():
        raise SystemExit("数据库不存在，请先执行：.venv/bin/python python/build_demo_database.py")
    if not PYTHON.exists():
        raise SystemExit("项目虚拟环境不存在，请先执行：python3 -m venv .venv")

    requests = [
        {"method": "resolve_product", "params": {"product_name": "家用咖啡机"}},
        {"method": "analyze_markets", "params": {"confirmed_hs6": "851671"}},
        {"method": "analyze_customers", "params": {}},
    ]
    environment = dict(os.environ)
    environment["MAOZHI_DATABASE"] = str(DATABASE)
    process = subprocess.Popen(
        [str(PYTHON), str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    for request in requests:
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.write(json.dumps({"method": "recommend_operations", "params": {"target_market": "PHL"}, "_note": "先单独测试前置条件"}, ensure_ascii=False) + "\n")
    process.stdin.close()

    results = [json.loads(line) for line in process.stdout if line.strip()]
    process.wait(timeout=30)
    if len(results) != 4:
        raise SystemExit(f"Worker返回数量错误：{len(results)}")

    product, markets, customers, prerequisite = results
    assert product["ok"] is True
    assert product["data"]["candidates"]
    assert markets["ok"] is True
    assert len(markets["data"]["markets"]) == 5
    assert markets["data"]["markets"][0]["forecast"]["method"] == "ARIMA(1,1,0)"
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
        env=environment,
        check=True,
    )
    recommendation = json.loads(full_process.stdout.strip())
    assert recommendation["ok"] is True
    assert len(recommendation["data"]["cards"]) >= 4
    print("PASS resolve_product")
    print("PASS analyze_markets: 5 markets, formal ARIMA")
    print("PASS analyze_customers: 240 customers, formal K-means")
    print("PASS prerequisite guard: strategy blocked without analyses")
    print("PASS recommend_operations: strategy cards generated")


if __name__ == "__main__":
    main()
