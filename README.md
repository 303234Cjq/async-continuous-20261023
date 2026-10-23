# 独立时钟输入、连续采集与故障恢复：2026-10-23

正式24批：21批有效采集，共1,533,000个B翻转边沿；3批故意耗尽缓冲并整次拒收。有效采集和故障拒收均经独立wire重解析核对。另存v1失败冒烟、v2通过冒烟和上一版基线，不混入正式分母。

## 本轮实际推进

从5 MS/s有限窗口分支新增1 MS/s连续采集分支。原三线不变，Pico一次启动后只采样，DK自身16 MHz标称时基驱动TIMER→DPPI→GPIO，并由硬件计数到N自停。两个时钟独立，没有跨MCU直接相减时间戳。

源v1未请求HFXO，1k间隔996–998样本，违反原定周期门槛；所有1000个边沿及数据块完整，失败保留。v2请求并确认HFXO，启动记录`hf_before_request=0 / hf_high_accuracy=1`。同类1k冒烟平均1000.001001样本并通过。它不溯源解释旧无线丢包。

## 正式矩阵

| 组 | 运行数 | 条件 | 结果 |
|---|---:|---|---|
| 短测 | 15 | 1k/2k/4k/8k/2011 ticks，各3批 | 全通过，69000边沿 |
| 缓冲故障 | 3 | 首块后前台暂停导出500 ms | 错误位2，导出4/23块，整次无效 |
| 下一run恢复 | 3 | 故障后新ID重新ARM，不刷写/不发重启命令 | 全通过，24000边沿 |
| 长测 | 3 | 8k×60标称秒，480000翻转/批 | 全通过，共1440000边沿 |

长测每批924块、60555264个双通道样本、15138816字节原始payload，连续60.555264标称秒。首次到末次B边沿含479999个间隔。8k翻转对应4 kHz方波；不是8k完整方波周期。

| 长测run | 平均间隔/样本 | 与标称125的相对差/ppm | 数据块 |
|---|---:|---:|---:|
| 2026108400 | 125.000029167 | 0.233334 | 924 |
| 2026108401 | 125.000029167 | 0.233334 | 924 |
| 2026108402 | 125.000029167 | 0.233334 | 924 |

## 预置门槛与独立核对

`async-gates-v1`在首个候选上板前冻结，v1失败后没有放宽周期门槛：源数与采集数一致、块完整、A单个下降/上升、名义A位置1000–10000样本、宽28–100样本、周期在floor/ceil名义值±2样本、平均相对配置差≤5000 ppm，源锚点残差≤16 DK ticks。这是功能与配置一致性门槛，不是校准精度声明。

独立检查器不导入主分析器，重读wire头、块CRC及最终状态，再用32位XOR检测边沿，与主分析的u64整数索引逐项相等。三个独立检查文件在开发证据目录。软件测试覆盖截断、CRC损坏、重排、计数不符、物理边沿缺失及全局错误时禁止输出指标。

## 可研究的改进与边界

1. 以连续样本索引、DMA块所有权、CRC、源计数及终态构成整次有效性约束；覆盖风险出现时整次拒收，而不是选看似正常的片段。
2. 实物触发缓冲耗尽，随后新epoch无需刷写/发送重启命令恢复；三组对照均通过。这里注入的是固件前台导出暂停，不是已复现USB主机背压。
3. 非整数周期2011/16=125.6875样本，约7956.24翻转/s，检验125/126格点的量化行为，不将采样量化当成源真实抖动。

当前测量采样率只有标称1 MS/s，`sample_hz_estimated=null`；两个板相互比较只提供相对时基一致性，不能给出绝对准确度。没有ESB报文、USB HID报告或成品鼠标。旧5 MS/s有限窗口与随机输入报告模型仍独立保留。方法增量不等于已证明新颖性或专利性。

## 复算和证据

```sh
/opt/nordic/ncs/toolchains/0c0f19d91c/bin/python3 /Applications/biyesheji/software/async_stream/independent_check.py /Applications/biyesheji/research/runs/async_stream_20261023_long_v1 --output /tmp/async_long_recheck.json
PYTHONPATH=/tmp/biyesheji_pico_plotdeps /opt/nordic/ncs/toolchains/0c0f19d91c/bin/python3 /Applications/biyesheji/software/async_stream/report.py
```

- [交互报告](index.html)、[机器可读汇总](results.json)。
- [短测原始目录](evidence/research/runs/async_stream_20261023_short_v1/index.html)、[故障与恢复](evidence/research/runs/async_stream_20261023_fault_recovery_v1/index.html)、[长测](evidence/research/runs/async_stream_20261023_long_v1/index.html)。
- [失败冒烟](evidence/research/runs/async_stream_20261023_smoke_1k_v1/index.html)、[修正后冒烟](evidence/research/runs/async_stream_20261023_smoke_1k_v2/index.html)。
- [构建/上板/源码快照/核对/交接](evidence/research/runs/async_stream_20261023_development/index.html)。原桌面固件备份未动，DK2006不参与。
- 机制参考：[RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)、[Nordic HFCLK](https://docs.nordicsemi.com/r/bundle/ps_nrf54lm20a/page/clock.html-concept_rp5_fgp_bs)及本机NCS时钟驱动；实测证据以本地运行记录为准。

当前留板与待机查询见开发目录`closeout.json`及`final_device_log.json`。未来仍需外部参考核验；待第二兼容节点可用后再做完整无线双端观测与事件身份核对。
