#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=int, required=True); parser.add_argument('--edges', type=int, required=True)
    parser.add_argument('--period-ticks', type=int, required=True); parser.add_argument('--hold-ms', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--execute', action='store_true')
    parser.add_argument('--connection-record', type=Path); parser.add_argument('--dk-port')
    a = parser.parse_args(); p = core.plan(a.run, a.edges, a.period_ticks, a.hold_ms)
    if a.execute and (not a.connection_record or not a.dk_port): parser.error('execution requires verified connection and explicit DK console')
    a.output.mkdir(parents=True, exist_ok=False); core.pc.save_json(a.output / 'plan.json', p)
    core.pc.save_json(a.output / 'gates.json', core.GATES)
    if not a.execute: print(json.dumps(p)); return
    record = json.loads(a.connection_record.read_text()); core.pc.validate_connection(record)
    core.pc.save_json(a.output / 'connection_record.json', record)
    import serial
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    found = [v for v in ports if v.serial_number == core.pp.PICO_SERIAL and v.vid == 0x2e8a and v.pid == 10 and v.product == 'Pico_Async_Stream']
    if len(found) != 1: raise core.pp.ProtocolError('one Pico_Async_Stream required')
    dk = core.pc.find_port(ports, core.pp.DK_SERIAL, False, a.dk_port)
    core.pc.save_json(a.output / 'ports.json', dict(pico=found[0].device, dk=dk.device))
    with serial.Serial(found[0].device, 115200, timeout=4, write_timeout=3, exclusive=True) as ps, serial.Serial(dk.device, 115200, timeout=4, write_timeout=3, exclusive=True) as ds:
        result = core.session(ps, ds, p, a.output)
    print(json.dumps(result, ensure_ascii=False))
    if result['experiment_gate'] != 'PASS': raise SystemExit(1)


if __name__ == '__main__': main()
