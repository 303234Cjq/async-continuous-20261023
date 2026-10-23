#!/usr/bin/env python3
"""Rebuild an offline scientific report from saved acquisitions only."""
from array import array
from collections import Counter,defaultdict
import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/experiments/async_continuous_20261023'
DEV=ROOT/'research/runs/async_stream_20261023_development'


def load(p):return json.loads(p.read_text())
def relative(p):return os.path.relpath(p,OUT)


def main():
    OUT.mkdir(parents=True,exist_ok=True); rows=[]
    for phase,folder in [('short','async_stream_20261023_short_v1'),('fault','async_stream_20261023_fault_recovery_v1'),('long','async_stream_20261023_long_v1')]:
        base=ROOT/'research/runs'/folder; complete=load(base/'complete.json');assert complete['gate']=='PASS'
        certificate=load(DEV/(phase+'_independent_final.json'));assert certificate['gate']=='PASS'
        certificates={r['run']:r for r in certificate['results']}
        for folder in sorted(p for p in base.iterdir() if p.is_dir() and (p/'result.json').exists()):
            p=load(folder/'plan.json');r=load(folder/'result.json');t=load(folder/'tail.json');source=load(folder/'source_end.json')
            cert=certificates[p['run']];assert r['experiment_gate']=='PASS'
            rows.append(dict(phase=phase,plan=p,result=r,tail=t,source=source,independent=cert,folder=relative(folder)))
    valid=[r for r in rows if r['result']['gate']=='PASS']; faults=[r for r in rows if r['result']['gate']=='FAIL']
    summary=dict(formal_runs=len(rows),valid_runs=len(valid),injected_invalid_runs=len(faults),
        accepted_hardware_B_transitions=sum(r['result']['metrics']['actual_B_transitions'] for r in valid),
        long_runs=sum(r['phase']=='long' for r in rows),long_source_seconds_nominal=60,
        nominal_sample_rate_hz=1000000,sample_rate_estimated=None,
        scope='Independent-clock wired pulse train and continuous capture, not wireless latency',
        built=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    assert summary['formal_runs']==24 and summary['valid_runs']==21 and len(faults)==3
    failed=ROOT/'research/runs/async_stream_20261023_smoke_1k_v1'
    diagnostic=load(failed/'failure_diagnostic.json')
    result=dict(summary=summary,rows=rows,failed_smoke=dict(folder=relative(failed),result=load(failed/'result.json'),diagnostic=diagnostic),
        passed_smoke=dict(folder=relative(ROOT/'research/runs/async_stream_20261023_smoke_1k_v2'),result=load(ROOT/'research/runs/async_stream_20261023_smoke_1k_v2/result.json')))
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none'})
    fig,axes=plt.subplots(1,2,figsize=(11,3.8),constrained_layout=True)
    before={int(k):v for k,v in diagnostic['gaps'].items()}; after=result['passed_smoke']['result']['metrics']['period_gap_histogram']
    axes[0].bar(before.keys(),before.values(),color='#d46a45');axes[0].axvline(1000,color='#183944',ls='--',label='nominal 1000')
    axes[0].set(title='v1: HFXO not requested; failed period gate',xlabel='Interval (Pico samples)',ylabel='Intervals');axes[0].legend()
    axes[1].bar([int(k) for k in after],list(after.values()),color='#087f83')
    axes[1].set(title='v2: HFXO requested and verified',xlabel='Interval (Pico samples)',ylabel='Intervals')
    for ext in ('svg','png'):fig.savefig(OUT/('clock_source.'+ext),dpi=160)
    plt.close(fig)
    hist=Counter()
    for r in rows:
        if r['plan']['label']=='off_grid':hist.update({int(k):v for k,v in r['result']['metrics']['period_gap_histogram'].items()})
    fig,ax=plt.subplots(figsize=(7.5,3.5),constrained_layout=True);positions=[0,1]
    measured=[hist[k]/sum(hist.values())*100 for k in (125,126)]
    ax.bar([v-.18 for v in positions],measured,width=.34,label='captured',color='#087f83')
    ax.bar([v+.18 for v in positions],[31.25,68.75],width=.34,label='ideal quantization proportion',color='#a8c4c3')
    ax.set(xticks=positions,xticklabels=['125 samples','126 samples'],ylabel='Intervals (%)',title='Off-grid source: 2011 / 16 = 125.6875 samples nominal');ax.legend()
    for ext in ('svg','png'):fig.savefig(OUT/('off_grid.'+ext),dpi=160)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,3.8),constrained_layout=True)
    for r in rows:
        if r['phase']!='long':continue
        folder=OUT/r['folder'];edges=array('Q');edges.frombytes((folder/'edge_samples.u64le').read_bytes())
        if sys.byteorder!='little':edges.byteswap()
        indices=list(range(0,len(edges),800));indices.append(len(edges)-1)
        axes[0].plot([i/8000 for i in indices],[edges[i]-edges[0]-125*i for i in indices],label=f"batch {r['plan']['repeat']+1}",lw=1.3)
        gh=r['result']['metrics']['period_gap_histogram'];axes[1].plot([int(k) for k in gh],[v/(len(edges)-1)*100 for v in gh.values()],marker='o',label=f"batch {r['plan']['repeat']+1}")
    axes[0].set(xlabel='Source time (s, nominal)',ylabel='Cumulative residual (Pico samples)',title='Relative clock drift; no absolute calibration');axes[0].legend()
    axes[1].set(xlabel='Interval (Pico samples)',ylabel='Intervals (%)',title='8k: three 60 s source runs');axes[1].legend()
    for ext in ('svg','png'):fig.savefig(OUT/('continuous_8k.'+ext),dpi=160)
    plt.close(fig)
    table='\n'.join('<tr data-phase="'+r['phase']+'"><td>'+str(r['plan']['run'])+'</td><td>'+html.escape(r['plan']['label'])+'</td><td>'+str(r['plan']['repeat']+1)+'</td><td>'+('完整' if r['result']['gate']=='PASS' else '按预期拒收')+'</td><td>'+str(r['result']['metrics']['actual_B_transitions'] if r['result']['metrics'] else '不接受')+'</td><td>'+('%.9f'%r['result']['metrics']['mean_interval_samples'] if r['result']['metrics'] else '—')+'</td><td><a href="'+r['folder']+'/result.json">判定</a> · <a href="'+r['folder']+'/stream_wire.bin">原始流</a> · <a href="'+r['folder']+'/source_serial.txt">DK日志</a></td></tr>' for r in rows)
    faulttable=''.join(f"<tr><td>{r['plan']['run']}</td><td>{r['tail']['produced']}/{r['tail']['requested_blocks']}</td><td>{r['tail']['errors']}</td><td>{r['source']['edge_count']}</td><td>整次无效；metrics=null</td></tr>" for r in faults)
    text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>独立时钟与连续采集实测</title>
