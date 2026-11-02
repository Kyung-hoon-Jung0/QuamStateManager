"""Standalone inputs from the port re-cabling audit (14 scenarios)."""
LF = 'quam.components.ports.analog_outputs.LFFEMAnalogOutputPort'
MWO = 'quam.components.ports.analog_outputs.MWFEMAnalogOutputPort'
MWI = 'quam.components.ports.analog_inputs.MWFEMAnalogInputPort'
DO = 'quam.components.ports.digital_outputs.FEMDigitalOutputPort'

def lf(con, fem, p, **kw):
    return {'controller_id': con, 'fem_id': fem, 'port_id': p, '__class__': LF, **kw}

def mwo(con, fem, p, **kw):
    return {'controller_id': con, 'fem_id': fem, 'port_id': p, '__class__': MWO, **kw}

def mwi(con, fem, p, **kw):
    return {'controller_id': con, 'fem_id': fem, 'port_id': p, '__class__': MWI, **kw}

def put(ports, group, con, fem, p, ent):
    ports.setdefault(group, {}).setdefault(con, {}).setdefault(str(fem), {})[str(p)] = ent

def chip(lines):
    """lines: {(qubit, line, field): (group, con, fem, port)} -> state, wiring"""
    q = {}
    w = {}
    for (qb, ln, fld), (g, c, f, p) in lines.items():
        q.setdefault(qb, {'__class__': 'qb.T'}).setdefault(ln, {'__class__': 'qb.L'})[fld] = f'#/wiring/qubits/{qb}/{ln}/{fld}'
        w.setdefault(qb, {}).setdefault(ln, {})[fld] = f'#/ports/{g}/{c}/{f}/{p}'
    return ({'qubits': q}, {'wiring': {'qubits': w}, 'network': {}})
AO = "analog_outputs"
DOG = "digital_outputs"


def c1_mw_a():
    L = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 4), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2)}
    old, ow = chip(L)
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 4, mwo('con1', 1, 4, band=2, upconverter_frequency=5000000000.0, full_scale_power_dbm=-5))
    put(P, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=3, upconverter_frequency=7000000000.0, full_scale_power_dbm=1))
    old['ports'] = P
    L2 = dict(L)
    L2['q1', 'xy', 'opx_output'] = ('mw_outputs', 'con1', 1, 3)
    new, nw = chip(L2)
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 1, 3, mwo('con1', 1, 3, band=3, upconverter_frequency=7000000000.0, full_scale_power_dbm=-11))
    put(P2, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=3, upconverter_frequency=7000000000.0, full_scale_power_dbm=-11))
    new['ports'] = P2
    return (old, ow, new, nw)


def c1_mw_b():
    L = {('q1', 'resonator', 'opx_output'): ('mw_outputs', 'con1', 1, 1), ('q1', 'resonator', 'opx_input'): ('mw_inputs', 'con1', 1, 1), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 8)}
    old, ow = chip(L)
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 1, mwo('con1', 1, 1, band=2, upconverter_frequency=7100000000.0, full_scale_power_dbm=-8))
    put(P, 'mw_inputs', 'con1', 1, 1, mwi('con1', 1, 1, band=2, downconverter_frequency='#/ports/mw_outputs/con1/1/1/upconverter_frequency', gain_db=3))
    put(P, 'mw_outputs', 'con1', 1, 8, mwo('con1', 1, 8, band=2, upconverter_frequency=5500000000.0))
    old['ports'] = P
    L2 = dict(L)
    L2['q1', 'resonator', 'opx_output'] = ('mw_outputs', 'con1', 2, 1)
    L2['q1', 'resonator', 'opx_input'] = ('mw_inputs', 'con1', 2, 1)
    L2['q2', 'xy', 'opx_output'] = ('mw_outputs', 'con1', 1, 1)
    new, nw = chip(L2)
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 2, 1, mwo('con1', 2, 1, band=2, upconverter_frequency=7100000000.0))
    put(P2, 'mw_inputs', 'con1', 2, 1, mwi('con1', 2, 1, band=2, downconverter_frequency='#/ports/mw_outputs/con1/2/1/upconverter_frequency'))
    put(P2, 'mw_outputs', 'con1', 1, 1, mwo('con1', 1, 1, band=2, upconverter_frequency=5500000000.0))
    new['ports'] = P2
    return (old, ow, new, nw)


