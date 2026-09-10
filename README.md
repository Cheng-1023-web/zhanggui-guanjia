# 掌柜管家 · 商户运营 LLM Agent 智能助手

面向中小商户的 LLM Agent 运营助手，覆盖商品、订单、营销等业务场景，
实现「意图识别 → 任务规划 → 工具选择 → Function Calling → 结果处理 → 自然语言回复」的
完整闭环（ReAct 范式），并提供会话记忆分层与 JSON Structured Output 稳定性优化。

> **关于本仓库的数据诚实性**：下文所有基线指标均由实际运行 `python -m app.cli eval`
> 与 `python -m app.evaluation.test_structured` 取得，可在本地一键复现。
> 项目默认使用**零依赖规则基线**（规则意图路由 + 确定性参数抽取），
> 保证无网络、无 GPU、无模型权重的环境下 clone 即可跑通全部数字。
> 生产级组件（Ollama/vLLM 真实 LLM 驱动的 ReAct 循环、量化、压测）的代码路径
> **均已实现但默认关闭**，启用方式见「[接入真实 LLM](#接入真实-llmollamavllm)」。

---

## 架构

```
用户输入 ──▶ ① 记忆层（事实抽取 + 长期记忆召回 + 短期上下文裁剪）
              │
              ▼
         ② 任务规划器 RulePlanner（意图识别 + 单步/多步任务分解 / 信息不足→澄清反问）
              │  Plan: intent + steps[{tool, args}] + need_clarify
              ▼
         ③ ReAct 循环（≤ max_iters=6）
              Thought → Action(JSON Structured Output) → Observation ─┐
                ▲                                                     │
                └─────────────────────────────────────────────────────┘
              │  工具执行前强制 JSON Schema 校验，参数非法不执行业务函数
              ▼
         ④ 结果处理：多步结果组装 / 长期记忆折叠（episode）/ 会话持久化
              │
              ▼
            自然语言回复
```

**11 个业务工具**（JSON Schema 定义，注册于 `app/tools/`）：

| 域 | 工具 | 能力 |
|---|---|---|
| 商品 | `query_products` | 关键词/类目/价格过滤，销量·价格·评分·库存排序 |
| 商品 | `update_price` | 改价 + 毛利率变化提示 |
| 商品 | `check_inventory` | 单品库存 / 低于预警线清单 / 库存紧张排行 |
| 商品 | `restock_product` | 补货登记 |
| 订单 | `query_orders` | 订单号精查 / 状态 + 时间范围（今天·昨天·上周六·上月…） |
| 订单 | `order_stats` | 订单量 / GMV / 客单价 / 状态分布 / Top 商品 |
| 订单 | `process_refund` | 退款处理（状态机校验：仅「退款申请中」可退） |
| 营销 | `create_coupon` | 满减券 / 折扣券（含参数合理性校验） |
| 营销 | `suggest_campaign` | 数据驱动的清仓/拉新/复购方案 |
| 营销 | `sales_report` | 按品类/商品/日期汇总 GMV |
| 营销 | `query_reviews` | 差评检索 + 共性问题归因 |

**三层记忆**（`app/agent/memory.py`）：

1. **短期上下文记忆**：滑动窗口（最近 6 轮）+ token 预算裁剪（1200 token）；
2. **任务草稿板**：单次 ReAct 循环内的 Thought/Action/Observation 中间态，任务结束折叠；
3. **长期记忆**：事实抽取（店名/主营/偏好，规则式可解释）+ 情景片段向量检索
   （字符 bigram 哈希向量 256 维，余弦 Top-3 召回注入上下文），会话持久化到 `data/sessions/`。

---

## 快速开始

```bash
# Python 3.10+
pip install -r requirements.txt

# 1. 演示 6 个典型场景（改价/退款/建券/多步规划/差评…）
python -m app.cli demo

# 2. 单条问答
python -m app.cli run -q "现在哪些商品库存不足"

# 3. 交互式多轮对话（测试记忆：先说"我们店叫XX"，再问业务）
python -m app.cli chat

# 4. 工具调用评测（40 条评测集）
python -m app.cli eval

# 5. 结构化输出修复链路实测（170 条污染样本）
python -m app.evaluation.test_structured

# 6. 启动 Web 服务（FastAPI，http://127.0.0.1:8010）
python -m app.cli serve
```

---

## 实测数据（规则基线，可复现）

### 工具调用评测 —— `python -m app.cli eval`

| 指标 | 数值 |
|---|---|
| 评测集规模 | **40 条**（覆盖 14 类场景，含多步规划、澄清反问、闲聊拒答） |
| 工具选择准确率 | **97.5%**（39/40） |
| 参数抽取准确率 | **97.5%** |
| 端到端成功率 | **97.5%** |
| 端到端执行成功率 | **97.5%** |
| 单轮延迟（进程内热路径） | **mean 0.42ms / median 0.39ms / P95 0.77ms**（40 query × 5 轮 = 200 次采样，已预热） |

> ⚠️ **延迟口径说明**：`python -m app.cli eval` 报告中的 `avg_latency_ms` 在**首次冷启动**时
> 可达约 46ms（含模块加载与分词词典初始化），预热后稳定在 **0.4ms 量级**。
> 上表取的是 200 次采样的热路径值。该延迟为**规则基线路径**（不含模型推理），
> 因此数值本身不构成性能卖点，列出仅为说明规则路由的开销可忽略。

分场景：商品查询 5/5 · 改价 3/3 · 库存 4/4 · 补货 2/2 · 订单查询 3/3 ·
订单统计 3/3 · 报表 2/3 · 退款 3/3 · 优惠券 3/3 · 营销策划 3/3 ·
**多步规划 1/1** · 评价 2/2 · 闲聊零调用 3/3 · 澄清反问 2/2

**唯一失败案例（保留不修）**：q22「8月份哪款产品卖得最好」——需要按时间窗聚合（sales_report），
规则路由被「产品卖得最好」词面引到 query_products。这是**规则基线的语义上限**，
正是接入 LLM 做意图理解的直接动机（见失败归因，与掌柜智库的 hash 嵌入失败案例同一方法论）。

### JSON Structured Output 修复链路 —— `python -m app.evaluation.test_structured`

以 34 个工具调用样本为基材，程序化注入 5 类 LLM 常见输出污染
（markdown 围栏 / 中文引号 / 尾逗号 / 前后噪声文本 / 组合污染），共 **170 条**：

| 解析方式 | 解析成功率 |
|---|---|
| 裸 `json.loads`（对照组） | **0/170** |
| 修复（repair）+ Schema 校验（validate） | **170/170（100%）** |

配合「校验失败 → 错误信息注入 Prompt → 带反馈重试（≤2 次）」闭环，
这是 LLM 模式下工具调用成功率保障的核心机制。报告：`data/logs/structured_eval_report.json`。

### 多轮记忆验证

- 「我叫小王，我们店叫青柠小铺，主营奶茶」→ 长期事实 `店名：青柠小铺`、`主营：奶茶` 被抽取并持久化；
- 会话内再次提问时事实与相似历史片段自动注入上下文（`/api/session/{sid}` 可查）；
- 记忆分层写入见 `app/agent/memory.py`（短期/草稿板/长期三段式）。

---

## 接入真实 LLM（Ollama/vLLM）

代码路径已全部实现（`app/llm/client.py` 标准库直连 OpenAI 兼容协议；
`app/agent/react.py` 的 `_run_llm` 完整 ReAct 循环）：

```yaml
# config.yaml
llm:
  provider: "openai"                      # Ollama
  api_base: "http://127.0.0.1:11434/v1"
  model: "qwen2.5:7b-instruct-q4_K_M"
structured:
  max_retries: 2
```

```bash
# Ollama 路径
ollama run qwen2.5:7b-instruct-q4_K_M
python -m app.cli run -q "8月份哪款产品卖得最好"     # 规则基线失败的语义场景，LLM 路径可解

# 三种模式对照评测（结构化输出的价值量化）
python -m app.cli eval --mode rule               # 规则基线
python -m app.cli eval --mode llm                # LLM + JSON 约束 + 校验重试
python -m app.cli eval --mode llm-unstructured   # LLM 无约束自由文本（对照组）
```

**LLM 路径下的评测口径**：`--mode llm` 与 `--mode llm-unstructured` 的成功率差值
即「JSON Structured Output + 校验重试」对工具调用稳定性的提升幅度；
输出质量故障（围栏包裹/引号错误/尾逗号/噪声文本）由修复链路兜底（见上节 0% → 100%）。

### 推理压测（吞吐 / TTFT / 并发）

```bash
python scripts/bench_infer.py --api-base http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b-instruct-q4_K_M --concurrency 1,4,8 --n 10 --stream
```

输出吞吐量（tokens/s）、TTFT 均值/P95、并发扩展曲线，报告写入 `data/logs/bench_report.json`。

### 模型量化

`scripts/quantize_llama.sh`：llama.cpp 路线 FP16 → INT8(q8_0) / INT4(q4_K_M) GGUF，
产出体积对比与 Ollama 加载验证命令；显存/内存占用采样命令内置于脚本输出提示。
（量化精度损失用 `eval --mode llm` 在量化前后模型上各跑一轮对比即可量化。）

### Docker 容器化

`deployment/docker-compose.yml` 双容器：vLLM 推理服务（GPU，开启 prefix-caching）
+ Agent API 服务，通过环境变量自动注入 LLM 配置：

```bash
docker compose -f deployment/docker-compose.yml up -d
```

---

## FastAPI 服务

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/chat` | POST | 对话，返回答案 + 意图 + 工具调用链 + 耗时 |
| `/api/tools` | GET | 11 个工具的名称/描述/JSON Schema |
| `/api/session/{sid}` | GET | 会话轮数 / 长期事实 / 记忆片段 / 最近消息 |
| `/api/health` | GET | 健康检查（provider / 工具数 / 数据规模） |
| `/` | GET | Web 聊天界面（`web/index.html`） |

---

## 目录结构

```
├── app/
│   ├── config.py               # 配置加载 + 环境变量覆盖（docker 注入）
│   ├── structured.py           # JSON Schema 校验 + 修复 + 带反馈重试闭环
│   ├── cli.py                  # run / chat / tools / eval / serve / demo
│   ├── agent/
│   │   ├── react.py            # ReAct 引擎（规则路径 + LLM 路径）
│   │   ├── planner.py          # 意图识别 + 单/多步任务规划 + 澄清反问
│   │   └── memory.py           # 三层记忆（短期/草稿板/长期向量记忆）
│   ├── tools/
│   │   ├── base.py             # Tool/Registry/Observation（Function Calling 契约）
│   │   ├── store.py            # 业务数据仓库 + 相对时间解析 + 变更审计日志
│   │   ├── product_tools.py    # 商品域 4 工具
│   │   ├── order_tools.py      # 订单域 3 工具
│   │   └── marketing_tools.py  # 营销域 4 工具
│   ├── llm/client.py           # OpenAI 兼容客户端（流式/非流式，标准库实现）
│   ├── api/server.py           # FastAPI 服务
│   └── evaluation/
│       ├── eval_set.json       # 40 条评测集（14 类场景）
│       ├── metrics.py          # 工具选择/参数/端到端指标
│       └── test_structured.py  # 结构化修复链路实测（170 污染样本）
├── data/                       # 模拟商户数据（15 商品/120 订单/39 评价，种子固定可复现）
├── scripts/                    # bench_infer.py 压测 / quantize_llama.sh 量化 / gen_mock_data.py
├── deployment/                 # Dockerfile + docker-compose（vLLM + Agent）
├── prompts/                    # 6 套可复用 Prompt 模板
└── web/index.html              # 轻量聊天前端
```

**规模**：2879 行 Python（app 2612 + scripts 267），11 个工具，5 个 REST 接口，7 个 CLI 命令（run / chat / tools / eval / serve / demo / stats），6 套 Prompt 模板。

---

## 设计取舍

| 决策 | 取舍 | 理由 |
|---|---|---|
| 默认规则基线而非真实 LLM 驱动 | 意图泛化能力弱（q22 案例） | 离线可复现全部指标；LLM 路径代码已就绪，一行配置切换 |
| 自实现 ReAct 循环而非引 LangChain/LangGraph | 多写约 300 行 | 引擎/记忆/工具对两条路径完全复用；避免重依赖，可对照官方范式讲解 |
| 字符 bigram 哈希向量做长期记忆检索 | 无真正语义召回 | 零依赖可运行；接口与真实 Embedding 相同，替换只改 `hash_vector` |
| Schema 校验前置到工具执行之前 | 参数非法直接返回错误 Observation | 与「执行后报错」相比，错误信息可携带 Schema 反馈给模型修复 |
| 模拟商户数据种子固定 | 数据非真实 | 保证相对时间解析与评测确定性；结构对齐真实业务库 |

---

## 已知局限

1. **规则基线的语义上限**：q22 类语义改写场景失败（97.5% 的全部损失来源），LLM 路径可解
2. **LLM 模式指标待实测**：吞吐/TTFT/并发/量化精度损失需在有 GPU 或本地模型的环境运行压测脚本取得
3. **长期记忆检索为词法哈希向量**，无语义召回；事实抽取为规则式，复杂表述覆盖有限
4. **单商户单进程**：无多租户/鉴权，SessionStore 为文件存储，高并发需换 Redis
5. **评测集 40 条**：样本量中等，分场景指标存在波动
6. **无流式前端渲染**：LLM 路径的流式输出客户端已实现（`chat_stream`），Web 前端未接入

---

## 复现步骤

```bash
cd zhanggui-guanjia
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli demo                                  # 6 场景演示
python -m app.cli eval                                  # 期望: 端到端 97.5%（39/40）
python -m app.evaluation.test_structured                # 期望: 0/170 → 170/170
python -m app.cli serve                                 # Web: http://127.0.0.1:8010
```

> **关于 `data/logs/`**：评测报告（`eval_report.json`、`structured_eval_report.json`）
> 由上述命令运行时生成，已在 `.gitignore` 中忽略、不纳入版本控制。
> 本文引用的全部指标均由你本地运行同一命令复现，而非依赖仓库内的快照文件。
> 同理 `data/sessions/`（会话持久化）与 `data/mutations.jsonl`（业务变更审计日志）
> 均为运行时产物。`data/products.json`、`orders.json`、`reviews.json` 为
> `scripts/gen_mock_data.py` 的确定性产出（种子固定、数据截止日固定为 2026-09-09），已纳入版本控制。

## License

MIT
