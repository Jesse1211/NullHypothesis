# NullHypothesis · v0 设计账本

> 一个回测框架:假设过去某段时间真的按这条策略操作,账户最后会变成什么样。
>
> **v0 的目标是证明管线闭合,作为地基。验收标准是一条曲线。**
>
> 本文件是构建期的唯一真相来源。构建代理引用 ADR 编号,不重新讨论已锁决策。

---

## 0 · v0 的产品形态与边界

**能做**

- 换策略、换数据、换区间,反复跑
- 比较多条策略在**这段历史上**的结果
- 看到一条策略的净值形状、交易频率

**不能做**

- 判断哪条策略更好
- 说任何关于未来的话
- 保证这些数字可信(未算滑点、未支持限价/止损、未验时序稳健性)

**→ v0 的产品形态:一个可以反复喂策略的管线。它的输出还不能用来做决定。**

### 明确非目标(❌ 不做,且是决策而非遗漏)

滑点 · 做空 · 杠杆 · 多标的 · 组合 · 分红税 · 风险指标(Sharpe/回撤/Beta) · 参数扫描 · walk-forward · 联网取数 · 数据库 · 限价单/止损单 · 权限模型 · 复权计算 · 策略在线编辑 · CSV 上传 · 认证/多用户

**Web UI**:阶段一非目标;**阶段二实现**(见 §8–§12)。

---

## 1 · 领域模型

**BC(限界上下文)**:`Backtest`(回测)。新建。统一语言:中文概念 + 英文标识符。

**边界**:BC 内拥有「账本推进」的全部语义。**不拥有**:数据获取与复权(BC 外的前提,见 ADR-001)、策略逻辑(使用者提供)、图表美学(matplotlib)。

**聚合根**:`Backtest`。唯一入口,持有价格序列、账本、订单队列,守护全部不变式。外部只能通过它推进时间。

### 实体 / 值对象

| 概念 | 类型 | 理由 |
|---|---|---|
| `Backtest` | **聚合根** | 有身份,是事务边界 |
| `Account`(账本) | **聚合内部实体** | 无独立身份、无 id、**不可被聚合外引用**。每个 `Backtest` 恰有一个,随聚合消亡 |
| `Bar`(K 线) | **值对象** | 由 `(date, O, H, L, C, V)` 的值定义,无身份 |
| `TargetOrder` / `ShareOrder` | **值对象** | 不可变。`TargetOrder` 只持 `weight`;T+1 的解析**不改写它**,而是产出一个新的 `Fill`(ADR-016) |
| `Fill`(成交) | **值对象** | 由 `(date, side, shares, price, fee)` 定义,产生后不可变。字段名见 ADR-037 |
| `EquityPoint` | **值对象** | `(date, cash, shares, equity)` |
| `FeeRate` | **值对象** | `rate >= 0`(ADR-039/043) |
| `GapStatistics` | **值对象** | `(max_gap_pct, max_gap_date, mean_abs_gap_pct)`(ADR-021/044) |
| `RunResult` | **值对象** | 聚合根的**发布契约**(ADR-044),字段见 ADR-038 |

> **注**:`Order` 在参考实现中是实体(有 `cancel()`)。我们退化为值对象的真正理由是 **I4**:队列中任一时刻最多一个订单(ADR-041 的末次胜出),故永远无需区分两个订单。ADR-005 的「丢弃」作用于**队列**而非订单本身,不赋予订单生命周期。若 v1 引入 GTC(OQ-06 ⑤),I4 失效,`Order` 须回归实体。
>
> **交易日的定义见 ADR-042** —— 它是 CSV 的一行,不是市场日历上的一天。

### 不变式(聚合根职责)

| # | 不变式 | 来源 |
|---|---|---|
| **I1** | `cash >= 0` | ADR-010 |
| **I2** | `shares >= 0`(仅多头) | ADR-009 |
| **I3** | `equity[i] == cash[i] + shares[i] * Bar[i].Close`,**每个交易日**,容差 `abs(diff) < 0.01`(分) | ADR-003 |
| **I4** | 订单队列长度 `<= 1` | ADR-041(**非** ADR-008 推论) |
| **I5** | 聚合根在第 T 日传给策略的切片**只含** `Bars[0..T]`,且为独立副本 | ADR-002 / ADR-036 |
| **I6** | `equity` 序列长度 `== 交易日数`(ADR-042) | ADR-003(每 bar 一次估值) |
| **I7** | 成交价 `== 成交日的 Open` | ADR-002 |

> **I3 不是同义反复的前提**:`equity` 按 bar 存储于 `EquityPoint`;验收门必须用**独立影子账本**从初始资金逐笔重算 `cash`/`shares` 再比对(见 §5 T3/T5),不得调用被测的 ledger 方法。
>
> **I5 的诚实边界**:聚合根只能保证**自己传出去的**是 `Bars[0..T]` 的独立副本。策略若自行读磁盘、保留跨调用引用、或用模块级变量越界取数,**不在框架防护范围内** —— 这是契约责任,不是运行时守卫。构建期**不得**为此实现沙箱。

### 生命周期

`created -> running -> finished`。`finished` 是终态,不支持重跑(重跑 = 新建实例)。

---

## 2 · ADR 账本

### ADR-001 · 数据契约:预复权是框架外的前提

输入 CSV 列为 `Date,Open,High,Low,Close[,Volume]`。**OHLC 四列必须预先全部按同一复权因子调整。**

框架内**没有任何机制**处理分红与拆股 —— 无事件表、无因子列、无价格调整代码。因此修正**只能**发生在数据进入框架之前。

复权完成后,从框架视角看分红与拆股**不存在**:分红已折进价格序列不再是现金流,拆股造成的跳空已被抹平。框架因此**不需要知道**这两件事曾发生过。

**推论**:框架读到的 `Close` 就是全部真相。它无法区分"这是复权价还是原始价",也不该试图区分。喂进未复权数据,框架会照算,产出一条错的曲线,**且不会报错**。这是契约的责任,不是框架的 bug。

**该前提不可验证,只能靠文档 + 输出声明守护**(文案见 `contracts.yaml` 的 `frozen_text.assumption_adjusted`,由 ADR-021 输出)。`Volume` 不被引擎读取,故不纳入约束。

### ADR-002 · 执行语义:T 日 Close 决策,T+1 日 Open 成交

策略在第 T 根 K 线收盘后被调用,可见截至 T 日的全部数据。其产生的订单在**第 T+1 根 K 线的 `Open`** 成交。

#### 生态系统实测(2026-10-03,GitHub 源码,逐条可复核)

| 框架 | 默认成交 | 源码证据 |
|---|---|---|
| **backtrader** | **下一根 开盘** | `brokers/bbroker.py`:`('coc', False)`、`('coo', False)`;`_try_exec_market` 默认分支 `exprice = popen` |
| **PyAlgoTrade** | **下一根 开盘** | `broker/fillstrategy.py` `DefaultStrategy.fillMarketOrder`:`price = bar.getOpen(...)`;`backtesting.py` 注释 *"It is VERY important that the broker subscribes to barfeed events before the strategy."* |
| **backtesting.py** | **下一根 开盘** | docstring *"market orders are filled on next bar's open"*;`trade_on_close=False` 默认;`broker.next()` 先于 `strategy.next()` |
| **zipline-reloaded** | **下一根 收盘** | `gens/tradesimulation.py`:`blotter.get_transactions()` 在 `handle_data()` **之前**,注释 *"orders placed in the last bar"*;`finance/slippage.py` 默认模型 `price = data.current(order.asset, "close")` |
| **vectorbt** | **同一根 收盘** | `from_signals` 的 `price` 默认 `np.inf`;`portfolio/enums.py` *"If `np.inf`, replaced by the current close."* **源码无前视偏差警告** |
| **bt** | **同一根**(用户给的价格列) | `backtest.py`:`strategy.update(dt)` → `strategy.run()` 同一个 `dt`;无 OHLC 概念,实践中传收盘价 |
| LEAN | 无法判定 | `Orders/Fills/FillModel.cs` 给出价格字段(`bar.Close`),但日线市价单是否延后一根取决于引擎时间切片,源码不足以判定 |

**统计(分母写明)**:6 个框架从源码核实,LEAN 无法判定。

- **「T 日决策 → T+1 成交」(不论什么价):4 / 6** —— 延后执行这个**原则**是多数
- **「T 日 Close 决策 → T+1 **Open** 成交」(本框架的做法):3 / 6** —— 最常见的单一选择,**但不过半**
- **同一根 K 线收盘成交:2 / 6** —— 真实存在的反例,且 vectorbt 很流行

**→ 所以本 ADR 不是「行业标准」。** 可辩护的表述是:**在选择延后执行的框架中最常见的那一种**。

#### 各框架给出的理由(实测:0/6 提到前视偏差)

| 框架 | 理由类别 | 原文 |
|---|---|---|
| backtrader | **「那是作弊」** | *"This is actually **cheating**, because the bar is **closed** and any order should first be matched against the prices [in the next bar]"* |
| backtesting.py | **「K 线内无法决策」** | *"cannot make decisions / trades **within** candlesticks"* —— 数据粒度论证,**不是**偏差论证 |
| PyAlgoTrade | 未说明理由 | 只强调 *"It is VERY important..."*,未说为什么 |
| zipline / vectorbt / bt | 未说明理由 | 循环顺序清楚,但无注释解释选择 |

**关键发现:没有任何框架把「杜绝前视偏差」作为理由。** 本 ADR 早先的草稿把它写成理由,**源码不支持**;那是作者自己的归类,现已删除。两个有解释的框架给的是不同的理由(公平性 vs 数据粒度)。

#### 实现上的核心规则:出队先于入队

```python
for bar in bars:
    resolve_queue(bar)      # A 结算【昨天】的意图
    strategy.next()         # B 问【今天】的意图,入队
```

**T+1 成交语义的全部实现就是 A 在 B 之前。** 把这两行调换,语义立刻退化为同一根 K 线成交 —— 不需要改任何其他代码、不会报错、曲线照样画得出来。**整条纪律挂在两行的顺序上。**

同一模式在三个框架中被独立采用:

| 框架 | 源码体现 |
|---|---|
| backtesting.py | `broker.next()` 先于 `strategy.next()` |
| zipline-reloaded | `blotter.get_transactions()` 先于 `handle_data()`,注释写明填的是 *"orders placed in the last bar"* |
| PyAlgoTrade | *"It is **VERY** important that the broker subscribes to barfeed events **before** the strategy."* |

PyAlgoTrade 那句的语气值得注意:作者知道这行顺序承载了语义,**但没写为什么**。这正是最易被改坏的代码 —— 看起来是两个无关调用,实际是一条语义契约。故 T4 的门断言 **T 日入队的订单对象与 T+1 出队的是同一个对象**(`is` 比较),用以抓出「每日重算」的实现:它结果可能对,但纪律已经没了。

#### 本 ADR 真正的依据

1. **结构性质(可独立检验,不依赖权威)**:策略在 T 日只见过 `Close`,成交用 T+1 的 `Open` —— 后者在决策那一刻尚不存在,故策略**不可能**看到自己的成交价。
2. **与日线数据的物理约束一致**:日线里「收盘后的下一个真实可成交时刻」就是次日开盘。*(该问题在分钟级/逐笔回测中不存在 —— 那里决策与成交相隔毫秒。T+1 开盘成交是**日线**的产物。)*
3. **使用者的明确选择**:方案「T 日 Close 成交」被拒绝,理由是其理想化(收盘后才知道的价格,却假设能按它成交)。

#### 已核实:同一根 K 线成交在物理上不可实现

**美股收盘集合竞价时间线(美东时间),多源一致:**

| 关口 | Nasdaq Closing Cross | NYSE Closing Auction |
|---|---|---|
| 新 MOC 单可提交至 | **15:55 之前** | **15:50 之前** |
| 新 LOC 单可提交至 | 15:58 之前 | 15:50 之前 |
| 撤改在场 on-close 单 | **15:50 之前** | **15:50 之前** |
| 集合竞价成交(收盘价产生) | **16:00** | **16:00** |

**决定性推论**:MOC 订单必须在 **15:50–15:55** 之前提交,而收盘价在 **16:00** 才产生。提交订单的那一刻,**收盘价尚不存在**。且 15:50 起 on-close 单**不可撤改**。

**因此:用 T 日 `Close` 作为决策输入、再按 T 日 `Close` 成交,物理上不可实现。** 它不是一种更激进的惯例,而是在模拟一件做不到的事 —— 这与 backtrader 把它称为「作弊」(*"the bar is closed"*)的判断一致。

**反方检查(是否存在「先看到官方收盘价、再按该价成交」的机制)**:NYSE 在 15:50–16:00 的 Imbalance Freeze 期间**仅**允许针对已公布 Significant Imbalance 的**反向**挂单 —— 那是给已公布失衡提供流动性,不是自由按收盘价成交,且此时收盘价仍未产生。未发现任何允许「观察到官方收盘价后仍按该价成交」的机制。

**溯源**(经 GitHub 代码搜索定位到的引用链,均指向交易所一手文件):

- Nasdaq Equity Rule **4754**(Nasdaq Closing Cross),经 SR-NASDAQ-2018-052 修订(SEC Release 34-84454,2018-10 批准)—— 该修订把 LOC 截止延至 15:58 并引入迟到 LOC 的重定价
- Nasdaq *Opening and Closing Crosses FAQ*(2025)Q7/Q8/Q10/Q17/Q20/Q25
- NYSE Rule **7.35B**(Closing Auction; Closing Auction Imbalance Freeze)
- NYSE *Opening and Closing Auctions Fact Sheet*(2024),"Closing order types" / "Closing timeline"
- 券商侧交叉验证(各自截止时间**早于**交易所,留路由时间):IBKR 交易课程(NYSE 15:50 / Nasdaq MOC 15:55 / LOC 15:58)、Fidelity 帮助页(on-close 须 15:40 前,且 *"Nasdaq does not accept on the close orders"*)、Alpaca(post-15:50 拒单)

**本会话无网络工具**(已对所有 agent 类型探测确认),上述条目经 GitHub 代码搜索在公开仓库中定位,**交易所原文 URL 未经本会话直接 fetch**。数值在多个独立来源间一致(方向无分歧:全部严格早于 16:00),但按 §7 第 3 条,若要作为对外引用请复核一手 PDF。

### ADR-003 · 估值:equity = cash + shares × Close

每个交易日按**当日 `Close`** 市价计值:`equity = cash + shares * Close`。

**修正说明**:本 ADR 早期草稿写作"对齐参考实现的 `equity = cash + 未实现盈亏`"。该定义**不适用于我们** —— 参考实现采用保证金记账,建仓时 `cash` 未扣全额成交额,故两公式在其系统内相等。我们是无杠杆全额付款,`cash` 真实扣光,只有 `cash + shares * Close` 成立。

### ADR-004 · 不使用 Adj Close 列

**来源**:设计期对参考实现全库搜索未发现 `Adj Close`。该库已卸载、网络被拒,构建期无法复核 → 视为**设计决策**。我们不使用该列;复权由 ADR-001 的契约承担。

### ADR-005 · 最后一根 K 线上的订单直接丢弃

最后一根 K 线没有 T+1,其上产生的订单被**静默丢弃**。

**来源**:读参考实现源码推断 —— 其循环结束后不再调用 `_process_orders()`。「且无 warning」是**缺失行为**的断言,属推断而非实测。该库已卸载,构建期无法复核;视为**设计决策**。

### ADR-006 · 策略接口:显式订单队列 + 声明式意图

**已被 ADR-014 实质修正。** 保留的部分:显式的 `Order` 队列(T 日入队、T+1 开盘出队成交)。这是 ADR-002「信号与成交分离」的**物理载体** —— 该纪律在声明式隐含约定里看不见,在队列数据结构里看得见。

**不保留的部分**:参考实现的 `buy(size=...)` API 形态 —— 理由见下一段与 ADR-014。*(ADR-011 已被取代,其正文仅作历史记录,不得作为活理由引用)*

**不借用其 API 形态的理由**:参考实现无「目标仓位」概念(其 `size=0.5` 意为"拿可用流动性的 50% 下这一单",语义不同)。我们只借其**执行语义**,不借 API。

### ADR-007 · 手续费:按成交额比例,默认 0.0

`fee = shares * price * rate`。默认 `rate = 0.0`。**买卖双向均收取,方向见 ADR-043。**

**理由**:与参考实现默认值一致;零成本曲线作为基准,加费后看差异。

### ADR-008 · 重复下单:差额为 0 则无声跳过

引擎计算差额,**差额为 0 则不生成订单、不报警、静默跳过**。幂等是引擎的责任,策略作者可以每天调用 `target()`。

**有意偏离参考实现。** 设计期**实测**其做法:生成订单 → T+1 因可用保证金耗尽 → 取消 + 发 `UserWarning`,60 根 K 线产生 57 个警告。该行为无文档记载,源码留有作者自我质疑 `# XXX: The order is canceled by the broker?`。*(该实测数字记录于本账本;库已卸载,构建期不可复核)*

**偏离理由**:v0 的地基必须可信。一个每日报警但结果莫名正确的引擎,让你无法从警告流中辨别真问题。

### ADR-009 · 仅多头,不支持做空

`shares ∈ [0, +∞)`。`sell` 类操作只能平仓;超额卖出或无持仓卖出 **报错退出**。

**理由**:做空需额外建保证金/借券/空头估值/强平模型,远超"证明管线闭合"。**设计决策**(构建期不可复核):设计期读参考实现源码,其 `sell(size=50)` 在持多 100 股时是 FIFO 平仓 50 股而非开空,语义反直觉;我们直接排除该复杂度。

### ADR-010 · 账本不变式

**I1 `cash >= 0` 的理由(本 ADR 的独立内容)**:v0 无杠杆、全额付款,现金不得透支。负现金意味着借钱,而借钱需要利息模型与强平逻辑(ADR-015 把杠杆推到 v1)。I1 由 ADR-016 的「T+1 开盘才算股数」在构造上保证(永不超买)。

