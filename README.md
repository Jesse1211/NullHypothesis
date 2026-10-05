# NullHypothesis

> 假设过去某段时间真的按这条策略操作,账户最后会变成什么样。

一个手写的回测框架。本体是**一个循环 + 一本账**:逐日读价 → 问策略今天该持多少
→ 与昨天不同就买卖并扣费 → 记下现金、持股、总资产。

依赖(本机均已装):`pandas` `matplotlib` `pytest` `PyYAML` `fastapi` `uvicorn`
`pydantic` · Node `v23.10` / npm `10.9`。**`backtesting.py` 不是依赖** —— 它只在
设计期作为语义参考,已从环境卸载。

---

## 这东西能做什么、不能做什么

**能做** —— 换策略、换数据、换区间反复跑;比较多条策略在**这段历史上**的结果;
看一条策略的净值形状与交易频率。

**不能做** —— 判断哪条策略更好;说任何关于未来的话;保证这些数字可信。

**未计滑点,不支持做空 / 杠杆 / 限价 / 止损,未验时序稳健性。此输出不能用来做
决定。** 这不是待办清单,是明确的非目标(见 `DESIGN.md` §0)。

两条贯穿全程的前提,界面上也始终显示:

1. **OHLC 必须已预先复权**,框架**不验证**这一点(ADR-001)。复权只能发生在数据
   进入框架之前 —— 框架内没有任何处理分红与拆股的机制。
2. **仅支持市价单。T 日意图在 T+1 开盘无条件执行**(ADR-002)。

---

## 跑起来

### 命令行

```bash
python3 backtest.py --data data/aapl.csv --strategy strategies/buy_and_hold.py
```

> **`data/` 在全新 checkout 上是空的** —— 仓库不带数据(行情数据不该进 git,
> 且复权状态无法在仓库里保证)。自己放一份 CSV 进去,格式见下一节。

| flag | 必填 | 默认 | 约束 |
|---|---|---|---|
| `--data` | ✅ | — | 文件须存在 |
| `--strategy` | ✅ | — | 可重复(多策略对比);stem 不得重复 |
| `--cash` | ❌ | `100000.0` | `> 0` |
| `--fee` | ❌ | `0.0` | `0 <= fee < 0.1` |
| `--out` | ❌ | `out/` | 不存在则创建 |

退出码:`0` 成功 · `2` 数据校验失败 · `3` 策略异常 · `4` CLI 参数非法。

每次运行在 `out/<YYYYMMDD-HHMMSS>/` 下留一份归档:净值 PNG、`trades.csv`、
`summary.txt`、`summary.json`、`run.json`,多策略时还有 `comparison.png`。

### Web 界面

```bash
cd frontend && npm install && npm run build && cd ..
python3 -m uvicorn api:app --port 8000
# 打开 http://127.0.0.1:8000
```

同源托管,**无需 CORS 配置**。只监听 `127.0.0.1`(ADR-034)。

顶栏两个 tab,**二选一**(ADR-048):

| tab | 左栏 | 主栏 |
|---|---|---|
| **跑一次** | 策略多选 / 数据 / 资金 / 费率 | 汇总 → 对比 → 曲线 → 交易清单 |
| **历史** | 归档列表 | 当时那次的 PNG 快照 + 汇总 |

主栏顺序是**结论先行**(ADR-047):你问的是「账户最后会变成什么样」,所以
汇总排第一,曲线是证据排第二。曲线下面紧跟两样东西,位置本身是契约:归档
PNG 的出处标注,以及**「已知局限」警示区块** —— 后者是 ADR-001/002 那两条
不可验证前提在界面上的唯一守卫,不得降级为脚注。

跑完**不会自动跳到历史页** —— 结果就在当前页,只让历史 tab 的角标 +1。

开发时用 vite 代理:

```bash
python3 -m uvicorn api:app --port 8000   # 一个终端
cd frontend && npm run dev               # 另一个,:5173 代理 /api → :8000
```

---

## 输入数据

CSV,列为 `Date,Open,High,Low,Close[,Volume]`。`Adj Close` 若存在会被**丢弃**
—— 复权是框架外的责任(见上面的前提 1)。

校验会去重、按日期升序排序;非单调递增只告警不报错。缺失的交易日对框架不存在
——「交易日」的定义就是**校验后 CSV 的一行**,框架没有交易日历(ADR-042)。

`data/` 放 CSV,`strategies/` 放策略文件 —— Web 界面扫这两个目录。**不支持上传**:
数据只能由能访问服务器文件系统的人放进去,这样「这份数据是否已复权」有一个明确
的责任人(ADR-027)。

---

## 写一条策略

继承 `Strategy`,实现 `next()`。文件里恰好一个 `Strategy` 子类。

```python
from nullhypothesis.strategy import Strategy


class BuyAndHold(Strategy):
    def next(self) -> None:
        self.target(weight=1.0)      # 永远满仓
```

均线交叉这类需要状态的,用 `init()` 做一次性准备:

```python
class MaCross(Strategy):
    def init(self) -> None:
        self.prev_above = None       # init() 在第 0 根之前恰调用一次

    def next(self) -> None:
        close = self.data['Close']
        if len(close) < 60:
            return
        above = close.rolling(20).mean().iloc[-1] > close.rolling(60).mean().iloc[-1]
        if self.prev_above is not None and above != self.prev_above:
            self.target(weight=1.0 if above else 0.0)   # 边沿触发
        self.prev_above = above
```

**可读**:`self.data`(截至今日的历史,是独立副本)、`self.cash`、`self.shares`、
`self.equity`。四个都只读。

**可调**:

| 方法 | 含义 |
|---|---|
| `self.target(weight=w)` | 持仓占**可投资产**的比例。`w=1.0` 满仓,`w=0.0` 清仓 |
| `self.order(shares=n)` | 买卖 `n` 股(正买负卖) |

两者语义正交。同一个 `next()` 内多次调用 → **末次胜出**(ADR-041)。

`target` 的分母是**可投资产 = cash + shares × Open_{T+1}**,不是 `equity`
(后者按 Close 估值)。这样写是因为成交发生在 T+1 开盘,用 Close 当分母会算出
一个在成交时点并不成立的股数。

### 两个容易踩的点

**差额为 0 不生成订单。** 所以 `buy_and_hold` 天天调 `target(weight=1.0)` 只会
成交 1 次 —— 幂等是引擎的责任(ADR-008),策略不必自己记「我是不是已经买了」。

**最后一根 K 线上的意图会被丢弃。** 这是 T+1 成交语义的必然推论:兑现它需要一个
不存在的明天(ADR-005)。

---

## T+1 成交:整个语义就是两行的顺序

```python
for bar in bars:
    fill = bt.resolve_queue(bar)   # 阶段 A · 开盘:结算【昨天】的意图
    strategy.next()                # 阶段 B · 收盘:问【今天】的意图
```

调换 A 与 B,语义立刻退化为同一根 K 线成交 —— **不报错、曲线照画**,只是数字
全是假的(它用了当天收盘才知道的信息去当天成交)。

所以第一笔成交最早在第 2 根 K 线上。`DESIGN.md` 的 ADR-002 里有一份六个参考框架
的实现调研:3 个用 next-open、1 个 next-close、2 个 same-close —— **它不是行业
标准**,我们选它的理由写在那条 ADR 里。

---

## 买入持有:唯一有封闭解的策略

**买入持有的净值曲线必须与价格曲线形状完全一致。** 界面上两条画在一张图里
(左轴净值、右轴价格),重合与否肉眼可见 —— 这是整条管线最强的一个外部校验:
成交价、股数、估值基准、残余现金,任何一处错了形状都会分开。

精确形式(实测零误差,不是近似):

```
equity[i] == shares[i] * Close[i] + cash[i]      逐日,误差 0.000e+00
(equity[i] - cash[i]) / Close[i] == 持股数        【完全恒定】
```

**必须减掉 `cash`**:买不满整股会剩一点(fee=0 时 0.10,fee=0.0013 时 6.78),
那是个固定偏移。直接用 `equity/Close` 会看到 8.3e-03 的漂移 —— 那不是缺陷。

**手续费的唯一作用路径是「少买几股」**,于是投入部分永远低一个**恒定**比例,
且该比例恰等于股数比:

| | fee=0 | fee=0.0013 |
|---|---|---|
| 成交 | 3663 股 @ 27.30 | **3658** 股 @ 27.30 |
| 残余现金 | 0.10 | 6.78 |
| 总收益 | 145.49% | 145.17% |
| 投入部分比值 | — | **0.9986349986** = 3658/3663 |

反向对照也有门:**零交易策略的净值必须是水平线而价格在动**。没有这条,一个
把 `equity` 直接设成 `Close × 常数` 的错误实现会通过上面所有比值门 —— 它在
买入持有下看起来完美。

---

## 代码在哪

```
nullhypothesis/        引擎(阶段一)
  data.py              CSV 读取与校验
  strategy.py          Strategy 基类 + 文件加载
  account.py           账本(聚合内部实体,不对外暴露)
  engine.py            聚合根:订单队列 + T+1 结算
  run.py               主循环 + 跳空统计 + 值对象
  report.py plot.py    汇总文本 / PNG 与 trades.csv
  errors.py            领域错误类型
backtest.py            CLI
api.py                 FastAPI(阶段二)
frontend/src/          React + TypeScript 界面
strategies/ data/ out/ 策略 / 数据 / 归档
tests/                 460 条门
contracts.yaml         所有被钉死的字面量的单一来源
DESIGN.md              决策账本:49 条 ADR + 11 条不变式 + 验收门
```

**`DESIGN.md` 是唯一真相来源。** 改行为前先读相关 ADR —— 代码注释里引用的编号
都能在那里查到。`contracts.yaml` 存所有被测试断言的字面量(格式串、文件名、
退出码、正则、数值 fixture),改它会同时影响代码与门。

---

## 测试

```bash
python3 -m pytest tests/ -q                       # 460 passed, 2 skipped
cd frontend && npx tsc --noEmit && npx vitest run # 零错误 / 102 passed
```

两条 skip 是设计上预期的,各自会在相反的条件下转为执行:

- `test_api.py` 的静态挂载守卫门 —— 只在 `frontend/dist` **不存在**时有意义
  (验证缺少构建产物时 API 仍能起);`npm run build` 跑过之后它自动跳过
- `test_real_stack.py` 的 LAN 探测 —— 取不到非回环 IPv4 时跳过,并已记入盲区。
  同文件里真正断言 ADR-034 的那条(解析 uvicorn 启动日志,断言出现
  `127.0.0.1` 且**不出现** `0.0.0.0`)始终执行

门不只测「跑通」,也测**语义**:影子账本逐日对账、T+1 顺序、末次胜出、
前端零算术(交易次数必须读 `summary.trade_count` 而不是 `trades.length`)、
路径穿越白名单、布局契约。

**已知盲区**(自动化门结构上看不见,只能人工验):

- jsdom 无布局引擎 —— `ResponsiveContainer` 在父容器无高度时渲染成零宽白图,
  这是字面意义的白屏,测试里查 `<Line>` 仍然通过
- 图表与 PNG 的**视觉**正确性(只验数据绑定)
- `threading.Lock` 不跨进程:多 worker 下第二道防线是归档目录的独占 `mkdir`