def c1_mw_c():
    L = {('q1', 'resonator', 'opx_output'): ('mw_outputs', 'con1', 1, 1), ('q1', 'resonator', 'opx_input'): ('mw_inputs', 'con1', 1, 1), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 8)}
    old, ow = chip(L)
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 1, mwo('con1', 1, 1, band=2, upconverter_frequency=7100000000.0))
    put(P, 'mw_inputs', 'con1', 1, 1, mwi('con1', 1, 1, band=2, downconverter_frequency='#/ports/mw_outputs/con1/1/1/upconverter_frequency'))
    put(P, 'mw_outputs', 'con1', 1, 8, mwo('con1', 1, 8, band=2, upconverter_frequency=5500000000.0))
    old['ports'] = P
    L2 = dict(L)
    L2['q1', 'resonator', 'opx_output'] = ('mw_outputs', 'con1', 1, 8)
    L2['q2', 'xy', 'opx_output'] = ('mw_outputs', 'con1', 1, 1)
    new, nw = chip(L2)
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 1, 8, mwo('con1', 1, 8, band=2, upconverter_frequency=7100000000.0))
    put(P2, 'mw_inputs', 'con1', 1, 1, mwi('con1', 1, 1, band=2, downconverter_frequency='#/ports/mw_outputs/con1/1/8/upconverter_frequency'))
    put(P2, 'mw_outputs', 'con1', 1, 1, mwo('con1', 1, 1, band=2, upconverter_frequency=5500000000.0))
    new['ports'] = P2
    return (old, ow, new, nw)


def c1_mw_d():
    L = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2)}
    old, ow = chip(L)
    old['qubits']['q1']['xy']['upconverter'] = 2
    old['qubits']['q2']['xy']['upconverter'] = 1
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=2, upconverters={'1': {'frequency': 5000000000.0}, '2': {'frequency': 6000000000.0}}, full_scale_power_dbm=4))
    old['ports'] = P
    L2 = dict(L)
    L2['q1', 'xy', 'opx_output'] = ('mw_outputs', 'con1', 1, 6)
    new, nw = chip(L2)
    new['qubits']['q1']['xy']['upconverter'] = 1
    new['qubits']['q2']['xy']['upconverter'] = 1
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 1, 6, mwo('con1', 1, 6, band=2, upconverter_frequency=6000000000.0))
    put(P2, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=2, upconverter_frequency=5000000000.0))
    new['ports'] = P2
    return (old, ow, new, nw)


def c1_mw_e():
    L = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 4), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 6), ('q3', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2)}
    old, ow = chip(L)
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 4, mwo('con1', 1, 4, band=2, upconverter_frequency=5000000000.0, full_scale_power_dbm=-5))
    put(P, 'mw_outputs', 'con1', 1, 6, mwo('con1', 1, 6, band=2, upconverter_frequency=6000000000.0, full_scale_power_dbm=2))
    put(P, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=3, upconverter_frequency=8000000000.0, full_scale_power_dbm=10))
    old['ports'] = P
    L2 = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2)}
    new, nw = chip(L2)
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=2, upconverters={'1': {'frequency': 5000000000.0}, '2': {'frequency': 6000000000.0}}, full_scale_power_dbm=-11))
    new['ports'] = P2
    return (old, ow, new, nw)
def c2_lf_a():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q2': ('con1', 5, 3)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, delay=20, offset=0.12, sampling_rate=2000000000.0, upsampling_mode='pulse', output_mode='amplified', exponential_filter=[[0.1, 30.0]], feedforward_filter=[1.1, -0.1], crosstalk={'3': 0.13}))
    put(P, AO, 'con1', 5, 3, lf('con1', 5, 3, delay=0, offset=0.0, crosstalk={'1': 0.31}))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 6, 1), 'q2': ('con1', 5, 3)})
    P2 = {}
    put(P2, AO, 'con1', 6, 1, lf('con1', 6, 1, delay=0, offset=0.0, sampling_rate=1000000000.0, upsampling_mode='mw', output_mode='direct', exponential_filter=[], feedforward_filter=[], crosstalk={}))
    put(P2, AO, 'con1', 5, 3, lf('con1', 5, 3, delay=0, offset=0.0, crosstalk={}))
    put(P2, AO, 'con1', 6, 3, lf('con1', 6, 3, delay=0, offset=0.0, crosstalk={}))
    new['ports'] = P2
    return (old, ow, new, nw)