I2 见 ADR-009(仅多头),I3 见 ADR-003(估值定义)。本 ADR 不重复它们。

### ~~ADR-011~~ · ~~size 单参数重载~~ — 已被 ADR-014 取代

原决策:`size` 单参数,`0<size<1` 为比例、`>=1` 为绝对股数。

**取代理由**:(a) 单参数无法容纳杠杆 —— 比例的分母会随杠杆变化,绝对股数不受杠杆影响,两者对杠杆的反应不同;(b) `size=1` 语义歧义(1 股?还是 100% 仓位?),参考实现靠"严格小于 1"的浮点边界切开,故其满仓值为 `0.9999`。

### ADR-012 · 股数取整:floor

目标股数向下取整。算出 0 股则按 ADR-008 无声跳过。小数股不支持。

### ADR-013 · 出图:matplotlib + Agg 后端

输出 PNG 文件。`Agg` 为无界面后端,不依赖显示环境。

**`plot.py` 必须在 import `pyplot` 之前调用 `matplotlib.use("Agg")`**:

```python
import matplotlib
matplotlib.use("Agg")          # 必须在下一行之前
import matplotlib.pyplot as plt
```

*(实测本机默认后端是 `macosx` 而非 `Agg`。它在当前 GUI 会话里 `savefig` 恰好能成功,所以这个缺陷不会立刻暴露 —— 但无人值守构建会依赖一个未被断言的环境属性。此前 `Agg` 在全文只出现在本 ADR,没有任何门检查它。)*

### ADR-014 · 下单接口拆为两个正交方法

```python
self.target(weight=w)   # 目标仓位,分母是【可投资产】(见下方定义,不是 equity)
self.order(shares=n)    # 绝对股数,n>0 买、n<0 卖
```

- `target(weight=w)`:引擎在 **T+1 开盘时**算 `目标股数 = floor(w * 可投资产_{T+1开盘} / Open_{T+1})`,减当前股数得差额
- `order(shares=n)`:差额就是 `n` 本身;`n < 0` 使 `shares` 变负 → 按 ADR-009 **报错**(守护 I2)
- 两者语义正交,无重载。同一 `next()` 内多次调用按 ADR-041 末次胜出

**可负担性约束(ADR-014a,2026-10-03 增补)**:钉死的公式在 `weight ≈ 1` 且 `rate > 0` 时会超买并破坏 I1。实测:`cash=100000`、`rate=0.0013`、`Open=27.07`、`weight=1.0` → `floor(100000/27.07) = 3694` 股,`gross+fee = 100126.58`,**超支 126.58**。

故在算出目标股数后追加一条递减约束:

```python
target = floor(w * investable / Open)        # 钉死的公式不变
while target > shares_held and (target - shares_held) * Open * (1 + rate) > cash:
    target -= 1                               # 只在【增量】真正买不起时生效
```

**约束作用于差额,不是整个仓位。** 已持有的股份不需要再买一次,故只有
`target - shares_held` 这部分要用现金负担。

*(2026-10-04 修正:本条初版写作 `target * Open * (1+rate) > investable` ——
对**整个仓位**计费。但 `investable` 把已持股按 `Open` 计入了,而循环却对
全部股份收费,于是 `shares_held > 0` 时过度递减。实测后果:买入持有在第二根
K 线上会**卖出 1 股**(差额 −1)然后来回倒腾。三个钉死 fixture 都从空仓起,
故全部照常通过 —— 缺陷只在持仓后出现。由 T8 的 `--fee` 端到端门抓到。)*

- `weight = 0.5` → **1847** 股,**与原公式完全一致**(本来就买得起,约束不触发)
- `weight = 1.0` → **3689** 股(原公式的 3694 超买)

**为什么不改除数**:把除数改成 `Open × (1 + rate)` 能一步解决,但它会改变**每一笔** `rate > 0` 的目标成交 —— `weight = 0.5` 从 1847 变 1844,而那里从无超买风险;ADR-038 的示例 3694 也会变 3689。那是修改仓位语义,不是修 bug。本约束只在超支时生效,影响面最小。

*(来源:T4 的 Dev 发现了这个缺陷 —— 设计期从未测试「满仓 + 非零费率」这个组合。Dev 的修法越界了(改了钉死的除数且未改 ADR),被 Standards 席按 §7 第 5 条阻拦,正确;缺陷本身是真的,由使用者裁决为本约束。)*

**「可投资产」的精确定义(关键)**:

```
可投资产_{T+1开盘} ≔ cash + shares * Open_{T+1}
```

**它不是 `equity`。** `equity`(ADR-003)是 `cash + shares * Close`,按**收盘价**估值,每个交易日一个。而成交发生在**开盘**,那一刻当日 Close 尚不存在。两者是不同的量,**I3 不适用于成交时刻**。

此前本 ADR 写作「分母是总资产」而 ADR-003 把总资产定义为 Close 基准 —— 那是一处语义冲突,它决定系统里每一笔成交的大小,现予澄清。

**「分母是可投资产而非现金」的理由**:杠杆的本质是「仓位可以超过 100%」,`weight > 1.0` 在该语义下天然延伸(见 ADR-015)。若分母是现金,已持仓时 `target(weight=1.0)` 只会拿零钞再买一点,而非调到满仓。

### ADR-015 · 杠杆:v0 不实现,接口留位

`weight ∈ [0, 1]`。`weight > 1.0` **报错**,提示文案见 `contracts.yaml` 的 `frozen_text.leverage_not_implemented`。

未来加入 `leverage` 参数后,`weight` 上界变为 `leverage`,**策略代码无需改动**。

### ADR-016 · 目标股数在 T+1 开盘时才计算

`TargetOrder` 入队时只存**意图**(`weight`),不存股数。到 T+1 开盘时,用 `cash + shares * Open_{T+1}` 与 `Open_{T+1}` 计算股数(定义见 ADR-014)。解析**不改写** `TargetOrder`,而是产出一个新的 `Fill`。

**理由**:I1(`cash >= 0`)是不变式,不该靠运气成立。若在 T 日锁定股数,T+1 跳空上涨会使成交额超过总资产 → 现金变负。本方案下永不超买。

**已知代价(见 ADR-020)**:该设计使引擎在任何跳空下都"适应"新价格并执行意图,从而把跳空风险隐藏得更彻底 —— 锁定股数的方案会在跳空上涨时因钱不够而暴露问题。此代价由 ADR-020/021 显式承担。

### ADR-017 · 入口:CLI

```
python backtest.py --data X.csv --strategy S.py [--strategy S2.py ...] \
                   --cash N --fee R --out DIR
```

### ADR-018 · 对比报告

`--strategy` 可重复传入。多条时额外输出 `comparison.png`(多曲线叠加)+ 屏幕对比表;每条策略仍各自输出其 `<name>_trades.csv`。

对比表**只陈述数字,不排名、不评判** —— 它仅说明这段历史,不说明哪条策略更好。

### ADR-019 · 数据校验:载入时一次校完,全部响亮失败

| 情况 | 行为 |
|---|---|
| 缺必须列 | `ValueError`,指明缺哪列 |
| OHLC 含 NaN | `ValueError`,指明行号 + 列名 |
| 价格 `<= 0` | `ValueError`,指明行号 + 列名 + 值 |
| 重复日期 | `ValueError`,指明日期 + 出现次数 |
| 空文件 / 仅表头 | `ValueError` |
| 日期非单调递增 | **排序 + 提示**(非错误) |

**理由**:回测的每一个静默降级都会变成一条你以为可信却不可信的曲线。前填价格 = 静默制造一天假数据。

### ADR-020 · v0 仅支持市价单,T 日意图在 T+1 开盘无条件执行

策略在 T 日表达的意图,会在 T+1 开盘**无条件执行**,不论开盘价如何跳空。策略**无法表达价格条件**。

**这是 ADR-002「决策过期」的本质。** 价格过期本身不是缺陷 —— 现实中你晚上下单、次日开盘成交,成交价当时就是未知的。但**决策内容**可能在 T+1 开盘已失去意义(跳空腰斩时引擎仍会忠实满仓),而策略没有机会撤回。

**现实中亦无此保护**:挂市价单遇跳空就是在跳空价成交。要保护需限价单/止损单 —— 参考实现为此提供 `limit`/`stop`/`sl`/`tp` 四种参数。那是 v1。

### ADR-021 · 汇总必须打印跳空统计与数据假设

> **本 ADR 是全文唯一绑死在「日线」上的决策。** 其余 ADR 要么与粒度无关(队列结构、I1–I7、出队先于入队),要么只是参数(费率、取整)。
>
> 理由:跳空统计量化的是「决策时刻与成交时刻之间的间隔」。日线下这个间隔约 17.5 小时(隔夜),跳空可达 8%;分钟线下约 1 分钟,跳空通常是 0.0x%,这个数会变得无聊。换粒度时**本 ADR 应重新讨论**,大概率要换成滑点/冲击成本/队列位置之类的诊断。
>
> **未验证**:分钟及更细粒度下,「按下一根 bar 的 `Open` 成交」这个假设有多现实,未经调研。日线 `Open` 是**开盘集合竞价**的产物(官方价、全体同价、有规则与截止时间);分钟 bar 的 `Open` 只是**那一分钟内首笔成交**,无集合竞价、无官方性。两者不是同一种东西,故日线的论证不可直接外推。按 §7 第 3 条标注为推断。

汇总输出固定包含:

```
 最大跳空      <max_gap_pct 按 field_formats>  <max_gap_date 按 field_formats>
 平均绝对跳空  <mean_abs_gap_pct 按 field_formats>
───────────────────────────────
 ⚠ <frozen_text.assumption_adjusted>
 ⚠ <frozen_text.assumption_market_order>
```

`<...>` 均为 `contracts.yaml` 的 key —— **本块不含任何字面值**。渲染后的样子由 `field_formats` 决定:`max_gap_pct` 带符号、`mean_abs_gap_pct` 不带(绝对值均值恒 ≥ 0)。

*(此块原先手写成 `-8.42%` / `0.31%`,与九行外的格式表矛盾 —— 第 9 轮实证:把它改成 `+0.31%`,全部契约测试仍绿。现在它没有字面值可改)*

这两条 `⚠` 也必须出现在 API 响应的 `assumptions` 字段(ADR-038)与 Web 界面上 —— 它们是 ADR-001 那条不可验证前提的唯一守卫,不得在界面层静默丢失。

### 屏幕文本的数值格式(阶段一)

**格式规格见 `contracts.yaml` 的 `formats` 与 `field_formats`,此处不复述。**

要点:`total_return_pct` / `max_gap_pct` 用**带符号**百分比;`mean_abs_gap_pct` 用**无符号**百分比(绝对值均值恒 ≥ 0,加 `+` 号零信息)。

*(此处原有一张格式表,与 ADR-021 自己的输出样例、T6 的两条门共五处独立复述同一个决定 —— 于是它在九行之内与输出样例矛盾(`0.31%` vs `+0.31%`)。第 9 轮实证:把门改回错值,契约门 26 个测试全绿。现在格式只有一处定义,`tests/test_design_binding.py` 断言它不被复述)*

跳空定义:`Open_t / Close_{t-1} - 1`,`t` 从第 2 根 K 线起,故 `len(gaps) == 交易日数 - 1`。

- `max_gap_pct` / `max_gap_date`:**按绝对值选取**最大的那一笔,**按原符号显示**(故可能是 `-8.42%`)。绝对值相等时取**较早**的日期
- `mean_abs_gap_pct`:**绝对值的平均**(非带符号平均 —— 后者在真实数据上接近 0,看起来合理却无意义)
- 单行 CSV(ADR-023)时 `gaps` 为空 → 三值(`max_gap_pct` / `max_gap_date` / `mean_abs_gap_pct`)均为 `null`,汇总按 `formats.null_display` 显示,**不得抛异常**

**理由**:这是唯一能让你看见「我的决策在多大的价格偏移上被执行」的数字。2% 则过期无妨;30% 则某笔成交实际发生在一个完全不同的世界里。让 ADR-020 的局限**可量化**,而非只写在文档里。

### ADR-022 · 策略异常或非法值:立即终止

策略抛出异常或请求非法值(`NaN`/`None`/负数/`weight > 1`)时**立即终止**:

- 不写任何输出文件
- 进程以非零状态退出
- 错误信息含:交易日序号 + 日期 + 原始 traceback

```
❌ StrategyError: 策略在第 42 个交易日 (2015-03-02) 抛出异常
   └─ ZeroDivisionError: division by zero
      at strategies/my_strategy.py:12
```

**理由**:策略 bug 被吞掉会产生一条你以为可信却不可信的曲线,这是 v0 最该避开的失真。

### ADR-023 · 单行 CSV:允许,输出零交易 + 提示

仅 1 个交易日时允许运行。无 T+1 故任何订单被丢弃(ADR-005),结果必然是零交易、`equity == 初始资金`。曲线含 1 个点,`trades.csv` 仅表头,并提示 `contracts.yaml` 的 `frozen_text.single_day_notice`。跳空统计为 `null`(ADR-021)。

### ADR-024 · 输出目录:每次跑建时间戳子目录

`--out DIR` 下建 `DIR/YYYYMMDD-HHMMSS/`(本地时区),结果写入其中。历史结果不丢失。目录内容见 ADR-037。

**身份必须构造性唯一,不得依赖时钟。** 时间戳是秒粒度而单次运行是毫秒级,同秒两次运行会抢同一目录。实现须用 `os.mkdir`(**非** `exist_ok=True`)独占创建;`FileExistsError` 则追加 `-2`、`-3`… 重试。`run_id` 因此可能带后缀,格式见 ADR-038。

**归档须记录 `run.json`**(ADR-037),内含完整 `RunRequest`(策略名、数据文件名、资金、费率)与各策略 `summary`。否则 ADR-032 的历史列表无法报告策略名与数据文件 —— 它们无法从文件名反推。

---

### ADR-035 · 文件与模块布局(构建期不得变更)

```
backtest.py                    # CLI 入口(ADR-017/039);【归档目录的独占创建归这里】
                               #   _timestamp() -> str
                               #     返回 YYYYMMDD-HHMMSS。create_archive_dir 必须【经此函数】
                               #     取时间戳 —— 这是 T8/T11 碰撞门的 monkeypatch 接缝,
                               #     内联 datetime.now() 会让那两条门结构上无法执行
                               #   create_archive_dir(out: Path) -> tuple[str, Path]
                               #     独占 os.mkdir + -2/-3 重试(ADR-024),返回 (run_id, dir)
                               #   build_report(run_id, request, results) -> RunReport  (ADR-044)
                               #   write_run_json(report: RunReport, png_paths: dict,
                               #                  out_dir: Path) -> None     # ADR-037
                               #     run.json 是【按运行】的元数据(含 request/assumptions),
                               #     不是绘图产物,故归此层而非 plot.py;png 路径由
                               #     plot.write() 的返回值提供
                               #   stem 唯一性校验也在此层(ADR-037/039),run() 不做
api.py                         # 阶段二 FastAPI:暴露 app / HOST / PORT
                               #   T9  → GET /api/strategies, /api/data
                               #         + validate_run_id(s)->bool, validate_strategy_name(s)->bool
                               #         + 文件【末尾】的静态挂载(见下)
                               #   T10 → POST /api/run
                               #   T11 → GET /api/runs, /api/runs/{id}
                               #   各任务只【追加自己的路由函数】,不改他人的
                               #   pydantic 模型集中在文件顶部(ADR-038 的单一真相来源)
                               #
                               #   串行化接缝(T10 的门必须能观测到锁本身):
                               #     RUN_LOCK: threading.Lock               # ADR-028
                               #     execute_run(req: RunRequest) -> RunReport
                               #       唯一的 run 入口;T10 对它打桩记录进入/退出时刻
                               #
                               #   引用阶段一的产出者必须按【模块属性】,否则无法打桩:
                               #     import nullhypothesis.plot as plot   →  plot.write(...)
                               #     ✗ from nullhypothesis.plot import write
                               #       (已绑定的名字 monkeypatch.setattr(plot,"write") 打不到)
                               #
                               #   静态挂载归 T9,且必须是文件最后一条注册:
                               #     if Path("frontend/dist").is_dir():
                               #         app.mount("/", StaticFiles(directory="frontend/dist",
                               #                                   html=True), name="spa")
                               #   挂在 "/" 会遮蔽后注册的路由,故必须最后;T9 先于 T12,
                               #   那时 dist/ 还不存在,所以必须有 is_dir() 守卫(ADR-033)
nullhypothesis/
  __init__.py                  # T1 建(空文件即可)
  data.py                      # T1  load_csv(path) -> pd.DataFrame
  strategy.py                  # T2  Strategy 基类 + load_strategy(path)
  account.py                   # T3  Account:
                               #       .cash -> float        (只读属性)
                               #       .shares -> int        (只读属性)
                               #       .apply(fill: Fill) -> None
                               #       .equity_at(close: float) -> float   # == cash + shares*close
  engine.py                    # T4+T5  Backtest(聚合根)、订单队列、主循环
                               #   run(strategy_paths: list[str], df, cash: float,
                               #       fee: float) -> list[RunResult]      # T5,不校验 stem 唯一性
                               #   compute_gaps(df) -> list[float]
                               #     ADR-021 的跳空序列;长度 == 交易日数 - 1(T5 的门断言它)
                               #   Backtest.queue -> list                  # 订单队列,T4 的门对它采样
                               #   Backtest.enqueue(order) -> None         # 入队;T4 的 push_count 打在这里
                               #   Backtest.resolve_queue(bar) -> Fill|None
                               #     出队并成交;T4 的门在此采样「出队前」与比对 is 同一性
                               #     (必须是命名方法 —— 内联 self.queue.pop() 会让那条门无法执行)
                               #   值对象:RunResult / RunReport / Summary / RunRequest
                               #          / EquityPoint(字段见 ADR-044)
  report.py                    # T6  render(report: RunReport, out_dir: Path) -> str
                               #      返回屏幕文本,同时把 summary.txt(该文本的副本)
                               #      与 summary.json 写入 out_dir
                               #      (必须有 out_dir:RunReport 只带 run_id,不带归档路径)
  plot.py                      # T7  write(report: RunReport, out_dir: Path) -> dict[str, str]
                               #      PNG + trades.csv + comparison.png;返回各 png 路径
  errors.py                    # T1 一次写全两个类(ADR-040);T2/T8 只 import,不新建
                               #   DataValidationError / StrategyError
strategies/                    # 策略文件(ADR-026 扫描此目录)
data/                          # CSV(ADR-027 扫描此目录)
out/                           # 归档(ADR-024)
tests/                         # pytest
frontend/                      # 阶段二(ADR-037)
```

