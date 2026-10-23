# Pico W：有界连续双通道采集

版本`continuous-input-v1`，使用原三线，GP12仅输出一次低／高阻启动脉冲，之后只采集；GP13始终输入。无线关闭，系统时钟标称125 MHz，PIO0 SM0用一条`in pins, 2`采样GP12/13，整数分频125，标称1 MS/s。未作外部时基校准。

4个DMA通道接力，每块4096个32位字，包含65536个双通道样本，持续65.536 ms；总缓冲64 KiB。每个通道使用16 KiB写地址环，防止通道重触发后越过自己的缓冲区。DMA链触发会自动重载传输计数，不能假设计数归零会阻止覆盖。

块具有`ARMED → BUSY → READY → SENDING → ARMED`所有权；任何未导出块被再次使用都使整次运行无效。正常结束也检查下一块所有权。发生覆盖时，部分已产生的块可能受到影响，不能以CRC正确为理由保留其时间统计。

## 接线与协议

- GP12 ↔ DK9577 P1.03；GP13 ↔ DK P1.04；GND ↔ GND。电源输出不互连。
- `I`查询身份和方向；`R run blocks hold_ms`执行4–1850块有限采集，run非零且不能与上一轮相同。
- 启动采集约2 ms后GP12拉低约32 μs并释放。起点仍从采样的实际边沿取得。
- 首行`STREAM_BEGIN`为JSON；之后每块44字节头＋16384字节数据；最后`STREAM_END`和`STREAM_READY`。
- 头为11个小端u32：魔数两字`PSTRM001`、run、seq、起始样本低／高字、字节数、PIO FDEBUG、错误位、payload CRC32、前40字节CRC32。
- 样本为每u32低位优先16个两位状态，bit0=A，bit1=B。样本索引是原始整数，时间换算使用标称采样率；`sample_hz_estimated=null`。
- 错误位：顺序1，缓冲所有权／背压2，DMA4，超时8，主动终止16。END全局错误优先于早先块头。
- `hold_ms`仅用于有界故障注入：首块就绪后暂停前台导出，DMA继续运行。它不等于已测试真实USB主机背压。
- 运行中`Q`请求终止。传输发生协议异常时主机保留数据、发送Q并冻结DK，不自动重新烧录或重启。

## 构建

`python3 /Applications/biyesheji/firmware/pico_continuous_capture/build.py`

构建脚本固定本地Pico SDK及工具链并保存日志与镜像哈希。原5 MS/s有限窗口实现保留在`firmware/pico_polling_phase/`，不把本分支标为5 MS/s连续采集。
