# B30R_fixed.py
# Versão corrigida: decode/encode B30R (base30 glifos + separadores 'u'..'z'),
# separação int/float, e fatoração (Miller-Rabin + Pollard Rho).
# Uso: from B30R_fixed import b30r_encode, b30r_decode, factorize_int

from typing import Union, Dict, List, Tuple
from math import floor
from random import randrange
import random
import sys

# Opcional: aumentar recursionlimit se forem blocos muitos profundos
sys.setrecursionlimit(10000)

# ---------------------------
# Configuração de símbolos
BC = "0123456789ABCDEFGHIJKLMNOPQRST"  # 0..29 (A=10,...,T=29)
MAP30 = {c: i for i, c in enumerate(BC)}
MAP30.update({c.lower(): i for i, c in enumerate(BC)})  # case-insensitive digits

SEPARATORS = "uvwxyz"   # ordem: u (base[1]) ... z (base[6])
SEPS_UP = [s.upper() for s in SEPARATORS]
SEPS_LO = [s.lower() for s in SEPARATORS]

# ---------------------------
# Bases (inteiros puros). Expanda conforme necessário; use int literals.
BASES: List[int] = [
    30,
    30030,
    614889782588491410,
    232862364358497360900063316880507363070,
    367009731827331916465034565550136732339800312955331782619462457039988073311157667212930,
    509558935064289364432032169616857776489168568369134671296055828054188240764364761921821351373922822013621199759688858354748131233614846920025560717744496960296617420071391914813530238313960697008021210,
    # se necessário: acrescente bases maiores como int
]

# ---------------------------
# UTIL: acha índice do separador uppercase em BASES (u->1, v->2,...)
def sep_index_from_char(ch: str) -> int:
    ch_up = ch.upper()
    if ch_up in SEPS_UP:
        return SEPS_UP.index(ch_up) + 1
    raise ValueError("separador inválido")

# ---------------------------
# ENCODE inteiro puro (iterativo/recursivo seguro)
def encode_int(n: int) -> str:
    if n < 0:
        raise ValueError("encode_int espera n >= 0")
    if n == 0:
        return "0"
    # encontra maior base aplicável
    k = 0
    for i in range(len(BASES)-1, 0, -1):
        if n >= BASES[i]:
            k = i
            break
    if k == 0:
        # base-30 direta
        out = []
        while n > 0:
            n, r = divmod(n, 30)
            out.append(BC[r])
        out.reverse()
        return "".join(out)
    # caso hierárquico: dividir em blocos de tamanho 'BASES[k]'
    sep_char = SEPARATORS[k-1]  # 'u' para k=1
    parts = []
    while n > 0:
        n, rem = divmod(n, BASES[k])
        parts.append(encode_int(rem))  # rem < BASES[k]
    parts.reverse()
    return sep_char.join(parts)

# ---------------------------
# ENCODE float: se inteiro puro -> encode_int; se frac != 0 -> int.frac (base30 frac)
def encode_float(val: Union[float, str], frac_digits: int = 16) -> str:
    # recebe val numérico (float/mpf) ou string convertível; aqui assumimos já convertido
    # foco: -1 < x < 1 => retorna "0.<frac>" (ou só "<frac>" se preferir)
    v = float(val)
    neg = v < 0
    v_abs = abs(v)
    int_part = int(floor(v_abs))
    frac = v_abs - int_part
    if frac == 0.0:
        s = encode_int(int_part)
        return ("-" if neg else "") + s
    # encode inteiro se int_part > 0
    s_int = "0" if int_part == 0 else encode_int(int_part)
    s_frac_chars = []
    tmp = frac
    for _ in range(frac_digits):
        tmp *= 30.0
        d = int(floor(tmp))
        if d < 0 or d >= 30:
            d = max(0, min(29, d))
        s_frac_chars.append(BC[d])
        tmp -= d
    s_frac = "".join(s_frac_chars)
    return ("-" if neg else "") + s_int + "." + s_frac

# ---------------------------
# DECODE inteiro/mixto (aceita separadores u..z anywhere)
def decode_mixed(s: str) -> int:
    """Decodifica string hierárquica (ex: '1u2P5' ou 'CHD') para int.
       Funciona case-insensitive e trata separadores dos maiores para menores.
    """
    if s is None:
        return 0
    s = s.strip()
    if s == "":
        return 0
    # Normalize: preserve case for digits and separators, but we'll work upper
    S = s.upper()
    # se houver qualquer separador, achar o maior presente (z->u)
    for sep_idx in range(len(SEPS_UP)-1, -1, -1):  # from z->u
        sep = SEPS_UP[sep_idx]
        if sep in S:
            base = BASES[sep_idx+1]  # sep_idx 0 -> BASES[1]
            parts = S.split(sep)
            total = 0
            for p in parts:
                if p == "":
                    # string vazia -> zero bloco
                    val = 0
                else:
                    val = decode_mixed(p)  # recursivo; p has no sep >= current sep
                total = total * base + val
            return total
    # caso sem separadores: interpretar como base30 puro
    total = 0
    for ch in S:
        if ch not in MAP30:
            raise ValueError(f"glifo inválido: {ch} (esperado 0-9,A-T ou separador u..z)")
        total = total * 30 + MAP30[ch]
    return total