**理由**:三个 Dev agent 并行写 T1–T4 时,若各自发明模块树,T4/T5 的 import 不解析,`pytest` 收集为零或全是 ImportError。名字必须先定。

### ADR-036 · Strategy 基类接口(构建期不得变更)

```python
class Strategy:
    data: pd.DataFrame        # bars[0..T],I5 截断,index 为 Date,列为 Open/High/Low/Close[/Volume]
    cash: float               # 只读
    shares: int               # 只读
    equity: float             # 只读,== cash + shares * data['Close'].iloc[-1]

    def init(self) -> None: ...            # 可选,第 0 根 K 线之前调用一次
    def next(self) -> None: ...            # 必需,每根 K 线收盘后调用,无参数
    def target(self, weight: float) -> None: ...   # ADR-014
    def order(self, shares: int) -> None: ...      # ADR-014
```

- 引擎每根 K 线调用 `next()`,**无参数**;策略通过 `self.data` 读取截至今日的历史。移动均线即 `self.data['Close'].rolling(n).mean()`
- `self.data` 是**独立副本**,策略无法通过它触及第 T+1 根及以后(I5)
- **`init()` 时的状态**:`self.data` 为**空 DataFrame**(列齐全、零行)、`cash == 初始资金`、`shares == 0`、`equity == cash`。在 `init()` 内调用 `target()`/`order()` → `ValueError`。*(「回测开始前」若不写明,`self.data` 是空、是全量(违反 I5)、还是不存在,是个硬币抛掷;而移动均线策略最自然的写法就是在 `init()` 里准备窗口)*
- **`self.equity` 的口径**:`== cash + shares * self.data['Close'].iloc[-1]`,即 ADR-003 的 `equity`(收盘基准)。它**不是** ADR-014 的「可投资产」(开盘基准)。`init()` 时 `self.data` 为空,故 `equity == cash`
- 策略类**可任意命名**,但每个文件恰好一个 `Strategy` 子类(关闭 OQ-02)
- 加载方式:`importlib.util.spec_from_file_location` + `module_from_spec` + `spec.loader.exec_module`。**禁止 `exec`/`eval`/`compile`**(关闭 OQ-01,并使 T9 的 grep 门在构造上可满足)
- 零个或 2+ 个 `Strategy` 子类 → `ValueError` 并指明文件名

### ADR-037 · 输出文件名与 `summary.json`(构建期不得变更)

`<stem>` ≔ 策略文件名去 `.py`。两个 `--strategy` 的 stem 相同 → `ValueError`。

```
out/<YYYYMMDD-HHMMSS[-N]>/
  <stem>_equity.png           # 单策略净值曲线(ADR-013)
  <stem>_trades.csv           # 交易清单
  summary.txt                 # 屏幕汇总的副本
  summary.json                # 结构化汇总(机器可读,T10 对账用)
  run.json                    # RunRequest + 各策略 summary(ADR-032 的历史列表读它)
  comparison.png              # 仅多策略时产生(ADR-018)
```

T8 的「三类输出齐全」≔ `<stem>_equity.png`、`<stem>_trades.csv`、`summary.txt` 三者都存在。

**`Fill` 值对象的属性名(构建期不得变更 —— T4 的门读 `fill.shares`/`fill.price`/`fill.fee`/`fills[0].date`/`fills[1].side`)**:

```python
Fill: date: str  side: str  shares: int  price: float  fee: float
```

恰好这五个。`cash_after` / `shares_after` **不是** `Fill` 的字段 —— 它们由**聚合根**在产生每个 `Fill` 时同时记录,作为 `RunResult.trades_ledger` 的并行序列(ADR-044)。T7 只是把两者拼成 CSV 行。*(此前写作「由 T7 在写 CSV 时根据账本状态计算」是错的:ADR-044 禁止 T7 import `account`,它没有账本可读)*

**`trades.csv` 表头**:见 `contracts.yaml` 的 `trades_csv_header`(七列,顺序固定)。

`side ∈ {BUY, SELL}`;`date` 为 `YYYY-MM-DD`;`shares` **恒为正**;`price`/`fee`/`cash_after` 保留 2 位小数;`shares_after` 为整数。

**`summary.json`**(每次运行**一个**,含全部策略 —— 一个运行目录可以有 N 条策略):

```jsonc
{ "run_id": "20261003-172400",
  "results": [
    { "strategy": "buy_and_hold",
      "summary": { /* ADR-038 的 summary 块,逐字段相同 */ } }
    // 多策略时此数组有多项
  ],
  "assumptions": [ /* contracts.yaml 的 frozen_text.assumption_adjusted
                      与 frozen_text.assumption_market_order,逐字 */ ] }
```

顶层恰好三个键:`run_id` / `results` / `assumptions`。

*(此前本节把 `summary.json` 写成单策略形状(顶层一个 `strategy` 键),而同一目录下 `<stem>_equity.png` / `<stem>_trades.csv` 是**按策略**的、ADR-018 又允许一次跑 N 条 —— 该 schema 无法表示多策略运行,而 T10 的主对账门正是读这个文件。且当时的散文说「summary 块 + 顶层 assumptions」(两部分)与示例的三个顶层键不符。现统一为 `results[]` 数组形状,与 `run.json`、与 ADR-038 的响应体同构。)*

**`run.json`**(**T8** 的 `backtest.write_run_json` 产出,T11 消费 —— 跨阶段锁定边界,字段名必须在此钉死):

```jsonc
{ "run_id": "20261003-172400",
  "request": { "strategies": ["buy_and_hold.py"], "data_file": "aapl.csv",
               "cash": 100000.0, "fee": 0.0 },
  "results": [ { "strategy": "buy_and_hold",
                 "summary": { /* 同上 */ },
                 "png_path": "out/20261003-172400/buy_and_hold_equity.png" } ],
  "assumptions": ["...", "..."],
  "comparison_png_path": null }
```

此 schema **逐字段等于** `GET /api/runs/{id}` 的响应体(ADR-038)—— T11 直接返回该文件内容即可,无需转换。

### ADR-038 · API 契约(字段名在此钉死,T10 不得自行发明)

```jsonc
// POST /api/run  请求
{ "strategies": ["buy_and_hold.py"], "data_file": "aapl.csv",
  "cash": 100000, "fee": 0.0 }

// POST /api/run  响应 200
{ "run_id": "20261003-172400",
  "results": [
    { "strategy": "buy_and_hold",
      "equity": [ { "date": "2015-01-02", "equity": 100000.0 } ],
      // 下面这笔可复算:floor(100000/27.07)=3694,3694×27.07=99996.58,余 3.42
      "trades": [ { "date": "2015-01-05", "side": "BUY", "shares": 3694,
                    "price": 27.07, "fee": 0.0,
                    "cash_after": 3.42, "shares_after": 3694 } ],
      "summary": { "start": "2015-01-02", "end": "2024-12-31",
                   "bars": 2516, "initial_cash": 100000.0,
                   "final_equity": 916062.13, "total_return_pct": 816.06,
                   "trade_count": 1,
                   "max_gap_pct": -8.42, "max_gap_date": "2020-03-16",
                   "mean_abs_gap_pct": 0.31 },
      "png_path": "out/20261003-172400/buy_and_hold_equity.png" }
  ],
  "comparison_png_path": null,
  "assumptions": [ /* frozen_text.assumption_adjusted,
                       frozen_text.assumption_market_order —— 逐字 */ ] }

// GET /api/strategies → ["buy_and_hold.py", "ma_cross.py"]
// GET /api/data       → ["aapl.csv"]
// GET /api/runs       → [ { "run_id": "...", "strategies": ["..."],
//                           "data_file": "aapl.csv", "final_equity": 916062.13 } ]  // 最新优先
//                        final_equity 取 results[0](--strategy 的第一条)的值;多策略时详情页看全部
// GET /api/runs/{id}  → 逐字段等于该归档的 run.json(ADR-037),含 request / results / assumptions
//                        / comparison_png_path
```

**`summary` 块的字段 ↔ 中文术语对照(T6 渲染屏幕文本、T10 逐字段对账,都照此表,不得各自猜)**:

| 字段 | 中文术语 | 类型 / 格式 |
|---|---|---|
| `start` / `end` | **区间**(首末交易日) | `YYYY-MM-DD` |
| `bars` | **交易日数**(ADR-042) | `int` |
| `initial_cash` | 初始资金 | `float` |
| `final_equity` | 期末资产(== `equity[-1]`) | `float` |
| `total_return_pct` | 总收益 | `float`,**已乘 100** |
| `trade_count` | 交易次数(== `len(fills)`,ADR-042) | `int` |
| `max_gap_pct` / `max_gap_date` | 最大跳空 / 其日期 | `float`(带符号)/ `YYYY-MM-DD`;无跳空时均为 `null` |
| `mean_abs_gap_pct` | 平均绝对跳空 | `float`;无跳空时 `null` |

**所有金融量都是已完成全部算术的标量**:`total_return_pct: 816.06` 已乘 100,前端**只追加 `%`**。`pydantic` 模型在 `api.py` 中是单一真相来源,`frontend/src/types.ts` 逐字段镜像它。

**精度(必须钉死,否则门不可满足)**:`final_equity`、`initial_cash`、`total_return_pct`、`max_gap_pct`、`mean_abs_gap_pct` 在进入 `summary` 前一律 `round(x, 2)`。

*(不钉死就会出现这种情况:由真实价格算出的 `-8%` 跳空浮点值是 `-7.9999999999999964`,`== -8.00` 为假;而 `16/3` 的平均绝对跳空是 `5.333…`,**不存在任何** 2 位小数值能让 `== 5.33` 成立。于是返回全精度浮点的正确实现会被门红掉,而取整的实现又无 ADR 授权。T10 的「金额按 2 位小数定点比较」此前也是在假设这条规则存在。)*

`run_id` 格式见 `contracts.yaml` 的 `patterns.run_id`,用于路径前必须校验(防穿越)。

### ADR-039 · CLI 参数表(构建期不得变更)

| flag | 类型 | 必填 | 默认 | 校验 |
|---|---|---|---|---|
| `--data` | path | ✅ | — | 文件须存在 |
| `--strategy` | path,可重复 | ✅ | — | 文件须存在;stem 不得重复 |
| `--cash` | float | ❌ | `100000.0` | `> 0`,否则 `ValueError` |
| `--fee` | float | ❌ | `0.0` | `0 <= fee < 0.1`,否则 `ValueError` |
| `--out` | path | ❌ | `out/` | 目录可不存在(自动创建) |

### ADR-040 · 领域错误类型与退出码

`nullhypothesis/errors.py` 定义:

```python
class DataValidationError(ValueError):   # ADR-019
    file: str; row: int | None; column: str | None
class StrategyError(RuntimeError):       # ADR-022
    strategy: str; bar_index: int; date: str   # __cause__ 携带原始异常
```

退出码:`0` 成功 · `2` `DataValidationError` · `3` `StrategyError` · `4` CLI 参数非法。

ADR-031 的 `code` 映射:`DataValidationError → DATA_VALIDATION`、`StrategyError → STRATEGY_ERROR`、pydantic 校验失败 → `INVALID_REQUEST`、文件名不存在 → `NOT_FOUND`(仅阶段二)。

### ADR-041 · 每根 K 线多次下单:末次胜出

策略在同一个 `next()` 内多次调用 `target()`/`order()` → **最后一次调用胜出**,先前调用被丢弃,队列中始终只有一个订单。混用 `target()` 与 `order()` 同理,按调用顺序末次胜出。

**理由**:这是 I4(队列长度 ≤ 1)的真正来源。此前 I4 被标为「ADR-008 推论」,那是错的 —— ADR-008 只说零差额不生成订单,从未规定多次调用。

### ADR-042 · 交易日与交易次数的定义

- **交易日** ≔ 输入 CSV 经 ADR-019 校验(去重、排序)后的**一行**。框架**没有交易日历**,不知道哪些自然日缺失,也不检测缺口 —— 缺失的交易日对框架不存在。这与 ADR-001 同属契约责任。
- **交易日数** ≔ 校验后的行数。
- **交易次数** ≔ `len(fills)`,即实际成交笔数。买入与卖出各计一笔(非往返计数)。

### ADR-043 · 手续费双向收取

```
买入:cash -= shares * price + fee
卖出:cash += shares * price - fee
fee  = shares * price * rate      (rate >= 0,ADR-039)
```

**买卖双向均收费。** 此前 ADR-007 只给了公式,未说方向,导致 T3 的门「现金扣减 == 成交额 + 手续费」对卖出字面错误。

### ADR-044 · 聚合根只对外暴露不可变快照:`RunResult`(按策略)与 `RunReport`(按运行)

**两层,职责分明。** 此前本 ADR 只定义了按策略的 `RunResult`,但 `summary.json` / `run.json` / `summary.txt` / `comparison.png` 全是**按运行**的 —— 导致 T7 的两条门互相矛盾(一边要求产出含 `request`/`assumptions` 的 `run.json`,一边要求只接受 `RunResult`,而后者没有这些字段)。现补齐外层:

```python
# nullhypothesis/engine.py
@dataclass(frozen=True)
class RunResult:          # 按【策略】—— 与 ADR-038 的 results[i] 同构
    strategy: str
    equity: list[EquityPoint]
    trades: list[Fill]
    trades_ledger: list[tuple[float, int]]   # 与 trades 等长并行:每笔成交后的 (cash, shares)
    summary: Summary      # 含 GapStatistics 三个字段
    # 注意:没有 png_path —— 见下

@dataclass(frozen=True)
class RunReport:          # 按【运行】—— 与 summary.json / run.json 同构
    run_id: str
    request: RunRequest   # strategies / data_file / cash / fee
    results: list[RunResult]
    assumptions: list[str]
```

**`Backtest.run()` 产出 `list[RunResult]`;`RunReport` 由 CLI 层(`backtest.py`)在创建归档目录后组装** —— 因为 `run_id` 只有在 ADR-024 的独占 `mkdir` 成功后才确定,聚合根无从知道。

**`png_path` / `comparison_png_path` 不是 `RunResult` 的字段**。`plot.write()` 写完文件后**返回**各路径,由 CLI 层的 `write_run_json` 填入 `run.json`、由 T10 填入 ADR-038 的响应体。*(此前把 `png_path` 放进 `RunResult` 是错的:它的值含 `run_id`,而 `run_id` 由 **CLI 层的 `create_archive_dir`** 产生,聚合根不拥有输出目录)*

**`trades[].cash_after` / `shares_after` 由聚合根填入 `Fill` 之外的 CSV 行**:T7 不得 import `account`,故它没有账本状态。正确做法是**聚合根在产生每个 `Fill` 时同时记录成交后的 `cash`/`shares`**,作为 `RunResult.trades` 的并行序列 `trades_ledger: list[tuple[float, int]]`。*(ADR-037 此前写「由 T7 在写 CSV 时根据账本状态计算」—— T7 无账本可读,那句是错的)*

T6/T7 **只消费 `RunReport`**(其 `results[i]` 为纯 `RunResult`),不得遍历内部 `Bar` 列表或 `Account`。

**`GapStatistics` 由聚合根(`engine.py`,T5)计算**并放入 `RunResult.summary` —— 它需要 `Bar` 列表,而只有聚合根能读。T6 只负责**渲染**这三个已算好的标量(以及 `null` 时显示 `N/A`)。*(此前 ADR-021 挂在 T6 名下,但 T6 拿到的是 `RunReport`,里面跳空值已经存在 —— 于是 T6 的门要么是在对 T5 随手放进去的值做同义反复,要么逼 T6 去遍历 bars 违反本 ADR)*

**`Summary` 与 `RunRequest` 的字段(T6/T7 的门要手工构造它们,故必须钉死)**:

```python
@dataclass(frozen=True)
class Summary:            # 与 ADR-038 的 summary 块逐字段对应
    start: str; end: str; bars: int
    initial_cash: float; final_equity: float
    total_return_pct: float; trade_count: int
    max_gap_pct: float | None; max_gap_date: str | None
    mean_abs_gap_pct: float | None

@dataclass(frozen=True)
class RunRequest:
    strategies: list[str]; data_file: str; cash: float; fee: float

@dataclass(frozen=True)
class EquityPoint:        # 四个字段。T5 的 I3 逐日对账读 cash/shares,必须钉死
    date: str; cash: float; shares: int; equity: float
```

**`EquityPoint` 的 JSON 投影**:ADR-038 的 `equity[]` 只序列化 `date` 与 `equity` 两个键 —— `cash`/`shares` 是内部对账所需、不出 API。*(此前 `EquityPoint` 的字段只出现在 §1 的表里,而 §1 不在 §7 第 8 条的名字契约清单中。于是 T5 的 agent 可以把字段叫 `c`/`s` 并通过自己的门,而 T10 的 `equity` 逐点对账与 T13 的 I9 读到的是别的东西 —— 这与 `equity_at` 是同一个缺陷)*

**理由**:此前 §1 只规定了写的边界(「外部只能通过它推进时间」),对读没有规定,T6 最自然的实现就是直接读 Bar 列表 —— 越过聚合根。

## 3 · OQ(未解决,构建期不得擅自决定)

