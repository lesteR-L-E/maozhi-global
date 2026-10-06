# Python 分析 Worker

## 作用

`analysis_worker.py` 是 Electron 主进程调用的确定性分析层。它不负责对话，也不调用大模型，只做三件事：从 DuckDB 查询数据、运行分析算法、输出结构化 JSON。

## 本地初始化

在项目根目录创建虚拟环境并安装依赖。

Windows：

```powershell
py -m venv .venv
.\.venv\Scripts\pip.exe install -r .\python\requirements.txt
```

macOS/Linux：

```bash
python3 -m venv .venv
.venv/bin/pip install -r python/requirements.txt
```

维护者可从数据组交付包构建真实数据库：

```bash
python python/build_real_database.py \
  --source-dir "<数据交付包目录>" \
  --tariff-csv "<fact_tariff_final.csv路径>"
```

输出为 `data/maozhi-real.duckdb`，也是 Worker 的默认数据库。构建脚本不会修改
原始 `trade.duckdb` 或 CSV。`build_demo_database.py` 仅用于生成旧版演示/回归数据。

## 数据边界

- 贸易、宏观、关税和国家/HS维表为公开真实数据；
- 客户与交易表为比赛模拟数据，不能用于真实客户结论；
- 数据库整体标记为 `MIXED`；
- 市场横向评分使用2023年共同完整年度；
- 关税使用2022年 China-specific Applied Tariff，不解释为 RCEP tariff；
- 月度序列少于60点时使用历史 CAGR，不运行 ARIMA。

## Worker 协议

Worker 使用“一行请求、一行响应”的 JSON 协议：

```json
{"method":"analyze_markets","params":{"confirmed_hs6":"851671"}}
```

可用方法：`resolve_product`、`analyze_markets`、`analyze_customers`、`recommend_operations`。

真实数据接入没有改变 Agent 工具的输入输出协议。可以用
`MAOZHI_DATABASE=/path/to/file.duckdb` 指定其他数据库，也可以用 `PYTHON_BIN`
指定 Python 解释器。

## 冒烟测试

不调用大模型，只验证真实数据库和四个工具链路。

Windows：

```powershell
.\.venv\Scripts\python.exe .\python\smoke_test.py
```

macOS/Linux：

```bash
.venv/bin/python python/smoke_test.py
```

## 当前正式算法

- 市场预测：月度序列不少于60点时使用 `statsmodels.tsa.arima.model.ARIMA` 的 ARIMA(1,1,0)，以最后12个月回测并输出 MAE/RMSE/MAPE；不足60点时降级为历史 CAGR。
- 客户聚类：`sklearn.cluster.KMeans`，在StandardScaler特征上比较2–6个K，并以轮廓系数选择候选模型。
- 客户价值/市场指标归一化：`sklearn.preprocessing.MinMaxScaler`。

数据库查询层负责把真实字段映射到稳定的工具返回契约，Agent 工具的输入输出协议保持不变。