def c2_lf_b():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q2': ('con1', 5, 3)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, delay=20, crosstalk={'3': 0.13}))
    put(P, AO, 'con1', 5, 3, lf('con1', 5, 3, delay=4, crosstalk={'1': 0.31}))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 5, 7), 'q2': ('con1', 5, 3), 'q9': ('con1', 5, 1)})
    P2 = {}
    for p in (1, 3, 7):
        put(P2, AO, 'con1', 5, p, lf('con1', 5, p, delay=0, crosstalk={}))
    new['ports'] = P2
    return (old, ow, new, nw)


def c2_lf_c():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q3': ('con1', 5, 7)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, delay=20))
    put(P, AO, 'con1', 5, 7, lf('con1', 5, 7, delay=55, offset=0.3, exponential_filter=[[0.2, 40.0]], feedforward_filter=[1.2, -0.2]))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 5, 1), 'q9': ('con1', 5, 7)})
    P2 = {}
    for p in (1, 7):
        put(P2, AO, 'con1', 5, p, lf('con1', 5, p, delay=0, offset=0.0, exponential_filter=[], feedforward_filter=[]))
    new['ports'] = P2
    return (old, ow, new, nw)


def c2_lf_d():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q2': ('con1', 5, 2)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, delay=20))
    put(P, AO, 'con1', 5, 2, lf('con1', 5, 2, delay='#/ports/analog_outputs/con1/5/1/delay'))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 5, 6), 'q2': ('con1', 5, 2), 'q3': ('con1', 5, 1)})
    P2 = {}
    for p in (1, 2, 6):
        put(P2, AO, 'con1', 5, p, lf('con1', 5, p, delay=0))
    new['ports'] = P2
    return (old, ow, new, nw)


def c2_lf_e():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q2': ('con1', 5, 2)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, delay=20))
    put(P, AO, 'con1', 5, 2, lf('con1', 5, 2, delay='#./../1/delay'))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 5, 1), 'q2': ('con1', 5, 6)})
    P2 = {}
    for p in (1, 6):
        put(P2, AO, 'con1', 5, p, lf('con1', 5, p, delay=0))
    new['ports'] = P2
    return (old, ow, new, nw)
def lfchip(zmap):
    return chip({(q, 'z', 'opx_output'): (AO, c, f, p) for q, (c, f, p) in zmap.items()})


def c3_a():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q2': ('con1', 5, 3)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, delay=20, crosstalk={'3': 0.13}))
    put(P, AO, 'con1', 5, 3, lf('con1', 5, 3, delay=4, crosstalk={'1': 0.31}))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 6, 1), 'q2': ('con1', 5, 3), 'q9': ('con1', 5, 1)})
    P2 = {}
    for f, p in ((6, 1), (5, 3), (5, 1), (6, 3)):
        put(P2, AO, 'con1', f, p, lf('con1', f, p, delay=0, crosstalk={}))
    new['ports'] = P2
    return (old, ow, new, nw)