| # | 问题 | 构建期约定 |
|---|---|---|
| ~~OQ-01~~ | ~~策略文件如何被加载~~ | **已关闭 → ADR-036**:`importlib.util.spec_from_file_location`。禁止 `exec`/`eval`/`compile`。*(原为「由 T2 自行决定」—— 但 `exec` 会使 traceback 显示 `<string>` 而非文件行号,违反 ADR-022;且 T9 的门硬断言后端无 `exec(`,T2 选错会让一个阶段二的门在阶段一已锁定后失败)* |
| ~~OQ-02~~ | ~~策略类名约定~~ | **已关闭 → ADR-036**:类可任意命名,每文件恰好一个 `Strategy` 子类。*(原为「由 T2 决定」—— 但「固定名 `Strategy`」会与导入的基类同名冲突,且阶段二的 `GET /api/strategies` 返回文件名,隐含「一文件一策略」。T8/T12/T13 都消费此答案)* |
| **OQ-03** | 是否支持 `Date` 列为非日期(纯序号) | **不支持**,v0 要求可解析为日期。超出范围勿实现 |
| **OQ-04** | `comparison.png` 的 y 轴用绝对金额还是归一化净值 | 用**绝对金额**(与单策略图一致);归一化留待 v1 |
| **OQ-05** | 策略能否访问 `Volume` | 可以(数据原样传入),但 ADR-001 不保证其复权一致性。文档须注明 |
| **OQ-06** | `limit` / `stop` 订单 | **v0 不做**(2026-10-03 讨论后决定)。构建期**不得实现**,`order()` 不接受 `limit`/`stop` 参数。<br>已分析的五处语义裁决,留给 v1:<br>① 触发判断需读 `High`/`Low` —— 会使 ADR-001 的契约从"声明四列"变为"真的读取四列"<br>② 跳空穿越限价时成交价为 `min(Open, limit)`,即**可能以远好于指定价成交**,会系统性高估收益<br>③ 同一根 K 线内止损与止盈同时触及时哪个先成交,**从日线数据根本不可知**,只能任意裁决(参考实现选"止损优先");任意裁决易被误读为计算结果<br>④ 止损触发后按市价成交,故**不保证止在止损价**(跳空跌破时成交在开盘价)—— 这是"止损让我更安全"这一直觉的最大陷阱<br>⑤ 未触发挂单的寿命:当日作废保住 I4;GTC 会使多挂单共存,`Order` 从值对象变回实体,领域模型需改<br>**若 v1 要做**:建议只做 `limit` 买 + `stop` 卖(二者分别在无仓/有仓时生效,永不同时触发,从而完全避开 ③) |

---

## 4 · 任务 DAG

**全部任务共享 ADR-035(模块布局)、ADR-040(错误类型)、ADR-042(交易日/交易次数定义)、ADR-044(聚合根只暴露 `RunResult`)—— 不逐行重复。**

| 任务 | 标题 | 依赖 | ADR |
|---|---|---|---|
| **T1** | 数据加载与校验 | — | 001, **004**, 019, 023 |
| **T2** | 策略加载与协议 | — | 006, 014, 015, 022, **036**, **041** |
| **T3** | 账本原语与不变式 | T1 | 003, 007, 009, 010, **043** |
| **T4** | 订单队列与成交 | T2, T3 | 002, 005, 008, **012**, 014, 016, 020, **041** |
| **T5** | 主循环 + 跳空统计 + `Summary` | T1, T4 | 002, **003**, **021**, **038**, **042**, **044**, I5, I6 |
| **T6** | 汇总渲染 + `summary.txt` + `summary.json` | T5 | 018, 021, **037**, **038** |
| **T7** | 曲线 / 交易清单 | T5 | 013, 018, **037** |
| **T8** | CLI + 归档目录创建 + 两条样例策略 | T6, T7 | 017, 018, **022**, **024**, **039**, **040**, **044** |

**变更说明**:
- ADR-012(floor 取整)与手续费中的「成交额」原在 T3,但 `目标股数 = floor(w × 可投资产 / Open)` 属 ADR-014/016(T4),而 T3 只依赖 T1、无法产生成交价 —— **这是顺序错误**。现 ADR-012 移至 T4;T3 只做 `Account.apply(Fill)` 等原语,`Fill` 由调用方构造。
- ADR-004(不用 Adj Close)原未分配给任何任务,但它是 T1 的活约束(loader 必须不读该列)。
- ADR-022 原只在 T2,但其「不写输出文件 / 非零退出」是 T8 的职责。

---

## 4.5 · 产出物归属矩阵(每个文件/字段恰好一个产出者)

本矩阵存在的理由:前六轮审查中**每一轮**都出现同一类缺陷 —— **某个产出物的「职责」被挪动,但测它的「门」没跟着挪**,或**拥有者的函数签名缺少干活所需的参数**。矩阵把归属变成可核对的表,而不是散落在 ADR 正文里靠记忆对齐。

| 产出物 | 产出者(代码) | 门在哪个任务 | 备注 |
|---|---|---|---|
| **`errors.py` 两个错误类 + `__init__.py`** | **T1(一次写全)** | T1 | T2/T8 只 import,**不新建** —— T1/T2 并行,否则两个 agent 各写一半,后写者静默覆盖 |
| 价格 DataFrame | `data.load_csv` (T1) | T1 | — |
| `Strategy` 子类加载 | `strategy.load_strategy` (T2) | T2 | — |
| `Account` 账本原语 | `account.Account` (T3) | T3 | 逐笔,无「交易日」概念 |
| 订单队列 / 成交 | `engine.Backtest` (T4) | T4 | — |
| `equity[]` / `trades[]` / `trades_ledger[]` | `engine.run` (T5) | T5 | — |
| **`Summary`(含跳空统计)** | **`engine.run` (T5)** | **T5** | 只有聚合根能读 `Bar` 列表 |
| 屏幕汇总文本 | `report.render` (T6) | T6 | 仅渲染,不计算;格式见 ADR-021 格式表 |
| **屏幕对比表**(多策略) | `report.render` (T6) | **T6**(T8 再做端到端确认) | ADR-018;不排名不评判 |
| **`strategies/buy_and_hold.py` / `ma_cross.py`** | **T8** | T8 | **文件名钉死** —— §12 阶段二人工门按名引用 |
| `summary.txt` / `summary.json` | `report.render` (T6) | T6 | 需 `out_dir` 参数 |
| `<stem>_equity.png` / `comparison.png` / `<stem>_trades.csv` | `plot.write` (T7) | T7 | 需 `out_dir`;**不创建目录** |
| **归档目录 + `run_id`** | **`backtest.create_archive_dir` (T8)** | **T8** | 独占 `mkdir` + `-N` 重试 |
| **`RunReport` 组装** | **`backtest.build_report` (T8)** | T8 | `run_id` 只有建目录后才确定 |
| **`run.json`** | **`backtest.write_run_json` (T8)** | **T8** | 按运行的元数据,非绘图产物 |
| CLI 参数校验 / stem 唯一性 | `backtest.py` (T8) | T8 | `run()` 不做 |
| 资源列举端点 + 校验器 + 静态挂载 | `api.py` (T9) | T9 | 挂载必须最后注册 |
| `POST /api/run` | `api.py` (T10) | T10 | — |
| 契约 fixture | `tests/test_contract_fixture.py` (T10) | 契约门 | T10 唯一允许写出后端之外 |
| 历史端点 | `api.py` (T11) | T11 | 读 `run.json` |
| 前端状态 + 表单 | `App.tsx` / `RunForm.tsx` (T12) | T12 | — |
| 交互图 / 汇总面板 / 对比表 | T13 的三个组件 | T13 | 只接 props,不 fetch |
| 历史 UI / 错误卡片 | T14 的两个组件 | T14 | — |

> **这四条规则已被机械化。** 见 `contracts.yaml`(每个字面量的单一真相来源)与 `tests/test_contracts.py`(校验它自洽:算术可重算、格式可复现、引用不悬空、进程边界不矛盾、打桩接缝都已钉死)。
>
> **为什么必须机械化**:前八轮审查 29 个 BLOCKER,其中**第 6、7 轮的修复各自引入了下一轮的头号 BLOCKER** —— 机制相同:改了一处钉死的值,没重读显示同一个值的兄弟条款(同一个格式决定在文档里有五处独立复述)。**推断**(非实测 —— 按 §7 第 3 条标注):文档里**有可执行投影**的部分(ADR-035 的符号清单)在后续各轮未再回归,而只靠人读的部分每轮都出缺陷。*(第 9 轮审查指出我原先写作「ADR-035 ↔ `tests/test_layout.py` 七轮零回归」—— 但 `tests/test_layout.py` **尚未存在**,那是把推断当成了实测结果,正是 §7 第 3 条禁止的。)* 结论:**只靠人读维持的不变式会持续回归,规模不是原因。** `test_contracts.py` 写完后立刻抓出了两个本轮人工审查漏掉的未钉死接缝(`api.execute_run`、`Backtest.resolve_queue`)。
>
> **构建期纪律**:改 `contracts.yaml` 必须跑 `python3 -m pytest tests/test_contracts.py`;DESIGN.md 的条款**引用 key,不复述值**。

**自检规则一(归属)**:若某任务的门断言一个产出物,而该产出物在本表中归另一任务 —— **那是缺陷,不是分工**。要么把门移到拥有者,要么把归属改掉;两者都要在本表与 ADR-035 中同步更新。

**自检规则二(接缝)**:若某条门要 `monkeypatch` 一个函数,或读一个不在发布契约里的内部序列/属性,**该符号必须在 ADR-035 中被钉死为模块级公开名**。否则实现者内联它,门就结构上无法执行 —— 而门自己写的回退方案往往会红掉正确实现。*(本表第 6 轮加入时只覆盖了「文件/字段」,漏了这两类;第 7 轮的 `_timestamp`(接缝)与 `compute_gaps`(内部序列)就是这么漏掉的)*

**自检规则三(格式)**:若某条门断言一个**格式化后的字符串**,该格式必须在 `contracts.yaml` 的 `formats` / `field_formats` 中钉死,**且该字段只能有一处格式定义**。*(第 7 轮发现 T6 的门断言 `+25.00%` 而阶段一没有格式规定;第 8 轮发现我补的格式表又与 ADR-021 自己的输出样例矛盾 —— 同一个值在文档里有五处复述。现在 `field_formats` 是唯一来源,`test_contracts.py` 校验「绝对值百分比不得带 + 号」)*

**自检规则四(可执行性 —— 第 8 轮发现,前三条都漏了这一类)**:每条门必须能回答三个问题:**在哪个进程里跑**、**fixture 由谁造**、**被打桩的符号在该进程里如何绑定**。
- 若 fixture 的生产者不在本任务 DAG 的传递闭包内 → 缺陷
- 若需要 `monkeypatch` 却在 `subprocess` 边界之外 → **结构上无法执行**
- 若被打桩的符号是内联表达式而非模块级命名符号 → 无法执行

*(前三条规则都在审查「断言」,没有一条审查「到达断言所需的布置」。`contracts.yaml` 的 `gate_execution` 块把这三个问题变成了可校验的数据,`test_contracts.py` 断言 `in_process_required` 与 `subprocess_required` 不相交、每个打桩接缝都已钉死、每个 fixture 生产者都是具名任务。)*

## 5 · 验收门(每任务)

全局命令:

```
TEST_CMD     = python3 -m pytest -q
LINT_CMD     = python3 -m compileall -q .
CONTRACT_CMD = python3 -m pytest tests/test_contracts.py -q
```

**`CONTRACT_CMD` 在每个任务开工前与收工后各跑一次**,且它**不依赖任何产品代码** —— 它只校验 `contracts.yaml` 自洽。任何对钉死值的改动若破坏自洽,在写第一行实现代码之前就会红。

> **`LINT_CMD` 用 `compileall` 而非 `pyflakes`**:`pyflakes` **未安装**,而 `pip install` 在 `.claude/settings.json` 中被明确拒绝(有意如此)。`compileall` 是标准库,经已放行的 `python3 *` 执行,无需安装。*(原文写 `pyflakes`,回退方案写作「`py_compile` 全文件」—— 那不是可运行命令:「全文件」不是 glob,且 `py_compile` 对目录会失败。更坏的是它会诱使 agent 去跑被拒绝的 `pip install`,在无人值守时停在权限弹窗。)*

> **阶段一无 `REAL_STACK_GATE`**:无 DB、无外部服务,真实栈 == 真实 CSV 文件 + 真实 matplotlib 渲染,已包含在各门中。**阶段二不同** —— 见 §12 的真实栈门。各任务均须声明 `blindSpots`。

### T1 · 数据加载与校验

- `TEST_CMD` 全绿
- 每条 ADR-019 的失败情形各有一个测试,断言**异常类型 + 消息含行号/列名**:缺列、NaN、价格 `<= 0`、重复日期、空文件、仅表头
- 日期乱序 → 断言已排序、发出提示(不抛错)、**且 `df.index` 为 `0..n-1` 连续**
- 单行 CSV → 断言加载成功(ADR-023)
- **ADR-004**:CSV 含 `Adj Close` 列时 → 断言该列**不被读取**(不影响任何计算),引擎只用 OHLC
- 所有异常断言类型为 `DataValidationError`(ADR-040)且 `.file`/`.row`/`.column` 字段正确
- **真实数据状态**:测试须用**真实写到磁盘的 CSV 文件**,非内存 DataFrame
- `blindSpots` 须声明:未测试的 CSV 变体(编码、BOM、千分位分隔符、CRLF)

### T2 · 策略加载与协议

- `TEST_CMD` 全绿
- `target(weight=w)` 与 `order(shares=n)` 各有测试
- **非法值逐个成立**:`weight` 为 `NaN` / `None` / `-0.1` / `1.5` / 字符串 → 各自报错,消息含交易日序号与日期(ADR-022)
- **ADR-015 的边界与文案**:`weight=1.5` → 断言消息含 `frozen_text.leverage_not_implemented`;`weight=1.0` 与 `weight=0.0` → 断言**不报错**(闭区间)。*(`if weight >= 1: raise` 的 off-by-one 能通过所有其他 T2 条款,却会让 T4 的 ADR-016 fixture(用 `weight=1.0`)失败 —— 而那个失败会被归因到错误的 ADR)*
- **ADR-036 的属性契约**:断言 `self.cash` / `self.shares` / `self.equity` 在 `next()` 内可读且数值正确(参数与期望值见 `fixtures.t2_strategy_equity`);断言写入三者报错(只读)
- **ADR-036 的调用契约**:断言 `init()` 恰调用 **1** 次且在首次 `next()` 之前;断言 `init()` 内的 `self.data` 为**零行** DataFrame 且 `equity == cash`;断言 `init()` 内调 `target()`/`order()` → `ValueError`;断言 `next()` 以**零参数**调用(定义 `def next(self, bar)` 的策略文件 → 报错而非被当作合法)
- **ADR-036 的一文件一子类**:**零个**与**两个** `Strategy` 子类的文件 → 各断言 `ValueError` 且消息含文件名。*(静默取第一个的 loader 能通过其余全部条款,并会污染阶段二的 `GET /api/strategies`)*
- 策略抛异常 → 断言错误消息含**交易日序号 + 日期 + 原始异常类型 + 源文件行号**
- 断言 **I5**:策略收到的数据长度 `== T+1`,且不含第 T+1 根 K 线
- `blindSpots`

### T3 · 账本与不变式

> T3 实现 `Account.apply(fill)` 等账本原语;`Fill` 由**调用方构造**(成交价由 T4 决定)。故本门不含任何「成交价从哪来」的断言。

- `TEST_CMD` 全绿
- **影子账本验证(关键门,不得写成同义反复)—— 逐笔,非逐日**:测试自建影子账本,对一串**调用方构造的 `Fill`** 逐笔 `apply`,影子侧按 ADR-043 独立累加 `cash`/`shares`。每笔 apply 后断言 `shadow_cash == account.cash`、`shadow_shares == account.shares`(容差 0.01)
  - **影子侧的隔离要求(否则同义反复只破了一半)**:影子账本的算术必须**全部写在测试文件内** —— 用字面写死的 `rate` 自行算 `fee = shares * price * rate`,**不得读 `fill.fee`**,**不得调用 `Account` 的任何方法来算影子值**,**不得 import 被测模块的任何记账辅助函数**(含模块级私有函数)。额外断言 `fill.fee` 与影子自算的 fee 逐笔相等
    *(T3 当然要 import `Account` 本身 —— 它就是被测对象。禁的是「用被测代码算出期望值」:那样错误的费率会在两侧同时出现而对消)*
  - **必须用非零费率 `rate = 0.001`**。*(`rate=0` 时手续费方向错误、漏算、双算全部不可观测;而「影子侧读 `fill.fee`」会让错误的 rate 在两侧同时出现从而对消)*
- **估值函数单独测**:给一组**互异**的 Close 常量,断言 `account.equity_at(close) == shadow_cash + shadow_shares * close`
  - *(T3 只拥有 `account.py`、只依赖 T1,**没有** bar 列表、没有「交易日」概念、不产生 `equity[]` 序列 —— 那是 T5 的东西。此门早先写作「断言每个交易日 … `Bar[i].Close` … `equity[i]`」,那要求 T3 导入尚不存在的 `engine.py`,或在 Account 测试里自己重造一遍主循环 —— 正是变更说明声称要消除的顺序错误。**逐日形式只属于 T5**)*
  - *(影子账本的必要性:equity 的实现恰是 `cash + shares*Close`,直接断言会退化为 `x == x`,在 cash/shares 本身错误时也通过)*
- **ADR-043 手续费双向各一测**:买入断言 `cash` 减少 `== shares*price + fee`;**卖出断言 `cash` 增加 `== shares*price - fee`**(符号错误是最易犯且原门字面漏掉的)
- 断言影子侧**自算**的 `sum(fee)` 与现金总变动、成交额总和三者闭合,钉死手续费既未漏算也未双算。*(用 `fill.fee` 做这个闭合是在同一组数上做恒等式,错误的 rate 照样通过)*
- **I1**:构造使 `cash` 将变负的 `Fill` → 断言报错
- **I2**:`Account.apply` 一个使 `shares` 变负的 `Fill` → 断言报错(ADR-009)
- ADR-009:超额卖出 / 无持仓卖出 → 各自报错
- `blindSpots`