<style>:root{color-scheme:light;--ink:#173440;--teal:#087f83}*{box-sizing:border-box}body{font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:var(--ink);background:#f1f5f5;margin:0}main{max-width:1160px;margin:auto;padding:36px 24px 70px}h1{font-size:34px;line-height:1.3;margin:12px 0}h2{font-size:23px;margin:0 0 14px}h3{font-size:18px}a{color:#006b83}p{margin:10px 0}.label{letter-spacing:.12em;color:#527c7c;font-size:13px}.lead{max-width:920px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}.card,section{background:white;border:1px solid #dce6e6;border-radius:15px;padding:23px}.card strong{display:block;font-size:29px;color:var(--teal)}.card span{font-size:14px}section{margin-top:20px}.note{border-left:4px solid #c18135;background:#fff8ec;padding:13px 18px;margin:16px 0}.ok{border-left-color:var(--teal);background:#eaf7f3}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px 12px;border-bottom:1px solid #e4ecec;text-align:left;white-space:nowrap}th{background:#edf4f4}img{max-width:100%;height:auto}.diagram{font:15px/1.8 ui-monospace,monospace;white-space:pre-wrap;background:#edf5f5;border-radius:10px;padding:18px}select{padding:8px;border:1px solid #aac0c1;border-radius:7px;font:inherit}code{font-size:.92em}small{color:#52717b}li{margin:7px 0}@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}h1{font-size:28px}main{padding:24px 14px}}</style>
<main><div class="label">方向五 · 2026-10-23 · 有线方法验证</div><h1>从有限窗口，推进到独立时钟连续采集</h1>
<p class="lead">沿用Pico W＋DK9577及原三线：一次启动之后，DK用自己的硬件时钟输出边沿列，Pico连续记录并在采集期间导出。正式24次实验均达到各自预期：21次完整采集，3次故意耗尽缓冲后严格拒收。</p>
<div class="cards"><div class="card"><strong>1,533,000</strong><span>正式有效记录中的物理B边沿</span></div><div class="card"><strong>3 × 60 s</strong><span>8k边沿源长批次，逐批独立核对</span></div><div class="card"><strong>3 / 3</strong><span>缓冲故障拒收，随后新试验恢复</span></div><div class="card"><strong>1 MS/s</strong><span>标称采样率；实际校准值仍为null</span></div></div>
<div class="note">本轮是8000次GPIO翻转／秒的有线源与连续采集验证。方波基频为4 kHz。它没有运行ESB，不证明无线8k收发、USB报告率或端到端鼠标延迟。1 μs采样网格不等于1 μs绝对准确度。</div>
<section><h2>原理与固定接线</h2><div class="diagram">Pico GP12 ─── DK P1.03：一次低／高阻启动，之后Pico只采集
Pico GP13 ─── DK P1.04：DK TIMER20 → DPPI → GPIO翻转
Pico GND  ─── DK GND：共地；电源输出不互连

DK边沿硬件计数达到N → 硬件停止源 → 一次CPU完成通知
Pico同一条 in pins, 2 → 四块DMA环形缓冲 → 编号＋CRC → USB CDC
                       任一块连续性失效 → 整次无效</div>
<p>两个振荡器独立运行；共同启动不等于共享时钟。每块65536个样本、16384字节，四块共64 KiB。块序号与原始样本索引跨块延续。结束状态、源硬件计数和独立重解析共同核对，不靠“看起来连续”的图像放行。</p>
<p>标称换算：<code>f_s = 125 MHz / (125 × 1) = 1 MS/s</code>，<code>Δt = Δn / f_s</code>。没有外部参考，不能用DK与Pico的相互比较声称绝对校准；全部保存整数样本索引，估计采样率为null。</p></section>
<section><h2>先发现问题，再修正时钟配置</h2><p>v1冒烟的1000个边沿全部被计到，协议与数据块完整，但周期为996–998个采样点，未通过原定周期判据。v2增加HFXO请求、等待与状态确认；启动诊断记录请求前为0、请求后为1，随后同条件1k测试通过。没有为通过试验放宽周期门槛。</p><img src="clock_source.svg" alt="时钟源修正前后的间隔分布"><p><small>v1失败与v2冒烟均不计入正式24批。该修正只针对本分支，不据此重新解释此前无线丢包或旧模型的所有偏差。</small></p></section>
<section><h2>持续采集与非整数周期验证</h2><p>1k、2k、4k、8k、2011 ticks非整数周期各3批。后者标称125.6875 μs、约7956.24次翻转／秒，故采样间隔应主要落在125与126两个格点。它用于检查量化与跨块提取，不作为8k整频工况。</p><img src="off_grid.svg" alt="非整数采样周期分布"><p>8k长测每批源输出480000次翻转；首次到末次包含479999个间隔。每批连续采集60.555264标称秒并导出924个数据块。下面残差混合两个独立时基的相对频偏与采样量化，不是经校准的DUT真实抖动。左图按每800边沿取点，完整性与统计仍使用全量数据。</p><img src="continuous_8k.svg" alt="三批8k连续采集及相对时钟残差"></section>
<section><h2>实物故障注入与下一轮恢复</h2><p>首块就绪后，让Pico前台导出暂停500 ms；DMA继续运行，实际触发缓冲所有权错误位2。三次均在4块之后结束并拒收整次记录，先前已发出的块也不用于时间统计。紧接着用新run ID重新ARM，三次均成功采到8000个边沿；同一串口会话内未重刷、未发送重启命令。</p><div class="scroll"><table><thead><tr><th>故障run</th><th>导出块／计划块</th><th>错误位</th><th>冻结时源计数</th><th>处理</th></tr></thead><tbody>FAULTTABLE</tbody></table></div><div class="note">这是真实的固件导出暂停与缓冲耗尽，不是直接拔USB或真实主机限速实验。故障窗口的源可能继续输出至主机freeze，故源终计数与已采块中的边沿数不能混为同一个量。</div></section>
<section><h2>24次正式记录，可逐项追溯</h2><label>查看 <select id="phase"><option value="all">全部24次</option><option value="short">短测15次</option><option value="fault">故障与恢复6次</option><option value="long">长测3次</option></select></label><p id="visibleCount"></p><div class="scroll"><table id="runs"><thead><tr><th>run</th><th>工况</th><th>重复</th><th>采集判定</th><th>接受B边沿</th><th>平均间隔／样本</th><th>证据</th></tr></thead><tbody>TABLE</tbody></table></div><p>独立核对重新读取原始wire、逐块核验CRC，再用不同的32位XOR方法提取边沿；有效数据的整数边沿序列逐项与主分析器一致，故障记录保持无效。离线故障视图不冒充新增硬件试验。</p></section>
<section><h2>本轮得到的研究增量与下一关</h2><ul><li><b>完整性可验证：</b>连续原始索引、块所有权、源硬件计数与全局终态一起判断，故障时整次拒收。</li><li><b>恢复有实证：</b>真实耗尽后在下一run恢复，三次通过；不是只展示理想条件的均值改善。</li><li><b>时钟条件可审计：</b>发现“配置为16 MHz”不足以证明晶振已启用，并通过状态登记与固定门槛验证修正。</li></ul><p>这些是可写进毕设的方法与工程增量，尚不能据此声称首创、专利新颖性或任意负载下不丢样。下一关仍是外部参考核验，以及取得第二个兼容节点后把有线源换成带事件身份的无线输入链路。</p></section>
<section><h2>复现、原始资料与当前留板</h2><p><a href="results.json">汇总数据</a> · <a href="README.md">技术报告与复算命令</a> · <a href="../../../software/async_stream/README.md">主机协议</a> · <a href="../../../firmware/pico_continuous_capture/README.md">Pico实现</a> · <a href="../../../firmware/single_dk_async_source/README.md">DK实现</a> · <a href="../../../research/runs/async_stream_20261023_development/closeout.json">当前交接状态</a> · <a href="../polling_phase_20261023/index.html">上一阶段冻结报告</a></p><p>留板为Pico <code>continuous-input-v1</code>、DK <code>async-source-v2</code>；原三线保留，最终状态另存交接记录。桌面原固件备份未改动，DK2006未参与。</p><p><small>机制依据：<a href="https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf">RP2040 datasheet：PIO／DMA、链触发和地址环</a>；<a href="https://docs.nordicsemi.com/r/bundle/ps_nrf54lm20a/page/clock.html-concept_rp5_fgp_bs">Nordic HFCLK说明</a>，以及本机NCS 3.3.1时钟驱动。测量结论只来自本页链接的本地记录。</small></p></section>
<p><small>报告由已保存数据离线生成：BUILDTIME。此页面不控制开发板。</small></p></main><script>const select=document.getElementById('phase');function filter(){let n=0;for(const row of document.querySelectorAll('#runs tbody tr')){row.hidden=select.value!=='all'&&row.dataset.phase!==select.value;if(!row.hidden)n++;}document.getElementById('visibleCount').textContent='显示 '+n+' 次记录';}select.addEventListener('change',filter);filter();</script></html>'''
    text=text.replace('FAULTTABLE',faulttable).replace('TABLE',table).replace('BUILDTIME',html.escape(summary['built']))
    (OUT/'index.html').write_text(text)
    longrows=[r for r in rows if r['phase']=='long']
    md=f'''# 独立时钟输入、连续采集与故障恢复：2026-10-23

正式24批：21批有效采集，共{summary['accepted_hardware_B_transitions']:,}个B翻转边沿；3批故意耗尽缓冲并整次拒收。有效采集和故障拒收均经独立wire重解析核对。另存v1失败冒烟、v2通过冒烟和上一版基线，不混入正式分母。

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
'''+''.join(f"| {r['plan']['run']} | {r['result']['metrics']['mean_interval_samples']:.9f} | {r['result']['metrics']['relative_clock_difference_ppm']:.6f} | 924 |\n" for r in longrows)+'''
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
- [短测原始目录](../../../research/runs/async_stream_20261023_short_v1/)、[故障与恢复](../../../research/runs/async_stream_20261023_fault_recovery_v1/)、[长测](../../../research/runs/async_stream_20261023_long_v1/)。
- [失败冒烟](../../../research/runs/async_stream_20261023_smoke_1k_v1/)、[修正后冒烟](../../../research/runs/async_stream_20261023_smoke_1k_v2/)。
- [构建/上板/源码快照/核对/交接](../../../research/runs/async_stream_20261023_development/)。原桌面固件备份未动，DK2006不参与。
- 机制参考：[RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)、[Nordic HFCLK](https://docs.nordicsemi.com/r/bundle/ps_nrf54lm20a/page/clock.html-concept_rp5_fgp_bs)及本机NCS时钟驱动；实测证据以本地运行记录为准。

当前留板与待机查询见开发目录`closeout.json`及`final_device_log.json`。未来仍需外部参考核验；待第二兼容节点可用后再做完整无线双端观测与事件身份核对。
'''
    (OUT/'README.md').write_text(md)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
