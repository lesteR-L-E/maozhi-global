"""贸智全球 Python 分析 Worker。

职责只有三件事：从 DuckDB 查询数据、用正式分析库计算、返回结构化 JSON。
Electron 通过 stdin 发送一行请求，Worker 通过 stdout 返回一行结果。
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import date
from difflib import SequenceMatcher
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
DATABASE_PATH = Path(
    os.environ.get(
        "MAOZHI_DATABASE",
        str(PROJECT_ROOT / "data" / "maozhi-real.duckdb"),
    )
)
DATA_EVIDENCE = [{
    "source": "公开贸易、宏观和WITS/UNCTAD TRAINS数据",
    "period": "2020-2025",
    "note": "客户和交易表为比赛模拟数据，市场数据不是企业内部数据",
}]
HS_SEARCH_STOP_WORDS = frozenset({
    "a", "an", "and", "as", "by", "for", "from", "in", "of", "on",
    "or", "other", "than", "the", "to", "used", "with", "without",
})
MAX_HS_CANDIDATES = 8


def success_quality(
    forecast_method: str | None = None,
    status: str = "OK",
    missing: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    quality = {
        "status": status,
        "sourceType": "MIXED",
        "missing": missing or [],
        "warnings": warnings or [],
    }
    if forecast_method:
        quality["forecastMethod"] = forecast_method
    return quality


def failed_response(code: str, message: str, warning: str) -> dict:
    return {
        "ok": False,
        "summary": message,
        "evidence": [],
        "dataQuality": {"status": "UNAVAILABLE", "sourceType": "MIXED", "missing": [], "warnings": [warning]},
        "warnings": [warning],
        "error": {"code": code, "message": message},
    }


def response(
    summary: str,
    data: dict,
    warnings: list[str] | None = None,
    forecast_method: str | None = None,
    quality_status: str = "OK",
    missing: list[str] | None = None,
) -> dict:
    response_warnings = warnings or []
    return {
        "ok": True,
        "summary": summary,
        "data": data,
        "evidence": DATA_EVIDENCE,
        "dataQuality": success_quality(
            forecast_method,
            quality_status,
            missing,
            response_warnings,
        ),
        "warnings": response_warnings,
    }


def connect() -> duckdb.DuckDBPyConnection:
    """打开只读业务数据库；数据库不存在时给出明确错误。"""
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"业务数据库不存在：{DATABASE_PATH}")
    return duckdb.connect(str(DATABASE_PATH), read_only=True)


def normalize_search_text(value: object) -> str:
    """统一商品检索文本，保留英文、数字和中文。"""
    if value is None or pd.isna(value):
        return ""
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        " ",
        str(value).lower(),
    ).strip()


def normalize_search_token(token: str) -> str:
    """处理常见英文复数，提升商品描述的词语匹配率。"""
    if re.fullmatch(r"[a-z]+", token):
        if len(token) > 4 and token.endswith("ies"):
            return f"{token[:-3]}y"
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            return token[:-1]
    return token


def search_tokens(value: object) -> set[str]:
    normalized = normalize_search_text(value)
    tokens = {
        normalize_search_token(token)
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalized)
    }
    return {token for token in tokens if token not in HS_SEARCH_STOP_WORDS}


def text_match_score(query: str, field: object) -> float:
    """计算查询与一个HS名称或关键词的匹配分。"""
    normalized_query = normalize_search_text(query)
    normalized_field = normalize_search_text(field)
    if not normalized_query or not normalized_field:
        return 0.0
    if normalized_query == normalized_field:
        return 1.0
    if normalized_query in normalized_field:
        return 0.96
    if len(normalized_field) >= 3 and normalized_field in normalized_query:
        return 0.92

    query_tokens = search_tokens(normalized_query)
    field_tokens = search_tokens(normalized_field)
    if not query_tokens or not field_tokens:
        return 0.0
    overlap = query_tokens & field_tokens
    if not overlap:
        return 0.0

    coverage = len(overlap) / len(query_tokens)
    precision = len(overlap) / len(field_tokens)
    similarity = SequenceMatcher(
        None,
        normalized_query,
        normalized_field,
    ).ratio()
    return 0.70 * coverage + 0.10 * precision + 0.20 * similarity


def hs_candidate(row, confidence: float) -> dict:
    return {
        "hs6": str(row.hs_code),
        "name_cn": None if pd.isna(row.name_cn) else row.name_cn,
        "name_en": None if pd.isna(row.name_en) else row.name_en,
        "confidence": round(float(confidence), 3),
        "level": 6,
    }


def resolve_product(params: dict) -> dict:
    """根据商品维表进行精确或模糊匹配。"""
    product_name = str(params.get("product_name", "")).strip()
    search_terms_en_value = params.get("search_terms_en")
    search_terms_en = (
        search_terms_en_value.strip()
        if isinstance(search_terms_en_value, str)
        else ""
    )
    if not product_name:
        return failed_response("PRODUCT_REQUIRED", "未提供商品名称。", "请提供商品名称或HS编码。")

    connection = connect()
    try:
        if product_name.isdigit() and len(product_name) == 6:
            frame = connection.execute(
                """
                SELECT hs6_std AS hs_code, name_cn, name_en, keywords
                FROM dim_hs_code
                WHERE hs6_std = ?
                """,
                [product_name],
            ).df()
            candidates = [
                hs_candidate(row, 1.0)
                for row in frame.itertuples(index=False)
            ]
            needs_confirmation = False if candidates else True
        elif product_name.isdigit() and len(product_name) in (2, 4):
            frame = connection.execute(
                """
                SELECT hs6_std AS hs_code, name_cn, name_en, keywords
                FROM dim_hs_code
                WHERE hs6_std LIKE ?
                ORDER BY hs6_std
                LIMIT ?
                """,
                [f"{product_name}%", MAX_HS_CANDIDATES],
            ).df()
            candidates = [
                hs_candidate(row, 0.65)
                for row in frame.itertuples(index=False)
            ]
            needs_confirmation = True
        elif product_name.isdigit():
            candidates = []
            needs_confirmation = True
        else:
            frame = connection.execute(
                """
                SELECT hs6_std AS hs_code, name_cn, name_en, keywords
                FROM dim_hs_code
                """
            ).df()
            queries = [product_name]
            if search_terms_en:
                queries.append(search_terms_en)

            ranked = []
            for row in frame.itertuples(index=False):
                fields = [row.name_cn, row.name_en]
                if not pd.isna(row.keywords):
                    fields.extend(re.split(r"[;,；，]", str(row.keywords)))
                score = max(
                    text_match_score(query, field)
                    for query in queries
                    for field in fields
                )
                if score >= 0.35:
                    ranked.append((score, str(row.hs_code), row))

            ranked.sort(key=lambda item: (-item[0], item[1]))
            if ranked:
                cutoff = max(0.35, ranked[0][0] - 0.25)
                ranked = [item for item in ranked if item[0] >= cutoff]
            candidates = [
                hs_candidate(row, score)
                for score, _, row in ranked[:MAX_HS_CANDIDATES]
            ]
            # 名称检索只生成候选，最终HS编码必须由用户确认。
            needs_confirmation = True
    finally:
        connection.close()

    warnings = []
    if not candidates:
        warnings.append(
            "未在HS维表中找到可靠候选，请补充英文商品名称、用途、材质或6位HS编码。"
        )
    elif needs_confirmation:
        warnings.append("HS候选不是自动确认结果，请用户确认后再做市场分析。")

    summary = (
        "已从商品维表生成HS编码候选。"
        if candidates
        else "未能从商品维表确定HS编码候选。"
    )
    return response(
        summary,
        {
            "product_name": product_name,
            "search_terms_en": search_terms_en or None,
            "candidates": candidates,
            "needs_confirmation": needs_confirmation,
        },
        warnings,
    )


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
    return {"horizon_months": 12, "values": [float(x) for x in forecast.predicted_mean], "interval": {"low": float(interval[0, 0]), "high": float(interval[-1, 1])}, "intervals": [{"low": float(row[0]), "high": float(row[1])} for row in interval], "confidence_level": 0.80, "mae": mae, "rmse": rmse, "mape": mape, "points": len(values), "backtest_months": 12, "method": "ARIMA(1,1,0)", "enters_market_score": mape <= 25}


def analyze_markets(params: dict) -> dict:
    """从贸易、宏观和关税表计算市场指标、预测和综合评分。"""
    hs6 = str(params.get("confirmed_hs6", ""))
    if len(hs6) != 6 or not hs6.isdigit():
        return failed_response("HS_CONFIRMATION_REQUIRED", "市场分析需要用户确认的6位HS编码。", "请先确认HS编码。")
    requested = params.get("markets")
    connection = connect()
    countries = connection.execute(
        """
        SELECT iso3, name_cn
        FROM dim_country
        WHERE in_scope IN ('core', 'rcep')
          AND iso3 IS NOT NULL
        ORDER BY iso3
        """
    ).df()
    supported_markets = countries.iso3.tolist()
    if isinstance(requested, list) and requested:
        normalized_markets = list(dict.fromkeys(
            str(item).strip().upper()
            for item in requested
            if str(item).strip()
        ))
        unsupported_markets = [
            item for item in normalized_markets
            if item not in supported_markets
        ]
        if unsupported_markets:
            connection.close()
            return failed_response(
                "UNSUPPORTED_MARKET",
                "请求中包含数据库不支持的市场。",
                f"不支持的市场代码：{', '.join(unsupported_markets)}",
            )
        markets = normalized_markets
    else:
        markets = supported_markets

    # World 行用于市场总规模；同一张表里的其他 partner 行用于中国份额和 HHI。
    annual = connection.execute(
        """
        SELECT
            CAST(year AS VARCHAR) AS period,
            reporter_iso3,
            CAST(reporter_m49 AS INTEGER) AS reporter_code,
            TRY_CAST(
                NULLIF(TRIM(partner_m49), '')
                AS INTEGER
            ) AS partner_code,
            CASE
                WHEN partner_m49 = '000' THEN 'WLD'
                ELSE partner_iso3
            END AS partner_iso3,
            CASE flow
                WHEN 'Import' THEN 'M'
                WHEN 'Export' THEN 'X'
            END AS flow_code,
            trade_value AS trade_value_usd,is_aggregate
        FROM fact_trade_flow
        WHERE hs_code = ?
        """,
        [hs6],
    ).df()

    monthly = connection.execute(
        """
        SELECT
            printf('%04d-%02d', year, month) AS period,
            reporter_iso3,
            TRY_CAST(
                NULLIF(TRIM(partner_m49), '')
                AS INTEGER
            ) AS partner_code,
            CASE flow
                WHEN 'Import' THEN 'M'
                WHEN 'Export' THEN 'X'
            END AS flow_code,
            trade_value AS trade_value_usd
        FROM fact_trade_monthly
        WHERE hs_code = ?
        """,
        [hs6],
    ).df()

    macro = connection.execute(
        """
        SELECT
            country_iso3 AS iso3,
            year,
            gdp AS gdp_usd,
            gdp_growth AS gdp_growth_rate,
            population,
            trade_gdp_pct,
            source
        FROM fact_macro
        """
    ).df()

    tariff = connection.execute(
        """
        SELECT
            reporter_iso3 AS reporter_market_code,
            hs_code AS hs6,
            mfn_year,
            mfn_rate,
            ahs_year,
            ahs_rate AS applied_rate,
            ahs_type,
            source
        FROM fact_tariff
        WHERE hs_code = ?
        """,
        [hs6],
    ).df()

    market_metrics_all = connection.execute(
        """
        SELECT
            country.iso3 AS reporter_iso3,
            market.year,
            market.market_size,
            market.china_share,
            market.hhi / 10000.0 AS hhi,
            market.top3_share
        FROM preagg_market AS market
        INNER JOIN dim_country AS country
            ON country.m49_code = market.reporter_m49
        WHERE market.hs_code = ?
        """,
        [hs6],
    ).df()

    connection.close()
    requested_metrics = market_metrics_all[
        market_metrics_all.reporter_iso3.isin(markets)
    ].copy()
    if requested_metrics.empty:
        return failed_response(
            "MARKET_UNAVAILABLE",
            "没有可分析的市场。",
            f"HS{hs6}在市场预聚合表中没有记录。",
        )

    coverage_by_year = requested_metrics.groupby("year")[
        "reporter_iso3"
    ].nunique()
    complete_years = coverage_by_year[
        coverage_by_year == len(markets)
    ].index.tolist()
    if complete_years:
        analysis_year = int(max(complete_years))
        year_selection = "LATEST_COMMON"
    else:
        best_coverage = int(coverage_by_year.max())
        best_years = coverage_by_year[
            coverage_by_year == best_coverage
        ].index.tolist()
        analysis_year = int(max(best_years))
        year_selection = "MAX_COVERAGE_LATEST"

    market_metrics = requested_metrics[
        requested_metrics.year == analysis_year
    ].copy()
    available_markets = set(market_metrics.reporter_iso3)
    missing_markets = sorted(set(markets) - available_markets)
    coverage_warnings = []
    missing_categories = set()
    monthly_missing_markets = []
    monthly_insufficient_markets = []
    tariff_missing_markets = []
    tariff_not_applicable_markets = []
    macro_missing_markets = []
    forecast_degraded_markets = []
    rows = []
    analysis_period = str(analysis_year)
    skipped_markets = set(missing_markets)

    for market in markets:
        market_metric = market_metrics[
            market_metrics.reporter_iso3 == market
        ]

        market_annual = annual[
            (annual.reporter_iso3 == market)
            & (annual.partner_code == 0)
            & (annual.period <= analysis_period)
        ].sort_values("period")

        market_monthly = monthly[
            (monthly.reporter_iso3 == market)
            & (monthly.partner_code == 0)
            & (monthly.flow_code == "M")
        ].sort_values("period")

        imports = market_annual[
            market_annual.flow_code == "M"
        ].sort_values("period")

        exports = market_annual[
            market_annual.flow_code == "X"
        ].sort_values("period")

        if market_metric.empty or len(imports) < 2:
            skipped_markets.add(market)
            continue

        latest_import = float(
            market_metric.iloc[0].market_size
        )
        previous_import = float(
            imports.iloc[-2].trade_value_usd
        )
        china_share = float(
            market_metric.iloc[0].china_share
        )
        hhi = float(
            market_metric.iloc[0].hhi
        )
        top3_share = float(
            market_metric.iloc[0].top3_share
        )

        market_macro = macro[
            (macro.iso3 == market)
            & (macro.year == analysis_year)
        ]
        macro_growth = (
            float(market_macro.iloc[0].gdp_growth_rate)
            if not market_macro.empty
            else None
        )

        market_tariff = tariff[
            tariff.reporter_market_code == market
        ]
        tariff_rate = None
        tariff_year = None
        if not market_tariff.empty:
            tariff_row = market_tariff.iloc[0]
            if not pd.isna(tariff_row.applied_rate):
                tariff_rate = float(tariff_row.applied_rate)
            if not pd.isna(tariff_row.ahs_year):
                tariff_year = int(tariff_row.ahs_year)

        market_missing = []
        market_warnings = []
        if len(market_monthly) == 0:
            market_missing.append("MONTHLY_TRADE")
            monthly_missing_markets.append(market)
            market_warnings.append(
                "该商品没有月度贸易序列，增长分析使用年度CAGR。"
            )
        elif len(market_monthly) < 60:
            market_missing.append("MONTHLY_TRADE_INSUFFICIENT")
            monthly_insufficient_markets.append(market)
            market_warnings.append(
                f"月度贸易序列只有{len(market_monthly)}点，少于60点，增长分析使用年度CAGR。"
            )
        if market_macro.empty:
            market_missing.append("MACRO")
            macro_missing_markets.append(market)
            market_warnings.append(
                f"缺少{analysis_year}年宏观数据，宏观和依赖度指标不可用。"
            )
        if tariff_rate is None:
            if market == "CHN" and not market_tariff.empty:
                market_missing.append("TARIFF_NOT_APPLICABLE")
                tariff_not_applicable_markets.append(market)
                market_warnings.append(
                    "中国作为Reporter时对中国AHS不适用，关税指标留空。"
                )
            else:
                market_missing.append("TARIFF")
                tariff_missing_markets.append(market)
                market_warnings.append(
                    "该商品缺少China-specific Applied Tariff，关税指标留空。"
                )

        years = imports.trade_value_usd.to_numpy()
        cagr = float((years[-1] / years[0]) ** (1 / max(1, len(years) - 1)) - 1) if len(years) >= 2 and years[0] > 0 else None

        if len(market_monthly) >= 60:
            forecast = calculate_forecast(
                market_monthly.trade_value_usd.reset_index(drop=True)
            )
            forecast["periods"] = [
                str(period)
                for period in pd.period_range(
                    pd.Period(
                        str(market_monthly.iloc[-1].period),
                        freq="M",
                    ) + 1,
                    periods=12,
                    freq="M",
                )
            ]
            forecast_growth = (
                float(
                    forecast["values"][-1]
                    / forecast["values"][0]
                    - 1
                )
                if forecast["enters_market_score"]
                else None
            )
            if not forecast["enters_market_score"]:
                forecast_degraded_markets.append(market)
                market_warnings.append(
                    "ARIMA回测误差超过阈值，增长评分使用年度CAGR。"
                )
        else:
            forecast = {
                "horizon_months": 0,
                "values": [],
                "periods": [],
                "interval": None,
                "intervals": [],
                "confidence_level": None,
                "mae": None,
                "rmse": None,
                "mape": None,
                "points": len(market_monthly),
                "backtest_months": 0,
                "method": "CAGR_FALLBACK",
                "enters_market_score": False,
            }
            forecast_growth = None

        trade_competitiveness = None
        if not exports.empty and latest_import + float(exports.iloc[-1].trade_value_usd) > 0:
            trade_competitiveness = (float(exports.iloc[-1].trade_value_usd) - latest_import) / (float(exports.iloc[-1].trade_value_usd) + latest_import)
        growth_potential = cagr if forecast_growth is None else 0.5 * cagr + 0.5 * forecast_growth
        rows.append({
            "market": market,
            "name_cn": countries[countries.iso3 == market].iloc[0].name_cn,
            "annual_period": analysis_period,
            "history": [{"period": str(row.period), "value": float(row.trade_value_usd)} for row in market_monthly.itertuples()],
            "annual_import": latest_import,
            "annual_export": float(exports.iloc[-1].trade_value_usd) if not exports.empty else None,
            "trade_balance": float(exports.iloc[-1].trade_value_usd - imports.iloc[-1].trade_value_usd) if not exports.empty else None,
            "tc_competitiveness": trade_competitiveness,
            "yoy": latest_import / previous_import - 1 if previous_import else None,
            "china_share": china_share,
            "hhi": hhi,
            "top3_share": top3_share,
            "dependency": latest_import / float(market_macro.iloc[0].gdp_usd) * 100 if not market_macro.empty else None,
            "macro_growth": macro_growth,
            "tariff_rate": tariff_rate,
            "tariff_year": tariff_year,
            "cagr_3y": cagr,
            "forecast_growth": forecast_growth,
            "growth_potential": growth_potential,
            "forecast": forecast,
            "product_structure": [{"hs6": hs6, "share": 1.0}],
            "data_quality": {
                "status": "PARTIAL" if market_missing else "OK",
                "missing": market_missing,
                "warnings": market_warnings,
                "annual_years": len(imports),
                "monthly_points": len(market_monthly),
                "forecast_method": forecast["method"] if forecast["enters_market_score"] else "CAGR_FALLBACK"
            }
        })
    if not rows:
        return failed_response("MARKET_UNAVAILABLE", "没有可分析的市场。", "请检查HS编码或市场范围。")
    missing_markets = sorted(skipped_markets)
    if missing_markets:
        missing_categories.add("MARKET_COVERAGE")
        coverage_warnings.append(
            f"HS{hs6}在{analysis_year}年缺少可比较的年度贸易记录："
            f"{', '.join(missing_markets)}。"
        )
    if monthly_missing_markets:
        missing_categories.add("MONTHLY_TRADE")
        coverage_warnings.append(
            "以下市场没有该商品的月度贸易序列，增长分析使用年度CAGR："
            f"{', '.join(monthly_missing_markets)}。"
        )
    if monthly_insufficient_markets:
        missing_categories.add("MONTHLY_TRADE_INSUFFICIENT")
        coverage_warnings.append(
            "以下市场月度序列少于60点，增长分析使用年度CAGR："
            f"{', '.join(monthly_insufficient_markets)}。"
        )
    if tariff_missing_markets:
        missing_categories.add("TARIFF")
        coverage_warnings.append(
            "以下市场缺少该商品的China-specific Applied Tariff："
            f"{', '.join(tariff_missing_markets)}。"
        )
    if tariff_not_applicable_markets:
        missing_categories.add("TARIFF_NOT_APPLICABLE")
        coverage_warnings.append(
            "中国作为Reporter时对中国AHS不适用，关税指标留空。"
        )
    if macro_missing_markets:
        missing_categories.add("MACRO")
        coverage_warnings.append(
            f"以下市场缺少{analysis_year}年宏观数据："
            f"{', '.join(macro_missing_markets)}。"
        )
    if forecast_degraded_markets:
        coverage_warnings.append(
            "以下市场ARIMA回测未达到评分阈值，增长评分使用年度CAGR："
            f"{', '.join(forecast_degraded_markets)}。"
        )
    if tariff_missing_markets or tariff_not_applicable_markets:
        coverage_warnings.append(
            "关税不完整时综合评分属于部分数据评分，不宜与字段完整的商品评分直接比较。"
        )
    size_scores = min_max_score([row["annual_import"] for row in rows])
    growth_scores = min_max_score([row["growth_potential"] or 0 for row in rows])
    competition_scores = min_max_score([row["hhi"] or 1 for row in rows], reverse=True)
    china_scores = min_max_score([row["china_share"] for row in rows])
    tariff_values = [
        row["tariff_rate"]
        if row["tariff_rate"] is not None and not pd.isna(row["tariff_rate"])
        else 100
        for row in rows
    ]
    tariff_scores = min_max_score(tariff_values, reverse=True)
    macro_scores = min_max_score([
        row["macro_growth"] if row["macro_growth"] is not None else 0
        for row in rows
    ])
    for index, row in enumerate(rows):
        macro_growth = row["macro_growth"]
        dimensions = {
            "market_size": {"raw": row["annual_import"], "score": size_scores[index], "weight": 0.20},
            "growth": {"raw": row["growth_potential"], "score": growth_scores[index], "weight": 0.20},
            "competition": {"raw": row["hhi"], "score": competition_scores[index], "weight": 0.15},
            "china_supply": {"raw": row["china_share"], "score": china_scores[index], "weight": 0.15},
            "tariff": {"raw": row["tariff_rate"], "score": tariff_scores[index], "weight": 0.15},
            "macro": {"raw": macro_growth, "score": macro_scores[index], "weight": 0.15},
        }
        row["dimensions"] = dimensions
        row["total_score"] = round(sum(
            item["score"] * item["weight"]
            for item in dimensions.values()
        ), 2)
    rows.sort(key=lambda item: item["total_score"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["recommendation"] = "PRIORITY" if row["total_score"] >= 65 else "WATCH" if row["total_score"] >= 40 else "CAUTION"
        row["geometry_key"] = row["market"]
    quality_status = (
        "PARTIAL"
        if missing_categories or forecast_degraded_markets
        else "OK"
    )
    summary = (
        "已完成市场分析；部分字段因数据不可用或预测质量不足而降级或留空。"
        if quality_status == "PARTIAL"
        else "已完成市场分析，结果包含贸易指标、关税、宏观环境、预测和综合评分。"
    )
    return response(
        summary,
        {
            "hs6": hs6,
            "markets": rows,
            "scope": {
                "supported_markets": supported_markets,
                "requested_markets": markets,
                "available_markets": [row["market"] for row in rows],
                "missing_markets": missing_markets,
                "analysis_year": analysis_year,
                "year_selection": year_selection,
                "data_completeness": quality_status,
                "ranking": "请求市场相对排序",
                "global": "World aggregate",
            },
        },
        coverage_warnings,
        forecast_method="ARIMA(1,1,0)+CAGR_FALLBACK",
        quality_status=quality_status,
        missing=sorted(missing_categories),
    )


def analyze_customers(params: dict) -> dict:
    """从客户和订单表计算RFM+G+I、正式K-means、价值和流失风险。"""
    observation = pd.Timestamp(params.get("observation_date", "2025-12-31"))
    market = params.get("market")
    connection = connect()
    customer_frame = connection.execute(
        """
        SELECT
            customer_id,
            country_iso3 AS country_code,
            customer_type,
            company_size
        FROM dim_customer
        """
    ).df()
    transaction_frame = connection.execute(
        """
        SELECT
            order_id,
            line_id,
            customer_id,
            order_date,
            hs_code AS hs6,
            net_sales_usd AS net_amount_usd,
            country_iso3 AS country_code
        FROM fact_transaction
        WHERE status = 'normal'
        """
    ).df()
    connection.close()
    if market:
        customer_frame = customer_frame[customer_frame.country_code == market]
        transaction_frame = transaction_frame[transaction_frame.country_code == market]
    transaction_frame["order_date"] = pd.to_datetime(transaction_frame["order_date"])
    first_order_dates = transaction_frame.groupby("customer_id")["order_date"].min()
    customer_frame["first_order_date"] = customer_frame["customer_id"].map(first_order_dates)
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
        rows.append({"customer_id": customer.customer_id, "country_code": customer.country_code, "customer_type": customer.customer_type, "company_size": customer.company_size, "first_order_date": pd.Timestamp(customer.first_order_date).date().isoformat(), "R": (observation - last_order).days, "F": frequency, "M": monetary, "G": growth, "I": int(orders.hs6.nunique()), "recent_6_frequency": len(recent_6), "previous_6_frequency": len(previous_6), "recent_6_amount": float(recent_6.net_amount_usd.sum()), "previous_6_amount": float(previous_6.net_amount_usd.sum()), "new_customer": first_order_age < 180, "churn_sample_sufficient": first_order_age >= 365})
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
    def cluster_label(value):
        if pd.isna(value):
            return "NEW"
        return cluster_names.get(value, f"客户群{int(value) + 1}")

    frame["cluster_label"] = frame.cluster.map(cluster_label)
    clusters = []
    for cluster_id, group in frame.groupby("cluster"):
        clusters.append({"cluster": int(cluster_id), "label": cluster_names.get(cluster_id, f"客户群{int(cluster_id) + 1}"), "size": len(group), "mean_value_score": round(float(group.value_score.mean()), 2)})
    customer_records = frame.drop(columns=["M_score", "F_score", "R_score", "G_score", "avg_order_interval_days", "D", "Fd", "Md", "recent_6_frequency", "previous_6_frequency", "recent_6_amount", "previous_6_amount"], errors="ignore").replace({np.nan: None}).to_dict(orient="records")
    data = {"scope": {"market": market, "product": "ALL_PRODUCTS"}, "observation_date": observation.date().isoformat(), "customers": customer_records, "clusters": clusters, "cluster_model": {"algorithm": "K-means", "k": cluster_count, "selection": "轮廓系数主导、业务可解释性校验", "silhouette_score": best_score, "standardization": "StandardScaler"}, "value_model": {"formula": "0.5M'+0.2F'+0.1R'+0.2G'", "normalization": "全体客户池MinMaxScaler", "grades": {"S": 85, "A": 70, "B": 50}}, "churn_model": {"type": "规则评分，不是机器学习预测", "windows": "两个6个月", "grades": {"HIGH": 70, "MEDIUM": 40}, "new_customer_rule": "首单距今<6个月不参与K-means；<12个月不参与流失分级"}, "dataset_note": "比赛演示模拟数据，非真实企业数据"}
    return response(f"已完成{len(frame)}家客户的正式K-means分群、价值和流失风险分析。", data, ["客户数据为比赛模拟数据，价值分不代表利润，流失分不是概率。"])


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
    data = {"target_market": market, "hs6": market_analysis.get("hs6"), "customer_scope": customer_analysis.get("scope"), "cards": cards, "action_whitelist": sorted({action for actions in action_map.values() for action in actions}), "forbidden": ["具体价格", "折扣", "账期", "赠品"]}
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
            result = failed_response("WORKER_ERROR", "分析执行失败。", str(error))
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