### T4 · 订单队列与成交

> **进程内**(`contracts.yaml` 的 `gate_execution.in_process_required`):队列采样与 `push_count` 都要对 `Backtest.enqueue` / `resolve_queue` 打桩,`monkeypatch` 跨不过 `subprocess` 边界。

> 本门的每一条都必须**钉死具体数值**。原门多处写作「断言按 X 重算」—— 那是散文:执行它必须先算出期望值,而算期望值只能复用被测实现的同一公式,结果是同义反复。

- `TEST_CMD` 全绿
- **I7 四价互异 fixture(关键门)**:构造 4 根 K 线,四个候选价**量级可区分**(如 bar1 `Open=50,Close=100`;bar2 `Open=200,Close=400`)。策略**仅在第 1 根**(index 0)下 `order(shares=1)`。断言 `len(fills)==1`、`fills[0].date == bars[1].date`、`fills[0].price == 200`。四个候选值(`bars[0].Open=50`、`bars[0].Close=100`、`bars[1].Close=400`、正确值 `200`)**全部互异**,故任一种错位必然失败
- **决策侧配对**:逐根断言 `strategy.next()` 被调用时 `len(self.data) == bar_index + 1` —— 钉死「策略在第 i 根收盘后被调用」与「成交在第 i+1 根开盘」的配对关系。*(仅断言切片长度不够:引擎可以切片长度正确却把策略调早一根)*
- **ADR-012 floor 取整钉死(区分 floor / round / ceil)**:
  - 参数与期望值见 `contracts.yaml` 的 `fixtures.adr012_floor_vs_ceil`:断言 `fill.shares == expect_shares`,**并显式断言 `!= reject_shares`**(`ceil` 的结果);断言成交后 `cash == expect_cash_after` 且 `cash < one_share_price`(证明未被 clamp,也未留下够买整股的余钱)
  - 参数见 `fixtures.adr012_floor_vs_round`:目标 `floor` 后为 `expect_shares`(0)→ 断言 **`push_count == expect_push_count`、无警告**(ADR-012 → ADR-008 的交接),**并显式断言 `shares` 未变**。*(同一组数下 `round` 与 `ceil` 均为 `reject_round`,故此 fixture 同时区分三者)*
  - *(原门只写「余额不足一股 → 股数为 0」,未钉死数值 —— 作者会选 `floor(0.5)` 这种 `round` 也给 0 的 fixture,区分不出取整方式)*
- **ADR-008 三条分解**(原门一条可被两种错误实现混过):
  - 对队列 `push` 打桩计数:同一 `weight` 连续 10 日 → 断言 **`push_count == 1`**(非仅「成交次数 == 1」)。*(拦住「每天生成订单、成交时因钱不够静默 no-op」的实现 —— 它成交次数也是 1)*
  - `weight=0.5`(钱有余)连续 10 日 → 断言 `push_count == 1`、成交 1 次、警告 0
  - **价格变动使同一 `weight` 的目标股数改变** → 断言**产生第二笔成交**。*(拦住反向错误:「weight 未变就永远跳过」的缓存意图 bug —— 它会让仓位永久冻结,且能通过原门的每一条)*
- **ADR-016 数值钉死**:`cash=10000`,bar_T `Close=100`,bar_T+1 `Open=200`,T 日 `target(weight=1.0)` → 断言 `fill.shares == 50`(按 T+1 开盘价),**并显式断言 `!= 100`**(按 T 日收盘价锁定的错误实现)
- **ADR-014 分母钉死(三向可分,全部数值写死)**:`cash=10000`,`fee=0`;bar1 `Open=100`,bar2 `Open=100`。T0 日 `target(weight=0.5)` → bar1 成交 **50 股**,现金剩 **5000**。T1 日 **`target(weight=0.8)`**(**不可用 1.0**,见下)。

  | 实现 | 第二次算法 | 终局持仓 |
  |---|---|---|
  | **正确**:分母 = 可投资产,算目标再减持仓 | `floor(0.8×10000/100) − 50 = +30` | **80 股** |
  | 错误 ①:分母 = 剩余现金,算目标再减持仓 | `floor(0.8×5000/100) − 50 = −10` | **40 股** |
  | 错误 ②:分母 = 剩余现金,结果**当作差额**(「拿剩余现金的 80% 去买」) | `floor(0.8×5000/100) = +40` | **90 股** |

  - 断言**终局持仓** `shares == expect_final_shares`,并显式断言 `!= reject_target_from_cash` 与 `!= reject_spend_cash`。三者判然可分(`contracts.yaml` 的 `fixtures.adr014_denominator_three_way`,由 `test_contracts.py` 验算)
  - **第二个 weight 必须 ≠ 1.0**:`weight=1.0` 时正确实现恰好花光现金,而「花光剩余现金」的错误 ② 给出**相同**的终局持仓 —— 此 fixture 早先用 `1.0`,放过了错误 ②,而那正是参考实现的语义、最可能被照抄的一种
  - *(两个 `Open` 必须写死,否则期望股数无从计算;必须先持仓再 re-target,从空仓起三种算法无法分辨)*
- *(「成交后 `cash < 一股价格`」的 clamp 检查已并入上方 ADR-012 的 `Open=300` fixture。它作为独立条款曾是错的:`weight < 1` 时正确实现本就会留下大量现金,会红掉正确实现;`weight = 1.0` 时又恒真而测不出东西 —— 必须绑定到钉死的 fixture 才有意义)*
- **ADR-020**:构造 T+1 跳空腰斩 → 断言订单仍**无条件执行**(不跳过、不截断)
- **ADR-041 末次胜出,三个 case**:
  - 同一 `next()` 内调 `target(0.3)` 再调 `target(0.8)` → 断言只成交一笔且按 `0.8`
  - 调 `target(0.5)` 再调 `order(100)` → 断言按 `order(100)`
  - **末次差额为 0 的 case(关键)**:`cash=10000`、`Open=100` 恒定,先 `target(0.5)` 使持仓 50 股、现金 5000(故当前 weight 恰为 `0.5`)。次日同一 `next()` 内先调 `order(100)` 再调 `target(0.5)` → 断言 **`push_count == 0`、`len(fills)` 不增加**。
    *(末次胜出后 ADR-008 生效,先前的 `order(100)` 必须被**丢弃**而非回退执行。缺此 case 会放过一种实现:「每次调用都入队,T+1 执行最后一个**非零差额**的」—— 它能通过前两个 case、通过 I4、通过所有 ADR-008 条款,却在此 case 错误地成交 100 股。这也是 ADR-041 与 I4 唯一会对「哪个订单存活」产生分歧的地方)*
- **I4 带采样点,且必须证明队列真的存在**:在主循环每根 K 线的入队后与出队前**各断言一次** `len(queue) <= 1`,断言断言执行次数 `== 2 * 交易日数`;**并在至少一个采样点断言 `len(queue) == 1`**;**再断言 T 日入队的订单对象与 T+1 出队的是同一个**(`is` 比较)。
  - *(「恒 `<= 1`」被 `len(queue) == 0` 恒成立满足 —— 一个完全绕过队列、把差额存成标量字段次日直接应用的实现能通过它、通过 I7、通过所有 ADR-008 条款,而 ADR-006 说队列是「信号与成交分离的物理载体」,那个实现里它根本不存在。`is` 比较则钉死「T 日入队、T+1 出队」而非每日重算)*
- **ADR-005**:最后一根 K 线下单 → 断言被丢弃且无异常
- **I2 经 `order()`**:`order(shares=-999)` 在持仓 100 股时 → 断言报错(ADR-009/ADR-014)
- `blindSpots`

### T5 · 主循环

- `TEST_CMD` 全绿
- **I6**:断言 `len(equity) == 交易日数`(ADR-042 定义)
- **I3 全程,逐日,用影子账本**:影子侧从初始资金出发按 ADR-043 逐笔累加,遵守 T3 门的**同一隔离要求**(算术全部写在测试内、字面写死 `rate=0.001`、不读 `fill.fee`、不借被测代码算期望值);断言**每个交易日**:
  - `shadow_cash == equity[i].cash`、`shadow_shares == equity[i].shares`
  - `equity[i].equity == shadow_cash + shadow_shares * Bar[i].Close`(容差 0.01)
  - **构造 Close 全不相同的数据**,使错位必然失败
  - *(读 `equity[i].cash` / `.shares` 而**不是** `account.cash` / `account.shares` —— §1 声明 `Account` 「不可被聚合外引用」、ADR-044 重申 T6/T7 不得触及它,而同一条门又禁止 import `account`。逐日的 `cash`/`shares` 本来就在 `EquityPoint` 里,那才是契约允许的读法。此前本门写 `account.cash` 是在要求测试去读一个设计禁止触及的实体)*
  - *(逐日形式只在此处 —— T3 无 bar 列表,只做逐笔)*
- **I5 的真实边界(三条,缺一不可)**:
  - 跑两次同一策略,一次纯读,一次在 `next()` 中对 `self.data` 同时做 `iloc[0, 0] = -1` **与** `self.data['Close'].values[:] = 0` 两种写法 → 断言两次 `equity` 序列**逐点相等**
  - 断言上述写入**不抛** `SettingWithCopyWarning`。*(若抛,说明传出的是视图而非副本;而 pandas 2.2 的 copy-on-write 可能让写入静默 no-op,使测试**因错误的理由**通过 —— 同一份代码在 CoW 关闭的环境下就会失败)*
  - 断言策略在第 i 根保存的 `self.data` 引用,到第 j > i 根时**行数仍为 `i+1`**(每次调用是**新**副本,而非一份被反复 re-slice 的同一对象)
- 零交易策略(恒 `target(weight=0)`)→ 断言曲线为水平线、`equity` 恒等于初始资金、`trades` 为空
- 买入持有策略 → 断言交易次数 `== 1`
- **ADR-019 排序后等价**:乱序 CSV 与其手工排序版本 → 断言跑出**逐点相等**的 equity 序列;并断言载入后 `df.index` 为 `0..n-1` 连续。*(pandas 排序后留下陈旧 RangeIndex 会让 `df.iloc[i]` 与 `df.loc[i]` 分叉,之后每个 `i+1` 查找都读错 bar)*
- **多策略隔离**:断言 `run([A])` 的 A 结果与 `run([A,B])` 的 A 结果**逐点相等**;再用 `A_copy.py`(与 `A.py` 字节相同但**文件名不同**)断言 `run([A, A_copy])` 产生两条**相同**曲线而非一条。*(模块级可变状态、复用的 `Backtest` 实例、`importlib` 模块缓存都会让这条失败,而结构性断言「各策略输出独立」测不出来。**不能写 `run([A,A])`** —— ADR-037 规定同 stem 报错,那样会红掉正确实现;stem 唯一性校验属 CLI 层,见下)*
- **分层声明**:stem 唯一性校验属 **CLI 层**(`backtest.py`,ADR-039),`run()` 不做该校验 —— 故 `run()` 接受同一策略文件两次,而 CLI 拒绝
- **ADR-021 跳空统计的计算(从 T6 移来 —— 只有聚合根能读 `Bar` 列表)。三个错误实现各有一测**:
  - 4 根 K 线,日期 `2020-01-02/03/06/07`,构造 gap 序列 `[+5%, -8%, +3%]`(gap 从第 2 根起,三个 gap 分别落在 `01-03`、`01-06`、`01-07`)→ 断言 `summary.max_gap_pct == -8.00`(**按绝对值选取、按原符号显示**)、`max_gap_date == "2020-01-06"`。
    **比较 ADR-038 规定的 `round(x, 2)` 之后的值** —— 由真实价格算出的原始浮点是 `-7.9999999999999964`,直接比必假。*(拦住 `max()` 而非 `max(abs())`)*
  - 同 fixture → 断言 `mean_abs_gap_pct == expect_mean_abs_gap_pct`。该组数的**带符号**平均恰为 `reject_mean_signed`(即 0),故错误实现与正确值判然可分 —— 这是选这组数的原因。注意该值是 `16/3` 取整后的结果,**无 `rounding` 规则时此断言永不可满足**
  - **平手取较早**:gap 序列 `[+5%, -5%]` → 断言 `max_gap_date` 为**较早**那天。*(此规则最容易被实现成「`max()` 碰巧返回哪个就哪个」)*
  - 断言 **`engine.compute_gaps(df)`**(ADR-035 钉死)的返回长度 `== 交易日数 - 1`,第 1 根 K 线不产生跳空。*(拦住 `Open_0 / Close_{-1}` 的边界错误。此前本条写「内部 gap 序列」却无任何 ADR 钉死该名字,而条款自己承认 `RunReport` 不暴露它 —— 等于要求测试去断言一个未命名的私有物。现把生产者提为 ADR-035 的公开符号)*
  - 单行 CSV → 断言三值均为 `None`、**不抛异常**(空序列的 `max()` 会抛 `ValueError`)
- **`summary` 每个字段的数值门(从 T6 移来 —— 只有 T5 有真实 bars 与 fills,T6 只能对自己写进去的字面量做同义反复)**:
  - 参数与期望值见 `fixtures.t5_summary_values`:一份真实 CSV 跑出 `initial_cash` / `final_equity` → 断言 `summary.total_return_pct == expect_total_return_pct`,**并显式断言 `!= reject_ratio_unscaled`(比率未乘 100)与 `!= reject_wrong_quotient`(误用期末/初始之比)**
  - 断言 `summary.bars == 交易日数`(ADR-042)、`start`/`end` **逐字等于** CSV 首末行日期、`trade_count == len(trades)`、`final_equity == equity[-1].equity`、`initial_cash == run() 传入的 cash 实参`
  - *(「比率未乘 100」是整个 API 面最高后果的缺陷:T10 的 CLI↔API 对账两侧同源,该 bug 在两侧相等因而通过;T13 的 I8 表只在 mock 上验前端。**此门是唯一能抓到它的地方**,所以它必须在有真实数据的 T5)*
- `blindSpots`

### T6 · 汇总输出

- `TEST_CMD` 全绿
- **必须同时产出 `summary.json`**(schema 见 ADR-037,顶层 `run_id`/`results`/`assumptions`,`results[].summary` 的字段照 ADR-038)。屏幕文本与 JSON **同源**(从同一 `RunResult` 渲染),断言两者数值一致。多策略时断言 `results` 的项数 == 策略数
- 断言屏幕输出**逐项包含**(中文术语 ↔ 字段名对照见 ADR-038 的表):区间(`start`/`end`)、交易日数(`bars`)、初始资金、期末资产、总收益、交易次数、**对账行**(`frozen_text.reconcile_line`,**固定文案** —— 其中「总资产」是冻结的展示用词,指 `equity`,见 ADR-003)
- **ADR-021 的渲染部分(计算部分已移至 T5 —— T6 拿到的是 `RunReport`,跳空值已算好)**:
  - 给一份 `max_gap_pct = -8.42` / `max_gap_date = "2020-03-16"` / `mean_abs_gap_pct = 0.31` 的纯 `RunReport` → 断言屏幕文本含 `-8.42%`、`2020-03-16`、`0.31%`
  - 三值为 `null` 的纯 `RunReport`(单行 CSV 情形)→ 断言按 `formats.null_display` 显示、**不抛异常**
- **ADR-021 的两条 `⚠`**:断言屏幕输出与 `summary.json` 的 `assumptions` 字段都包含它们
- **`summary.txt` 归 T6**(ADR-035/037):断言它被写入运行目录,且内容**逐字等于**屏幕汇总文本。*(此前 ADR-035 只给 report.py 分了「汇总文本 + summary.json」,而 T8 的门要求 `summary.txt` 存在 —— 该文件无人认领)*
- **ADR-018 屏幕对比表(归 T6,此前只有 T8 的端到端门)**:给一份含 **2 个** `results` 的纯 `RunReport` → 断言屏幕文本含每策略一行的对比表(两行,各含策略名与其 `final_equity` / `total_return_pct`);给**单个** `results` 的 `RunReport` → 断言**不**渲染对比表。*(T6 的 ADR 列有 018 却无任何条款测它 —— 一个多策略时静默不输出对比表的渲染器能通过 T6 全部门,失败只在 T8 浮现并被归因到错误的任务)*
- *(**`summary` 字段的数值门已全部移至 T5** —— 与跳空统计同理:T6 收到的是 `RunReport`,`total_return_pct` 等值**已经算好**,而 ADR-044 禁止 T6 重算。于是在 T6 规定的「手工构造纯 `RunReport`」fixture 里,测试作者是**自己把 `25.0` 写进 `Summary` 再断言它等于 `25.0`** —— 同义反复,测的是测试自己的字面量。第 5 轮只把这个推理用在了跳空上,没有推广到同一条门里的其余六个字段)*
- **渲染断言(T6 真正能测的)**:给一份 `total_return_pct = 25.0` / `final_equity = 12500.0` / `bars = 2516` / `trade_count = 47` 的纯 `RunReport`:
  - 断言**屏幕文本**中逐项出现 `+25.00%`、`12,500.00`、`2,516`、`47`(格式见 ADR-021 钉死的格式表)
  - 断言 `summary.json` 的 `results[0].summary` 中对应字段为**裸标量** `25.0` / `12500.0` / `2516` / `47`,类型分别为 `float`/`float`/`int`/`int`
  - 即:**屏幕文本是 JSON 的格式化投影,JSON 本身不含格式化字符串**
  - *(此门早先写作「断言屏幕文本**与** `summary.json` 中逐项出现 `+25.00%`、`12,500.00`」—— 那要求 JSON 里存格式化字符串,与 ADR-037/038 把这些字段钉死为 `float`/`int` 标量直接冲突,也会让 T10 的逐字段对账与 T11 的递归相等**永不可能通过**。这是第 6 轮那次重写引入的回归)*
- **ADR-044 聚合根边界(否则该 ADR 无门)**:断言 `report.render(...)` **只接受 `RunReport`** —— 传入一个用 `dataclasses.replace` 构造的、**与任何 `Backtest` 实例无关**的纯 `RunReport`,断言输出完全正确;并断言 `report.py` 不 import `engine` 的聚合类或 `account` 的任何符号(可 import `RunReport`/`RunResult`)
- `blindSpots`

