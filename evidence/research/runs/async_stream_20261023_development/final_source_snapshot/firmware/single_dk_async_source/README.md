# DK9577：独立时钟、硬件自停的有限边沿源

`async-source-v2`不是无线收发固件，也不是逐输入响应固件。它通过一次P1.03下降沿启动，随后TIMER20按DK自身时钟产生P1.04翻转列。Pico只接收，不为每个边沿提供节拍。

v1未请求HFXO；1k冒烟虽有1000个完整边沿，但间隔落在996–998个Pico样本，未通过事先的周期判据。失败数据保留。v2通过Zephyr时钟on/off管理器请求并保持HFXO，等待就绪后才初始化试验；状态同时输出请求前和当前的高精度时钟状态。它解决此分支的时钟配置问题，不构成对旧无线丢包或旧报告模型偏差的溯源结论。

## 硬件路径

1. P1.03 GPIOTE → DPPI → TIMER20 CLEAR/START、TIMER21 CAPTURE0、TIMER22 COUNT。
2. TIMER20 COMPARE0（标称16 MHz，比较后清零）→ DPPI → P1.04 OUT翻转、TIMER23 COUNT、TIMER21 CAPTURE1。
3. TIMER23首次计数 → TIMER21 CAPTURE3；第N次计数 → TIMER20 STOP、TIMER21 CAPTURE2。
4. 第N次计数仅通知CPU一次，禁用路由，输出完成状态。正常边沿不逐次进入ISR。

TIMER22/23读计数使用CC4，避免覆盖用于终止／首次计数的CC0/CC1阈值。时间锚点是外设内部事件，不替代物理GPIO观测。首次计数与最后源事件路径不同，因此锚点比较保留固定路径容差。

## 控制

- `status`：身份、时钟源、状态、计数及硬件时间锚点。
- `arm run N period_ticks`：run非零且不同于上一轮；N为2–960000之间偶数；周期16000/8000/4000/2000/2011 ticks，N×周期不超过1920000000 ticks。先置P1.04低并清计数，等启动下降沿。
- `freeze`：停止源并解除启动路由。中途冻结的源不能算作完整的N事件运行。
- 默认不发射无线，不修改PMIC/UICR，不需要网络。仅使用应用固件范围刷写及读回校验。

周期2000 ticks对应标称8000次翻转/秒，方波基频4000 Hz；2011 ticks对应约7956.24次翻转/秒，专用于非整数采样周期验证。两者均不等价于ESB业务报告率。

构建：`python3 /Applications/biyesheji/firmware/single_dk_async_source/build.py`。
