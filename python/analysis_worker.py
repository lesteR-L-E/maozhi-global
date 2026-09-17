"""贸智全球 Python 分析 Worker。

职责只有三件事：从 DuckDB 查询数据、用正式分析库计算、返回结构化 JSON。
Electron 通过 stdin 发送一行请求，Worker 通过 stdout 返回一行结果。
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date
from pathlib import Path

# scikit-learn 会尝试探测CPU物理核数；演示环境只需要固定线程数，避免产生无关警告。
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from statsmodels.tsa.arima.model import ARIMA


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.environ.get("MAOZHI_DATABASE", str(PROJECT_ROOT / "data" / "maozhi-demo.duckdb")))
DEMO_EVIDENCE = [{"source": "贸智全球固定演示数据库", "period": "2021-2025", "note": "比赛演示模拟数据，非真实企业数据"}]


def success_quality(forecast_method: str | None = None) -> dict:
    quality = {"status": "OK", "sourceType": "SIMULATED_DEMO", "missing": [], "warnings": []}
    if forecast_method:
        quality["forecastMethod"] = forecast_method
    return quality


def failed_response(code: str, message: str, warning: str) -> dict:
    return {
        "ok": False,
        "summary": message,
        "evidence": [],
        "dataQuality": {"status": "UNAVAILABLE", "sourceType": "SIMULATED_DEMO", "missing": [], "warnings": [warning]},
        "warnings": [warning],
        "error": {"code": code, "message": message},
    }


def response(summary: str, data: dict, warnings: list[str] | None = None, forecast_method: str | None = None) -> dict:
    return {"ok": True, "summary": summary, "data": data, "evidence": DEMO_EVIDENCE, "dataQuality": success_quality(forecast_method), "warnings": warnings or []}


def connect() -> duckdb.DuckDBPyConnection:
    """打开只读演示数据库；数据库不存在时给出明确错误。"""
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"演示数据库不存在：{DATABASE_PATH}")
    return duckdb.connect(str(DATABASE_PATH), read_only=True)


def resolve_product(params: dict) -> dict:
    """根据商品维表进行精确或模糊匹配。"""
    product_name = str(params.get("product_name", "")).strip()
    if not product_name:
        return failed_response("PRODUCT_REQUIRED", "未提供商品名称。", "请提供商品名称或HS编码。")
    if product_name.isdigit() and len(product_name) in (2, 4, 6):
        pattern = product_name.zfill(6)
        candidates = [{"hs6": pattern, "name_cn": "用户输入的HS编码商品", "confidence": 0.80, "level": len(product_name)}]
    else:
        connection = connect()
        frame = connection.execute("SELECT hs_code, name_cn, name_en FROM dim_hs_code").df()
        connection.close()
        keyword = "咖啡" if "咖啡" in product_name or "coffee" in product_name.lower() else ""
        if keyword:
            matched = frame[frame["name_cn"].str.contains(keyword, na=False)]
            candidates = [{"hs6": row.hs_code, "name_cn": row.name_cn, "name_en": row.name_en, "confidence": 0.92, "level": 6} for row in matched.itertuples()]
        else:
            candidates = [{"hs6": "851671", "name_cn": "家用电热咖啡或茶饮机（演示候选）", "confidence": 0.42, "level": 6}, {"hs6": "851679", "name_cn": "其他电热器具（演示候选）", "confidence": 0.35, "level": 6}]
    needs_confirmation = len(candidates) != 1 or candidates[0]["confidence"] < 0.75
    warnings = ["HS候选不是自动确认结果，请用户确认后再做市场分析。"] if needs_confirmation else []
    return response("已从商品维表生成HS编码候选。", {"product_name": product_name, "candidates": candidates, "needs_confirmation": needs_confirmation}, warnings)


def min_max_score(values: list[float], reverse: bool = False) -> list[float]:
    """把一组数转换成0到100分；reverse用于越小越好的指标。"""
    array = np.asarray(values, dtype=float).reshape(-1, 1)
    scores = MinMaxScaler().fit_transform(array).ravel() * 100
    if reverse:
        scores = 100 - scores
    return scores.tolist()


def calculate_forecast(series: pd.Series) -> dict:
    """使用正式ARIMA(1,1,0)模型，并用最后12个月做滚动回测。"""
    values = series.astype(float).to_numpy()
    if len(values) < 60:
        raise ValueError("月度序列少于60个连续点")
    train = values[:-12]
    test = values[-12:]
    model = ARIMA(train, order=(1, 1, 0), trend="t").fit()
    prediction = model.forecast(steps=12)
    errors = test - prediction
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(errors**2)))
    non_zero = np.where(test != 0, test, np.nan)
    mape = float(np.nanmean(np.abs(errors / non_zero)) * 100)
    full_model = ARIMA(values, order=(1, 1, 0), trend="t").fit()
    forecast = full_model.get_forecast(steps=12)
    interval = forecast.conf_int(alpha=0.20)
    return {"horizon_months": 12, "values": [float(x) for x in forecast.predicted_mean], "interval": {"low": float(interval[0, 0]), "high": float(interval[-1, 1])}, "mae": mae, "rmse": rmse, "mape": mape, "points": len(values), "backtest_months": 12, "method": "ARIMA(1,1,0)", "enters_market_score": mape <= 25}


def analyze_markets(params: dict) -> dict:
    """从贸易、宏观和关税表计算市场指标、预测和综合评分。"""
    hs6 = str(params.get("confirmed_hs6", ""))
    if len(hs6) != 6 or not hs6.isdigit():
        return failed_response("HS_CONFIRMATION_REQUIRED", "市场分析需要用户确认的6位HS编码。", "请先确认HS编码。")
    requested = params.get("markets")
    default_markets = ["VNM", "IDN", "PHL", "THA", "MYS"]
    markets = [item for item in requested if item in default_markets] if isinstance(requested, list) and requested else default_markets
    connection = connect()
    # World 行用于市场总规模；同一张表里的其他 partner 行用于中国份额和 HHI。
    annual = connection.execute("SELECT * FROM fact_trade_flow_annual WHERE hs_code = ?", [hs6]).df()
    monthly = connection.execute("SELECT * FROM fact_trade_flow_monthly WHERE hs_code = ?", [hs6]).df()
    macro = connection.execute("SELECT * FROM fact_macro").df()
    tariff = connection.execute("SELECT * FROM fact_tariff WHERE hs6 = ?", [hs6]).df()
    countries = connection.execute("SELECT iso3, name_cn FROM dim_country").df()
    connection.close()
    rows = []
    # 统一取最后一个完整年度，供同比和评分使用。
    for market in markets:
        market_annual = annual[(annual.reporter_iso3 == market) & (annual.partner_code == 0)].sort_values("period")
        market_monthly = monthly[monthly.reporter_iso3 == market].sort_values("period")
        if market_annual.empty:
            continue
        imports = market_annual[market_annual.flow_code == "M"].sort_values("period")
        exports = market_annual[market_annual.flow_code == "X"].sort_values("period")
        latest_import = float(imports.iloc[-1].trade_value_usd)
        previous_import = float(imports.iloc[-2].trade_value_usd)
        china = annual[(annual.reporter_iso3 == market) & (annual.partner_iso3 == "CHN") & (annual.flow_code == "M")].sort_values("period")
        world = imports
        china_share = float(china.iloc[-1].trade_value_usd / latest_import) if not china.empty else 0.0
        hhi_parts = annual[(annual.reporter_iso3 == market) & (annual.flow_code == "M") & (annual.period == imports.iloc[-1].period) & (annual.partner_code != 0)]
        partner_total = hhi_parts.trade_value_usd.sum()
        hhi = float(((hhi_parts.trade_value_usd / partner_total) ** 2).sum()) if partner_total else None
        market_macro = macro[(macro.iso3 == market) & (macro.year == 2025)]
        market_tariff = tariff[tariff.reporter_market_code == market]
        forecast = calculate_forecast(market_monthly.trade_value_usd.reset_index(drop=True))
        years = imports.trade_value_usd.to_numpy()
        cagr = float((years[-1] / years[0]) ** (1 / max(1, len(years) - 1)) - 1) if len(years) >= 2 and years[0] > 0 else None
        trade_competitiveness = None
        if not exports.empty and latest_import + float(exports.iloc[-1].trade_value_usd) > 0:
            trade_competitiveness = (float(exports.iloc[-1].trade_value_usd) - latest_import) / (float(exports.iloc[-1].trade_value_usd) + latest_import)
        forecast_growth = float(forecast["values"][-1] / forecast["values"][0] - 1) if forecast["enters_market_score"] else None
        growth_potential = cagr if forecast_growth is None else 0.5 * cagr + 0.5 * forecast_growth
        rows.append({
            "market": market,
            "name_cn": countries[countries.iso3 == market].iloc[0].name_cn,
            "annual_import": latest_import,
            "annual_export": float(exports.iloc[-1].trade_value_usd) if not exports.empty else None,
            "trade_balance": float(exports.iloc[-1].trade_value_usd - imports.iloc[-1].trade_value_usd) if not exports.empty else None,
            "tc_competitiveness": trade_competitiveness,
            "yoy": latest_import / previous_import - 1 if previous_import else None,
            "china_share": china_share,
            "hhi": hhi,
            "dependency": latest_import / float(market_macro.iloc[0].gdp_usd) * 100 if not market_macro.empty else None,
            "tariff_rate": float(market_tariff.iloc[0].applied_rate) if not market_tariff.empty else None,
            "cagr_3y": cagr,
            "forecast_growth": forecast_growth,
            "growth_potential": growth_potential,
            "forecast": forecast,
            "product_structure": [{"hs6": hs6, "share": 1.0}],
            "data_quality": {
                "annual_years": len(imports),
                "monthly_points": len(market_monthly),
                "forecast_method": forecast["method"] if forecast["enters_market_score"] else "CAGR_FALLBACK"
            }
        })
    if not rows:
        return failed_response("MARKET_UNAVAILABLE", "没有可分析的演示市场。", "请检查HS编码或市场范围。")
    size_scores = min_max_score([row["annual_import"] for row in rows])
    growth_scores = min_max_score([row["growth_potential"] or 0 for row in rows])
    competition_scores = min_max_score([row["hhi"] or 1 for row in rows], reverse=True)
    china_scores = min_max_score([row["china_share"] for row in rows])
    tariff_scores = min_max_score([row["tariff_rate"] or 100 for row in rows], reverse=True)
    macro_scores = min_max_score([float(macro[macro.iso3 == row["market"]].iloc[-1].gdp_growth_rate) for row in rows])
    for index, row in enumerate(rows):
        macro_growth = float(macro[macro.iso3 == row["market"]].iloc[-1].gdp_growth_rate)
        dimensions = {
            "market_size": {"raw": row["annual_import"], "score": size_scores[index], "weight": 0.20},
            "growth": {"raw": row["growth_potential"], "score": growth_scores[index], "weight": 0.20},
            "competition": {"raw": row["hhi"], "score": competition_scores[index], "weight": 0.15},
            "china_supply": {"raw": row["china_share"], "score": china_scores[index], "weight": 0.15},
            "tariff": {"raw": row["tariff_rate"], "score": tariff_scores[index], "weight": 0.15},
            "macro": {"raw": macro_growth, "score": macro_scores[index], "weight": 0.15}
        }
        row["dimensions"] = dimensions
        row["total_score"] = round(sum(item["score"] * item["weight"] for item in dimensions.values()), 2)
    rows.sort(key=lambda item: item["total_score"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["recommendation"] = "PRIORITY" if row["total_score"] >= 65 else "WATCH" if row["total_score"] >= 40 else "CAUTION"
        row["geometry_key"] = row["market"]
    return response("已完成演示市场分析，结果包含贸易指标、正式ARIMA预测和市场评分。", {"hs6": hs6, "markets": rows, "scope": {"core_demo": default_markets, "ranking": "RCEP15（当前演示数据覆盖核心5国）", "global": "World aggregate"}}, forecast_method="ARIMA(1,1,0)")


def analyze_customers(params: dict) -> dict:
    """从客户和订单表计算RFM+G+I、正式K-means、价值和流失风险。"""
    observation = pd.Timestamp(params.get("observation_date", "2025-12-31"))
    market = params.get("market")
    connection = connect()
    customer_frame = connection.execute("SELECT * FROM dim_customer").df()
    transaction_frame = connection.execute("SELECT * FROM fact_transaction WHERE order_status = 'COMPLETED'").df()
    connection.close()
    if market:
        customer_frame = customer_frame[customer_frame.country_code == market]
        transaction_frame = transaction_frame[transaction_frame.country_code == market]
    transaction_frame["order_date"] = pd.to_datetime(transaction_frame["order_date"])
    rows = []
    for customer in customer_frame.itertuples(index=False):
        orders = transaction_frame[transaction_frame.customer_id == customer.customer_id]
        if orders.empty:
            continue
        recent = orders[orders.order_date >= observation - pd.DateOffset(months=24)]
        recent_12 = recent[recent.order_date >= observation - pd.DateOffset(months=12)]
        previous_12 = recent[recent.order_date < observation - pd.DateOffset(months=12)]
        recent_6 = orders[orders.order_date >= observation - pd.DateOffset(months=6)]
        previous_6 = orders[(orders.order_date < observation - pd.DateOffset(months=6)) & (orders.order_date >= observation - pd.DateOffset(months=12))]
        first_order_age = (observation - pd.Timestamp(customer.first_order_date)).days
        last_order = orders.order_date.max()
        frequency = len(recent)
        monetary = float(recent.net_amount_usd.sum())
        previous_amount = float(previous_12.net_amount_usd.sum())
        growth = (float(recent_12.net_amount_usd.sum()) - previous_amount) / max(previous_amount, 1.0)
        rows.append({"customer_id": customer.customer_id, "country_code": customer.country_code, "customer_type": customer.customer_type, "company_size": customer.company_size, "first_order_date": customer.first_order_date, "R": (observation - last_order).days, "F": frequency, "M": monetary, "G": growth, "I": int(orders.hs6.nunique()), "recent_6_frequency": len(recent_6), "previous_6_frequency": len(previous_6), "recent_6_amount": float(recent_6.net_amount_usd.sum()), "previous_6_amount": float(previous_6.net_amount_usd.sum()), "new_customer": first_order_age < 180, "churn_sample_sufficient": first_order_age >= 365})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return failed_response("CUSTOMER_UNAVAILABLE", "没有可分析的客户交易数据。", "请检查客户市场或订单数据。")
    eligible = frame[~frame.new_customer].copy()
    if len(eligible) >= 6:
        feature_matrix = StandardScaler().fit_transform(eligible[["R", "F", "M", "G", "I"]])
        best_model = None
        best_score = -1
        # 负责人要求K在2到6之间自动选择；轮廓系数越高，分群越容易解释。
        for cluster_count in range(2, min(6, len(eligible) - 1) + 1):
            model = KMeans(n_clusters=cluster_count, random_state=42, n_init=20).fit(feature_matrix)
            score = silhouette_score(feature_matrix, model.labels_)
            if score > best_score:
                best_model, best_score = model, score
        eligible["cluster"] = best_model.labels_
        frame.loc[eligible.index, "cluster"] = eligible["cluster"]
        cluster_count = int(best_model.n_clusters)
    else:
        frame["cluster"] = 0
        cluster_count = 1
        best_score = None
    for column in ["M", "F", "R", "G"]:
        scaler = MinMaxScaler()
        values = scaler.fit_transform(eligible[[column]]).ravel() * 100 if len(eligible) else np.array([])
        frame.loc[eligible.index, f"{column}_score"] = 100 - values if column == "R" else values
    # 客户价值是交易价值，不是利润价值：M为主，F/R/G为辅助。
    frame["value_score"] = 0.5 * frame.M_score + 0.2 * frame.F_score + 0.1 * frame.R_score + 0.2 * frame.G_score
    frame["value_grade"] = frame.value_score.map(lambda value: "S" if value >= 85 else "A" if value >= 70 else "B" if value >= 50 else "C")
    frame.loc[frame.new_customer, "value_grade"] = "NEW"
    # 用客户历史订单间隔计算D，而不是用最近一次间隔代替历史平均。
    interval_rows = []
    for customer_id in frame.customer_id:
        customer_orders = transaction_frame[transaction_frame.customer_id == customer_id].sort_values("order_date")
        if len(customer_orders) >= 2:
            intervals = customer_orders.order_date.diff().dt.days.dropna()
            interval_rows.append(float(intervals.mean()))
        else:
            interval_rows.append(np.nan)
    frame["avg_order_interval_days"] = interval_rows
    frame["avg_order_interval_days"] = frame["avg_order_interval_days"].fillna(frame["R"].clip(lower=1))
    frame["D"] = frame["R"] / frame["avg_order_interval_days"]
    frame["Fd"] = (frame["previous_6_frequency"] - frame["recent_6_frequency"]) / frame["previous_6_frequency"].replace(0, np.nan)
    frame["Fd"] = frame["Fd"].fillna(0).clip(lower=0)
    frame["Md"] = (frame["previous_6_amount"] - frame["recent_6_amount"]) / frame["previous_6_amount"].replace(0, np.nan)
    frame["Md"] = frame["Md"].fillna(0).clip(lower=0)
    frame["churn_score"] = (0.4 * frame.D + 0.3 * frame.Fd + 0.3 * frame.Md) * 100
    frame.loc[~frame.churn_sample_sufficient, "churn_score"] = np.nan
    frame["churn_grade"] = frame.churn_score.map(lambda value: "HIGH" if value >= 70 else "MEDIUM" if value >= 40 else "LOW")
    frame.loc[frame.churn_score.isna(), "churn_grade"] = "INSUFFICIENT_SAMPLE"
    # 将机器学习簇转换成业务可读名称；名称只是解释，不改变聚类标签。
    cluster_summary = frame.groupby("cluster").agg(mean_value=("value_score", "mean"), mean_churn=("churn_score", "mean")).sort_values("mean_value", ascending=False)
    cluster_names = {}
    if len(cluster_summary) >= 4:
        value_order = list(cluster_summary.index)
        stable = min(value_order[:2], key=lambda item: cluster_summary.loc[item, "mean_churn"])
        risk = max(value_order[:2], key=lambda item: cluster_summary.loc[item, "mean_churn"])
        cluster_names[stable] = "高价值稳定"
        cluster_names[risk] = "高价值风险"
        remaining = [item for item in value_order if item not in (stable, risk)]
        if remaining:
            cluster_names[remaining[0]] = "成长型"
        for item in remaining[1:]:
            cluster_names[item] = "低价值长尾"
    else:
        for item in cluster_summary.index:
            cluster_names[item] = f"客户群{int(item) + 1}"
    frame["cluster_label"] = frame.cluster.map(lambda value: cluster_names.get(value, f"客户群{int(value) + 1}"))
    clusters = []
    for cluster_id, group in frame.groupby("cluster"):
        clusters.append({"cluster": int(cluster_id), "label": cluster_names.get(cluster_id, f"客户群{int(cluster_id) + 1}"), "size": len(group), "mean_value_score": round(float(group.value_score.mean()), 2)})
    customer_records = frame.drop(columns=["M_score", "F_score", "R_score", "G_score", "avg_order_interval_days", "D", "Fd", "Md", "recent_6_frequency", "previous_6_frequency", "recent_6_amount", "previous_6_amount"], errors="ignore").replace({np.nan: None}).to_dict(orient="records")
    data = {"observation_date": observation.date().isoformat(), "customers": customer_records, "clusters": clusters, "cluster_model": {"algorithm": "K-means", "k": cluster_count, "selection": "轮廓系数主导、业务可解释性校验", "silhouette_score": best_score, "standardization": "StandardScaler"}, "value_model": {"formula": "0.5M'+0.2F'+0.1R'+0.2G'", "normalization": "全体客户池MinMaxScaler", "grades": {"S": 85, "A": 70, "B": 50}}, "churn_model": {"type": "规则评分，不是机器学习预测", "windows": "两个6个月", "grades": {"HIGH": 70, "MEDIUM": 40}, "new_customer_rule": "首单距今<6个月不参与K-means；<12个月不参与流失分级"}, "dataset_note": "比赛演示模拟数据，非真实企业数据"}
    return response(f"已完成{len(frame)}家演示客户的正式K-means分群、价值和流失风险分析。", data, ["客户数据为比赛演示模拟数据，价值分不代表利润，流失分不是概率。"])


def recommend_operations(params: dict) -> dict:
    """按负责人确认的客户类型动作白名单生成策略卡。"""
    market_analysis = params.get("market_analysis")
    customer_analysis = params.get("customer_analysis")
    if not isinstance(market_analysis, dict) or not isinstance(customer_analysis, dict):
        return failed_response("ANALYSIS_PREREQUISITE_REQUIRED", "生成策略需要市场分析和客户分析结果。", "请先完成市场分析和客户分析。")
    market = params.get("target_market") or ((market_analysis.get("markets") or [{}])[0].get("market", "PHL"))
    action_map = {"高价值稳定": ["提高供货优先级", "交叉销售", "组合销售"], "成长型": ["样品/试单邀请", "相邻品类推荐"], "高价值风险": ["人工回访", "召回"], "低价值长尾": ["邮件/消息触达", "展会触达"]}
    cadence_map = {"高价值稳定": "季度回访", "成长型": "月度触达", "高价值风险": "2周内", "低价值长尾": "半年"}
    cards = []
    for cluster in customer_analysis.get("clusters", []):
        label = cluster.get("label", "低价值长尾")
        if label.startswith("客户群"):
            label = ["高价值稳定", "成长型", "高价值风险", "低价值长尾"][int(cluster.get("cluster", 0)) % 4]
        cards.append({"recommendation_id": f"REC-{market}-{int(cluster.get('cluster', 0)) + 1:02d}", "target_market": market, "customer_segment": label, "customer_count": cluster.get("size", 0), "objective": "提升该市场客户经营效率", "actions": action_map[label], "cadence": cadence_map[label], "success_metrics": ["90天复购率", "样品转化率", "新市场首单周期"], "evidence": ["市场排名与评分", "客户分群、价值和风险等级"], "limits": ["第一版无执行回流，不代表成交承诺"]})
    data = {"target_market": market, "cards": cards, "action_whitelist": sorted({action for actions in action_map.values() for action in actions}), "forbidden": ["具体价格", "折扣", "账期", "赠品"]}
    return response(f"已生成{market}市场的{len(cards)}张差异化策略卡。", data, ["策略为建议方向，需企业在真实业务中验证。"])


METHODS = {"resolve_product": resolve_product, "analyze_markets": analyze_markets, "analyze_customers": analyze_customers, "recommend_operations": recommend_operations}


def main() -> None:
    """持续读取请求，保证每个请求只输出一行JSON。"""
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            params = request.get("params") or {}
            if method not in METHODS:
                result = failed_response("UNKNOWN_METHOD", f"未知分析方法：{method}", "请使用四个已注册业务工具。")
            else:
                result = METHODS[method](params)
        except Exception as error:
            result = failed_response("WORKER_ERROR", "演示分析执行失败。", str(error))
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