### T7 · 曲线与交易清单输出

- `TEST_CMD` 全绿
- **ADR-013 的 `Agg` 后端**:断言 `plot.py` 在 import `pyplot` **之前**调用了 `matplotlib.use("Agg")`,且 import 后 `matplotlib.get_backend().lower() == "agg"`。*(实测本机默认是 `macosx` —— 该 ADR 此前无任何门)*
- **真实渲染**:断言 PNG **文件真实生成**于 `out/<ts>/<stem>_equity.png`(ADR-037)、字节数 `> 0`、可被 `matplotlib`/PIL 重新读取 —— 不接受 mock 掉 `savefig`
- `trades.csv` 表头断言**逐字等于** `contracts.yaml` 的 `trades_csv_header`;行内容逐字段断言,含 `side ∈ trade_sides`、`shares` 恒为正、日期按 `formats.date`
- 断言 `cash_after`/`shares_after` 两列取自 `RunResult.trades_ledger` 的并行序列(ADR-044),且断言 `len(trades_ledger) == len(trades)`。*(T7 不得 import `account`,它没有账本可读 —— 这两列必须由聚合根提供)*
- 零交易 → 断言 `trades.csv` **仅表头**(不报错、不缺文件)
- *(**ADR-024 的独占 `mkdir` 门已移至 T8**:ADR-044 把归档目录创建交给 CLI 层(`backtest.py` 的 `create_archive_dir`),而 `plot.write(report, out_dir)` 是**接收** `out_dir`、不创建它 —— 所以 T7 结构上无法执行该门。这是第 4 轮修复时"挪了职责没挪门"的残留)*
- 断言 `plot.write` **不创建**目录:传入一个不存在的 `out_dir` → 断言报错或由调用方负责,**不得**静默 `mkdir`
- *(**`run.json` 的门已移至 T8** —— 它是按运行的元数据(含 `request`/`assumptions`),由 `backtest.write_run_json` 产出,不是 `plot.write` 的输出。ADR-035 对 `plot.py` 列的输出里没有它,而 ADR-035 是冻结契约,T7 若去写它只能停下上报 = 无人值守挂死)*
- 断言 `plot.write(...)` 的**返回值**是 `{"<stem>": "<path>", ..., "comparison": "<path>"|None}` —— `run.json` 里的 `png_path`/`comparison_png_path` 由它提供(ADR-035)
- **ADR-044**:断言 `plot.write(...)` **只接受 `RunReport`**(其 `results[i]` 为纯 `RunResult`)—— 传入一个用 `dataclasses.replace` 构造的、**与任何 `Backtest` 实例无关**的纯 `RunReport`,断言输出完全正确;断言 `plot.py` 不 import `engine` 的聚合类或 `account` 的任何符号(可 import `RunReport`/`RunResult` 这两个值对象)
- **单策略时 `comparison.png` 必须不存在**:断言归档目录中无该文件。*(ADR-037 写明「仅多策略时产生」,但只有「多策略时存在」被断言过 —— 恒产生一张单线图的实现能通过)*
- `blindSpots` 须声明:未验证图像的**视觉**正确性(仅验证文件有效)

### T8 · CLI 与两条样例策略

- `TEST_CMD` 全绿
- **端到端真实运行**:用真实 CSV 跑完整 CLI(`subprocess`),断言退出码 0、**三类输出齐全**≔ `<stem>_equity.png`、`<stem>_trades.csv`、`summary.txt`(ADR-037)
- 买入持有 → 断言交易次数 `== 1`
- **均线穿越用手工钉死的 fixture,且必须包含判别性 case**:钉死 MA 窗口(`short=2`、`long=3`)与 8 个 `Close` 常量,fixture **必须包含**:
  - (a) **空仓时的下穿** → 断言**不产生成交**(ADR-008 + ADR-042)
  - (b) **持仓时的下穿** → 断言产生 `SELL`
  - (c) **最后一根 K 线上的穿越** → 断言订单被丢弃(ADR-005)
  - 断言 `summary.trade_count == len(trades) == <钉死值>`,且该值**严格小于穿越次数**
  - *(原门「交易次数 == 穿越次数,由数据独立计算」有三个问题:「独立计算」几乎必然复用策略同一个 rolling-mean helper → 同义反复;ADR-008 下空仓下穿不成交,故该等式**字面错误**;而把 fixture 完全交给作者,他会自然挑一个「一次干净上穿 + 一次持仓下穿」的 8 根数据 —— 恰好把刚解释过的那个区别**避开**,于是「每次穿越都成交」的实现照样通过)*
- **ADR-039 CLI 参数校验(全表,非只数值两项)**:
  - `--cash 0` / `--cash -1` / `--fee -0.1` / `--fee 0.5` → 各自退出码 `exit_codes.invalid_cli_args`
  - `--data missing.csv` / `--strategy missing.py` → 各自 `exit_codes.invalid_cli_args`(**不是** `exit_codes.data_validation` —— 后者是「文件存在但内容非法」的另一条路径)
  - `--strategy s.py --strategy s.py`(同 stem)→ `exit_codes.invalid_cli_args` 且 stderr 含 stem 名(ADR-037)
  - `--out` 指向**不存在的多级路径** → 断言自动创建、退出码 `0`
  - 省略 `--cash`/`--fee` → 断言用 ADR-039 的默认值
- **`run.json`(从 T7 移来 —— 归 `backtest.write_run_json`,见 ADR-035)**:断言它存在、且**逐字段**符合 ADR-037 钉死的 schema(`run_id` / `request.{strategies,data_file,cash,fee}` / `results[].{strategy,summary,png_path}` / `assumptions` / `comparison_png_path`)。*(T11 跨阶段锁定边界消费它,字段名错会让阶段二无法读取)*
- **ADR-024 独占创建(归 `backtest.py`,见 ADR-035)。必须【进程内】跑 —— `monkeypatch` 跨不过 `subprocess` 边界**:
  - 本条款**不用** `subprocess`:`import backtest` 后 `monkeypatch.setattr(backtest, "_timestamp", lambda: "20261003-172400")`,再直接调 `create_archive_dir(out)` / `main()`。*(T8 其余条款用 `subprocess` 跑完整 CLI,那是**另外的**条款,不走碰撞路径。此前本条只说了「打哪个符号」,没说「在哪个进程」—— 若两次运行在子进程里,打桩无效、真实时钟不碰撞、零个 `-2` 目录,于是断言会**红掉正确实现**,正是本条款自己要防的事)*
  - `monkeypatch` 把 **`backtest._timestamp`**(ADR-035 钉死的接缝)固定为常量,使两次运行**必然**同秒 → 断言目录名为 `<ts>` 与 `<ts>-2`,两份结果均完整;第三次运行 → `<ts>-3`
  - 预先手工 `mkdir` 一个 `<ts>` 目录并放入哨兵文件,再跑一次 → 断言写入 `<ts>-2` 且 `<ts>` 内的哨兵文件**未被触碰**(钉死 `os.mkdir` 而非 `exist_ok=True`)
  - *(写成「在同一秒内连续跑两次」是在跟时钟赛跑:matplotlib 冷导入 + 真实渲染很容易跨过秒边界 → 得到两个不同时间戳、零个 `-2` 目录,于是「第二个带 `-2` 后缀」会**红掉正确实现**。而若为消除 flake 弱化成「两个目录」,碰撞路径就再也不被走到,`exist_ok=True` 畅通无阻。run 身份唯一性是 I10 的基础)*
- **单行 CSV 端到端(此前只在 T1 测过加载、T6 测过跳空,从未走完整 CLI)**:单行 CSV 跑完整 CLI → 断言退出码 `0`、三类输出齐全、`trades.csv` 仅表头、`summary.json` 的 `results[0].summary` 中 `final_equity == initial_cash` 且 `bars == 1` 且三个 gap 字段均为 `null`、stdout **逐字含**「数据仅 1 日,无法产生交易」、PNG 可被 PIL 读取**且非全白**(断言非背景色像素数 `> 0`)。*(matplotlib 用默认线型画单个点会渲染出空白绘图区 —— 一张字面意义的白图,而「字节数 > 0 且可重读」照样通过)*
- **`--fee` 端到端**:显式传 `--fee 0.0013` → 断言 `summary.json` 的费用口径正确、`trades.csv` 的 `fee` 列非零。*(T3 只在账本层测过 rate>0;CLI 把 `0.1` 解析成 10% 而非 0.1% 的错误能通过其余所有门)*
- **ADR-018**:两条策略同时传入 → 断言 `comparison.png` 生成、对比表含两行、各策略 `trades.csv` 独立
- **ADR-022 + ADR-040**:故意报错的策略 → 断言退出码 `exit_codes.strategy_error`、stderr 含交易日序号+日期+traceback、**且 `--out` 下目录数不增加**(失败的运行不得留下空的时间戳目录 —— 它会被 T11 列为无内容的 run,违反 I10)
- 坏 CSV → 断言退出码 `exit_codes.data_validation`
- `blindSpots`

### 跨任务最终门(合并到 trunk 后)

- **ADR-035 布局契约门(`tests/test_layout.py`)** —— 没有它,布局契约无任何强制力:
  - 对 ADR-035 列出的每个模块 `importlib.import_module` 成功
  - 断言符号存在:`nullhypothesis.data.load_csv`、`nullhypothesis.strategy.Strategy` / `load_strategy`、`nullhypothesis.engine.Backtest` / `run` / `RunResult` / `RunReport`、`nullhypothesis.errors.DataValidationError` / `StrategyError`、`nullhypothesis.report.render`、`nullhypothesis.plot.write`、`api.app` / `api.HOST` / `api.PORT` / `api.validate_run_id` / `api.validate_strategy_name`
  - 断言 `Backtest.queue` 与 `Backtest.enqueue` 存在(T4 的门对它们打桩与采样,此前名字只在门里)
  - 断言值对象可独立构造:`Summary` / `RunRequest` / `RunResult` / `RunReport` / **`EquityPoint`** / `Fill` 的字段名与 ADR-044/037 逐一相符(T6/T7 的门要手工构造它们;T5 的 I3 门读 `EquityPoint.cash`/`.shares`)
  - 断言 `backtest._timestamp` / `create_archive_dir` / `build_report` / `write_run_json` 存在(ADR-035/024/037/044)—— `_timestamp` 是 T8/T11 碰撞门的 monkeypatch 接缝,必须是可打桩的模块级符号
  - **断言全部打桩接缝存在**(`contracts.yaml` 的 `gate_execution.monkeypatch_seams`):`Backtest.enqueue`、`Backtest.resolve_queue`、`api.execute_run`、`api.RUN_LOCK`、`plot.write`、`backtest._timestamp`。*(第 9 轮教训:本轮新钉死的 `execute_run` 与 `resolve_queue` 都漏了布局门断言 —— 而 `test_contracts.py` 只 grep 名字在文档里出现过,散文提一句就满足了。现由 `tests/test_design_binding.py` 机械核对)*
  - 断言 `engine.compute_gaps` 存在(ADR-021/035)
  - 断言 `errors.py` **同时**含 `DataValidationError` 与 `StrategyError`(T1 一次写全;T1/T2 并行,漏写一个会被后写者静默覆盖)
  - **断言签名含必需参数**:`inspect.signature(report.render).parameters` 含 `out_dir`;`inspect.signature(plot.write).parameters` 含 `out_dir`。*(`render` 此前的签名没有 `out_dir`,而 T6 的门要求它往运行目录写两个文件 —— 而 ADR-035 是冻结的名字契约,T6 只能停下上报,在无人值守时就是挂死)*
  - **断言 `Account` 的方法与属性名**(ADR-035 钉死,否则 T3 的门自产自销、T5 又读不到):`Account.apply`、`Account.equity_at`、`Account.cash`、`Account.shares` 全部存在。*(`equity_at` 此前只出现在 T3 的门里、不在任何 ADR 中 —— T3 的 agent 可以把它叫 `value_at` 并让自己的门通过,而 T5 的 I3 门读 `account.cash`/`account.shares` 也同样没被钉死)*
  - *(ADR-035 自述的失败模式是「`pytest` 收集为零或全是 ImportError」—— 但**收集为零时 `pytest -q` 退出码是 5,而「没有测试」常被误读为绿**;`compileall` 在任何布局下也都退出 0。所以那个失败模式恰恰是现有门看不见的)*
- **断言收集到的测试数 `> 0`**:跨任务门须检查 `pytest` 退出码**不是 5**,零收集不得当作通过
- 全套 `TEST_CMD` 在**干净 trunk**(`git status` 干净、本地 == `origin/main`)上全绿
- 真实端到端:下载/构造一份真实 CSV,跑完整 CLI,**人眼看一次 PNG**
- 每任务的 `blindSpots` 汇总成文,附在交付报告

---

---

# 阶段二 · 白屏化(Web 界面)

> **阶段一(T1–T8)验收通过后才开始构建。** 设计在此一次写完,构建分两次。
>
> **分层验收的理由**:账本对不对、界面通不通,是两个问题。混在一起会让「端到端全绿」掩盖账本错误 —— 这正是本文件 §12 结尾那条「全绿 ≠ 系统正确」要防的事。

## 8 · 阶段二的领域模型

**新 BC**:`Presentation`(呈现)。**不是** `Backtest` BC 的一部分。

**边界(最重要的一条)**:`Presentation` 可以**读取** `Backtest` 的结果,但**不得重新计算任何金融量**。

前端不得自己算收益率、不得自己算跳空、不得自己做归一化。所有数字由后端算好传过来。前端把 `916,062` 显示成 `916,062`,而不是把 `equity[-1]` 格式化一遍。

**理由**:一旦前端有自己的计算,就有两个事实来源。

**跨 BC 引用**:`Presentation` 只持有 run 的**标识**(时间戳目录名),不持有 `Backtest` 的聚合对象。

### 实体 / 值对象

| 概念 | 类型 | 说明 |
|---|---|---|
| `RunRequest` | **值对象** | `(strategies[], data_file, cash, fee)` — 表单内容,不可变 |
| `RunResult` | **值对象** | 一次运行的完整结果,可序列化为 JSON |
| `RunArchive` | **实体** | 有身份(时间戳目录名),持久存在于文件系统 |
| `StrategyRef` / `DataRef` | **值对象** | 文件**名**,不是文件内容 |

### 不变式(后端强制)

| # | 不变式 | 来源 |
|---|---|---|
| **I10** | `GET /api/runs` 列出的每一项都能被 `GET /api/runs/{id}` 读取(无悬空条目);失败或不完整的归档目录**不被列出** | ADR-032 |
| **I11** | 后端 API **不接受任何代码字符串**;策略只能按文件名引用 | ADR-026 |

### 约定(仅由 §12 验收门守护,**无运行时强制**)

| # | 约定 | 来源 |
|---|---|---|
| **I8** | 前端显示的任何金融数字 == 后端 JSON 的对应字段(**前端零算术**) | ADR-025 |
| **I9** | Recharts 画的曲线与归档 PNG 来自**同一份** equity 序列 | ADR-030 |

> **诚实声明**:I8/I9 没有运行时守卫 —— 强制点是 React 组件树,没有任何聚合能强制「屏幕上的数字等于 JSON 字段」。它们由 §12 的行为式门(不自洽 mock)守护;**门被删掉则约定失效**。这是约定,不是不变式,文档如实记录以免被误读为有强制保障。
>
> **I9 是读模型一致性的直接应用**:净值这一个量有**两个呈现路径**(Recharts 与 PNG),必须同源 —— 见 §12 结尾那条「全绿 ≠ 系统正确」。

## 9 · 阶段二 ADR

### ADR-025 · Presentation BC 不得重新计算金融量

见上方边界说明。

**后端返回的每个金融量都是已完成全部算术的标量**:百分比以**已乘 100** 的数值返回(`total_return_pct: 816.06`),金额为 `float`,计数为 `int`。字段名见 ADR-038。

**前端允许的「格式化」穷举如下,无第六类**:
1. 插入千分位分隔符
2. 追加 `%` / 货币符号
3. 日期 strftime(后端给 `YYYY-MM-DD`,前端可改显示格式)
4. 小数位截断
5. **正数前置 `+` 号**(纯符号判断,无算术)—— 后端给 `816.06`,前端显示 `+816.06%`。*(第 5 条是后补的:I8 的门期望显示 `+42.00%`,而后端给的是裸 float,原先的四条里没有一条允许加号,门变成不可满足)*

**乘、除、加、减一律在后端。** 此前本 ADR 把「百分号」列为格式化却未说百分比已在后端乘过 100 —— 把比率变百分比本身就是乘法,那是一处界线不清,现予澄清。

**例外**:图表坐标轴刻度与网格线(ADR-030 的豁免)。

**不得从别处推导**:`交易次数` 读 `summary.trade_count`,**不是** `trades.length`;`期末资产` 读 `summary.final_equity`,**不是** `equity[equity.length-1]`。这两个是最常见的违规,且都不含算术,所以靠看代码里有没有 `/` `*` 是抓不到的。

### ADR-026 · 策略只能按文件名引用,API 绝不接受代码

后端扫描 `strategies/*.py`,`GET /api/strategies` 返回文件名列表。`POST /api/run` 只接受**文件名**。

**API 绝不接受代码字符串,不提供任何 `exec`/`eval` 路径。** 浏览器内写策略 → ❌ 明确非目标。写策略仍用编辑器改 `.py` 文件。

**理由**:零代码执行风险。前端的职责是「跑 + 看」,不是「写」。

### ADR-027 · CSV 只能从服务器 `data/` 目录选择

后端扫描 `data/*.csv`,`GET /api/data` 返回文件名列表,前端提供选择器。

**不支持浏览器上传** → ❌ 明确非目标。ADR-001 的「预复权」契约仍由你在文件层面保证。

### ADR-028 · 运行为同步请求,按钮禁用防重入

