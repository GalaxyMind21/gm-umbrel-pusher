"""Minimal pure-Python Ed25519 VERIFY (RFC 8032 section 5.1.7). Stdlib only; verify-only, no signing/keys here."""
import hashlib
p = 2**255 - 19; L = 2**252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p; I = pow(2, (p - 1) // 4, p)
def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p; B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p; D = 2 * P[2] * Q[2] % p; E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)
def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1: Q = _add(Q, P)
        P = _add(P, P); s >>= 1
    return Q
def _eq(P, Q): return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0
def _xrec(y, sign):
    if y >= p: return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p); x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p: x = x * I % p
    if (x * x - x2) % p: return None
    if x == 0 and sign: return None
    return p - x if (x & 1) != sign else x
def _dec(b):
    if len(b) != 32: return None
    y = int.from_bytes(b, "little"); s = y >> 255; y &= (1 << 255) - 1
    x = _xrec(y, s); return None if x is None else (x, y, 1, x * y % p)
gy = 4 * pow(5, p - 2, p) % p; G = (_xrec(gy, 0), gy, 1, _xrec(gy, 0) * gy % p)
def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64: return False
    A = _dec(pub); R = _dec(sig[:32])
    if A is None or R is None: return False
    s = int.from_bytes(sig[32:], "little")
    if s >= L: return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % L
    return _eq(_mul(s, G), _add(R, _mul(h, A)))