# ---------------------------
# DECODE fractional part (apenas base30 simples: "ABC" -> sum d_i * 30^{-i})
def decode_frac(frac_str: str) -> float:
    if frac_str == "":
        return 0.0
    # apenas aceitar dígitos base30 (sem separadores)
    S = frac_str.strip()
    denom = 1.0
    val = 0.0
    for ch in S:
        if ch.upper() not in MAP30:
            raise ValueError(f"glifo inválido na fracção: {ch}")
        val = val * 30.0 + MAP30[ch.upper()]
        denom *= 30.0
    return val / denom

# ---------------------------
# API público: decodificar string B30R (int ou float)
def b30r_decode(s: str) -> Union[int, float]:
    s = s.strip()
    neg = s.startswith("-")
    if neg:
        s = s[1:].strip()
    if "." in s:
        int_s, frac_s = s.split(".", 1)
    else:
        int_s, frac_s = s, ""
    int_val = decode_mixed(int_s) if int_s else 0
    if frac_s:
        frac_val = decode_frac(frac_s)
        res = float(int_val) + frac_val
    else:
        res = int_val
    return -res if neg else res

# ---------------------------
# API público: codificar número (int or float)
def b30r_encode(value: Union[int, float, str], frac_digits: int = 16) -> str:
    # se é string numérica, tente parsear e manter tipo
    if isinstance(value, str):
        # se contiver '.' assume float; se contiver separador assume string hierárquica -> retorná-la normalizada
        if any(sep in value for sep in SEPS_UP+SEPS_LO):
            # normalize separators to lowercase for output
            return value.strip()
        try:
            if "." in value:
                v = float(value)
                return encode_float(v, frac_digits)
            else:
                v = int(value)
                return encode_int(v)
        except Exception:
            # se não for numérico, assume que é já uma glif string e retorna upper-normalizada
            return value.strip()
    # numérico
    if isinstance(value, int):
        return encode_int(value)
    # float-like
    try:
        v = float(value)
    except Exception:
        v = float(value)
    # se inteiro puro (ex.: 123.0), tratar como inteiro
    if abs(v - round(v)) < 1e-16:
        return encode_int(int(round(v)))
    return encode_float(v, frac_digits)

# ---------------------------
# FATORAÇÃO: Miller-Rabin + Pollard Rho (heurístico, sem limites)
# Implementação adaptada (probabilística) — pode demorar para números enormes.
# Retorna dicionário {prime: exponent}
def is_probable_prime(n: int, k: int = 12) -> bool:
    if n < 2:
        return False
    small_primes = [2,3,5,7,11,13,17,19,23,29,31,37,41,43,47]
    for p in small_primes:
        if n % p == 0:
            return n == p
    # write n-1 = d * 2^s
    d = n-1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    # test k rounds
    for _ in range(k):
        a = randrange(2, n-1)
        x = pow(a, d, n)
        if x == 1 or x == n-1:
            continue
        for _ in range(s-1):
            x = (x*x) % n
            if x == n-1:
                break
        else:
            return False
    return True

def pollard_rho(n: int) -> int:
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3
    # random polynomial f(x) = x^2 + c
    while True:
        x = randrange(2, n-1)
        y = x
        c = randrange(1, n-1)
        d = 1
        while d == 1:
            x = (x*x + c) % n
            y = (y*y + c) % n
            y = (y*y + c) % n
            d = gcd(abs(x-y), n)
            if d == n:
                break
        if d > 1 and d < n:
            return d

def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)

def factorize_int(n: int) -> Dict[int,int]:
    """Retorna dicionário com fatores primos e expoentes. Probabilístico; pode demorar para números enormes."""
    if n == 0:
        return {0:1}
    if n < 0:
        res = factorize_int(-n)
        res[-1] = 1
        return res
    if n == 1:
        return {1:1}
    factors: Dict[int,int] = {}
    def _factor(m: int):
        if m == 1:
            return
        if is_probable_prime(m):
            factors[m] = factors.get(m, 0) + 1
            return
        d = pollard_rho(m)
        if d is None:
            # fallback brute-force (small)
            for i in range(2, int(m**0.5)+1):
                if m % i == 0:
                    _factor(i)
                    _factor(m//i)
                    return
            # prime fallback
            factors[m] = factors.get(m,0)+1
            return
        _factor(d)
        _factor(m//d)
    _factor(n)
    return factors

# ---------------------------
# Exemplos de uso (para testar localmente):
print(b30r_decode("1uCHD"))    # deve retornar 41353 (int)
print(b30r_encode(41353))      # deve retornar "1uCHD"
print(b30r_encode("IGM"))      # retorna "IGM" (já glif)
print(b30r_decode("1665"))     # retorna 32585
print(factorize_int(32585))    # exemplo de fatoração
#
# NOTAS:
# - Factorização é heurística; para números ~10^4000 pode demorar muito.
# - Se quiser, eu adapto a saída de encode_float para suprimir o "0" inicial
#   em casos -1<x<1 (só devolver ".ABC..."), ou deixo "0.ABC..." (padrão atual).