`POST /api/run` **同步**返回完整结果。运行期间前端禁用「跑」按钮并显示 spinner。

**无任务队列、无轮询、无 WebSocket。** 理由:2516 行数据跑起来是毫秒级;异步任务是 Speculative Generality。

**但按钮禁用只是 UX 提示,不是并发保证。** 按钮不是事务边界 —— 两个标签页、一次 `curl`、一次刷新都能绕过它。**服务端必须自己串行化**:对同一 `--out` 目录的 run 加进程内锁(`threading.Lock`),并依赖 ADR-024 的独占 `mkdir` 作为第二道防线。

此前本 ADR 写作「按钮禁用是最简单且正确的并发解法」—— 那是错的,现予更正。

### ADR-029 · 策略选择器多选,对应 CLI 的 `--strategy` 可重复

多选 2+ 条 → 一张图多条曲线叠加 + 对比表。单选也走同一端点。

**对比表只陈述数字** —— 不得加排序箭头、不得高亮「最佳」、不得排名(承接 ADR-018)。

### ADR-030 · 后端同时返回 equity 序列与归档 PNG 路径

`POST /api/run` 返回体同时含:
- `equity`:逐日序列,供前端 Recharts 绘制(可 hover / 缩放 / 点 legend 隐藏某条)
- `png_path`:后端 matplotlib 产生的归档 PNG(ADR-013 保留,CLI 与历史归档都用它)

**两者必须来自同一份 equity 数据(I9)。**

**理由**:静态图无法回答「某一天账户到底是多少」。交互图让你 hover 读取**逐日 equity 值**与日期,这是 React 值回票价的地方。

**不包括回撤。** 此前本 ADR 的立论写作「这段回撤是什么时候、跌了多少」—— 那自相矛盾:回撤在 §0 是明确非目标,且 ADR-025 禁止前端算术(算回撤需要 running-max)。立论已更正为 hover 读取逐日值。

**图表装饰的豁免**:Recharts 自行计算的**坐标轴刻度、网格线、y 域**不受 I8 约束 —— 它们是图表装饰,不是从领域数据派生的金融结论。但 Recharts 与归档 PNG **均用绝对金额**,前端**不做归一化**(承接 OQ-04)。

### ADR-031 · 错误以结构化 JSON 返回

```json
HTTP 400
{
  "code": "DATA_VALIDATION",
  "message": "Close 为 NaN",
  "detail": { "file": "aapl.csv", "row": 42, "column": "Close" }
}
```

`code` 取值:`DATA_VALIDATION`(ADR-019)、`STRATEGY_ERROR`(ADR-022,`detail` 含 `bar_index`/`date`/`traceback`)、`INVALID_REQUEST`、`NOT_FOUND`。

**「响亮失败」的精神保持不变**,但命令行文本直接贴到网页上很难读;结构化后前端能突出显示行号/日期。

### ADR-032 · 历史浏览复用 ADR-024 的时间戳目录

- `GET /api/runs` → 历史列表(时间戳、策略名、数据文件、期末资产)
- `GET /api/runs/{id}` → 该次的汇总 + 归档 PNG

**归档项显示 PNG,不重建 Recharts** —— 那是当时的快照。无数据库。

历史列表读每个归档目录的 `run.json`(ADR-037/024)—— 策略名与数据文件名**无法从文件名反推**,必须显式记录。此前本 ADR 写「无额外存储、无 `equity.json`」,现修正为:**允许且要求 `run.json` 这一个清单文件**。它不是第二个事实来源,而是归档自身的元数据。

目录被手动删除、或缺 `run.json`(失败运行留下的空目录)→ `GET /api/runs` **不列出它**(扫描即事实,I10 由此成立)。

### ADR-033 · 开发用 vite 代理,交付由 FastAPI 托管构建产物

```
开发:  uvicorn api:app --port 8000
       npm run dev          # :5173,代理 /api → :8000

交付:  npm run build        # → frontend/dist/
       uvicorn api:app --port 8000
       → http://127.0.0.1:8000 全部就绪
```

交付形态**同源**,故**无需 CORS 配置** —— CORS 配错是经典的「真白屏」来源。

### ADR-045 · 归档 PNG 经专用端点服务,**不**把 `out/` 挂成静态目录

ADR-038 把 `png_path` 钉死为 `out/<run_id>/<stem>_equity.png`,ADR-032 要求
历史详情显示归档 PNG 快照 —— 但 ADR-033 只挂载了 `frontend/dist`,而 `out/`
在它**外面**。实测 `GET /out/<run_id>/<stem>_equity.png` → **404**:三个前端
门全绿(它们用 mock),只有真实浏览器里会看到裂图。

**决策**:加一个专用端点,**不**挂 `StaticFiles("out")`。

```
GET /api/runs/{run_id}/png/{name}   →  image/png
    run_id  经 validate_run_id 白名单校验(ADR-038 的 patterns.run_id)
    name    经白名单校验:只接受 ADR-037 的三种归档图名
```

**理由**:

1. **把 `out/` 整个挂成静态目录等于把全部历史归档变成可枚举的静态资源** ——
   目录列举、任意文件名探测都随之开放。专用端点只暴露「这个 run 的这张图」。
2. **复用已经写好并测过的 `validate_run_id`** —— 穿越防护是同一套白名单正则,
   不是第二条各自演化的校验路径(那正是 ADR-038 钉死它的理由)。
3. `name` 也走白名单而非黑名单:`<stem>` 由用户提供的策略文件名决定,
   `..%2f` 之类的构造必须在拼路径**之前**就被挡下。

**前端据此拼 URL**:`png_path` 的形态不变(ADR-038 不动,它仍是归档里的
相对路径,CLI 的归档也仍然自洽),前端从 `run_id` + 文件名拼端点 URL。
*(这是 ADR-025 允许的「路径拼接」,不是金融量算术。)*

**`vite.config.ts` 因此只需代理 `/api`**,与 ADR-033 原文一致 —— 归档图走
`/api/runs/.../png/...`,不再需要额外代理 `/out`。

### ADR-037b · 前端文件布局与组件契约(构建期不得变更)

```
frontend/
  package.json  vite.config.ts  tsconfig.json  index.html
  src/
    main.tsx                      # T12  挂载 App
    App.tsx                       # T12  持有全部状态,组装所有组件
    types.ts                      # T12  逐字段镜像 ADR-038 的 pydantic 模型
    api.ts                        # T12  fetch 封装:listStrategies/listData/run/listRuns/getRun
    components/
      RunForm.tsx                 # T12
      EquityChart.tsx             # T13  Recharts
      SummaryPanel.tsx            # T13
      ComparisonTable.tsx         # T13
      HistoryList.tsx             # T14
      ErrorCard.tsx               # T14
    __fixtures__/run_response.json  # 由 T10 的 pytest 生成(见 §12 契约门)
```

**`App.tsx` 是唯一的状态持有者**(关闭 OQ-10):

```ts
const [runResult, setRunResult] = useState<RunResponse | null>(null)
const [error,     setError]     = useState<ApiError | null>(null)
const [loading,   setLoading]   = useState(false)
```

T13/T14 的组件**只接 props,不自己 fetch**。这是三个任务并行写同一个 `src/` 而不互相覆盖的唯一保障。

**脚手架不用 `npm create vite`** —— 该命令未在允许列表中,会让无人值守构建停在权限弹窗。T12 用 Write 手写上述配置文件,再用已放行的 `npm install` 安装。

**`frontend/package.json` 依赖(钉死,否则 T13 的门装不上)**:

```jsonc
"dependencies":   { "react": "^18.3.1", "react-dom": "^18.3.1", "recharts": "^2.15.0" },
"devDependencies":{ "typescript": "^5.7.0", "vite": "^6.0.0", "@vitejs/plugin-react": "^4.3.0",
                    "@types/react": "^18.3.0", "@types/react-dom": "^18.3.0",
                    "vitest": "^2.1.0", "jsdom": "^25.0.0",
                    "@testing-library/react": "^16.1.0", "@testing-library/jest-dom": "^6.6.0" }
```

`vite.config.ts` 必须把 `/api` 代理到 `http://127.0.0.1:8000`。

### ADR-034 · 仅监听 127.0.0.1

`api.py` 暴露模块级常量,使该决定**可被测试断言**(而非藏在命令行参数里):

```python
HOST = "127.0.0.1"
PORT = 8000
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
```

不绑 `0.0.0.0`。无认证、无多用户 → ❌ 明确非目标。不为公网部署设计。

## 10 · 阶段二 OQ

| # | 问题 | 构建期约定 |
|---|---|---|
| **OQ-07** | URL 带参数(`?strategy=x&data=y`)以便书签/分享 | **v0 不做**。构建期勿实现 |
| ~~OQ-08~~ | ~~下拉项过多是否需要搜索框~~ | **已关闭**:阈值为 **`> 20` 项**才渲染搜索框,由 T12 实现(非由 T12 决定)。*(原写「由 T12 决定」却同时固定了阈值,而 T12 的门双向断言该数字 —— 等于门在断言一个无 ADR 支撑的值)* |
| **OQ-09** | 暗色模式 / 移动端适配 | **不做** → 明确非目标 |
| ~~OQ-10~~ | ~~前端状态管理方案~~ | **已关闭 → ADR-037b**:`App.tsx` 用 `useState` 持有 `runResult \| error \| loading`,props 下传。**无状态库**。*(原为「由 T12 决定」—— 但 run 结果由 T12 的表单产生、T13 的图表消费,不是 T12 的私有决定)* |

## 11 · 阶段二任务 DAG

**全部阶段二任务共享 ADR-038(API 契约 / 字段名)、ADR-037b(前端布局与组件契约)。**

| 任务 | 标题 | 依赖 | ADR |
|---|---|---|---|
| **T9** | FastAPI 骨架 + 资源列举端点 | T8 | 026, 027, 034, I11 |
| **T10** | `POST /api/run` 端点 | T9 | **025**, 028, 029, 030, 031, **040** |
| **T11** | 历史端点 | T9 | 032, **024**, I10 |
| **T12** | React/TS 脚手架 + 表单 | T9 | 033, **037b**, OQ-08 |
| **T13** | Recharts 净值图 + 汇总 + 对比表 | T10, T12 | **025**, 029, 030, **021**, I8, I9 |
| **T14** | 历史浏览 UI + 错误卡片 | T11, T13 | 031, 032 |

## 12 · 阶段二验收门

全局(前端):

```
FE_TEST_CMD  = npm test          (vitest)
FE_LINT_CMD  = npx tsc --noEmit  (类型检查必须零错误)
FE_BUILD_CMD = npm run build
```

### T9 · FastAPI 骨架 + 列举端点

- `TEST_CMD` 全绿(用 `fastapi.testclient`)
- `GET /api/strategies` / `GET /api/data` → 断言返回**真实磁盘上**的文件名
- **空目录** → 断言返回 `[]` 而非报错
- **ADR-026 / I11 的 T9 部分(单元级,不依赖后续任务的端点)**:断言 `api.py` 顶层暴露的校验器 `validate_strategy_name(s) -> bool` 对一段代码串返回 `False`;并 `grep -E '\bexec\(|\beval\(|\bcompile\('` 整个后端作为廉价 tripwire。*(tripwire 会被 `getattr(builtins,...)`/`__import__` 绕过,且 ADR-036 允许的 `spec.loader.exec_module` 需用词边界才不误报 —— 故它只是补充)*
  - **行为式主门(传代码串给 `POST /api/run` → 断言 422/400 且无副作用文件)属 T10**,因为 T9 尚不存在该端点
- *(此前 T9 的门断言 `POST /api/run` 的行为、并要求 `run_id` 的逐值表 —— 但 ADR-035 把 `POST /api/run` 分给 T10、把 `run_id` 相关端点分给 T11,而两者都依赖 T9。那是与第 2 轮 T3 同一类的跨任务顺序错误:T9 的门要等后续任务的产物才能通过。现已把这两块分别下移到 T10 与 T11)*
- **ADR-034 可断言形式**:断言 `api.HOST == "127.0.0.1"` 且 `api.PORT == 8000`(ADR-034 把它们提为模块级常量正是为此 —— `TestClient` 不起 uvicorn,绑定地址无从观测)
- **路径穿越:必须是白名单正则,不是黑名单**。策略名与数据文件名各自传 `../../etc/passwd` → 全部拒绝
- **`run_id` 校验器的逐值表(T9 测纯函数,T11 测端点)**:ADR-035 规定 `api.py` 暴露 `validate_run_id(s) -> bool`,此处逐值断言:
  - **拒绝**:`"../../etc/passwd"`、`"20261003"`(缺时间段)、`"abc"`、`"/etc/passwd"`、`"20261003-172400/.."`、含 `\x00` 的值
  - **接受**:`"20261003-172400"` **与** `"20261003-172400-2"`
  - *(只测 `../` 会放过黑名单式实现 `if '..' in id or '/' in id: reject` —— 它接受 `%00`、Windows 绝对路径和任意目录名,而 ADR-038 规定的是正则白名单 `^\d{8}-\d{6}(-\d+)?$`。反向也要测:过严的 `^\d{8}-\d{6}$` 会让 ADR-024 的碰撞目录在阶段二读不到 —— 那正是 I10 的悬空条目)*
- **静态挂载的守卫(ADR-035/033)**:`frontend/dist` **不存在**时(T9 阶段必然如此)→ 断言 `api.py` 可正常 import、`app` 可用、`/api/strategies` 正常响应、**不抛异常**。*(无 `is_dir()` 守卫的 `StaticFiles(directory=...)` 在目录缺失时直接抛错,会让 T9 根本起不来)*
- `blindSpots` 须声明:`TestClient` 不经 ASGI/HTTP 真实栈,实际绑定地址与静态挂载**是否遮蔽 API 路由**不被本门覆盖(由 §12 的真实栈门覆盖)

### T10 · `POST /api/run`

> **进程内与子进程混用,逐条标注**:串行化断言与 I9 同源断言要打桩 → **进程内**;CLI↔API 对账要真起 CLI → **子进程**(`gate_execution` 已声明两者不相交)。

- `TEST_CMD` 全绿
- **数值一致性(关键门,必须真起 CLI 进程)**:
  - 用 `subprocess.run([sys.executable, "backtest.py", ...])` **真实起一个 CLI 进程**,断言退出码 0,读其 `summary.json`
  - 用**相同参数**打 API,断言两侧**按策略名配对后**,在 `final_equity`/`total_return_pct`/`trade_count`/`max_gap_pct`/`max_gap_date`/`mean_abs_gap_pct` 上**逐字段 `==`**(金额按 2 位小数定点比较),且 `equity` 序列**逐点相等**。*(两侧都是 `results[]` 数组,须按 `strategy` 配对,不得假设顺序相同)*
  - **多策略也要测一次**:传 2 条策略 → 断言 `summary.json` 的 `results` 有两项、与 API 的两项逐一对账
  - **必须显式传非默认的 `--fee 0.0013` 与非默认 `--cash 55000`**。*(全用默认值时,CLI 与 API 对 fee/cash 的解释差异不可见)*
  - *(原门只写「与 CLI 跑出的完全相同」:CLI 当时无机器可读输出,执行它只能正则刮中文汇总文本;且最可能的错误实现是「API 与 CLI 都调同一个 `run_backtest()`,测试拿 API 结果比对 `run_backtest()`」—— 那是拿引擎和自己比,CLI 参数解析、默认值、fee 口径全在比较之外)*
- **ADR-030 / I9**:断言返回体同时含 `equity` 与 `png_path`,`png_path` 指向**真实存在**的文件;断言渲染 PNG 时传入的序列**就是**响应里 `equity` 的同一份数据(对绘图调用打桩比对,非仅比末值 —— 两条不同序列可以共享末值)
- **ADR-029**:传 2 条策略 → 断言 `results` 有两项、各有独立 `equity`/`trades`/`summary`、`comparison_png_path` 指向真实文件;传 1 条策略 → 断言 `comparison_png_path is None`(**非 `""`、非指向文件**)
- **ADR-026 / I11 的行为式主门(从 T9 下移至此,因端点归 T10)**:`POST /api/run` 的 `strategies` 传入一段**有可观测副作用**的代码串(如写文件到 tmp)→ 断言 422/400 **且该文件未被创建**
- **ADR-031 每个 `code` 各一测**:坏 CSV → `DATA_VALIDATION` 含 `file`/`row`/`column`;报错策略 → `STRATEGY_ERROR` 含 `bar_index`/`date`/`traceback`;不存在的文件名 → `NOT_FOUND`;请求体字段类型错 → `INVALID_REQUEST`
- **ADR-021**:断言响应含 `assumptions` 字段且为那两条文案
- **ADR-028 服务端串行化(必须能观测到锁本身)**:对 run 入口打桩记录每次调用的进入/退出时刻 → 并发发两个 `POST /api/run`,断言两个区间**不重叠**;并断言产生两个不同 `run_id`、两份结果都完整。*(只断言「两个 run_id + 结果完整」不够:ADR-024 的独占 `mkdir` + `-N` 重试单独就能满足这三条,此门会对「完全没有锁」的实现亮绿灯 —— 它测的是第二道防线,看不见第一道的缺失)*
- 零交易策略(ADR-023)→ 断言 `trades: []` 且不报错
- **契约 fixture 落盘**:本门须把一份真实响应写入 `frontend/src/__fixtures__/run_response.json`,供 T13 的 mock 使用(见下方契约门)
- `blindSpots`

### T11 · 历史端点

