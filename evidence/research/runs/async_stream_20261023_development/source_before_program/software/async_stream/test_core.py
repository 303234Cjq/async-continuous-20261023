import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
import core


def header(p):
    return dict(version=core.PICO_VERSION, run=p['run'], blocks=p['blocks'], bytes_per_block=core.BYTES,
        samples_per_block=core.SAMPLES, sample_hz_nominal=core.FS, sample_hz_estimated=None,
        sys_hz=125000000, clkdiv=125, instructions_per_sample=1, input_base=12,
        A_drive='low_or_high_z_start_only', B_input_only=True, hold_ms=p['hold_ms'])


READY = b'STREAM_READY version=continuous-input-v1 idle=1 inputs=12,13 A_released=1 A_latch_high=0 B_input=1 local_pullup=0 sample_hz=1000000 words_per_block=4096 buffers=4\n'


def fixture(p):
    raw = bytearray([85] * (p['blocks'] * core.BYTES))
    def sample(n, bit): raw[n // 4] ^= bit << (2 * (n % 4))
    for n in range(2000, 2032): sample(n, 1)
    edges = [2000 + (i + 1) * (p['period_ticks'] // 16) for i in range(p['edges'])]
    for i in range(0, len(edges), 2):
        for n in range(edges[i], edges[i+1]): sample(n, 2)
    h = header(p)
    t = dict(run=p['run'], requested_blocks=p['blocks'], produced=p['blocks'], exported=p['blocks'],
             errors=0, fdebug=0, discarded_partial_words=0, A_released=True, B_input_only=True)
    arm = dict(errors=0, uart_dropped=0, cmd_overflow=0)
    end = dict(version=core.DK_VERSION, run=p['run'], armed=0, complete=1, input_count=1,
        edge_count=p['edges'], requested_edges=p['edges'], period_ticks=p['period_ticks'], marker_level=0,
        first_counter_tick=100000, last_edge_tick=100000 + (p['edges']-1)*p['period_ticks'],
        stop_counter_tick=100001 + (p['edges']-1)*p['period_ticks'], **arm)
    return raw, h, t, arm, end, edges


def wire(p, raw, h, t):
    out = bytearray(b'STREAM_BEGIN ' + json.dumps(h).encode() + b'\n')
    for i in range(p['blocks']):
        payload = raw[i*core.BYTES:(i+1)*core.BYTES]
        first = struct.pack('<10I', 0x52545350, 0x3130304d, p['run'], i, i*core.SAMPLES, 0,
                            len(payload), 0, 0, zlib.crc32(payload))
        out += first + struct.pack('<I', zlib.crc32(first)) + payload
    return out + b'\nSTREAM_END ' + json.dumps(t).encode() + b'\n' + READY


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.folder = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup); self.p = core.plan(99, 1000, 2000)

    def test_protocol_and_cross_block_scan(self):
        raw,h,t,arm,end,edges = fixture(self.p)
        ph,pt,tr = core.read_stream(io.BytesIO(wire(self.p,raw,h,t)), self.p, self.folder)
        r = core.analyze(self.folder,self.p,ph,pt,tr,arm,end)
        self.assertEqual(r['gate'], 'PASS'); self.assertEqual(r['metrics']['actual_B_transitions'], 1000)
        self.assertEqual(list(core.edge_scan(self.folder/'capture.bin')[1]),edges)

    def test_payload_crc_failure(self):
        raw,h,t,*_ = fixture(self.p); w = wire(self.p,raw,h,t)
        start = w.index(b'PSTRM001'); w[start+44+5] ^= 1
        with self.assertRaisesRegex(core.pp.ProtocolError,'payload CRC'):
            core.read_stream(io.BytesIO(w),self.p,self.folder)

    def test_header_crc_failure(self):
        raw,h,t,*_ = fixture(self.p); w=wire(self.p,raw,h,t); w[w.index(b'PSTRM001')+20] ^= 1
        with self.assertRaisesRegex(core.pp.ProtocolError,'header CRC'):
            core.read_stream(io.BytesIO(w),self.p,self.folder)

    def test_reordered_packet(self):
        raw,h,t,*_ = fixture(self.p); w=wire(self.p,raw,h,t); start=w.index(b'PSTRM001')
        w[start+12:start+16]=struct.pack('<I',1); w[start+40:start+44]=struct.pack('<I',zlib.crc32(w[start:start+40]))
        with self.assertRaisesRegex(core.pp.ProtocolError,'sequence'):
            core.read_stream(io.BytesIO(w),self.p,self.folder)

    def test_truncated_stream(self):
        raw,h,t,*_ = fixture(self.p)
        with self.assertRaises(core.pp.ProtocolError):
            core.read_stream(io.BytesIO(wire(self.p,raw,h,t)[:-120]),self.p,self.folder)

    def test_integrity_fail_never_produces_metrics(self):
        raw,h,t,arm,end,_=fixture(self.p); (self.folder/'capture.bin').write_bytes(raw)
        for changes in ({'errors':2},{'fdebug':1},{'produced':1},{'A_released':False}):
            r=core.analyze(self.folder,self.p,h,{**t,**changes},dict(packets=self.p['blocks'],packet_errors=[]),arm,end)
            self.assertEqual(r['gate'],'FAIL'); self.assertIsNone(r['metrics'])

    def test_source_count_mismatch(self):
        raw,h,t,arm,end,_=fixture(self.p); (self.folder/'capture.bin').write_bytes(raw)
        r=core.analyze(self.folder,self.p,h,t,dict(packets=self.p['blocks'],packet_errors=[]),arm,{**end,'edge_count':999})
        self.assertIsNone(r['metrics']); self.assertIn('source:edge_count',r['integrity_errors'])

    def test_physical_missing_edge(self):
        raw,h,t,arm,end,_=fixture(self.p); raw[:]=bytes([85])*len(raw); (self.folder/'capture.bin').write_bytes(raw)
        r=core.analyze(self.folder,self.p,h,t,dict(packets=self.p['blocks'],packet_errors=[]),arm,end)
        self.assertEqual(r['gate'],'FAIL'); self.assertIsNone(r['metrics'])

    def test_plan_and_direction_guards(self):
        for k,v in (('edges',999),('blocks',4),('run',0),('period_ticks',1)):
            with self.assertRaises(core.pp.ProtocolError): core.validate_plan({**self.p,k:v})
        with self.assertRaises(core.pp.ProtocolError): core.ready(READY.decode().replace('B_input=1','B_input=0').strip())

    def test_begin_cannot_change_rate(self):
        with self.assertRaises(core.pp.ProtocolError): core.validate_header({**header(self.p),'sample_hz_nominal':5000000},self.p)


if __name__=='__main__': unittest.main()