def c3_b():
    L = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2), ('q3', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 4)}
    old, ow = chip(L)
    for q, p in (('q1', 1), ('q3', 3)):
        old['qubits'][q]['xy']['digital_outputs'] = {'m': {'opx_output': f'#/wiring/qubits/{q}/xy/digital_output', '__class__': 'quam.components.channels.DigitalOutputChannel', 'delay': 57, 'buffer': 18}}
        ow['wiring']['qubits'][q]['xy']['digital_output'] = f'#/ports/{DOG}/con1/1/{p}'
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=2))
    put(P, 'mw_outputs', 'con1', 1, 4, mwo('con1', 1, 4, band=2))
    put(P, DOG, 'con1', 1, 1, {'controller_id': 'con1', 'fem_id': 1, 'port_id': 1, '__class__': DO, 'shareable': False, 'inverted': False, 'level': 'LVTTL'})
    put(P, DOG, 'con1', 1, 3, {'controller_id': 'con1', 'fem_id': 1, 'port_id': 3, '__class__': DO, 'shareable': True, 'inverted': True, 'level': 'TTL'})
    old['ports'] = P
    L2 = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2)}
    new, nw = chip(L2)
    new['qubits']['q1']['xy']['digital_outputs'] = {'m': {'opx_output': '#/wiring/qubits/q1/xy/digital_output', '__class__': 'quam.components.channels.DigitalOutputChannel', 'delay': 0, 'buffer': 0}}
    nw['wiring']['qubits']['q1']['xy']['digital_output'] = f'#/ports/{DOG}/con1/1/3'
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2, band=2))
    put(P2, DOG, 'con1', 1, 3, {'controller_id': 'con1', 'fem_id': 1, 'port_id': 3, '__class__': DO, 'shareable': False, 'inverted': False, 'level': 'LVTTL'})
    new['ports'] = P2
    return (old, ow, new, nw)


def c3_c():
    L = {('q1', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 2), ('q2', 'xy', 'opx_output'): ('mw_outputs', 'con1', 1, 4)}
    old, ow = chip(L)
    for q in ('q1', 'q2'):
        old['qubits'][q]['qdac_trig'] = {'__class__': 'x.Trig', 'opx_output': f'#/wiring/qubits/{q}/qt/digital_output'}
        ow['wiring']['qubits'][q]['qt'] = {'digital_output': '#/ports/digital_outputs/con1/1/1'}
    P = {}
    put(P, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2))
    put(P, 'mw_outputs', 'con1', 1, 4, mwo('con1', 1, 4))
    put(P, DOG, 'con1', 1, 1, {'controller_id': 'con1', 'fem_id': 1, 'port_id': 1, '__class__': DO, 'shareable': True, 'delay': 12})
    old['ports'] = P
    new, nw = chip(L)
    for q, p in (('q1', 1), ('q2', 2)):
        new['qubits'][q]['qdac_trig'] = {'__class__': 'x.Trig', 'opx_output': f'#/wiring/qubits/{q}/qt/digital_output'}
        nw['wiring']['qubits'][q]['qt'] = {'digital_output': f'#/ports/digital_outputs/con1/1/{p}'}
    P2 = {}
    put(P2, 'mw_outputs', 'con1', 1, 2, mwo('con1', 1, 2))
    put(P2, 'mw_outputs', 'con1', 1, 4, mwo('con1', 1, 4))
    for p in (1, 2):
        put(P2, DOG, 'con1', 1, p, {'controller_id': 'con1', 'fem_id': 1, 'port_id': p, '__class__': DO, 'shareable': True, 'delay': 0})
    new['ports'] = P2
    return (old, ow, new, nw)


def c3_d():
    old, ow = lfchip({'q1': ('con1', 5, 1), 'q3': ('con1', 5, 2), 'q2': ('con1', 6, 1), 'q4': ('con1', 6, 2)})
    P = {}
    put(P, AO, 'con1', 5, 1, lf('con1', 5, 1, crosstalk={'2': 0.11}))
    put(P, AO, 'con1', 5, 2, lf('con1', 5, 2, crosstalk={'1': 0.21}))
    put(P, AO, 'con1', 6, 1, lf('con1', 6, 1, crosstalk={'2': 0.66}))
    put(P, AO, 'con1', 6, 2, lf('con1', 6, 2, crosstalk={'1': 0.77}))
    old['ports'] = P
    new, nw = lfchip({'q1': ('con1', 6, 1), 'q3': ('con1', 5, 2), 'q2': ('con1', 5, 1), 'q4': ('con1', 6, 2)})
    P2 = {}
    for f in (5, 6):
        for p in (1, 2):
            put(P2, AO, 'con1', f, p, lf('con1', f, p, crosstalk={}))
    new['ports'] = P2
    return (old, ow, new, nw)

AUDIT_CASES = [c1_mw_a, c1_mw_b, c1_mw_c, c1_mw_d, c1_mw_e, c2_lf_a, c2_lf_b, c2_lf_c, c2_lf_d, c2_lf_e, c3_a, c3_b, c3_c, c3_d]
