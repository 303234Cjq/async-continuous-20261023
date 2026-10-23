"""Bounded, independent-clock pulse source and continuous-capture protocol.

No device discovery or firmware mutation. Invalid acquisition never yields timing
metrics. Nominal Pico and DK rates are configuration values, not calibration.
"""
from array import array
from collections import Counter
import json
import math
from pathlib import Path
import struct
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from software.external_dut import protocol as pp
from software.external_dut import collect as pc

FS, SAMPLES, BYTES = 1000000, 65536, 16384
PICO_VERSION, DK_VERSION = 'continuous-input-v1', 'async-source-v1'
MAGIC = b'PSTRM001'
GATES = {
    'version': 'async-gates-v1',
    'A_start_sample_inclusive': [1000, 10000], 'A_width_sample_inclusive': [28, 100],
    'period_gap_tolerance_samples': 2, 'relative_configuration_tolerance_ppm': 5000,
    'source_anchor_tolerance_ticks': 16,
    'capture_integrity': 'all blocks, CRCs, sequence, final flags and source counts must pass',
    'invalid_capture_policy': 'whole run invalid; no prefix timing metrics',
    'reference': 'none; not an absolute accuracy gate',
}


def validate_plan(p):
    for key in ('run', 'edges', 'period_ticks', 'blocks', 'hold_ms'):
        if type(p.get(key)) is not int: raise pp.ProtocolError('integer required: ' + key)
    if not 0 < p['run'] <= 0xffffffff or not 2 <= p['edges'] <= 960000 or p['edges'] % 2:
        raise pp.ProtocolError('invalid run or even edge count')
    if p['period_ticks'] not in (16000, 8000, 4000, 2000, 2011) or p['edges'] * p['period_ticks'] > 1920000000:
        raise pp.ProtocolError('unsupported source period or duration')
    if not 4 <= p['blocks'] <= 1850 or not 0 <= p['hold_ms'] <= 1000:
        raise pp.ProtocolError('invalid capture length or hold')
    if p['blocks'] * SAMPLES < (p['edges'] * p['period_ticks'] / 16 + 400000):
        raise pp.ProtocolError('insufficient capture margin')


def plan(run, edges, period_ticks, hold_ms=0):
    p = {'run': run, 'edges': edges, 'period_ticks': period_ticks,
         'blocks': max(4, math.ceil((edges * period_ticks / 16 + 500000) / SAMPLES)),
         'hold_ms': hold_ms, 'sample_hz_nominal': FS, 'sample_hz_estimated': None,
         'gate_version': GATES['version'], 'evidence_type': 'hardware_acquisition',
         'expected_valid': not bool(hold_ms)}
    validate_plan(p)
    return p


def ready(line):
    d = pp.fields(line, 'STREAM_READY')
    expected = dict(version=PICO_VERSION, idle=1, inputs='12,13', A_released=1,
                    A_latch_high=0, B_input=1, local_pullup=0, sample_hz=FS,
                    words_per_block=4096, buffers=4)
    if any(d.get(k) != v for k, v in expected.items()): raise pp.ProtocolError('Pico identity/direction guard failed')
    return d


def source_status(line, prefix, p=None, arm=False):
    d = pp.fields(line, prefix)
    expected = dict(version=DK_VERSION, input_pin='P1.03', marker_pin='P1.04', timer_hz=16000000)
    if p: expected.update(run=p['run'], requested_edges=p['edges'], period_ticks=p['period_ticks'])
    expected['armed'] = int(arm)
    if arm: expected.update(complete=0, input_count=0, edge_count=0, marker_level=0)
    if any(d.get(k) != v for k, v in expected.items()): raise pp.ProtocolError('DK identity/plan/state mismatch')
    return d


def validate_header(h, p):
    expected = dict(version=PICO_VERSION, run=p['run'], blocks=p['blocks'], bytes_per_block=BYTES,
                    samples_per_block=SAMPLES, sample_hz_nominal=FS, sample_hz_estimated=None,
                    sys_hz=125000000, clkdiv=125, instructions_per_sample=1, input_base=12,
                    A_drive='low_or_high_z_start_only', B_input_only=True, hold_ms=p['hold_ms'])
    if not isinstance(h, dict) or any(type(h.get(k)) is not type(v) or h.get(k) != v for k, v in expected.items()):
        raise pp.ProtocolError('continuous header contract mismatch')


