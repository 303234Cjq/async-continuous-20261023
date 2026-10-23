#!/usr/bin/env python3
"""Frozen finite campaigns; no flash/reset commands and stop on unexpected result."""
import argparse
import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import core


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=('short','fault','long'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--connection-record',type=Path,required=True)
    parser.add_argument('--dk-port',required=True)
    a=parser.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
    if a.phase=='short':
        cases=[dict(period_ticks=t,edges=n,hold_ms=0,label=label,repeat=r) for r in range(3)
            for t,n,label in ((16000,1000,'1k'),(8000,2000,'2k'),(4000,4000,'4k'),(2000,8000,'8k'),(2011,8000,'off_grid'))]
        start=2026108200
    elif a.phase=='fault':
        cases=[dict(period_ticks=2000,edges=8000,hold_ms=h,label='hold_fault' if h else 'recovery',repeat=r)
            for r in range(3) for h in (500,0)]; start=2026108300
    else:
        cases=[dict(period_ticks=2000,edges=480000,hold_ms=0,label='8k_60s',repeat=r) for r in range(3)]
        start=2026108400
    plans=[{**core.plan(start+i,c['edges'],c['period_ticks'],c['hold_ms']),**c} for i,c in enumerate(cases)]
    core.pc.save_json(a.output/'campaign_plan.json',dict(plans=plans,gates=core.GATES,
        phase=a.phase,created=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        no_flash_or_reset_commands=True,serial_connections_kept_open_between_runs=True))
    record=json.loads(a.connection_record.read_text()); core.pc.validate_connection(record)
    import serial
    from serial.tools import list_ports
    ports=list(list_ports.comports())
    ps=[p for p in ports if p.serial_number==core.pp.PICO_SERIAL and p.vid==0x2e8a and p.pid==10 and p.product=='Pico_Async_Stream']
    if len(ps)!=1:raise core.pp.ProtocolError('one identified Pico required')
    ds=core.pc.find_port(ports,core.pp.DK_SERIAL,False,a.dk_port)
    core.pc.save_json(a.output/'ports.json',dict(pico=ps[0].device,dk=ds.device))
    results=[]
    with serial.Serial(ps[0].device,115200,timeout=4,write_timeout=3,exclusive=True) as pico,serial.Serial(ds.device,115200,timeout=4,write_timeout=3,exclusive=True) as dk:
        for p in plans:
            folder=a.output/str(p['run']);folder.mkdir()
            core.pc.save_json(folder/'plan.json',p);core.pc.save_json(folder/'connection_record.json',record)
            print(json.dumps(dict(starting=p['run'],label=p['label'],repeat=p['repeat'])),flush=True)
            result=core.session(pico,dk,p,folder)
            row=dict(run=p['run'],label=p['label'],repeat=p['repeat'],experiment_gate=result['experiment_gate'],
                     capture_gate=result['gate'],metrics=result['metrics'])
            results.append(row);core.pc.save_json(a.output/'progress.json',dict(results=results))
            print(json.dumps(row),flush=True)
            if row['experiment_gate']!='PASS':raise RuntimeError('campaign stopped on failed gate')
    core.pc.save_json(a.output/'complete.json',dict(gate='PASS',runs=len(results),results=results,
        finished=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()))


if __name__=='__main__':main()
