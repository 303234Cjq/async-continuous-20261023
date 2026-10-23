#!/usr/bin/env python3
"""Independent wire re-parser and 32-bit XOR edge decoder. No core import."""
from array import array
from collections import Counter
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import zlib


def check(folder):
    p=json.loads((folder/'plan.json').read_text()); r=json.loads((folder/'result.json').read_text())
    blocksize=16384; nsamples=65536; chunks=[]; flags=[]
    with (folder/'stream_wire.bin').open('rb') as f:
        hline=f.readline(); assert hline.startswith(b'STREAM_BEGIN ')
        h=json.loads(hline.split(b' ',1)[1]); assert h['run']==p['run'] and h['sample_hz_nominal']==1000000
        assert h['version']=='continuous-input-v1' and h['blocks']==p['blocks'] and h['bytes_per_block']==blocksize
        assert h['samples_per_block']==nsamples and h['sys_hz']==125000000 and h['clkdiv']==125
        assert h['instructions_per_sample']==1 and h['input_base']==12 and h['sample_hz_estimated'] is None
        assert h['A_drive']=='low_or_high_z_start_only' and h['B_input_only'] is True and h['hold_ms']==p['hold_ms']
        for seq in range(p['blocks']+1):
            prefix=f.read(8)
            if prefix==b'\nSTREAM_':
                tline=prefix[1:]+f.readline(); assert tline.startswith(b'STREAM_END ')
                t=json.loads(tline.split(b' ',1)[1]); assert t['run']==p['run']
                break
            assert prefix==b'PSTRM001'
            hb=prefix+f.read(36);v=struct.unpack('<11I',hb)
            assert zlib.crc32(hb[:40])==v[10] and v[2]==p['run'] and v[3]==seq
            assert v[4]+(v[5]<<32)==seq*nsamples and v[6]==blocksize
            chunk=f.read(blocksize);assert len(chunk)==blocksize and zlib.crc32(chunk)==v[9]
            flags.append(v[7]|v[8]);chunks.append(chunk)
        else: raise AssertionError('no end')
        ready=f.readline();assert b'A_released=1' in ready and b'B_input=1' in ready
        assert not f.read()
    raw=b''.join(chunks);assert raw==(folder/'capture.bin').read_bytes()
    terminal=json.loads((folder/'source_end.json').read_text()); arm=json.loads((folder/'armed.json').read_text())
    complete=(len(chunks)==p['blocks'] and t['produced']==p['blocks'] and t['exported']==p['blocks'] and
              not t['errors'] and not t['fdebug'] and not any(flags) and t['A_released'] and t['B_input_only'])
    if not p['expected_valid']:
        assert not complete and t['errors']==2 and t['fdebug']==0 and r['metrics'] is None and r['gate']=='FAIL' and r['experiment_gate']=='PASS'
        assert t['produced']==t['exported']==len(chunks)<p['blocks'] and t['A_released'] and t['B_input_only']
        assert not any(terminal[k] for k in ('errors','uart_dropped','cmd_overflow'))
        return dict(run=p['run'],verified='EXPECTED_CAPTURE_INVALID',packets=len(chunks),edges_accepted=0,
                    terminal_errors=t['errors'],raw_sha256=hashlib.sha256(raw).hexdigest())
    assert complete
    assert terminal['edge_count']==p['edges'] and terminal['input_count']==1 and terminal['complete']==1
    assert terminal['armed']==0 and terminal['marker_level']==0 and terminal['hf_high_accuracy']==1
    assert arm['edge_count']==arm['input_count']==0 and arm['armed']==1
    for d in (arm,terminal):
        assert d['run']==p['run'] and d['version']=='async-source-v2' and d['requested_edges']==p['edges']
        assert d['period_ticks']==p['period_ticks'] and not any(d[k] for k in ('errors','uart_dropped','cmd_overflow'))
    a=[]; b=array('Q'); previous=raw[0]&3
    for wi,(word,) in enumerate(struct.iter_unpack('<I',raw)):
        different=(word^((word<<2)|previous))&0xffffffff
        amask=different&0x55555555; bmask=different&0xaaaaaaaa
        while amask:
            bit=amask&-amask;j=(bit.bit_length()-1)//2
            a.append((wi*16+j,(word>>(2*j))&1));amask-=bit
        while bmask:
            bit=bmask&-bmask;j=(bit.bit_length()-1)//2;b.append(wi*16+j);bmask-=bit
        previous=(word>>30)&3
    assert raw[0]&3==1 and previous==1 and len(a)==2 and [v for _,v in a]==[0,1]
    assert 1000<=a[0][0]<=10000 and 28<=a[1][0]-a[0][0]<=100
    assert len(b)==p['edges'] and b[0]>a[0][0]
    gaps=Counter(b[i]-b[i-1] for i in range(1,len(b))); nominal=p['period_ticks']/16
    assert min(gaps)>=math.floor(nominal)-2 and max(gaps)<=math.ceil(nominal)+2
    mean=(b[-1]-b[0])/(len(b)-1);assert abs((mean/nominal-1)*1e6)<=5000
    assert abs(((terminal['last_edge_tick']-terminal['first_counter_tick'])&0xffffffff)-(p['edges']-1)*p['period_ticks'])<=16
    assert ((terminal['stop_counter_tick']-terminal['last_edge_tick'])&0xffffffff)<=16
    if sys.byteorder!='little':b.byteswap()
    assert b.tobytes()==(folder/'edge_samples.u64le').read_bytes()
    m=r['metrics']; assert m['actual_B_transitions']==len(b) and m['mean_interval_samples']==mean
    assert m['period_gap_histogram']=={str(k):v for k,v in sorted(gaps.items())}
    assert r['gate']==r['experiment_gate']=='PASS'
    return dict(run=p['run'],verified='PASS',packets=len(chunks),edges_accepted=len(b),
                raw_sha256=hashlib.sha256(raw).hexdigest(),edge_sha256=hashlib.sha256(b.tobytes()).hexdigest())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folders',type=Path,nargs='+');parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args(); results=[]
    for path in a.folders:
        folders=[path] if (path/'plan.json').exists() else sorted(p for p in path.iterdir() if p.is_dir() and (p/'result.json').exists())
        for f in folders:
            result=check(f); results.append(result);print(json.dumps(result),flush=True)
    a.output.write_text(json.dumps(dict(gate='PASS',method='wire CRC reparse plus word XOR edge detection, no core imports',runs=len(results),
        accepted_hardware_edges=sum(r['edges_accepted'] for r in results),results=results),indent=2)+'\n')


if __name__=='__main__':main()