def read_stream(link, p, folder):
    """Only CRC/protocol work during live export; timing analysis runs afterwards."""
    with (folder / 'stream_wire.bin').open('wb') as wire, (folder / 'capture.bin').open('wb') as raw, (folder / 'blocks.jsonl').open('w') as index:
        line = pp.read_line(link, wire)
        if not line.startswith(b'STREAM_BEGIN '): raise pp.ProtocolError('STREAM_BEGIN missing: ' + repr(line))
        h = json.loads(line.split(b' ', 1)[1]); validate_header(h, p)
        pc.save_json(folder / 'header.json', h)
        seq, packet_errors = 0, []
        while True:
            prefix = pp.read_exact(link, 8, wire)
            if prefix == b'\nSTREAM_':
                line = prefix[1:] + pp.read_line(link, wire)
                if not line.startswith(b'STREAM_END '): raise pp.ProtocolError('unexpected stream boundary')
                tail = json.loads(line.split(b' ', 1)[1]); break
            if prefix != MAGIC: raise pp.ProtocolError('packet magic or stream boundary mismatch')
            header = prefix + pp.read_exact(link, 36, wire)
            v = struct.unpack('<11I', header)
            if zlib.crc32(header[:40]) != v[10]: raise pp.ProtocolError('header CRC')
            start = v[4] + (v[5] << 32)
            if v[2] != p['run'] or v[3] != seq or start != seq * SAMPLES or v[6] != BYTES or seq >= p['blocks']:
                raise pp.ProtocolError('packet run/sequence/length/offset')
            payload = pp.read_exact(link, BYTES, wire)
            if zlib.crc32(payload) != v[9]: raise pp.ProtocolError('payload CRC')
            if v[7] or v[8]: packet_errors.append({'seq': seq, 'fdebug': v[7], 'errors': v[8]})
            raw.write(payload)
            index.write(json.dumps(dict(seq=seq, start_sample=start, bytes=v[6], fdebug=v[7], errors=v[8], crc32=v[9], header_crc32=v[10])) + '\n')
            seq += 1
        pc.save_json(folder / 'tail.json', tail)
        r = ready(pp.read_line(link, wire).decode('ascii').strip())
    result = dict(packets=seq, packet_errors=packet_errors, ready=r)
    pc.save_json(folder / 'transport.json', result)
    return h, tail, result


def integrity(h, t, transport, p, arm, end, raw_size):
    problems = []
    try: validate_header(h, p)
    except pp.ProtocolError as exc: problems.append(str(exc))
    for key, value in dict(run=p['run'], requested_blocks=p['blocks'], produced=p['blocks'], exported=p['blocks'],
                           errors=0, fdebug=0, A_released=True, B_input_only=True).items():
        if type(t.get(key)) is not type(value) or t.get(key) != value: problems.append('tail:' + key)
    if transport.get('packets') != p['blocks'] or transport.get('packet_errors') or raw_size != p['blocks'] * BYTES:
        problems.append('block_count_or_packet_flags')
    for label, d in (('arm', arm), ('end', end)):
        for key in ('errors', 'uart_dropped', 'cmd_overflow'):
            if d.get(key) != 0: problems.append(label + ':' + key)
    for key, value in dict(version=DK_VERSION, run=p['run'], armed=0, complete=1, input_count=1,
                           requested_edges=p['edges'], edge_count=p['edges'], period_ticks=p['period_ticks'], marker_level=0).items():
        if end.get(key) != value: problems.append('source:' + key)
    return problems


_STATES = [tuple((b >> (2 * j)) & 3 for j in range(4)) for b in range(256)]
_CHANGES = [tuple(j for j in range(1, 4) if s[j] != s[j - 1]) for s in _STATES]


def edge_scan(path):
    """Fixed memory raw scan; no huge list of every sample."""
    a, b = [], array('Q'); previous = None; offset = 0; initial = None
    with path.open('rb') as stream:
        while chunk := stream.read(BYTES):
            for byte in chunk:
                if (byte == 85 and previous == 1) or (byte == 255 and previous == 3):
                    offset += 4; continue
                states = _STATES[byte]
                if initial is None: initial = states[0]
                positions = ((0,) if previous is not None and states[0] != previous else ()) + _CHANGES[byte]
                for j in positions:
                    old = previous if j == 0 else states[j - 1]; new = states[j]
                    if (old ^ new) & 1: a.append((offset + j, new & 1))
                    if (old ^ new) & 2: b.append(offset + j)
                previous = states[-1]; offset += 4
    return a, b, initial, previous, offset


