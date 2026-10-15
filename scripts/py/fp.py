#!/usr/bin/env python3
"""fp -- the exact floating-point formats the FPArithmetic components use, in Python.

WHY THIS FILE EXISTS, AND WHY IT IS A RE-IMPLEMENTATION

An FP component's behaviour is not visible in its own `propagate()`. `FpAdder.propagate` is four lines:
decode both operands, add, re-encode. Everything that can be wrong - the exponent bias, the subnormal
boundary, whether the result rounds to nearest or truncates - lives in the codec:

    Value.toDoubleValueFromAnyFloat()   -> the width picks the format
    Value.createKnown(BitWidth, double) -> and encodes back

Measured from those two methods (Value.java) and MiniFloat.java:

    fpwidth  8  -> MiniFloat143: 1 sign, 4 exponent, 3 significand, BIAS 7
    fpwidth 16  -> IEEE 754 binary16 (half)
    fpwidth 32  -> IEEE 754 binary32 (single)
    fpwidth 64  -> IEEE 754 binary64 (double)
    ANY OTHER   -> Value.ERROR. The catalogue's fpwidth sweep includes 1, 2, 3 and 4; those are
                   GEOMETRICALLY valid and FUNCTIONALLY produce the error value, which is a trap.

binary16/32/64 come from `struct`, which IS the reference implementation. MiniFloat143 has no library
form, so it is re-implemented here from the source's own algorithm, including round-to-nearest-even.
That is why the conformance cases are written at fpwidth=8: it is the format most likely to be got
wrong, and 8-bit operands let an exhaustive check run in 65,536 rows.
"""
from __future__ import annotations

import struct

# ---------------------------------------------------------------------------------------------
# MiniFloat143: 1 sign | 4 exponent | 3 significand, bias 7
# ---------------------------------------------------------------------------------------------
F143_EXP_BIAS = 7
F143_MAX_EXP_FIELD = 15
F143_MIN_SUBNORMAL = 2.0 ** -9       # the smallest nonzero magnitude, 0x1p-9 in the Java source

def f143_decode(b: int) -> float:
    """Bit pattern -> value. Mirrors Value.toFloatValueFromFP8 / MiniFloat.miniFloat143ToFloat."""
    b &= 0xFF
    sign = -1.0 if (b & 0x80) else 1.0
    exp_field = (b >> 3) & 0x0F
    signif = b & 0x07
    if exp_field == 0:
        # subnormal: 2**-9 * significand. Zero when the significand is zero.
        return sign * (2.0 ** -9 * signif)
    if exp_field == F143_MAX_EXP_FIELD:
        return sign * float('inf') if signif == 0 else float('nan')
    return sign * (2.0 ** (exp_field - F143_EXP_BIAS)) * (1.0 + signif / 8.0)

def f143_encode(f: float) -> int:
    """Value -> bit pattern, round-to-nearest-even. Mirrors MiniFloat.floatToMiniFloat143."""
    if f != f:                                                        # NaN
        # the source preserves the sign and bits 22..20 of the float's significand
        bits = struct.unpack('<I', struct.pack('<f', f))[0]
        return (0x80 if bits & 0x8000_0000 else 0) | 0x78 | ((bits & 0x0070_0000) >> 20)

    # the sign comes from the raw bits, unconditionally: -0.0 must stay negative, and testing
    # `f != 0` first dropped it to +0
    fbits = struct.unpack('<I', struct.pack('<f', f))[0]
    sign_bit = 0x80 if (fbits & 0x8000_0000) else 0
    abs_f = abs(f)

    if abs_f >= 2.0 ** 8:
        return sign_bit | 0x78                                        # overflow -> infinity
    if abs_f <= F143_MIN_SUBNORMAL * 0.5:
        return sign_bit                                               # rounds to zero

    # exponent and significand of the single-precision form
    exp = _floor_log2(abs_f)
    expdelta = 0
    msb = 0
    if exp < -6:
        expdelta = -6 - exp
        exp = -7
        msb = 0x0080_0000
    doppel = struct.unpack('<I', struct.pack('<f', abs_f))[0]
    f_signif_bits = (doppel & 0x007F_FFFF) | msb

    signif_bits = f_signif_bits >> (20 + expdelta)
    lsb = f_signif_bits & (1 << (20 + expdelta))
    rnd = f_signif_bits & (1 << (19 + expdelta))
    sticky = f_signif_bits & ((1 << (19 + expdelta)) - 1)
    if rnd != 0 and (lsb | sticky) != 0:
        signif_bits += 1

    return sign_bit | (((exp + F143_EXP_BIAS) << 3) + signif_bits) & 0xFF

def _floor_log2(x: float) -> int:
    """Java's Math.getExponent for a positive float."""
    e = struct.unpack('<I', struct.pack('<f', x))[0]
    raw = ((e >> 23) & 0xFF) - 127
    return raw

# ---------------------------------------------------------------------------------------------
# IEEE formats, from struct, which is the reference
# ---------------------------------------------------------------------------------------------
_STRUCT = {16: 'e', 32: 'f', 64: 'd'}
WIDTHS = (8, 16, 32, 64)

def decode(width: int, bits: int) -> float:
    if width == 8:
        return f143_decode(bits)
    if width not in _STRUCT:
        raise ValueError(f'fpwidth {width} is not a floating format; Logisim returns Value.ERROR')
    return struct.unpack('>' + _STRUCT[width], bits.to_bytes(width // 8, 'big'))[0]

def encode(width: int, value: float) -> int:
    if width == 8:
        return f143_encode(value)
    if width not in _STRUCT:
        raise ValueError(f'fpwidth {width} is not a floating format; Logisim returns Value.ERROR')
    return int.from_bytes(struct.pack('>' + _STRUCT[width], value), 'big')

def err_flag(width: int, value: float) -> int:
    """The ERR output: 1 when the RESULT is NaN, per FpAdder and friends."""
    return 1 if value != value else 0

if __name__ == '__main__':
    import sys
    print('  MiniFloat143 spot checks (decode -> value -> encode):')
    for b in (0x00, 0x01, 0x07, 0x08, 0x38, 0x40, 0x78, 0x79, 0x7C, 0x80, 0xF8):
        v = f143_decode(b)
        r = f143_encode(v)
        print(f'    0x{b:02x} -> {v!r:>12} -> 0x{r:02x}  {"round-trip" if r == b else "CHANGED"}')
    print(f'\n  formats: {WIDTHS};  fpwidth 1,2,3,4 are geometrically valid but functionally ERROR')
