# 独立时钟输入与连续采集验证

与`polling_phase`分开：本实验只用一次启动输入，随后观察DK硬件边沿列。实际采样、块编号、CRC、源硬件计数和结束状态一起决定完整性；只要任一失败，整次记录的`metrics`为null。

`core.py`实现严格协议、分块采集、原始整数边沿提取和判据；`collect.py`默认为只生成离线计划，显式`--execute`才访问串口；`matrix.py`是有界正式实验；`independent_check.py`从原始wire重新验证CRC，并用不同的32位XOR算法提取边沿，不导入主分析器。

## 复现入口

先确认设备已运行本分支的两个精确应用版本和既有三线，不能把这些命令用于未知固件。原接线记录中的旧应用版本属于历史接线确认，运行身份以本次READY／ARMED／END为准；未来实验使用本分支交接中的更新记录。

离线计划（不访问设备）：

```sh
/opt/nordic/ncs/toolchains/0c0f19d91c/bin/python3 /Applications/biyesheji/software/async_stream/collect.py --run 2026109000 --edges 8000 --period-ticks 2000 --output /Applications/biyesheji/research/runs/async_example_plan
```

正式实验需在上面命令增加`--execute --connection-record <已核实记录> --dk-port <经序列号核实的应用控制串口>`。输出目录必须不存在。不同运行使用新run ID，命令中没有烧录、recover或重启功能。矩阵三阶段为`short`、`fault`、`long`，每阶段遇意外失败即停。

软件验证：

```sh
/opt/nordic/ncs/toolchains/0c0f19d91c/bin/python3 -m unittest discover -s /Applications/biyesheji/software/async_stream -p 'test_*.py' -v
/opt/nordic/ncs/toolchains/0c0f19d91c/bin/python3 /Applications/biyesheji/software/async_stream/independent_check.py <实验目录> --output <新核对文件.json>
```

`report.py`只从已保存的实测及独立核对结果生成离线报告，不访问板卡。名义时间不构成绝对准确度校准，1 μs采样网格也不能证明信号本身具有0或1 μs抖动。