def analyze(folder, p, h, t, tr, arm, end):
    result = dict(run=p['run'], gate='FAIL', gates=GATES, integrity_errors=integrity(h, t, tr, p, arm, end, (folder / 'capture.bin').stat().st_size),
                  observation_errors=[], metrics=None, sample_hz_estimated=None)
    if result['integrity_errors']: return result
    a, b, initial, final, samples = edge_scan(folder / 'capture.bin'); errors = result['observation_errors']
    if initial != 1 or final != 1: errors.append('initial_or_final_A1B0')
    if len(a) != 2 or [v for _, v in a] != [0, 1]: errors.append('single_start_pulse_missing')
    elif not 1000 <= a[0][0] <= 10000 or not 28 <= a[1][0] - a[0][0] <= 100: errors.append('start_pulse_out_of_gate')
    if len(b) != p['edges']: errors.append('physical_edge_count')
    if a and b and b[0] <= a[0][0]: errors.append('source_before_start')
    gaps = Counter(b[i] - b[i - 1] for i in range(1, len(b)))
    nominal = p['period_ticks'] / 16
    if not gaps or min(gaps) < math.floor(nominal) - 2 or max(gaps) > math.ceil(nominal) + 2: errors.append('period_gap')
    average = (b[-1] - b[0]) / (len(b) - 1) if len(b) > 1 else None
    ppm = (average / nominal - 1) * 1e6 if average else None
    if ppm is None or abs(ppm) > 5000: errors.append('relative_configuration_consistency')
    span = (end['last_edge_tick'] - end['first_counter_tick']) & 0xffffffff
    source_span_residual = span - (p['edges'] - 1) * p['period_ticks']
    stop_delta = (end['stop_counter_tick'] - end['last_edge_tick']) & 0xffffffff
    if abs(source_span_residual) > 16 or stop_delta > 16: errors.append('source_hardware_anchor')
    if errors: return result
    with (folder / 'edge_samples.u64le').open('wb') as f:
        if sys.byteorder != 'little': b.byteswap()
        b.tofile(f)
    result.update(gate='PASS', metrics=dict(samples=samples, capture_seconds_nominal=samples / FS,
        actual_B_transitions=len(b), intervals=len(b)-1, A_falling_sample=a[0][0], A_rising_sample=a[1][0],
        first_B_sample=b[0], last_B_sample=b[-1], mean_interval_samples=average,
        period_gap_histogram={str(k): v for k, v in sorted(gaps.items())}, relative_clock_difference_ppm=ppm,
        source_span_residual_ticks=source_span_residual, source_stop_delay_ticks=stop_delta,
        rates_are_nominal=True, measurement_scope='independent-clock edge train; not wireless input latency'))
    return result


def session(pico, dk, p, folder):
    validate_plan(p); outcome = dict(status='ERROR', firmware_changed=False, board_reset_attempted=False)
    armed = False
    with (folder / 'pico_control.txt').open('wb') as pl, (folder / 'source_serial.txt').open('wb') as dl:
        try:
            pico.write(b'I\n'); _, line = pc.read_until(pico, pl, ('STREAM_READY',)); ready(line)
            dk.write(b'status\n'); _, line = pc.read_until(dk, dl, ('AS_STATUS',)); source_status(line, 'AS_STATUS')
            dk.write(f"arm {p['run']} {p['edges']} {p['period_ticks']}\n".encode()); armed = True
            _, line = pc.read_until(dk, dl, ('AS_ARMED',)); arm = source_status(line, 'AS_ARMED', p, arm=True)
            pc.save_json(folder / 'armed.json', arm); time.sleep(.02)
            pico.write(f"R {p['run']} {p['blocks']} {p['hold_ms']}\n".encode())
            h, tail, tr = read_stream(pico, p, folder)
            dk.write(b'freeze\n'); _, line = pc.read_until(dk, dl, ('AS_FROZEN',)); end = source_status(line, 'AS_FROZEN', p)
            armed = False; pc.save_json(folder / 'source_end.json', end)
            result = analyze(folder, p, h, tail, tr, arm, end)
            expected = result['gate'] == 'PASS' if p['expected_valid'] else (
                result['gate'] == 'FAIL' and bool(tail.get('errors', 0) & 2) and result['metrics'] is None)
            result['experiment_gate'] = 'PASS' if expected else 'FAIL'
            pc.save_json(folder / 'result.json', result)
            outcome.update(status='COLLECTED', experiment_gate=result['experiment_gate'], capture_gate=result['gate'])
            return result
        except Exception as exc:
            outcome['error'] = f'{type(exc).__name__}: {exc}'
            try: pico.write(b'Q\n'); outcome['abort_sent'] = True
            except Exception as e: outcome['abort_error'] = str(e)
            if armed:
                try:
                    dk.write(b'freeze\n'); _, line = pc.read_until(dk, dl, ('AS_FROZEN',))
                    pc.save_json(folder / 'source_end_after_error.json', pp.fields(line, 'AS_FROZEN'))
                    outcome['source_frozen_after_error'] = True
                except Exception as e: outcome['freeze_error'] = str(e)
            raise
        finally: pc.save_json(folder / 'acquisition.json', outcome)