- `TEST_CMD` 全绿
- **I10 硬断言**:`GET /api/runs` 的每一项都能被 `GET /api/runs/{id}` 成功读取
- **归档 fixture 由 T11 自己在【进程内】调阶段一的 CLI 层产生**(`backtest.create_archive_dir` + `backtest.write_run_json`,均为 T8 产出物,经 T11→T9→T8 的传递依赖可用),**不经 `POST /api/run`** —— 那是 T10 的端点,不在 T11 的依赖里。`monkeypatch` 把 `backtest._timestamp` 固定为常量使两次必然同秒 → 断言历史有 2 项、`run_id` 分别为 `<ts>` 与 `<ts>-2`(ADR-024)。*(此前本条要「两次运行」却没说归档从哪来:唯一的 run 端点归 T10,而 DAG 里 T11 不依赖 T10 —— 与第 2 轮 T3、第 7 轮 T9 同一类跨任务顺序错误)**(「跑两次 → 2 项」在秒粒度下要么 flaky 要么掩盖覆盖 bug;靠抢时钟不行)*
- 断言列表项的 `strategies`/`data_file` 与当初请求一致 —— 它们读自 `run.json`(ADR-032/037),**无法从文件名反推**
- **`GET /api/runs/{id}` ≡ 归档的 `run.json`,递归逐字段相等(跨阶段锁定边界的唯一消费侧门)**:断言响应体与该目录下的 `run.json` 键集合与值**递归相等**(递归进 `request` / `results[i]` / `results[i].summary`)。*(ADR-037 声明两者「逐字段等于」,但此前只有 T7 断言了生产侧;一个在输出时重命名或丢掉 `request`/`assumptions`/`comparison_png_path` 的实现能通过 T11 的全部条款,而 T14 用 mock 消费它)*
- 断言列表项的 `final_equity`:**多策略运行时取 `results[0]`(即 `--strategy` 的第一条)的 `final_equity`**,并断言该口径在列表与详情间一致
- **手动删掉一个归档目录** → 断言 `GET /api/runs` 不列出它、不抛异常
- **构造一个只有空目录、无 `run.json` 的归档**(模拟失败的运行)→ 断言**不被列出**(I10)
- `out/` 不存在 → 断言返回 `[]`
- **`run_id` 的端点级断言(T9 已测纯函数,此处测端点真的用了它)**:`GET /api/runs/{id}` 对 `../../etc/passwd`、`"abc"`、含 `\x00` 的值 → 各自 400/404 且**未读取任何目录外文件**;对 `"20261003-172400-2"`(ADR-024 的碰撞目录)→ 断言**能正常读取**
- `blindSpots`

### T12 · React/TS 脚手架 + 表单

- `FE_LINT_CMD`(`npx tsc --noEmit`)**零错误**
- `FE_BUILD_CMD` 成功,产出 `frontend/dist/index.html`
- `FE_TEST_CMD` 全绿
- **「跑」按钮启用的充要条件(逐条测)**:`selectedStrategies.length >= 1` **且** `dataFile != null` **且** `cash` 为 `> 0` 的数字 **且** `fee` 在 `[0, 0.1)`。逐项置空/置非法 → 断言禁用;四项齐全 → 断言启用。*(原门写「未选完 → 按钮禁用」并引用「产品 case B1/B2」—— 但 B1/B2 全文不存在,引用了一个不存在的权威,且「未选完」的必填集合从未定义)*
- **ADR-028**:运行中 → 断言按钮禁用
- **空态两种各一测**:(a) `strategies`/`data` 返回 `[]` → 断言显示「目录为空」文案;(b) 请求成功但尚未运行 → 断言显示「还没有结果」文案。两者都**不是白屏**
- **OQ-08 的阈值双向可证伪**:`listData` 返回 **21** 项 → 断言渲染搜索/过滤输入框;返回 **20** 项 → 断言**不**渲染它。*(OQ-08 是双边约束「超过 20 项才加」—— 恒加和恒不加都违反它,而此前无任何门,两者都能通过)*
- **后端连不上三种各一测**(这是「白屏化」反过来最该防的事):
  - `fetch` reject(网络层失败)→ 断言显示明确错误
  - **`200` + `Content-Type: text/html`**(vite 代理返回 502 HTML 页)→ 断言显示明确错误而非崩溃。*(真实的不可达后端走的是这条路径,`response.json()` 在不同位置抛不同的错;只 mock reject 的 `catch` 往往处理不了它)*
  - `500` + JSON 错误体 → 断言显示 `ErrorCard`
- **ADR-037b 布局契约门(`frontend/src/__tests__/layout.test.ts`)—— 否则 ADR-037b 全文无门**:
  - 断言 ADR-037b 列出的每个文件存在(`App.tsx`、`types.ts`、`api.ts`、六个组件)
  - 断言 `package.json` 的依赖键集合**等于** ADR-037b 钉死的那一组
  - 断言 T13/T14 的五个组件文件中**不含 `fetch(`**(只接 props,不自己取数)
  - 断言 `App.tsx` 是唯一出现 `useState<RunResponse` 的文件
  - *(ADR-037b 自述「这是三个任务并行写同一个 `src/` 而不互相覆盖的唯一保障」,却在第 8 轮前没有任何门断言它 —— T12 现有的门只验行为与构建成功,一个结构完全不同的 `src/` 也能通过。与 `errors.py` 无门而 T1/T2 并行是同一类缺陷,只是换了个 BC)*
- `blindSpots` 须声明:jsdom **无布局引擎**,一切元素尺寸为 0

### T13 · Recharts 图 + 汇总 + 对比表

- `FE_LINT_CMD` 零错误、`FE_TEST_CMD` 全绿
- **I8 硬断言(读模型一致性)—— 每个字段都要不自洽**:构造**一份** mock 响应,其中 I8 涉及的**每一个**字段都与可推导来源**故意不自洽**:
  | 字段 | 不自洽设置 | 断言显示 |
  |---|---|---|
  | `total_return_pct` | 期末 200000、初始 100000,但字段写 `42.0` | `+42.00%`(**非** `+100%`) |
  | `final_equity` | 见 `fixtures.i8_inconsistent_mock` | 按 `expect_rendered.final_equity`(**非** `reject_rendered.final_equity`) |
  | `trade_count` | 字段写 `7`,而 `trades` 数组长 3 | `7`(**非** `3`) |
  | `max_gap_pct` / `max_gap_date` | 字段写 `-8.42` / `"2020-03-16"` | 显示 `-8.42%`(**非** `8.42%`、**非** `-0.08%`、**非** `-842.00%`)且 `2020-03-16` 可见 |
  | `max_gap_pct: null` | 单行 CSV 的响应 | 按 `formats.null_display` 显示(**非** `null` / `NaN` / 空白)—— ADR-021 为 Web 界面钉死了该渲染 |
  - *(原门只把 `总收益` 一个字段做成不自洽,却在断言里列了五个字段 —— 一个把 `总收益` 直通、却用 `trades.length` 当交易次数的前端能通过。而 `trades.length` 这种违规**不含算术**,所以连被废弃的那个 grep 也抓不到它,是最可能发生的一种)*
  - 前端只允许 ADR-025 穷举的五类格式化
- **I9 可执行化**:断言 Recharts 的 `data` **点数 `== len(response.equity)`**,且首、末、中间任取一点**逐值相等**。*(原门「未经任何变换」无判定程序;降采样或归一化成 `{x,y}` 都是变换,只断言长度抓不到)*
- **ADR-029**:两条策略 → 断言图上两条 `<Line>`、对比表两行
- **ADR-018 的「不排名」可执行化**:断言对比表的 `<th>` 元素**无 `onClick` handler 且无 `aria-sort` 属性**;断言表内无 `className` 含 `best`/`highlight`/`winner` 的元素。*(原门「断言表格无排序控件、无「最佳」高亮」在陈述上不可证伪 —— 什么算排序控件没有定义)*
- 单条策略 → 断言图上一条线、无对比表
- 零交易 → 断言曲线为水平线、交易清单显示空态
- **ADR-021**:断言两条 `⚠` 假设文案在界面上**可见**。*(它们是 ADR-001 那条不可验证前提的唯一守卫;Web 界面是非作者实际使用的界面,不得静默丢失)*
- `blindSpots` 须声明:(a) 未验证图表的**视觉**正确性(仅验证数据绑定);(b) **jsdom 无布局引擎** —— `ResponsiveContainer` 在真实浏览器里父容器无高度时渲染为零宽白图,而 jsdom 中查询 `<Line>` 仍然通过。**这正是一种字面意义的白屏,自动化门结构上看不见它**,只能由下方人工门覆盖

### T14 · 历史 UI + 错误卡片

- `FE_LINT_CMD` 零错误、`FE_TEST_CMD` 全绿
- **ADR-031 每个 `code` 各一个渲染测试**:`DATA_VALIDATION` 断言显示文件名+行号+列名;`STRATEGY_ERROR` 断言显示交易日序号+日期+traceback;`NOT_FOUND`/`INVALID_REQUEST` 各断言显示 `code` 与 `message`
- 历史列表为空 → 断言空态文案
- 点历史项 → 断言显示归档 PNG + 汇总
- **ADR-032**:断言历史详情视图中**不存在** Recharts 容器元素 —— 归档项只显示 PNG 快照,不重建交互图。*(这才是 ADR-032 的实际决定;原门只断言 PNG 存在,没有断言交互图的缺席)*
- `blindSpots`

### 阶段二 · 契约门(自动化,T10 与 T13 之间)

前端 mock 必须由**真实 API 响应生成**,否则后端改字段名时前端静默显示 `undefined` 而 `tsc`/`vitest`/`build` 全绿:

- T10 的一个 pytest(`tests/test_contract_fixture.py`)把真实响应写入 `frontend/src/__fixtures__/run_response.json`
- **目录创建的归属**:T10 与 T12 是 DAG 兄弟(都只依赖 T9),T10 可能先跑,那时 `frontend/` 还不存在。故 T10 用 `mkdir(parents=True, exist_ok=True)` 创建该路径 —— **这是 T10 唯一允许写到后端目录之外的地方**
- **该文件必须 commit 进 git**。T13 的前端门在**不先跑 pytest** 的干净 checkout 上必须通过。*(否则 `npm test` 在干净检出上 ENOENT;更坏的是 T13 的 agent 发现文件不存在就**手写**一份 —— 正是此门要防的事 —— 而四个前端门全绿)*
- T13 的前端测试**以该文件为基底**,不手写 JSON
- **I8 的不自洽 mock 以该 fixture 为基底,只就地覆写表中那几个字段的值**(键集合不变)。*(两条门否则直接冲突:I8 要求一份故意不自洽的 mock,而那按定义不可能是真实响应)*
- **契约门的比较必须递归且不可自我满足**:复制已提交的 fixture → 重新生成到**另一个路径** → 比较两者的键集合,**递归到所有嵌套对象**(`results[i]`、`results[i].summary`、`equity[0]`、`trades[0]` 逐层);比较后 `git diff --exit-code` 该文件,断言 CI 未改写它
  - *(只比顶层键集合会放过 `summary.final_equity` → `summary.finalEquity` 这类重命名 —— 而那正是 ADR-038 钉死、ADR-025 禁止前端自救的那一层。而「先生成再和自己比」永远绿,契约从未被检查)*

> *(原设计无此门。ADR-025/I8 禁止前端自行推导,所以字段名是跨任务硬契约:T10 产出、T13 消费。后端把 `final_equity` 改名,前端会渲染 `undefined` 为空白或 `NaN`,而三个前端门全部通过。)*

### 阶段二 · 真实栈门(自动化)

> 原设计声明「无 `REAL_STACK_GATE`:本项目无 DB、无外部服务」。**该判断对阶段一成立,对阶段二不成立** —— 阶段二的真实基质不是数据库,而是**文件系统持久层 + ASGI/HTTP 边界**,而 `TestClient` 两者都绕过。

- 用 `subprocess` 真起 `uvicorn api:app`,然后:
  - 断言 `127.0.0.1:8000` **可连**
  - **主断言(离线确定、零额外权限)**:捕获 `uvicorn` 的启动日志,断言其中出现 `Uvicorn running on http://127.0.0.1:8000`,**且不出现 `0.0.0.0`**。另断言 `api.HOST == "127.0.0.1"`
  - *(**不要用 `lsof`** —— 实测它存在于 `/usr/sbin/lsof` 但**不在 `.claude/settings.json` 的允许列表中**,无人值守构建会停在权限弹窗上。`psutil` 同理且未安装。解析启动日志不需要任何额外权限)*
  - **LAN 探测仅作可跳过的补充**:取不到非回环 IPv4 时 `pytest.skip` 并记入 `blindSpots`;连接须 `settimeout(2)`,断言「**未成功**」(`ConnectionRefusedError` 或 timeout 均算通过)
  - *(原门把 LAN 探测当主断言不可靠:`socket.gethostbyname(socket.gethostname())` 在本机实测抛 `gaierror`,而 UDP-connect-to-8.8.8.8 那招需要外网路由 —— §6 已声明网络被拒。于是该门要么报错(假失败、关于 ADR-034 零信息),要么被 try/except 包成跳过,从而对绑在 `0.0.0.0` 的后端**空洞通过**。且连不可路由的 LAN IP 通常是**超时**而非拒绝)*
- 同一进程下用 `httpx` 请求 `/`(断言返回 `index.html`)与 `/api/strategies`(断言返回 JSON),证明 ADR-033 的静态挂载**未遮蔽** API 路由。*(挂载顺序错误只在真实服务器 + 真实 `dist/` 下暴露)*
- 断言 `npm run build` 的产物真实存在于 `frontend/dist/index.html`

### 阶段二最终门(人工,不可省)

- `npm run build` 后仅跑 `uvicorn`,**在真实浏览器里打开 `http://127.0.0.1:8000`**
- 真实跑一次 `buy_and_hold`:**人眼确认** Recharts 曲线形状与归档 PNG 一致
- **缩小浏览器窗口再放大**:确认图表随容器重绘且**不消失**。*(jsdom 无布局引擎,`ResponsiveContainer` 的零宽白图只能人眼发现 —— 这是字面意义的白屏)*
- 真实跑一次多选对比:确认两条曲线、对比表数字与 CLI 输出一致
- 故意选一份坏 CSV:确认错误卡片可读
- **把后端停掉再刷新页面**:确认显示明确错误而非白屏。**两种部署形态各试一次**(vite dev 代理 / FastAPI 同源托管)—— 它们的失败表现不同
- 人眼看 PNG 的检查清单:y 轴量级与期末资产一致、x 轴首末日期与 CSV 首末一致、零交易策略为水平线、曲线无断点
- 汇总各任务 `blindSpots`

> **「全绿 ≠ 系统正确」在此尤其危险**:前端测试全部在 jsdom + mock 响应下运行。上面的契约门与真实栈门补上了字段名漂移、绑定地址、静态挂载三类;**剩下的布局类缺陷(零宽白图)自动化门结构上无法覆盖**,只能靠人工门,不可省略。

---

## 6 · 依赖

### 阶段一(Python)

| 包 | 状态 |
|---|---|
| `pandas 2.2.2` | 已装 |
| `pytest 9.0.3` | 已装 |
| `matplotlib 3.11.2` | 已装(本会话安装,已授权) |
| `Pillow (PIL) 10.3.0` | 已装 —— T7/T8 的「PNG 可被重读且非全白」门依赖它 |
| `PyYAML 6.0.1` | 已装 —— `CONTRACT_CMD` 读 `contracts.yaml` 依赖它。**缺失时契约门必须硬失败,不得 skip** |
| `httpx` | 随 FastAPI 生态已装 —— 真实栈门用它打 HTTP |
| `backtesting` | **不是依赖**。仅作设计期语义参考,已从环境卸载 |

### 阶段二(已验证就绪,无需新装)

| 包 | 状态 |
|---|---|
| `fastapi 0.137.1` | 已装 |
| `uvicorn 0.49.0` | 已装 |
| `pydantic 2.13.4` | 已装 |
| `node v23.10.0` / `npm 10.9.2` | 已装 |
| `react` / `typescript` / `vite` / `recharts` / `vitest` 等 | 构建期由 T12 通过已放行的 `npm install` 装入 `frontend/`。**完整依赖清单与版本见 ADR-037b** —— 原文只列了 5 个包,漏掉 `@vitejs/plugin-react`、`@types/react`、`@types/react-dom`、`jsdom`、`@testing-library/*`,而 T12/T13 的门都需要它们 |
| `pyflakes` | **未安装**,且 `pip install` 被拒绝。`LINT_CMD` 已改为标准库的 `compileall`(见 §5) |

> **权限核对(已实测)**:`npm install/ci/run/test/pkg`、`npx tsc/vitest/vite`、`node`、`uvicorn`、`python3`、`git`、`gh`、`mkdir` 均**已在允许列表中**。原文称「当前 `.claude/settings.json` 未允许 npm」—— 那是**错的**,已更正。
>
> 真正缺的是脚手架命令:`npm create vite` / `npx create-vite` **不匹配**任何现有规则(`Bash(npx vite*)` 的前缀不覆盖 `npx create-vite`)。ADR-037b 因此规定 T12 **手写**配置文件而非跑脚手架命令 —— 这同时让依赖集可复现。

---

## 7 · 构建期纪律

1. 加性、向后兼容。**但功能开关不得绕过任何 ADR 决策** —— ADR 锁定的行为(响亮失败、无声跳过、无条件执行)不得加 kill-switch 旁路。
2. **不得编造理由**。docstring/注释中引用的任何「项目规则」「约定」必须真实存在于本文件或 ADR 中。**测试名与注释中引用的「产品 case」编号同理** —— 本文件不含 B1/B2 之类的编号,不得引用。
3. **「已核实」只能用于账本记录了观测的地方**(一个实测数字、一句引用的源码)。否则写「推断」或「设计决策」。参考实现已从环境卸载且网络访问被拒,构建期**无法复核**任何关于它的陈述 —— 故所有此类陈述均为设计依据,不是可验证事实。
4. 每次交付报告 PR head commit hash;每次合并报告 trunk hash。
5. 实现时引用 ADR 编号。任务与 ADR 冲突 → 停止该节点并上报,不得自行发明架构。
6. 遵守 OQ(「按 OQ-03,不支持纯序号日期」)。
7. 返工追加 commit,不重写历史、不 `reset --hard`。
8. **ADR-035/036/037/037b/038/039 是名字契约,构建期不得变更。** 模块路径、类/方法名、文件名、CSV 列名、JSON 字段名一旦偏离,下游任务与阶段二全部断裂。要改必须停下上报。
