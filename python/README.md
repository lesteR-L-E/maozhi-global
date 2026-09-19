# Python 分析 Worker

## 作用

`analysis_worker.py` 是 Electron 主进程调用的确定性分析层。它不负责对话，也不调用大模型，只做三件事：从 DuckDB 查询数据、运行分析算法、输出结构化 JSON。

## 本地初始化

在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r python/requirements.txt
.venv/bin/python python/build_demo_database.py
```

生成的数据库为 `data/maozhi-demo.duckdb`。这是固定的比赛演示模拟数据，不是真实企业数据。

## Worker 协议

Worker 使用“一行请求、一行响应”的 JSON 协议：

```json
{"method":"analyze_markets","params":{"confirmed_hs6":"851671"}}
```

可用方法：`resolve_product`、`analyze_markets`、`analyze_customers`、`recommend_operations`。

可以用 `MAOZHI_DATABASE=/path/to/file.duckdb` 指定其他数据库。Electron 开发模式默认使用项目 `.venv/bin/python`；也可以用 `PYTHON_BIN` 指定Python解释器。

## 冒烟测试

不调用大模型、只验证数据库和四个工具链路：

```bash
npm run test:worker
```

## 当前正式算法

- 市场预测：`statsmodels.tsa.arima.model.ARIMA`，使用ARIMA(1,1,0)，最后12个月回测并输出MAE/RMSE/MAPE。
- 客户聚类：`sklearn.cluster.KMeans`，在StandardScaler特征上比较2–6个K，并以轮廓系数选择候选模型。
- 客户价值/市场指标归一化：`sklearn.preprocessing.MinMaxScaler`。

真实数据接入时，保持表名、字段和工具返回契约不变，替换数据库内容即可。
