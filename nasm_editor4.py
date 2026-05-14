"""
NASM Editor — IDE Assembly x64 completa em arquivo único
Suporta: Windows PE (exe/obj/dll), BIOS (bin), UEFI (efi)
Requer: pygame, nasm.exe no PATH ou pasta ./nasm/

ASMX Extension:
  NASM ⊆ ASMX  (todo NASM válido é ASMX válido)
  Botão [ASMX▶ASM] expande macros estruturais → NASM
  Botão [ASM▶ASMX] anota NASM → sintaxe ASMX (heurístico)
"""

import pygame
import sys
import os
import re
import subprocess
import shutil
import tempfile
import json
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE LAYOUT
# ─────────────────────────────────────────────────────────────────────────────
W, H = 1400, 860
TOP_H = 48
BOT_H = 160
LEFT_W = 60          # gutter números de linha
RIGHT_W = 240        # painel de sugestões
TOOLBAR_H = 36
FPS = 60
TAB_SIZE = 4
MAX_UNDO = 200
SCROLL_SPEED = 3

# ─────────────────────────────────────────────────────────────────────────────
# TEMA — monocromático âmbar / terminal retro
# ─────────────────────────────────────────────────────────────────────────────
T = {
    "bg":          (12, 12, 16),
    "bg2":         (18, 18, 26),
    "bg3":         (24, 24, 36),
    "border":      (40, 40, 60),
    "gutter":      (22, 22, 32),
    "gutter_fg":   (60, 60, 90),
    "lineno_cur":  (180, 140, 60),
    "cursor":      (220, 180, 80),
    "sel":         (50, 60, 90),
    "text":        (200, 200, 210),
    "comment":     (80, 100, 80),
    "keyword":     (100, 180, 255),
    "register":    (255, 160, 60),
    "number":      (120, 220, 140),
    "string":      (200, 130, 200),
    "label":       (255, 200, 80),
    "directive":   (160, 120, 255),
    "macro":       (255, 120, 120),
    "asmx":        (255, 180, 60),   # cor ASMX keywords
    "section":     (80, 200, 200),
    "error_line":  (80, 20, 20),
    "error_fg":    (255, 80, 80),
    "warn_line":   (60, 50, 10),
    "warn_fg":     (255, 200, 60),
    "ok":          (60, 200, 100),
    "panel_title": (100, 160, 255),
    "btn_bg":      (30, 34, 50),
    "btn_hover":   (45, 50, 80),
    "btn_border":  (60, 80, 140),
    "btn_active":  (60, 100, 200),
    "tooltip_bg":  (20, 20, 36),
    "tooltip_fg":  (180, 180, 200),
    "scrollbar":   (40, 44, 64),
    "scrollthumb": (80, 90, 130),
    "highlight":   (255, 230, 80),
    "ac_bg":       (20, 26, 42),
    "ac_sel":      (40, 60, 100),
    "ac_border":   (60, 100, 180),
    "ac_text":     (180, 210, 255),
    "ac_detail":   (100, 140, 180),
}

# ─────────────────────────────────────────────────────────────────────────────
# LISTA DE COMPLETIONS — NASM x64 AMD64
# ─────────────────────────────────────────────────────────────────────────────
NASM_COMPLETIONS: List[Dict] = []

def _c(word, kind, detail=""):
    NASM_COMPLETIONS.append({"word": word, "kind": kind, "detail": detail})

# Registradores 64-bit
for r in ["rax","rbx","rcx","rdx","rsi","rdi","rbp","rsp",
          "r8","r9","r10","r11","r12","r13","r14","r15"]:
    _c(r, "reg64", "64-bit general-purpose register")
# 32-bit
for r in ["eax","ebx","ecx","edx","esi","edi","ebp","esp",
          "r8d","r9d","r10d","r11d","r12d","r13d","r14d","r15d"]:
    _c(r, "reg32", "32-bit register")
# 16-bit
for r in ["ax","bx","cx","dx","si","di","bp","sp"]:
    _c(r, "reg16", "16-bit register")
# 8-bit
for r in ["al","ah","bl","bh","cl","ch","dl","dh",
          "sil","dil","bpl","spl",
          "r8b","r9b","r10b","r11b","r12b","r13b","r14b","r15b"]:
    _c(r, "reg8", "8-bit register")
# Segmento / controle
for r in ["cs","ds","es","fs","gs","ss"]:
    _c(r, "regseg", "Segment register")
for r in ["cr0","cr2","cr3","cr4","cr8"]:
    _c(r, "regcr", "Control register")
for r in ["dr0","dr1","dr2","dr3","dr6","dr7"]:
    _c(r, "regdr", "Debug register")
# SIMD
for i in range(16):
    _c(f"xmm{i}", "regxmm", "128-bit SSE register")
    _c(f"ymm{i}", "regymm", "256-bit AVX register")
    _c(f"zmm{i}", "regzmm", "512-bit AVX-512 register")
for i in range(8):
    _c(f"mm{i}",  "regmmx", "64-bit MMX register")
for i in range(8):
    _c(f"k{i}",   "regk",   "AVX-512 opmask register")

# ── Instruções gerais ──────────────────────────────────────────────────────
INSTRUCTIONS = {
    # Transferência de dados
    "mov":   "mov dst, src  — Move data",
    "movzx": "movzx dst, src — Move with zero-extend",
    "movsx": "movsx dst, src — Move with sign-extend",
    "movsxd":"movsxd dst, src — Move with sign-extend (64-bit)",
    "xchg":  "xchg a, b — Exchange",
    "lea":   "lea dst, [mem] — Load effective address",
    "push":  "push src — Push onto stack",
    "pop":   "pop dst — Pop from stack",
    "pusha": "pusha — Push all general-purpose (16/32)",
    "popa":  "popa — Pop all general-purpose (16/32)",
    "pushf": "pushf — Push FLAGS",
    "popf":  "popf — Pop FLAGS",
    "pushfq":"pushfq — Push RFLAGS (64-bit)",
    "popfq": "popfq — Pop RFLAGS (64-bit)",
    "lahf":  "lahf — Load AH from FLAGS",
    "sahf":  "sahf — Store AH into FLAGS",
    "cbw":   "cbw — Convert byte to word",
    "cwde":  "cwde — Convert word to dword",
    "cdqe":  "cdqe — Convert dword to qword (sign-extend EAX→RAX)",
    "cwd":   "cwd — Convert word to dword (DX:AX)",
    "cdq":   "cdq — Convert dword to qword (EDX:EAX)",
    "cqo":   "cqo — Convert qword to oword (RDX:RAX)",
    "xlat":  "xlat — Table look-up translation",
    "xlatb": "xlatb — Table look-up translation (no operand)",
    # Aritmética
    "add":   "add dst, src — Add",
    "adc":   "adc dst, src — Add with carry",
    "sub":   "sub dst, src — Subtract",
    "sbb":   "sbb dst, src — Subtract with borrow",
    "imul":  "imul dst, src [,imm] — Signed multiply",
    "mul":   "mul src — Unsigned multiply",
    "idiv":  "idiv src — Signed divide",
    "div":   "div src — Unsigned divide",
    "inc":   "inc dst — Increment by 1",
    "dec":   "dec dst — Decrement by 1",
    "neg":   "neg dst — Negate (two's complement)",
    "not":   "not dst — Bitwise NOT",
    "and":   "and dst, src — Bitwise AND",
    "or":    "or dst, src — Bitwise OR",
    "xor":   "xor dst, src — Bitwise XOR",
    "test":  "test a, b — Bitwise AND (sets flags, no store)",
    "cmp":   "cmp a, b — Compare (sub, sets flags only)",
    # Deslocamentos
    "shl":   "shl dst, count — Shift left logical",
    "shr":   "shr dst, count — Shift right logical",
    "sal":   "sal dst, count — Shift arithmetic left",
    "sar":   "sar dst, count — Shift arithmetic right",
    "rol":   "rol dst, count — Rotate left",
    "ror":   "ror dst, count — Rotate right",
    "rcl":   "rcl dst, count — Rotate left through carry",
    "rcr":   "rcr dst, count — Rotate right through carry",
    "shld":  "shld dst, src, count — Double-precision shift left",
    "shrd":  "shrd dst, src, count — Double-precision shift right",
    # Bit manipulation (BMI/BMI2)
    "bsf":   "bsf dst, src — Bit scan forward",
    "bsr":   "bsr dst, src — Bit scan reverse",
    "bt":    "bt base, offset — Bit test",
    "bts":   "bts base, offset — Bit test and set",
    "btr":   "btr base, offset — Bit test and reset",
    "btc":   "btc base, offset — Bit test and complement",
    "andn":  "andn dst, src1, src2 — AND NOT (BMI1)",
    "bextr": "bextr dst, src, ctrl — Bit field extract (BMI1)",
    "blsi":  "blsi dst, src — Extract lowest set bit (BMI1)",
    "blsmsk":"blsmsk dst, src — Get mask up to lowest set bit (BMI1)",
    "blsr":  "blsr dst, src — Reset lowest set bit (BMI1)",
    "bzhi":  "bzhi dst, src, idx — Zero high bits (BMI2)",
    "lzcnt": "lzcnt dst, src — Count leading zeros",
    "tzcnt": "tzcnt dst, src — Count trailing zeros",
    "popcnt":"popcnt dst, src — Population count",
    "pdep":  "pdep dst, src, mask — Parallel bits deposit (BMI2)",
    "pext":  "pext dst, src, mask — Parallel bits extract (BMI2)",
    "mulx":  "mulx dst_hi, dst_lo, src — Unsigned multiply (BMI2)",
    "rorx":  "rorx dst, src, imm8 — Rotate right (BMI2, no flags)",
    "sarx":  "sarx dst, src, shift — SAR without flags (BMI2)",
    "shlx":  "shlx dst, src, shift — SHL without flags (BMI2)",
    "shrx":  "shrx dst, src, shift — SHR without flags (BMI2)",
    # Controle de fluxo
    "jmp":   "jmp label — Unconditional jump",
    "call":  "call label/reg — Call procedure",
    "ret":   "ret [imm16] — Return from procedure",
    "retn":  "retn [imm16] — Near return",
    "retf":  "retf [imm16] — Far return",
    "je":    "je label — Jump if equal (ZF=1)",
    "jne":   "jne label — Jump if not equal (ZF=0)",
    "jz":    "jz label — Jump if zero (ZF=1)",
    "jnz":   "jnz label — Jump if not zero (ZF=0)",
    "jg":    "jg label — Jump if greater (signed)",
    "jge":   "jge label — Jump if greater or equal (signed)",
    "jl":    "jl label — Jump if less (signed)",
    "jle":   "jle label — Jump if less or equal (signed)",
    "ja":    "ja label — Jump if above (unsigned)",
    "jae":   "jae label — Jump if above or equal (unsigned)",
    "jb":    "jb label — Jump if below (unsigned)",
    "jbe":   "jbe label — Jump if below or equal (unsigned)",
    "jc":    "jc label — Jump if carry",
    "jnc":   "jnc label — Jump if no carry",
    "jo":    "jo label — Jump if overflow",
    "jno":   "jno label — Jump if no overflow",
    "js":    "js label — Jump if sign",
    "jns":   "jns label — Jump if no sign",
    "jp":    "jp label — Jump if parity",
    "jnp":   "jnp label — Jump if no parity",
    "jecxz": "jecxz label — Jump if ECX is zero",
    "jrcxz": "jrcxz label — Jump if RCX is zero",
    "loop":  "loop label — Loop with RCX",
    "loope": "loope label — Loop while equal",
    "loopne":"loopne label — Loop while not equal",
    # CMOVcc
    "cmove": "cmove dst, src — Conditional move if equal",
    "cmovne":"cmovne dst, src — Conditional move if not equal",
    "cmovg": "cmovg dst, src — Conditional move if greater",
    "cmovge":"cmovge dst, src — Conditional move if >=",
    "cmovl": "cmovl dst, src — Conditional move if less",
    "cmovle":"cmovle dst, src — Conditional move if <=",
    "cmova": "cmova dst, src — Conditional move if above",
    "cmovae":"cmovae dst, src — Conditional move if above/equal",
    "cmovb": "cmovb dst, src — Conditional move if below",
    "cmovbe":"cmovbe dst, src — Conditional move if below/equal",
    "cmovc": "cmovc dst, src — Conditional move if carry",
    "cmovs": "cmovs dst, src — Conditional move if sign",
    "cmovo": "cmovo dst, src — Conditional move if overflow",
    # SETcc
    "sete":  "sete dst — Set byte if equal",
    "setne": "setne dst — Set byte if not equal",
    "setg":  "setg dst — Set byte if greater (signed)",
    "setl":  "setl dst — Set byte if less (signed)",
    "seta":  "seta dst — Set byte if above (unsigned)",
    "setb":  "setb dst — Set byte if below (unsigned)",
    "sets":  "sets dst — Set byte if sign",
    "seto":  "seto dst — Set byte if overflow",
    # String
    "movs":  "movs — Move string (byte/word/dword/qword)",
    "movsb": "movsb — Move string byte",
    "movsw": "movsw — Move string word",
    "movsd": "movsd xmm, xmm/m128 — Move scalar double / movsd string dword",
    "movsq": "movsq — Move string qword",
    "cmps":  "cmps — Compare string",
    "cmpsb": "cmpsb — Compare string byte",
    "cmpsw": "cmpsw — Compare string word",
    "cmpsd": "cmpsd — Compare string dword",
    "cmpsq": "cmpsq — Compare string qword",
    "scas":  "scas — Scan string",
    "scasb": "scasb — Scan string byte",
    "scasw": "scasw — Scan string word",
    "scasd": "scasd — Scan string dword",
    "scasq": "scasq — Scan string qword",
    "lods":  "lods — Load string",
    "lodsb": "lodsb — Load string byte into AL",
    "lodsw": "lodsw — Load string word into AX",
    "lodsd": "lodsd — Load string dword into EAX",
    "lodsq": "lodsq — Load string qword into RAX",
    "stos":  "stos — Store string",
    "stosb": "stosb — Store AL into string",
    "stosw": "stosw — Store AX into string",
    "stosd": "stosd — Store EAX into string",
    "stosq": "stosq — Store RAX into string",
    "rep":   "rep — Repeat string operation prefix",
    "repe":  "repe — Repeat while equal prefix",
    "repne": "repne — Repeat while not equal prefix",
    "repz":  "repz — Repeat while zero prefix",
    "repnz": "repnz — Repeat while not zero prefix",
    # I/O
    "in":    "in al/ax/eax, dx/imm8 — Input from port",
    "out":   "out dx/imm8, al/ax/eax — Output to port",
    "ins":   "ins — Input string from port",
    "insb":  "insb — Input byte string",
    "insw":  "insw — Input word string",
    "insd":  "insd — Input dword string",
    "outs":  "outs — Output string to port",
    "outsb": "outsb — Output byte string",
    "outsw": "outsw — Output word string",
    "outsd": "outsd — Output dword string",
    # Controle de CPU
    "nop":   "nop — No operation",
    "hlt":   "hlt — Halt processor",
    "clc":   "clc — Clear carry flag",
    "stc":   "stc — Set carry flag",
    "cmc":   "cmc — Complement carry flag",
    "cld":   "cld — Clear direction flag",
    "std":   "std — Set direction flag",
    "cli":   "cli — Clear interrupt flag",
    "sti":   "sti — Set interrupt flag",
    "clts":  "clts — Clear task switched flag (CR0)",
    "int":   "int imm8 — Software interrupt",
    "int3":  "int3 — Breakpoint interrupt",
    "into":  "into — Interrupt on overflow",
    "iret":  "iret — Interrupt return",
    "iretd": "iretd — Interrupt return (32-bit)",
    "iretq": "iretq — Interrupt return (64-bit)",
    "syscall":"syscall — Fast system call (x64)",
    "sysret":"sysret — Return from fast system call",
    "sysenter":"sysenter — Fast system call (32-bit)",
    "sysexit":"sysexit — Fast system call exit (32-bit)",
    "cpuid": "cpuid — CPU identification",
    "rdtsc": "rdtsc — Read time-stamp counter",
    "rdtscp":"rdtscp — Read time-stamp counter and processor ID",
    "rdmsr": "rdmsr — Read model-specific register",
    "wrmsr": "wrmsr — Write model-specific register",
    "rdpmc": "rdpmc — Read performance monitoring counter",
    "lfence":"lfence — Load fence",
    "mfence":"mfence — Memory fence",
    "sfence":"sfence — Store fence",
    "pause": "pause — Spin-loop hint",
    "ud2":   "ud2 — Undefined instruction (guaranteed fault)",
    "wbinvd":"wbinvd — Write back and invalidate cache",
    "invd":  "invd — Invalidate cache",
    "invlpg":"invlpg [mem] — Invalidate TLB entry",
    "ltr":   "ltr src16 — Load task register",
    "str":   "str dst16 — Store task register",
    "lldt":  "lldt src16 — Load local descriptor table register",
    "sldt":  "sldt dst16 — Store LDTR",
    "lgdt":  "lgdt [mem] — Load global descriptor table register",
    "sgdt":  "sgdt [mem] — Store GDTR",
    "lidt":  "lidt [mem] — Load interrupt descriptor table register",
    "sidt":  "sidt [mem] — Store IDTR",
    "lmsw":  "lmsw src16 — Load machine status word",
    "smsw":  "smsw dst — Store machine status word",
    "clflush":"clflush [mem] — Flush cache line",
    "monitor":"monitor — Set up monitor address",
    "mwait": "mwait — Monitor wait",
    "xgetbv":"xgetbv — Get value of extended control register",
    "xsetbv":"xsetbv — Set value of extended control register",
    "xsave": "xsave [mem] — Save processor extended states",
    "xrstor":"xrstor [mem] — Restore processor extended states",
    "vmcall":"vmcall — Call VMM",
    "vmlaunch":"vmlaunch — Launch virtual machine",
    "vmresume":"vmresume — Resume virtual machine",
    "vmxoff":"vmxoff — Leave VMX operation",
    "vmxon": "vmxon [mem] — Enter VMX root operation",
    # Flags de prefixo e segmento
    "lock":  "lock — Lock prefix (atomic)",
    "cs":    "cs: — CS segment override prefix",
    "ds":    "ds: — DS segment override prefix",
    "es":    "es: — ES segment override prefix",
    "fs":    "fs: — FS segment override prefix",
    "gs":    "gs: — GS segment override prefix",
    "ss":    "ss: — SS segment override prefix",
    # FPU x87
    "fld":   "fld src — Load floating-point",
    "fst":   "fst dst — Store floating-point",
    "fstp":  "fstp dst — Store and pop floating-point",
    "fadd":  "fadd src — Add floating-point",
    "faddp": "faddp — Add and pop",
    "fsub":  "fsub src — Subtract floating-point",
    "fsubp": "fsubp — Subtract and pop",
    "fmul":  "fmul src — Multiply floating-point",
    "fdiv":  "fdiv src — Divide floating-point",
    "fcom":  "fcom src — Compare floating-point",
    "fcomp": "fcomp — Compare and pop",
    "fcompp":"fcompp — Compare and pop twice",
    "ficom": "ficom src — Integer compare",
    "fist":  "fist dst — Store integer",
    "fistp": "fistp dst — Store integer and pop",
    "fild":  "fild src — Load integer",
    "fxch":  "fxch st(i) — Exchange FP registers",
    "fabs":  "fabs — Absolute value",
    "fchs":  "fchs — Change sign",
    "fsqrt": "fsqrt — Square root",
    "fsin":  "fsin — Sine",
    "fcos":  "fcos — Cosine",
    "fptan": "fptan — Partial tangent",
    "fpatan":"fpatan — Partial arctangent",
    "fninit":"fninit — Initialize FPU (no-wait)",
    "finit": "finit — Initialize FPU",
    "fnsave":"fnsave [mem] — Save FPU state (no-wait)",
    "frstor":"frstor [mem] — Restore FPU state",
    "fldcw": "fldcw [mem16] — Load FPU control word",
    "fstcw": "fstcw [mem16] — Store FPU control word",
    "fnstcw":"fnstcw [mem16] — Store FPU control word (no-wait)",
    "wait":  "wait — Wait for FPU",
    "fwait": "fwait — Wait for FPU",
    # SSE / SSE2 / SSE3 / SSSE3 / SSE4
    "movaps":"movaps xmm, xmm/m128 — Move aligned packed single",
    "movups":"movups xmm, xmm/m128 — Move unaligned packed single",
    "movss": "movss xmm, xmm/m32 — Move scalar single",
    "movapd":"movapd xmm, xmm/m128 — Move aligned packed double",
    "movupd":"movupd xmm, xmm/m128 — Move unaligned packed double",
    "addps": "addps xmm, xmm/m128 — Add packed single",
    "addpd": "addpd xmm, xmm/m128 — Add packed double",
    "addss": "addss xmm, xmm/m32 — Add scalar single",
    "addsd": "addsd xmm, xmm/m64 — Add scalar double",
    "subps": "subps xmm, xmm/m128 — Subtract packed single",
    "subpd": "subpd xmm, xmm/m128 — Subtract packed double",
    "mulps": "mulps xmm, xmm/m128 — Multiply packed single",
    "mulpd": "mulpd xmm, xmm/m128 — Multiply packed double",
    "divps": "divps xmm, xmm/m128 — Divide packed single",
    "divpd": "divpd xmm, xmm/m128 — Divide packed double",
    "sqrtps":"sqrtps xmm, xmm/m128 — Square root packed single",
    "cvtsi2sd":"cvtsi2sd xmm, r/m — Convert integer to scalar double",
    "cvtsi2ss":"cvtsi2ss xmm, r/m — Convert integer to scalar single",
    "cvtsd2si":"cvtsd2si r, xmm/m64 — Convert scalar double to integer",
    "cvtss2si":"cvtss2si r, xmm/m32 — Convert scalar single to integer",
    "cvtss2sd":"cvtss2sd xmm, xmm/m32 — Convert single to double",
    "cvtsd2ss":"cvtsd2ss xmm, xmm/m64 — Convert double to single",
    "pxor":  "pxor mm/xmm, mm/xmm — Logical exclusive OR",
    "pand":  "pand mm/xmm, mm/xmm — Logical AND",
    "pandn": "pandn mm/xmm, mm/xmm — Logical AND NOT",
    "por":   "por mm/xmm, mm/xmm — Logical OR",
    "movdqa":"movdqa xmm, xmm/m128 — Move aligned double qword",
    "movdqu":"movdqu xmm, xmm/m128 — Move unaligned double qword",
    "movq":  "movq mm/xmm, r/m64 — Move quadword",
    "movd":  "movd mm/xmm, r/m32 — Move doubleword",
    "punpcklbw":"punpcklbw xmm, xmm/m128 — Unpack low bytes",
    "punpckhbw":"punpckhbw xmm, xmm/m128 — Unpack high bytes",
    "packuswb":"packuswb xmm, xmm/m128 — Pack with unsigned saturation",
    "pmaddwd":"pmaddwd xmm, xmm/m128 — Multiply and add packed integers",
    "psllw": "psllw xmm, xmm/imm8 — Shift packed words left logical",
    "pslld": "pslld xmm, xmm/imm8 — Shift packed dwords left logical",
    "psllq": "psllq xmm, xmm/imm8 — Shift packed qwords left logical",
    "psrlw": "psrlw xmm, xmm/imm8 — Shift packed words right logical",
    "psrld": "psrld xmm, xmm/imm8 — Shift packed dwords right logical",
    "psrlq": "psrlq xmm, xmm/imm8 — Shift packed qwords right logical",
    "pcmpeqb":"pcmpeqb xmm, xmm/m128 — Compare equal bytes",
    "pcmpeqw":"pcmpeqw xmm, xmm/m128 — Compare equal words",
    "pcmpeqd":"pcmpeqd xmm, xmm/m128 — Compare equal dwords",
    "pmovmskb":"pmovmskb r32, xmm — Move byte mask",
    "palignr":"palignr xmm, xmm/m128, imm8 — Concatenate and shift (SSSE3)",
    "pshufb":"pshufb xmm, xmm/m128 — Shuffle bytes (SSSE3)",
    "blendps":"blendps xmm, xmm/m128, imm8 — Blend packed singles (SSE4.1)",
    "blendpd":"blendpd xmm, xmm/m128, imm8 — Blend packed doubles (SSE4.1)",
    "pblendw":"pblendw xmm, xmm/m128, imm8 — Blend words (SSE4.1)",
    "dpps":  "dpps xmm, xmm/m128, imm8 — Dot product packed single (SSE4.1)",
    "ptest": "ptest xmm, xmm/m128 — Logical compare (SSE4.1)",
    "pcmpestri":"pcmpestri xmm, xmm/m128, imm8 — Compare strings explicit length, return index (SSE4.2)",
    "pcmpestrm":"pcmpestrm xmm, xmm/m128, imm8 — Compare strings explicit length, return mask (SSE4.2)",
    "pcmpistri":"pcmpistri xmm, xmm/m128, imm8 — Compare strings implicit length, return index (SSE4.2)",
    "pcmpistrm":"pcmpistrm xmm, xmm/m128, imm8 — Compare strings implicit length, return mask (SSE4.2)",
    "crc32": "crc32 dst, src — Accumulate CRC32 (SSE4.2)",
    # AVX
    "vmovaps":"vmovaps ymm, ymm/m256 — Move aligned packed single (AVX)",
    "vaddps":"vaddps ymm, ymm, ymm/m256 — Add packed single (AVX)",
    "vsubps":"vsubps ymm, ymm, ymm/m256 — Subtract packed single (AVX)",
    "vmulps":"vmulps ymm, ymm, ymm/m256 — Multiply packed single (AVX)",
    "vdivps":"vdivps ymm, ymm, ymm/m256 — Divide packed single (AVX)",
    "vxorps":"vxorps ymm, ymm, ymm/m256 — XOR packed single (AVX)",
    "vandps":"vandps ymm, ymm, ymm/m256 — AND packed single (AVX)",
    "vorps": "vorps ymm, ymm, ymm/m256 — OR packed single (AVX)",
    "vpxor": "vpxor ymm, ymm, ymm/m256 — XOR packed integer (AVX2)",
    "vpand": "vpand ymm, ymm, ymm/m256 — AND packed integer (AVX2)",
    "vpor":  "vpor ymm, ymm, ymm/m256 — OR packed integer (AVX2)",
    "vpcmpeqb":"vpcmpeqb ymm, ymm, ymm/m256 — Compare equal bytes (AVX2)",
    "vpmovmskb":"vpmovmskb r32, ymm — Move byte mask (AVX2)",
    "vzeroupper":"vzeroupper — Zero upper bits of YMM registers",
    "vzeroall":"vzeroall — Zero all YMM registers",
    # AES / CLMUL / SHA
    "aesenc": "aesenc xmm, xmm/m128 — AES encryption round",
    "aesenclast":"aesenclast xmm, xmm/m128 — AES last encryption round",
    "aesdec": "aesdec xmm, xmm/m128 — AES decryption round",
    "aesdeclast":"aesdeclast xmm, xmm/m128 — AES last decryption round",
    "aeskeygenassist":"aeskeygenassist xmm, xmm/m128, imm8 — AES key gen assist",
    "aesimc":"aesimc xmm, xmm/m128 — AES inverse mix columns",
    "pclmulqdq":"pclmulqdq xmm, xmm/m128, imm8 — Carry-less multiply",
    "sha1rnds4":"sha1rnds4 xmm, xmm/m128, imm8 — SHA1 rounds",
    "sha256rnds2":"sha256rnds2 xmm, xmm/m128 — SHA256 rounds",
    # ADX
    "adcx":  "adcx dst, src — Add with carry flag (ADX)",
    "adox":  "adox dst, src — Add with overflow flag (ADX)",
    # XSAVE / MPX / misc
    "bnd":   "bnd — MPX bound prefix",
    "xacquire":"xacquire — HLE acquire prefix",
    "xrelease":"xrelease — HLE release prefix",
}

for name, detail in INSTRUCTIONS.items():
    _c(name, "instr", detail)

# ── Diretivas NASM ──────────────────────────────────────────────────────────
DIRECTIVES = {
    "bits":       "bits 16/32/64 — Set code generation mode",
    "use16":      "use16 — Generate 16-bit code",
    "use32":      "use32 — Generate 32-bit code",
    "use64":      "use64 — Generate 64-bit code",
    "default":    "default rel/abs — Set default addressing",
    "section":    "section .name [flags] — Declare section",
    "segment":    "segment name — Declare segment (COM/obj)",
    "global":     "global sym — Export symbol",
    "extern":     "extern sym — Import external symbol",
    "common":     "common sym size — Declare common symbol",
    "absolute":   "absolute addr — Absolute section",
    "org":        "org addr — Set origin address",
    "times":      "times n instr — Repeat instruction n times",
    "align":      "align n — Align to boundary",
    "alignb":     "alignb n — Align with NOP fill",
    "db":         "db val[,...] — Define byte(s)",
    "dw":         "dw val[,...] — Define word(s) (16-bit)",
    "dd":         "dd val[,...] — Define dword(s) (32-bit)",
    "dq":         "dq val[,...] — Define qword(s) (64-bit)",
    "dt":         "dt val[,...] — Define tword (80-bit FP)",
    "do":         "do val[,...] — Define oword (128-bit)",
    "dy":         "dy val[,...] — Define yword (256-bit)",
    "dz":         "dz val[,...] — Define zword (512-bit)",
    "resb":       "resb n — Reserve n bytes",
    "resw":       "resw n — Reserve n words",
    "resd":       "resd n — Reserve n dwords",
    "resq":       "resq n — Reserve n qwords",
    "rest":       "rest n — Reserve n twords",
    "reso":       "reso n — Reserve n owords",
    "resy":       "resy n — Reserve n ywords",
    "resz":       "resz n — Reserve n zwords",
    "equ":        "label equ expr — Define constant",
    "macro":      "%macro name nparam — Begin macro definition",
    "endmacro":   "%endmacro — End macro definition",
    "define":     "%define name val — Simple text macro",
    "xdefine":    "%xdefine name val — Expand-once macro",
    "undef":      "%undef name — Undefine macro",
    "assign":     "%assign name expr — Numeric macro",
    "if":         "%if expr — Conditional assembly",
    "elif":       "%elif expr — Else-if conditional",
    "else":       "%else — Else branch",
    "endif":      "%endif — End conditional",
    "ifdef":      "%ifdef name — If defined",
    "ifndef":     "%ifndef name — If not defined",
    "ifmacro":    "%ifmacro name — If macro defined",
    "ifnmacro":   "%ifnmacro name — If macro not defined",
    "include":    "%include 'file' — Include file",
    "use":        "%use module — Use NASM standard macro package",
    "push":       "%push context — Push macro context",
    "pop":        "%pop — Pop macro context",
    "rotate":     "%rotate n — Rotate macro params",
    "rep":        "%rep n — Repeat block",
    "endrep":     "%endrep — End repeat block",
    "error":      "%error msg — Emit error",
    "warning":    "%warning msg — Emit warning",
    "line":       "%line linenum file — Override line info",
    "strcat":     "%strcat dst, src — Concatenate strings",
    "strlen":     "%strlen name, str — String length",
    "substr":     "%substr name, str, start [,len] — Substring",
    "idefine":    "%idefine name val — Case-insensitive define",
    "imacro":     "%imacro name nparam — Case-insensitive macro",
    "ifidn":      "%ifidn a, b — If strings identical",
    "ifidni":     "%ifidni a, b — If strings identical (case-insensitive)",
    "ifdifi":     "%ifdifi a, b — If strings different",
    "ifdif":      "%ifdif a, b — If strings different",
    "ifid":       "%ifid token — If token is identifier",
    "ifnum":      "%ifnum token — If token is number",
    "ifstr":      "%ifstr token — If token is string",
    "iftoken":    "%iftoken token — If token is defined",
    "exitrep":    "%exitrep — Exit %rep loop early",
    "exitmacro":  "%exitmacro — Exit macro early",
    "repl":       "%repl old, new — Replace macro context",
}

for name, detail in DIRECTIVES.items():
    _c(name, "directive", detail)
    if not name.startswith("%"):
        _c("%" + name, "directive", detail)

# ── ASMX extension keywords ─────────────────────────────────────────────────
ASMX_WORDS = {
    "@macro":    "ASMX: define user macro  @macro name(params)",
    "@endmacro": "ASMX: end macro definition",
    "@struct":   "ASMX: define struct  @struct Name: f:type; @end",
    "@end":      "ASMX: close struct/block",
    "@const":    "ASMX: named constant  @const NAME = value",
    "@vector":   "ASMX: typed array  @vector name, type, count",
    "@dict":     "ASMX: key-value block  @dict Name:",
    "@raw":      "ASMX: raw NASM passthrough  @raw ...",
    "defmacro":  "ASMX: grammar macro define  defmacro{name(p)}body{enddef",
    "enddef":    "ASMX: end defmacro block",
    "usemacro":  "ASMX: grammar macro call  usemacro{name(args)}endmacro",
    "endmacro":  "ASMX: end usemacro call",
}
for k, v in ASMX_WORDS.items():
    _c(k, "asmx", v)

# ── Seções comuns ───────────────────────────────────────────────────────────
SECTIONS = [
    (".text",   "Code section"),
    (".data",   "Initialized data"),
    (".bss",    "Uninitialized data"),
    (".rodata", "Read-only data"),
    (".rdata",  "Read-only data (PE)"),
    (".pdata",  "Exception handling (PE)"),
    (".xdata",  "Unwind data (PE)"),
    (".idata",  "Import table (PE)"),
    (".edata",  "Export table (PE)"),
    (".reloc",  "Relocation table (PE)"),
    (".tls",    "Thread-local storage (PE)"),
    (".rsrc",   "Resources (PE)"),
    (".debug",  "Debug information"),
]
for name, detail in SECTIONS:
    _c(name, "section", detail)

# ── Templates UEFI / Windows ─────────────────────────────────────────────────
SNIPPETS = {
    "win64_exe": """\
; Windows x64 Executable — Console Application
bits 64
default rel

section .text
global main
extern ExitProcess

main:
    ; Function prologue (shadow space for Win64 ABI)
    sub     rsp, 40

    ; === Your code here ===
    xor     ecx, ecx        ; exit code 0
    call    ExitProcess

section .data
    ; initialized data here

section .bss
    ; uninitialized data here
""",
    "win64_obj": """\
; Windows x64 COFF Object — linkable module
bits 64
default rel

section .text
global my_function

; int64_t my_function(int64_t a, int64_t b)
; Win64 ABI: rcx=a, rdx=b, return in rax
my_function:
    sub     rsp, 8          ; align stack to 16 bytes
    mov     rax, rcx
    add     rax, rdx
    add     rsp, 8
    ret

section .data
""",
    "win64_dll": """\
; Windows x64 DLL
bits 64
default rel

section .text
global DllMain
extern __imp_DisableThreadLibraryCalls

; DllMain(HINSTANCE hInstDll, DWORD fdwReason, LPVOID lpvReserved)
DllMain:
    sub     rsp, 40
    test    edx, edx        ; fdwReason
    jnz     .not_attach
    ; DLL_PROCESS_ATTACH
    sub     rsp, 32
    call    [__imp_DisableThreadLibraryCalls]
    add     rsp, 32
.not_attach:
    mov     eax, 1          ; return TRUE
    add     rsp, 40
    ret

section .data
""",
    "bios_boot": """\
; BIOS Boot Sector — x86 Real Mode (16-bit)
; Loaded at 0x7C00, exactly 512 bytes
bits 16
org 0x7C00

start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00
    sti

    ; Print message via BIOS INT 10h
    mov     si, msg
.loop:
    lodsb
    test    al, al
    jz      .halt
    mov     ah, 0x0E
    int     0x10
    jmp     .loop
.halt:
    hlt
    jmp     .halt

msg db 'Hello from BIOS!', 13, 10, 0

; Boot sector padding and signature
times 510-($-$$) db 0
dw 0xAA55
""",
    "uefi_app": """\
; UEFI Application — x64 PE+ executable
; Compile: nasm -f win64 uefi_app.asm -o uefi_app.obj
; Link:    link /subsystem:efi_application /entry:efi_main uefi_app.obj
bits 64
default rel

%define EFI_SUCCESS 0
%define EFI_ERROR   0x8000000000000000

; EFI_SYSTEM_TABLE offsets (simplified)
%define EFI_SYSTEM_TABLE.ConOut         0x40
%define EFI_SIMPLE_TEXT_OUTPUT.OutputString 0x08

section .text
global efi_main

; EFI_STATUS efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
; Win64 ABI: rcx=ImageHandle, rdx=SystemTable
efi_main:
    sub     rsp, 40
    mov     [rsp+32], rbx
    mov     [rsp+24], rsi

    mov     rbx, rdx                        ; save SystemTable
    mov     rsi, [rbx + EFI_SYSTEM_TABLE.ConOut]   ; ConOut protocol

    ; OutputString(ConOut, L"Hello UEFI!\\r\\n")
    lea     rdx, [msg_hello]
    mov     rcx, rsi
    call    qword [rsi + EFI_SIMPLE_TEXT_OUTPUT.OutputString]

    xor     eax, eax                        ; EFI_SUCCESS
    mov     rbx, [rsp+32]
    mov     rsi, [rsp+24]
    add     rsp, 40
    ret

section .data
msg_hello:
    dw 'H','e','l','l','o',' ','U','E','F','I','!',13,10,0
""",
    "syscall64": """\
; Linux x64 Syscall Example (reference, not Win64)
bits 64
default rel

section .text
global _start

_start:
    ; write(1, msg, msg_len)
    mov     rax, 1          ; sys_write
    mov     rdi, 1          ; stdout
    lea     rsi, [msg]
    mov     rdx, msg_len
    syscall

    ; exit(0)
    mov     rax, 60         ; sys_exit
    xor     rdi, rdi
    syscall

section .data
msg     db 'Hello, World!', 10
msg_len equ $-msg
""",
    "asmx_demo": """\
; ASMX Demo — macros estruturais
bits 64
@struct Point: x:dq; y:dq; @end
@const ORIGIN_X = 0
@const ORIGIN_Y = 0
@vector pts, dq, 16

@macro swap(a, b)
    xchg a, b
@endmacro

@macro clamp_rax(lo, hi)
    cmp rax, lo
    jge .cl_ok_%1
    mov rax, lo
.cl_ok_%1:
    cmp rax, hi
    jle .cl_hi_%1
    mov rax, hi
.cl_hi_%1:
@endmacro

section .data
origin: dq ORIGIN_X, ORIGIN_Y

section .text
global _start
_start:
    mov rax, 42
    @clamp_rax 0, 100
    @swap rax, rbx
    mov rdi, rax
    mov rax, 60
    syscall
""",
}

# ─────────────────────────────────────────────────────────────────────────────
# TOKENIZER / SYNTAX HIGHLIGHT
# ─────────────────────────────────────────────────────────────────────────────
RE_COMMENT  = re.compile(r';.*$')
RE_STRING   = re.compile(r"""(['"])(?:\\.|(?!\1).)*\1""")
RE_NUMBER   = re.compile(r'\b(?:0x[0-9a-fA-F]+|0b[01]+|0o[0-7]+|\d+)[hHqQbBoO]?\b')
RE_LABEL    = re.compile(r'^(\s*)(\.\w+|\w+)(:)', re.MULTILINE)
RE_REGISTER = re.compile(
    r'\b(r(?:ax|bx|cx|dx|si|di|bp|sp|8|9|1[0-5])'
    r'|e(?:ax|bx|cx|dx|si|di|bp|sp)'
    r'|[abcd][xlh]|[sd]il|[bs]pl'
    r'|r(?:8|9|1[0-5])[dwb]'
    r'|[cdefgs]s|[cd]r[02348]|dr[0-367]'
    r'|[xy]mm(?:1[0-5]|[0-9])'
    r'|zmm(?:3[01]|[12][0-9]|[0-9])'
    r'|mm[0-7]|k[0-7]'
    r'|rip|eip|rflags|eflags)\b', re.IGNORECASE)
RE_SECTION  = re.compile(r'\.(text|data|bss|rodata|rdata|pdata|xdata|idata|edata|reloc|tls|rsrc|debug)\b')
RE_DIRECTIVE= re.compile(r'\b(bits|default|section|segment|global|extern|common|absolute|org|times|align(?:b)?'
                          r'|d[bwdqtoy z]|res[bwdqtoy z]|equ|use\d+)\b'
                          r'|%(?:macro|endmacro|define|xdefine|undef|assign|if(?:n?def|macro|[a-z]*)?'
                          r'|elif|else|endif|include|use|push|pop|rotate|rep(?:n?)|endrep'
                          r'|error|warning|line|strcat|strlen|substr|[ix]define|[ix]macro|exitrep|exitmacro|repl)\b',
                          re.IGNORECASE)
RE_INSTR    = re.compile(
    r'^\s*(?:\.\w+:\s*)?(?:\w+:\s*)?(' +
    '|'.join(sorted(INSTRUCTIONS.keys(), key=len, reverse=True)) +
    r')\b', re.IGNORECASE | re.MULTILINE)
RE_ASMX     = re.compile(
    r'@(?:macro|endmacro|struct|end|const|vector|dict|raw)\b'
    r'|defmacro\s*\{'
    r'|usemacro\s*\{'
    r'|\benddef\b'
    r'|\bendmacro\b',
    re.IGNORECASE)

def tokenize_line(text: str) -> List[Tuple[int, int, str]]:
    """Retorna lista (start, end, 'color_key') para highlight."""
    spans = []
    comment_m = RE_COMMENT.search(text)
    comment_start = comment_m.start() if comment_m else len(text)

    # strings
    for m in RE_STRING.finditer(text):
        if m.start() < comment_start:
            spans.append((m.start(), m.end(), "string"))

    # comment
    if comment_m:
        spans.append((comment_m.start(), len(text), "comment"))

    # labels
    for m in RE_LABEL.finditer(text):
        if m.start(2) < comment_start:
            spans.append((m.start(2), m.end(3), "label"))

    # ASMX keywords (antes de directives)
    for m in RE_ASMX.finditer(text):
        if m.start() < comment_start:
            spans.append((m.start(), m.end(), "asmx"))

    # section names
    for m in RE_SECTION.finditer(text):
        if m.start() < comment_start:
            spans.append((m.start(), m.end(), "section"))

    # directives
    for m in RE_DIRECTIVE.finditer(text):
        if m.start() < comment_start:
            spans.append((m.start(), m.end(), "directive"))

    # instructions
    for m in RE_INSTR.finditer(text):
        if m.start(1) < comment_start:
            spans.append((m.start(1), m.end(1), "keyword"))

    # registers
    for m in RE_REGISTER.finditer(text):
        if m.start() < comment_start:
            spans.append((m.start(), m.end(), "register"))

    # numbers
    for m in RE_NUMBER.finditer(text):
        if m.start() < comment_start:
            spans.append((m.start(), m.end(), "number"))

    # sort and deduplicate (first-come priority)
    spans.sort(key=lambda s: s[0])
    deduped = []
    used_up = 0
    for start, end, kind in spans:
        if start >= used_up:
            deduped.append((start, end, kind))
            used_up = end
    return deduped

# ─────────────────────────────────────────────────────────────────────────────
# ASMX PREPROCESSOR  —  ASMX ↔ NASM  bidirecional
# Pipeline: source.asmx → asmx_to_asm() → NASM válido → nasm.exe
# Inverso:  source.asm  → asm_to_asmx() → ASMX anotado (heurístico)
#
# Sintaxe suportada (ASMX → NASM):
#   @const NAME = VALUE             → %define NAME VALUE
#   @vector NAME, type, count       → NAME_data: times count type 0
#                                     + %define NAME_SIZE count
#   @struct NAME:                   → %define NAME_FIELD offset  (por campo)
#     field: type [N]               → %define NAME_SIZE total
#   @end
#   @flags NAME:                    → %define NAME_FLAG 0xVALUE  (auto-bitmask)
#     FLAG [= VALUE]
#   @end
#   @dict NAME: KEY=VAL,...         → %define NAME_KEY VAL  (inline ou multi-linha)
#   @raw ASM_LINE                   → ASM_LINE  (passthrough literal)
#   @expr REG, EXPR                 → sequência de instruções aritméticas
#   @macro NAME(params)             → registra macro interna (expande em 2ª fase)
#   @endmacro
#   @NAME args                      → chamada de macro (expande em 2ª fase)
#   @include "file"                 → %include "file"
#   @link "lib"                     → ; @link lib  (anotação, sem suporte de linker aqui)
#   @criar/@ler/@modificar/@deletar/@exibir STRUCT → stubs de função NASM
#   defmacro{NAME(params)}body{enddef  → forma gramatical alternativa
#   usemacro{NAME(args)}endmacro       → chamada gramatical alternativa
#
# Compatibilidade: NASM ⊆ ASMX  (todo NASM válido passa sem alteração)
# ─────────────────────────────────────────────────────────────────────────────

# ── tamanhos NASM por tipo ──────────────────────────────────────────────────
_SZ: Dict[str, int] = {
    'db': 1, 'dw': 2, 'dd': 4, 'dq': 8, 'dt': 10, 'do': 16, 'dy': 32, 'dz': 64,
    'resb': 1, 'resw': 2, 'resd': 4, 'resq': 8,
    'byte': 1, 'word': 2, 'dword': 4, 'qword': 8,
}

def _sz(type_str: str, count_str: str = "1") -> int:
    """Calcula tamanho em bytes de um campo: tipo [N]."""
    type_str = type_str.strip().lower()
    # "db 16" → tipo=db, count=16
    parts = type_str.split()
    base_type = parts[0]
    base_sz = _SZ.get(base_type, 8)
    try:
        n = int(count_str.strip())
    except Exception:
        n = 1
    return base_sz * n


def _mx_split(s: str) -> List[str]:
    """Split de argumentos respeitando nesting de parênteses/colchetes e aspas."""
    r: List[str] = []
    b = ''
    d = 0
    q = ''
    for c in s:
        if q:
            b += c
            if c == q:
                q = ''
            continue
        if c in '"\'':
            q = c
            b += c
            continue
        if c in '([{':
            d += 1
        elif c in ')]}':
            d -= 1
        if c == ',' and d == 0:
            r.append(b.strip())
            b = ''
        else:
            b += c
    if b.strip():
        r.append(b.strip())
    return r


def _mx_rep(text: str, mapping: Dict[str, str]) -> str:
    """Substituição de parâmetros de macro no corpo de uma linha."""
    # %1, %2, ... (sem word boundary)
    for k, v in sorted(
            ((k, v) for k, v in mapping.items() if k.startswith('%')),
            key=lambda x: -len(x[0])):
        text = text.replace(k, v)
    # nomes simbólicos (com word boundary)
    for k, v in sorted(
            ((k, v) for k, v in mapping.items() if not k.startswith('%')),
            key=lambda x: -len(x[0])):
        text = re.sub(rf'\b{re.escape(k)}\b', v, text)
    return text


def _parse_field(fld: str) -> tuple:
    """
    Parseia uma declaração de campo: "name: type [count]"
    Retorna (field_name, type_str, count_int, size_bytes).
    Suporta: 'x: dq', 'name: db 16', 'hp: dd', etc.
    """
    fld = fld.strip()
    if ':' not in fld:
        return None, None, 1, 0
    fn_raw, ft_raw = fld.split(':', 1)
    fn = fn_raw.strip()
    ft = ft_raw.strip()
    # separa tipo de count: "db 16" → type=db, count=16
    tp_parts = ft.split()
    base_type = tp_parts[0].lower() if tp_parts else 'dq'
    count = 1
    if len(tp_parts) >= 2:
        try:
            count = int(tp_parts[1], 0)
        except ValueError:
            count = 1
    size = _SZ.get(base_type, 8) * count
    return fn, base_type, count, size


def _expand_struct_fields(fields_text: str, nm_up: str) -> List[str]:
    """
    Dado o texto de campos (separado por ';' ou newlines),
    gera as linhas %define STRUCT_FIELD offset e %define STRUCT_SIZE total.
    """
    # normaliza: substitui newlines por ';'
    text = re.sub(r'\s*\n\s*', ';', fields_text)
    raw_fields = [x.strip() for x in text.split(';') if x.strip()]
    out = []
    off = 0
    for fld in raw_fields:
        fn, base_type, count, size = _parse_field(fld)
        if fn is None:
            continue
        out.append(f'%define {nm_up}_{fn.upper()} {off}')
        off += size
    out.append(f'%define {nm_up}_SIZE {off}')
    return out


def _auto_flags(entries: List[tuple]) -> List[tuple]:
    """
    Atribui máscaras de bit automáticas para entradas sem valor.
    entries: lista de (name, value_or_None)
    Retorna: lista de (name, int_mask)
    """
    used = set()
    fixed = {}
    for name, val in entries:
        if val is not None:
            try:
                v = int(str(val).strip(), 0)
                used.add(v)
                fixed[name] = v
            except Exception:
                pass
    result = []
    auto_bit = 1
    for name, val in entries:
        if name in fixed:
            result.append((name, fixed[name]))
            # avança auto_bit para além do valor fixado
            while auto_bit <= fixed[name]:
                auto_bit <<= 1
        else:
            while auto_bit in used:
                auto_bit <<= 1
            result.append((name, auto_bit))
            used.add(auto_bit)
            auto_bit <<= 1
    return result


def _expand_expr(dest_reg: str, expr: str, uid_counter: List[int]) -> List[str]:
    """
    Expansão simplificada de @expr dest, EXPR para sequência NASM.
    Suporta: operadores +,-,*,/,%,&,|,^,<<,>>,==,!=,<,>,<=,>=,sqrt()
    Para expressões complexas, emite mov + operações sequenciais.
    """
    out = []
    expr = expr.strip()
    uid = uid_counter[0]
    uid_counter[0] += 1

    # sqrt(x) → FPU sequence
    m_sqrt = re.match(r'^sqrt\s*\(\s*(.+)\s*\)$', expr)
    if m_sqrt:
        arg = m_sqrt.group(1).strip()
        lbl = f'__sqrt_tmp_{uid}'
        out += [
            f'section .data',
            f'{lbl}: dq 0',
            f'section .text',
            f'    mov qword [{lbl}], {arg}',
            f'    fild qword [{lbl}]',
            f'    fsqrt',
            f'    fistp qword [{lbl}]',
            f'    mov {dest_reg}, [{lbl}]',
        ]
        return out

    # Detecta operador de comparação → setcc
    cmp_ops = [('==', 'sete'), ('!=', 'setne'), ('<=', 'setle'),
               ('>=', 'setge'), ('<', 'setl'), ('>', 'setg')]
    for op_str, setcc in cmp_ops:
        if op_str in expr:
            parts = expr.split(op_str, 1)
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            out += [
                f'    mov {dest_reg}, {lhs}',
                f'    cmp {dest_reg}, {rhs}',
                f'    {setcc} al',
                f'    movzx {dest_reg}, al',
            ]
            return out

    # Operadores binários aritméticos — ordem de precedência (mais fraca primeiro)
    # Tenta dividir na última ocorrência de cada operador de baixa precedência
    def split_last(e: str, op: str) -> Optional[tuple]:
        """Encontra última ocorrência de op fora de parênteses."""
        depth = 0
        pos = -1
        op_len = len(op)
        i = len(e) - op_len
        while i >= 0:
            ch = e[i:i+op_len]
            # conta parênteses da direita para esquerda
            for j in range(i + op_len - 1, i - 1, -1):
                if e[j] == ')':
                    depth += 1
                elif e[j] == '(':
                    depth -= 1
            if ch == op and depth == 0:
                pos = i
                break
            i -= 1
        if pos < 0:
            return None
        return e[:pos].strip(), e[pos+op_len:].strip()

    bin_ops = [
        ('|', 'or'), ('^', 'xor'), ('&', 'and'),
        ('<<', 'shl'), ('>>', 'shr'),
        ('+', 'add'), ('-', 'sub'),
        ('*', 'imul'), ('/', None), ('%', None),
    ]
    for op_str, nasm_op in bin_ops:
        res = split_last(expr, op_str)
        if res:
            lhs, rhs = res
            if not lhs:
                continue
            out.append(f'    mov {dest_reg}, {lhs}')
            if op_str == '/':
                out += [
                    f'    mov rax, {dest_reg}',
                    f'    cqo',
                    f'    idiv {rhs}',
                    f'    mov {dest_reg}, rax',
                ]
            elif op_str == '%':
                out += [
                    f'    mov rax, {dest_reg}',
                    f'    cqo',
                    f'    idiv {rhs}',
                    f'    mov {dest_reg}, rdx',
                ]
            elif op_str == '<<':
                out += [
                    f'    mov rcx, {rhs}',
                    f'    shl {dest_reg}, cl',
                ]
            elif op_str == '>>':
                out += [
                    f'    mov rcx, {rhs}',
                    f'    shr {dest_reg}, cl',
                ]
            elif op_str == '*':
                out.append(f'    imul {dest_reg}, {rhs}')
            else:
                out.append(f'    {nasm_op} {dest_reg}, {rhs}')
            return out

    # Unário ~ (NOT) ou negação
    if expr.startswith('~'):
        inner = expr[1:].strip()
        out += [f'    mov {dest_reg}, {inner}', f'    not {dest_reg}']
        return out
    if expr.startswith('-'):
        inner = expr[1:].strip()
        out += [f'    mov {dest_reg}, {inner}', f'    neg {dest_reg}']
        return out

    # Simples mov (literal, registrador, referência de memória)
    out.append(f'    mov {dest_reg}, {expr}')
    return out


def _expand_crud_op(op: str, struct_name: str) -> List[str]:
    """Gera stub NASM para operações CRUD sobre uma struct."""
    fn = f'{struct_name.lower()}_{op}'
    return [
        f'; CRUD stub: {fn}',
        f'{fn}:',
        f'    ; TODO: implementar {op} para {struct_name}',
        f'    ret',
        f'',
    ]


def asmx_to_asm(src: str) -> str:
    """
    ASMX → NASM: expansão estrutural completa em uma única leitura do texto.
    Fase 1: expansão de blocos estruturais (@struct, @flags, @const, @vector,
            @dict, @raw, @expr, @include, @link, CRUD, @macro/@endmacro).
    Fase 2: expansão iterativa de chamadas de macro (até 64 passes).
    Retorna NASM válido pronto para nasm.exe.
    """
    # ── Pré-normalização: separa multi-statements ASMX na mesma linha ─────
    # Ex: "@const W=80; @const H=25" → duas linhas
    # NÃO quebra linhas que contenham @struct ... @end na mesma linha
    def _pre_split(text: str) -> str:
        result_lines = []
        for ln in text.splitlines():
            s = ln.strip()
            # deixa intacto se é um bloco em linha única (@struct/@flags com @end)
            if re.search(r'@(?:struct|flags)\b.*@end', s, re.IGNORECASE):
                result_lines.append(ln)
                continue
            # separa apenas @const/@vector/@expr/@raw/@include/@link
            ln2 = re.sub(r'\s*;\s*(@(?:const|vector|expr|raw|include|link)\b)',
                         r'\n\1', ln)
            result_lines.append(ln2)
        return '\n'.join(result_lines)

    src = _pre_split(src)

    L = src.splitlines()
    O: List[str] = []
    M: Dict[str, tuple] = {}   # macros: name → (params_list, body_lines)
    uid_counter = [0]

    def next_uid() -> str:
        v = uid_counter[0]
        uid_counter[0] += 1
        return str(v)

    i = 0
    while i < len(L):
        raw = L[i]
        s = raw.strip()

        # ── linha vazia ou comentário puro → passthrough ──────────────────
        if not s or s.startswith(';'):
            O.append(raw)
            i += 1
            continue

        # ── remove comentário inline para match (preserva linha original) ─
        # Para @struct/@flags em linha única (com @end), não strip semicolons
        if re.search(r'@(?:struct|flags)\b.*@end', s, re.IGNORECASE):
            s_no_comment = s
        else:
            # Encontra o primeiro ';' fora de strings
            _in_q = ''
            _nc_idx = len(s)
            for _ci, _ch in enumerate(s):
                if _in_q:
                    if _ch == _in_q:
                        _in_q = ''
                elif _ch in ('"', "'"):
                    _in_q = _ch
                elif _ch == ';':
                    _nc_idx = _ci
                    break
            s_no_comment = s[:_nc_idx].strip()

        # ══════════════════════════════════════════════════════════════════
        # @macro NAME(params) ... @endmacro
        # ══════════════════════════════════════════════════════════════════
        mdef = (re.match(r'@macro\s+(\w+)\s*\((.*?)\)', s_no_comment) or
                re.match(r'defmacro\{\s*(\w+)\s*\((.*?)\)\s*\}', s_no_comment))
        if mdef:
            nm = mdef.group(1)
            ps = [x.strip() for x in _mx_split(mdef.group(2)) if x.strip()]
            body: List[str] = []
            i += 1
            while i < len(L):
                t = L[i].strip()
                tl = t.lower()
                if tl.startswith('@endmacro') or tl.startswith('enddef'):
                    i += 1
                    break
                body.append(L[i])
                i += 1
            M[nm] = (ps, body)
            continue

        # ══════════════════════════════════════════════════════════════════
        # @const NAME = VALUE
        # ══════════════════════════════════════════════════════════════════
        mc = re.match(r'@const\s+(\w+)\s*=\s*(.+)', s_no_comment)
        if mc:
            name = mc.group(1)
            val = mc.group(2).strip().rstrip(';').strip()
            O.append(f'%define {name} ({val})')
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @vector NAME, type, count
        # ══════════════════════════════════════════════════════════════════
        mv = re.match(r'@vector\s+(\w+)\s*,\s*(\w+)\s*,\s*(.+)', s_no_comment)
        if mv:
            nm, tp, cnt = mv.groups()
            cnt = cnt.strip().rstrip(';').strip()
            sz = _SZ.get(tp.lower(), 8)
            O.append(f'{nm}_data: times {cnt} {tp} 0')
            O.append(f'%define {nm.upper()}_SIZE ({cnt})')
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @struct NAME: ...fields... @end  (single-line)
        # ══════════════════════════════════════════════════════════════════
        ms1 = re.match(r'@struct\s+(\w+)\s*:(.*?)@end', s_no_comment, re.IGNORECASE)
        if ms1:
            nm_up = ms1.group(1).upper()
            body_s = ms1.group(2)
            O.extend(_expand_struct_fields(body_s, nm_up))
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @struct NAME: ...fields...   (multi-line até @end)
        # ══════════════════════════════════════════════════════════════════
        ms2 = re.match(r'@struct\s+(\w+)\s*:(.*)', s_no_comment)
        if ms2:
            nm_up = ms2.group(1).upper()
            # coleta campos: resto da primeira linha + linhas seguintes até @end
            body_parts = [ms2.group(2).strip()]
            i += 1
            while i < len(L):
                t = L[i].strip()
                tl = t.lower()
                if re.match(r'@end\b', tl, re.IGNORECASE):
                    i += 1
                    break
                # campo inline com @end na mesma linha
                if '@end' in tl:
                    idx = tl.index('@end')
                    body_parts.append(t[:idx])
                    i += 1
                    break
                body_parts.append(t)
                i += 1
            body_s = ' ; '.join(p for p in body_parts if p)
            O.extend(_expand_struct_fields(body_s, nm_up))
            continue

        # ══════════════════════════════════════════════════════════════════
        # @flags NAME: ...entries... @end  (single-line)
        # ══════════════════════════════════════════════════════════════════
        mf1 = re.match(r'@flags\s+(\w+)\s*:(.*?)@end', s_no_comment, re.IGNORECASE)
        if mf1:
            nm_up = mf1.group(1).upper()
            entries_text = mf1.group(2)
            entries_raw = [x.strip() for x in re.split(r'[;,\n]', entries_text) if x.strip()]
            entries = []
            for e in entries_raw:
                em = re.match(r'(\w+)\s*(?:=\s*(.+))?', e)
                if em:
                    val = em.group(2)
                    entries.append((em.group(1), val))
            for name, mask in _auto_flags(entries):
                O.append(f'%define {nm_up}_{name.upper()} 0x{mask:X}')
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @flags NAME: ...entries...  (multi-line até @end)
        # ══════════════════════════════════════════════════════════════════
        mf2 = re.match(r'@flags\s+(\w+)\s*:(.*)', s_no_comment)
        if mf2:
            nm_up = mf2.group(1).upper()
            entries_parts = [mf2.group(2).strip()]
            i += 1
            while i < len(L):
                t = L[i].strip()
                tl = t.lower()
                if re.match(r'@end\b', tl, re.IGNORECASE):
                    i += 1
                    break
                if '@end' in tl:
                    idx = tl.index('@end')
                    entries_parts.append(t[:idx])
                    i += 1
                    break
                # ignora linhas de comentário puro dentro do bloco
                if not t or t.startswith(';'):
                    i += 1
                    continue
                entries_parts.append(t)
                i += 1
            entries_raw = []
            for part in entries_parts:
                for seg in re.split(r'[;\n]', part):
                    seg = seg.strip()
                    if seg and not seg.startswith(';'):
                        entries_raw.append(seg)
            entries = []
            for e in entries_raw:
                em = re.match(r'(\w+)\s*(?:=\s*(.+))?', e)
                if em:
                    entries.append((em.group(1), em.group(2)))
            for name, mask in _auto_flags(entries):
                O.append(f'%define {nm_up}_{name.upper()} 0x{mask:X}')
            continue

        # ══════════════════════════════════════════════════════════════════
        # @dict NAME: KEY=VAL, KEY=VAL,...  (inline, tudo na mesma linha)
        # ══════════════════════════════════════════════════════════════════
        md_inline = re.match(r'@dict\s+(\w+)\s*:\s*(.+)', s_no_comment)
        if md_inline:
            dname = md_inline.group(1)
            pairs_str = md_inline.group(2)
            pairs = [p.strip() for p in pairs_str.split(',') if p.strip()]
            O.append(f'; dict {dname}')
            for pair in pairs:
                km = re.match(r'(\w+)\s*=\s*(.+)', pair)
                if km:
                    key = km.group(1)
                    val = km.group(2).strip().rstrip(';').strip()
                    O.append(f'%define {dname.upper()}_{key.upper()} ({val})')
                    # macro de comparação NASM: %macro DICT_IS_KEY 2
                    O.append(f'%macro {dname.upper()}_IS_{key.upper()} 2')
                    O.append(f'    cmp %1, {dname.upper()}_{key.upper()}')
                    O.append(f'    je %2')
                    O.append(f'%endmacro')
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @dict NAME:  (multi-linha, KEY=VAL em linhas separadas)
        # Termina em linha vazia, @, section/global/bits, ou @end
        # ══════════════════════════════════════════════════════════════════
        md_block = re.match(r'@dict\s+(\w+)\s*:\s*$', s_no_comment)
        if md_block:
            dname = md_block.group(1)
            O.append(f'; dict {dname}')
            i += 1
            while i < len(L):
                t = L[i].strip()
                tl = t.lower()
                if (not t or
                        re.match(r'@end\b', tl) or
                        t.startswith('@') or
                        re.match(r'(?:section|global|bits|extern)\s', tl)):
                    break
                if t.startswith(';'):
                    i += 1
                    continue
                km = re.match(r'(\w+)\s*=\s*(.+)', t)
                if km:
                    key = km.group(1)
                    val = km.group(2).strip().rstrip(';').strip()
                    O.append(f'%define {dname.upper()}_{key.upper()} ({val})')
                    O.append(f'%macro {dname.upper()}_IS_{key.upper()} 2')
                    O.append(f'    cmp %1, {dname.upper()}_{key.upper()}')
                    O.append(f'    je %2')
                    O.append(f'%endmacro')
                else:
                    O.append(L[i])
                i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @raw REST  → passthrough literal
        # ══════════════════════════════════════════════════════════════════
        mr = re.match(r'@raw\s+(.*)', s_no_comment)
        if mr:
            O.append(mr.group(1))
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @include "file"  →  %include "file"
        # ══════════════════════════════════════════════════════════════════
        mi = re.match(r'@include\s+(.+)', s_no_comment)
        if mi:
            O.append(f'%include {mi.group(1).strip()}')
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @link "lib"  →  comentário (não compilável sem linker externo)
        # ══════════════════════════════════════════════════════════════════
        ml = re.match(r'@link\s+(.*)', s_no_comment)
        if ml:
            O.append(f'; @link {ml.group(1).strip()}')
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @expr REG, EXPRESSION
        # ══════════════════════════════════════════════════════════════════
        me = re.match(r'@expr\s+(\w+)\s*,\s*(.+)', s_no_comment)
        if me:
            dest_reg = me.group(1).strip()
            expr_str = me.group(2).strip()
            O.extend(_expand_expr(dest_reg, expr_str, uid_counter))
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @criar / @ler / @modificar / @deletar / @exibir STRUCT
        # ══════════════════════════════════════════════════════════════════
        crud_m = re.match(r'@(criar|ler|modificar|deletar|exibir)\s+(\w+)', s_no_comment, re.IGNORECASE)
        if crud_m:
            op = crud_m.group(1).lower()
            struct_name = crud_m.group(2)
            O.extend(_expand_crud_op(op, struct_name))
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # @end  isolado (fecha blocos não capturados — descarta silenciosamente)
        # ══════════════════════════════════════════════════════════════════
        if re.match(r'@end\b', s_no_comment, re.IGNORECASE):
            i += 1
            continue

        # ══════════════════════════════════════════════════════════════════
        # passthrough — NASM puro ou linha sem @-keyword no início
        # ══════════════════════════════════════════════════════════════════
        O.append(raw)
        i += 1

    # ── FASE 2: EXPANSÃO RECURSIVA DE MACROS (até 64 passes) ─────────────
    for _pass in range(64):
        N: List[str] = []
        changed = False

        for ln in O:
            s = ln.strip()
            hit = False

            for nm, (ps, body) in M.items():
                # ── Forma 1: @name arg1, arg2  ou  @name(arg1, arg2) ────
                m1 = re.match(rf'@{re.escape(nm)}\s*(.*)', s, re.IGNORECASE)
                if m1:
                    rest = m1.group(1).strip()
                    if rest.startswith('(') and rest.endswith(')'):
                        rest = rest[1:-1]
                    vals = _mx_split(rest) if rest else []
                    mp: Dict[str, str] = {}
                    for j, param in enumerate(ps):
                        mp[param] = vals[j] if j < len(vals) else ''
                    mp['%1'] = next_uid()
                    for bl in body:
                        N.append(_mx_rep(bl, mp))
                    changed = True
                    hit = True
                    break

                # ── Forma 2: usemacro{name(args)}endmacro ───────────────
                m2 = re.match(
                    rf'usemacro\{{\s*{re.escape(nm)}\s*\((.*?)\)\s*\}}endmacro',
                    s, re.IGNORECASE | re.DOTALL)
                if m2:
                    vals = _mx_split(m2.group(1))
                    mp = {}
                    for j, param in enumerate(ps):
                        mp[param] = vals[j] if j < len(vals) else ''
                    mp['%1'] = next_uid()
                    for bl in body:
                        N.append(_mx_rep(bl, mp))
                    changed = True
                    hit = True
                    break

            if not hit:
                N.append(ln)

        O = N
        if not changed:
            break

    return '\n'.join(O)


def asm_to_asmx(src: str) -> str:
    """
    NASM → ASMX (heurístico reverso, single-pass com detecção estrutural).
    Detecta padrões NASM gerados por asmx_to_asm e reconstrói sintaxe ASMX.
    Converte:
      %define NAME (VALUE)            → @const NAME = VALUE
      %define NAME VALUE              → @const NAME = VALUE
      %define STRUCT_FIELD offset     → agrupado em @struct ... @end
      %define STRUCT_SIZE total       → (termina o bloco @struct)
      %macro NAME N ... %endmacro     → @macro NAME(p1,...,pN) ... @endmacro
      NAME_data: times N type 0       → @vector NAME, type, N
      %define FLAG_NAME 0xVAL         → agrupado em @flags ... @end
      DICT_IS_KEY macros + defines    → @dict NAME: KEY=VAL,...
    Preserva sem alteração o que não reconhece.
    """
    lines = src.splitlines()
    out: List[str] = []
    i = 0

    # acumula %define STRUCT_FIELD para reconstruir @struct
    struct_defs: Dict[str, List[tuple]] = {}  # STRUCT → [(field, offset)]
    struct_sizes: Dict[str, int] = {}

    # acumula %define FLAG para reconstruir @flags
    flag_defs: Dict[str, List[tuple]] = {}    # FLAGS → [(name, mask)]

    # rastreia macros IS_ (geradas por @dict)
    dict_macros_seen: set = set()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── %define NAME_FIELD offset  (struct offset gerado por @struct) ─
        m = re.match(r'%define\s+([A-Z][A-Z0-9_]+)_([A-Z][A-Z0-9_]+)\s+(\d+)\s*$', stripped)
        if m:
            struct_up = m.group(1)
            field_up = m.group(2)
            offset = int(m.group(3))
            if field_up == 'SIZE':
                struct_sizes[struct_up] = offset
                # emite bloco @struct completo
                if struct_up in struct_defs:
                    out.append(f'@struct {struct_up.capitalize()}:')
                    for fname, foff in struct_defs[struct_up]:
                        # não temos o tipo original — usamos dq como default
                        out.append(f'    {fname.lower()}: dq')
                    out.append('@end')
                    del struct_defs[struct_up]
                # se não havia campos, emite só o SIZE como comentário
            else:
                struct_defs.setdefault(struct_up, []).append((field_up, offset))
            i += 1
            continue

        # ── %define NAME (0xVAL)  (flag bitmask gerado por @flags) ────────
        m = re.match(r'%define\s+([A-Z][A-Z0-9_]+)_(FLAG_\w+|[A-Z][A-Z0-9_]+)\s+\(?0x([0-9A-Fa-f]+)\)?\s*$', stripped)
        if m:
            prefix = m.group(1)
            flag_name = m.group(2)
            mask_hex = m.group(3)
            mask = int(mask_hex, 16)
            # só acumula se a máscara é potência de 2 (flag bitmask)
            if mask > 0 and (mask & (mask - 1)) == 0:
                flag_defs.setdefault(prefix, []).append((flag_name, mask))
                i += 1
                continue

        # ── %macro NAME_IS_KEY 2 / %endmacro  (dict IS macro) ─────────────
        m = re.match(r'%macro\s+([A-Z][A-Z0-9_]+)_IS_([A-Z][A-Z0-9_]+)\s+2\s*$', stripped)
        if m:
            dict_macros_seen.add((m.group(1), m.group(2)))
            # consome o bloco (cmp + je + %endmacro)
            i += 1
            while i < len(lines):
                t = lines[i].strip()
                i += 1
                if t.startswith('%endmacro'):
                    break
            continue

        # ── %define DICT_KEY (VAL)  → quando seguido de macro IS_KEY ───────
        # detectado pelo flag dict_macros_seen acumulado acima.
        # Emite @dict ao finalizar um grupo de defines+macros do mesmo prefixo.
        # (simplificado: converte %define simples não-struct não-flag)
        m = re.match(r'%define\s+([A-Z][A-Z0-9_]+)_([A-Z][A-Z0-9_]+)\s+\((.+)\)\s*$', stripped)
        if m:
            prefix = m.group(1)
            key = m.group(2)
            val = m.group(3)
            if (prefix, key) in dict_macros_seen:
                out.append(f'; @dict {prefix.lower()}: {key}={val}  (ver macros abaixo)')
                i += 1
                continue

        # ── %define NAME (VALUE)  ou  %define NAME VALUE  → @const ─────────
        m = re.match(r'%define\s+(\w+)\s+\((.+)\)\s*$', stripped)
        if not m:
            m = re.match(r'%define\s+(\w+)\s+(\S+)\s*$', stripped)
        if m:
            name = m.group(1)
            val = m.group(2)
            # não converte struct offsets ou flags (já tratados acima)
            is_struct_ref = bool(re.match(r'[A-Z][A-Z0-9_]+_[A-Z][A-Z0-9_]+$', name))
            if not is_struct_ref:
                out.append(f'@const {name} = {val}')
                i += 1
                continue

        # ── %macro NAME N → @macro NAME(p1,...,pN) ... @endmacro ──────────
        m = re.match(r'%macro\s+(\w+)\s+(\d+)', stripped)
        if m:
            nm = m.group(1)
            n = int(m.group(2))
            params = ', '.join(f'p{k+1}' for k in range(n))
            out.append(f'@macro {nm}({params})')
            i += 1
            while i < len(lines):
                t = lines[i].strip()
                if t.startswith('%endmacro'):
                    out.append('@endmacro')
                    i += 1
                    break
                bl = lines[i]
                for k in range(n):
                    bl = bl.replace(f'%{k+1}', f'p{k+1}')
                out.append(bl)
                i += 1
            continue

        # ── NAME_data: times N type 0  →  @vector NAME, type, N ───────────
        m = re.match(r'(\w+)_data:\s+times\s+(.+?)\s+(\w+)\s+0\s*$', stripped)
        if not m:
            m = re.match(r'(\w+)_data:\s+times\s+(.+?)\s+(\w+)\s+0', stripped)
        if m:
            out.append(f'@vector {m.group(1)}, {m.group(3)}, {m.group(2)}')
            i += 1
            continue

        # ── ; CRUD stub: funcname → reescreve como @crud ──────────────────
        m = re.match(r';\s*CRUD stub:\s*(\w+)_(\w+)', stripped)
        if m:
            struct_name = m.group(1)
            op = m.group(2)
            out.append(f'@{op} {struct_name.capitalize()}')
            # consome até o próximo 'ret' + linha vazia
            i += 1
            while i < len(lines):
                t = lines[i].strip()
                i += 1
                if t == '' or t == 'ret':
                    if t == 'ret':
                        break
                if t.startswith(';') or t.endswith(':') or t == 'ret':
                    continue
            continue

        # ── ; @dict dname  (comentário de @dict gerado) ────────────────────
        m = re.match(r';\s*@dict\s+(\w+)', stripped)
        if m:
            out.append(f'; @dict {m.group(1)}')
            i += 1
            continue

        # ── ; @link  →  @link ───────────────────────────────────────────────
        m = re.match(r';\s*@link\s+(.*)', stripped)
        if m:
            out.append(f'@link {m.group(1).strip()}')
            i += 1
            continue

        # ── passthrough ─────────────────────────────────────────────────────
        out.append(line)
        i += 1

    # flush pending @flags blocks
    for prefix, entries in flag_defs.items():
        # insere antes de qualquer referência a este prefix
        # (simplificado: apenas emite ao final como comentário estruturado)
        flag_lines = [f'@flags {prefix.capitalize()}:']
        for name, mask in entries:
            flag_lines.append(f'    {name} = 0x{mask:X}')
        flag_lines.append('@end')
        # adiciona no início do output (antes de section .text)
        insert_at = 0
        for idx, ln in enumerate(out):
            if re.match(r'section\s+\.text', ln.strip(), re.I):
                insert_at = idx
                break
        for fi, fl in enumerate(flag_lines):
            out.insert(insert_at + fi, fl)

    return '\n'.join(out)


# ─────────────────────────────────────────────────────────────────────────────
# NASM RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def find_nasm() -> str:
    for cand in ["nasm", "nasm.exe",
                 "./nasm/nasm.exe", "./nasm/nasm",
                 "nasm/nasm.exe",
                 "../nasm/nasm.exe"]:
        found = shutil.which(cand)
        if found:
            return found
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return "nasm"

def find_golink() -> str:
    for cand in ["GoLink", "GoLink.exe",
                 "./nasm/GoLink.exe", "./nasm/GoLink",
                 "nasm/GoLink.exe",
                 "../nasm/GoLink.exe"]:
        found = shutil.which(cand)
        if found:
            return found
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return "GoLink"

@dataclass
class BuildResult:
    ok: bool = False
    mode: str = ""
    log: str = ""
    output_file: str = ""
    errors: List[Dict] = field(default_factory=list)

def parse_nasm_errors(stderr: str) -> List[Dict]:
    errs = []
    for line in stderr.splitlines():
        m = re.match(r'(.+?):(\d+):\s*(error|warning|note):\s*(.*)', line, re.IGNORECASE)
        if m:
            errs.append({
                "file": m.group(1),
                "line": int(m.group(2)),
                "sev":  m.group(3).lower(),
                "msg":  m.group(4),
            })
    return errs

def run_nasm(source_text: str, mode: str, source_path: Optional[str] = None) -> BuildResult:
    """
    Modes: bin | obj_win64 | obj_elf64 | exe_win64 | bios | uefi
    """
    nasm = find_nasm()
    golink = find_golink()
    tmp = tempfile.mkdtemp(prefix="nasmed_")
    base = os.path.splitext(os.path.basename(source_path))[0] if source_path else "output"
    asm_path = os.path.join(tmp, base + ".asm")
    
    with open(asm_path, "w", encoding="utf-8") as f:
        f.write(source_text)

    result = BuildResult(mode=mode)

    # 1. Montagem (NASM)
    if mode == "bin" or mode == "bios":
        out = os.path.join(tmp, base + ".bin")
        cmd = [nasm, "-f", "bin", asm_path, "-o", out, "-l", os.path.join(tmp, base+".lst")]
    elif mode == "obj_win64":
        out = os.path.join(tmp, base + ".obj")
        cmd = [nasm, "-f", "win64", asm_path, "-o", out, "-l", os.path.join(tmp, base+".lst")]
    elif mode == "obj_elf64":
        out = os.path.join(tmp, base + ".o")
        cmd = [nasm, "-f", "elf64", asm_path, "-o", out, "-l", os.path.join(tmp, base+".lst")]
    elif mode == "uefi":
        out = os.path.join(tmp, base + ".obj")
        cmd = [nasm, "-f", "win64", asm_path, "-o", out, "-l", os.path.join(tmp, base+".lst")]
    elif mode == "exe_win64":
        out = os.path.join(tmp, base + ".obj")
        cmd = [nasm, "-f", "win64", asm_path, "-o", out, "-l", os.path.join(tmp, base+".lst")]
    else:
        result.log = f"Modo desconhecido: {mode}"
        return result

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        result.log = (stdout + "\n" + stderr).strip()
        result.errors = parse_nasm_errors(stderr)
        
        if proc.returncode != 0:
            result.ok = False
            return result

        # 2. Linkagem (GoLink) se necessário
        if mode == "exe_win64":
            exe_out = os.path.join(tmp, base + ".exe")
            # GoLink /entry main obj kernel32.dll user32.dll ...
            link_cmd = [
                golink, "/entry", "main", out,
                "kernel32.dll", "user32.dll", "gdi32.dll", "gdiplus.dll", 
                "opengl32.dll", "d3d12.dll"
            ]
            link_proc = subprocess.run(link_cmd, capture_output=True, text=True, timeout=10)
            result.log += "\n--- Linker Output ---\n"
            result.log += (link_proc.stdout or "") + (link_proc.stderr or "")
            
            if link_proc.returncode == 0:
                result.ok = True
                result.output_file = exe_out
                # Opcional: mover para a pasta do fonte se existir
                if source_path:
                    target_exe = str(Path(source_path).with_suffix(".exe"))
                    try:
                        shutil.copy2(exe_out, target_exe)
                        result.output_file = target_exe
                        result.log += f"\nExecutável gerado: {target_exe}"
                    except:
                        pass
            else:
                result.ok = False
                result.log += f"\nErro na linkagem (GoLink). Código: {link_proc.returncode}"
        else:
            result.ok = True
            result.output_file = out

    except FileNotFoundError as e:
        result.log = f"ERRO: Ferramenta não encontrada.\nTente colocar nasm.exe e GoLink.exe na pasta ./nasm/ ou no PATH."
    except subprocess.TimeoutExpired:
        result.log = "ERRO: Timeout ao executar ferramentas (>15s)."
    except Exception as e:
        result.log = f"ERRO inesperado: {e}"

    return result

# ─────────────────────────────────────────────────────────────────────────────
# EDITOR STATE
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class EditorState:
    lines: List[str] = field(default_factory=lambda: [""])
    cursor_row: int = 0
    cursor_col: int = 0
    sel_start: Optional[Tuple[int, int]] = None
    sel_end:   Optional[Tuple[int, int]] = None
    scroll_row: int = 0
    scroll_col: int = 0
    undo_stack: List = field(default_factory=list)
    redo_stack: List = field(default_factory=list)
    modified: bool = False
    file_path: Optional[str] = None

    def snapshot(self):
        return (
            [l for l in self.lines],
            self.cursor_row, self.cursor_col,
        )

    def push_undo(self):
        snap = self.snapshot()
        if self.undo_stack and self.undo_stack[-1] == snap:
            return
        self.undo_stack.append(snap)
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.modified = True

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.snapshot())
        lines, r, c = self.undo_stack.pop()
        self.lines = lines
        self.cursor_row = r
        self.cursor_col = c
        self.sel_start = self.sel_end = None

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.snapshot())
        lines, r, c = self.redo_stack.pop()
        self.lines = lines
        self.cursor_row = r
        self.cursor_col = c
        self.sel_start = self.sel_end = None

    def clamp_cursor(self):
        self.cursor_row = max(0, min(self.cursor_row, len(self.lines) - 1))
        self.cursor_col = max(0, min(self.cursor_col, len(self.lines[self.cursor_row])))

    def selection_ordered(self):
        if not self.sel_start or not self.sel_end:
            return None, None
        a, b = self.sel_start, self.sel_end
        if a > b:
            a, b = b, a
        return a, b

    def get_selected_text(self) -> str:
        a, b = self.selection_ordered()
        if a is None:
            return ""
        if a[0] == b[0]:
            return self.lines[a[0]][a[1]:b[1]]
        parts = [self.lines[a[0]][a[1]:]]
        for r in range(a[0]+1, b[0]):
            parts.append(self.lines[r])
        parts.append(self.lines[b[0]][:b[1]])
        return "\n".join(parts)

    def delete_selection(self):
        a, b = self.selection_ordered()
        if a is None:
            return
        if a[0] == b[0]:
            line = self.lines[a[0]]
            self.lines[a[0]] = line[:a[1]] + line[b[1]:]
        else:
            first = self.lines[a[0]][:a[1]]
            last  = self.lines[b[0]][b[1]:]
            self.lines = (self.lines[:a[0]] +
                          [first + last] +
                          self.lines[b[0]+1:])
        self.cursor_row, self.cursor_col = a
        self.sel_start = self.sel_end = None
        self.clamp_cursor()

    def insert_text(self, text: str):
        self.push_undo()
        if self.sel_start and self.sel_end:
            self.delete_selection()
        for ch in text:
            if ch == "\n":
                line = self.lines[self.cursor_row]
                rest = line[self.cursor_col:]
                self.lines[self.cursor_row] = line[:self.cursor_col]
                self.lines.insert(self.cursor_row + 1, rest)
                self.cursor_row += 1
                self.cursor_col = 0
            else:
                line = self.lines[self.cursor_row]
                self.lines[self.cursor_row] = line[:self.cursor_col] + ch + line[self.cursor_col:]
                self.cursor_col += 1

    def backspace(self):
        self.push_undo()
        if self.sel_start and self.sel_end:
            self.delete_selection()
            return
        if self.cursor_col > 0:
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = line[:self.cursor_col-1] + line[self.cursor_col:]
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            prev = self.lines[self.cursor_row - 1]
            curr = self.lines[self.cursor_row]
            self.cursor_col = len(prev)
            self.lines[self.cursor_row - 1] = prev + curr
            self.lines.pop(self.cursor_row)
            self.cursor_row -= 1

    def delete_forward(self):
        self.push_undo()
        if self.sel_start and self.sel_end:
            self.delete_selection()
            return
        line = self.lines[self.cursor_row]
        if self.cursor_col < len(line):
            self.lines[self.cursor_row] = line[:self.cursor_col] + line[self.cursor_col+1:]
        elif self.cursor_row < len(self.lines) - 1:
            next_line = self.lines.pop(self.cursor_row + 1)
            self.lines[self.cursor_row] = line + next_line

    def handle_tab(self, reverse=False):
        self.push_undo()
        if reverse:
            line = self.lines[self.cursor_row]
            if line.startswith("\t"):
                self.lines[self.cursor_row] = line[1:]
                self.cursor_col = max(0, self.cursor_col - 1)
            elif line.startswith(" " * TAB_SIZE):
                self.lines[self.cursor_row] = line[TAB_SIZE:]
                self.cursor_col = max(0, self.cursor_col - TAB_SIZE)
        else:
            self.insert_text("    ")  # 4 spaces

    def get_text(self) -> str:
        return "\n".join(self.lines)

    def set_text(self, text: str):
        self.lines = text.splitlines() or [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self.sel_start = self.sel_end = None
        self.scroll_row = 0
        self.scroll_col = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.modified = False

    def find_word_at_cursor(self) -> str:
        line = self.lines[self.cursor_row]
        col  = self.cursor_col
        m    = re.finditer(r'[\w.%@]+', line)
        for tok in m:
            if tok.start() <= col <= tok.end():
                return tok.group()
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# AUTOCOMPLETE ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def get_completions(prefix: str, max_results: int = 12) -> List[Dict]:
    if len(prefix) < 2:
        return []
    low = prefix.lower()
    exact   = [c for c in NASM_COMPLETIONS if c["word"].lower().startswith(low)]
    contains= [c for c in NASM_COMPLETIONS if low in c["word"].lower()
               and not c["word"].lower().startswith(low)]
    return (exact + contains)[:max_results]

KIND_COLOR = {
    "reg64":    T["register"],
    "reg32":    T["register"],
    "reg16":    T["register"],
    "reg8":     T["register"],
    "regseg":   T["register"],
    "regcr":    T["register"],
    "regdr":    T["register"],
    "regxmm":  (220, 140, 255),
    "regymm":  (200, 120, 240),
    "regzmm":  (180, 100, 220),
    "regmmx":  (160, 180, 255),
    "regk":    (180, 200, 100),
    "instr":    T["keyword"],
    "directive":T["directive"],
    "section":  T["section"],
    "asmx":     T["asmx"],
}

# ─────────────────────────────────────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def draw_rect_border(surf, color, rect, width=1, radius=0):
    pygame.draw.rect(surf, color, rect, width, border_radius=radius)

def draw_panel(surf, rect, bg=None, border=None, radius=4):
    bg = bg or T["bg2"]
    border = border or T["border"]
    pygame.draw.rect(surf, bg, rect, border_radius=radius)
    draw_rect_border(surf, border, rect, 1, radius)

def text_surface(font, text: str, color, antialias=True):
    return font.render(text, antialias, color)

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

# ─────────────────────────────────────────────────────────────────────────────
# BUTTON
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Button:
    rect: pygame.Rect
    label: str
    color_override: Optional[Tuple] = None
    tooltip: str = ""
    tag: str = ""
    active: bool = False

    def draw(self, surf, font, mouse_pos, pressed_tag=""):
        hov = self.rect.collidepoint(mouse_pos)
        act = self.active or (pressed_tag == self.tag)
        if act:
            bg = T["btn_active"]
        elif hov:
            bg = T["btn_hover"]
        else:
            bg = T["btn_bg"]
        bc = self.color_override or (T["btn_active"] if act else T["btn_border"])
        pygame.draw.rect(surf, bg, self.rect, border_radius=4)
        draw_rect_border(surf, bc, self.rect, 1, 4)
        lbl = font.render(self.label, True, T["text"])
        lx  = self.rect.centerx - lbl.get_width() // 2
        ly  = self.rect.centery - lbl.get_height() // 2
        surf.blit(lbl, (lx, ly))

    def clicked(self, event) -> bool:
        return (event.type == pygame.MOUSEBUTTONDOWN and
                event.button == 1 and
                self.rect.collidepoint(event.pos))

# ─────────────────────────────────────────────────────────────────────────────
# SCROLLBAR
# ─────────────────────────────────────────────────────────────────────────────
class Scrollbar:
    def __init__(self, rect: pygame.Rect, orientation="v"):
        self.rect = rect
        self.orientation = orientation
        self.dragging = False
        self.drag_offset = 0

    def draw(self, surf, value, max_value, visible):
        pygame.draw.rect(surf, T["scrollbar"], self.rect, border_radius=3)
        if max_value <= 0:
            return
        ratio = visible / max(visible, max_value + visible)
        if self.orientation == "v":
            th = max(20, int(self.rect.height * ratio))
            ty = self.rect.y + int((self.rect.height - th) * (value / max(1, max_value)))
            thumb = pygame.Rect(self.rect.x, ty, self.rect.width, th)
        else:
            tw = max(20, int(self.rect.width * ratio))
            tx = self.rect.x + int((self.rect.width - tw) * (value / max(1, max_value)))
            thumb = pygame.Rect(tx, self.rect.y, tw, self.rect.height)
        pygame.draw.rect(surf, T["scrollthumb"], thumb, border_radius=3)

# ─────────────────────────────────────────────────────────────────────────────
# NASM EDITOR — MAIN APP
# ─────────────────────────────────────────────────────────────────────────────
class NasmEditor:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("NASM Editor — x64 IDE + ASMX")
        self.screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)
        self.clock  = pygame.time.Clock()

        # Fonts
        mono_candidates = ["Consolas", "Courier New", "DejaVu Sans Mono", "monospace"]
        self.font_mono  = None
        for name in mono_candidates:
            try:
                f = pygame.font.SysFont(name, 16)
                if f:
                    self.font_mono = f
                    break
            except:
                pass
        if not self.font_mono:
            self.font_mono = pygame.font.SysFont(None, 17)
        self.font_ui    = pygame.font.SysFont("Segoe UI", 15) if os.name == "nt" else self.font_mono
        self.font_small = pygame.font.SysFont("Consolas", 13) if os.name == "nt" else self.font_mono

        self.char_w = self.font_mono.size("M")[0]
        self.char_h = self.font_mono.get_linesize()
        self.line_h = self.char_h + 2

        # State
        self.editor   = EditorState()
        self.editor.set_text(SNIPPETS["win64_exe"])
        self.editor.file_path = None

        # Build
        self.build_result: Optional[BuildResult] = None
        self.build_mode   = "obj_win64"
        self.build_thread: Optional[threading.Thread] = None
        self.building     = False

        # UI
        self.cursor_blink   = 0.0
        self.cursor_visible = True
        self.ac_items:  List[Dict] = []
        self.ac_index   = 0
        self.ac_prefix  = ""
        self.ac_active  = False
        self.ac_trigger_col = 0

        self.show_log   = True
        self.log_scroll = 0
        self.find_mode  = False
        self.find_text  = ""
        self.find_results: List[Tuple[int,int]] = []
        self.find_idx   = 0

        self.snippet_menu = False
        self.tooltip_text = ""
        self.tooltip_rect: Optional[pygame.Rect] = None

        self.status_msg   = "Pronto.  NASM Editor — x64 AMD64 + ASMX"
        self.status_color = T["ok"]

        # Layout
        self._calc_layout()
        self._build_toolbar()
        self._build_compile_buttons()
        self._build_right_panel()

    # ─────────────────────────────────────────────────────────────────────────
    def _calc_layout(self):
        w, h = self.screen.get_size()
        self.toolbar_rect = pygame.Rect(0, 0, w, TOOLBAR_H)
        bot_h = BOT_H if self.show_log else 0
        self.editor_rect  = pygame.Rect(LEFT_W, TOOLBAR_H, w - LEFT_W - RIGHT_W, h - TOOLBAR_H - bot_h)
        self.gutter_rect  = pygame.Rect(0, TOOLBAR_H, LEFT_W, h - TOOLBAR_H - bot_h)
        self.log_rect     = pygame.Rect(0, h - bot_h, w, bot_h) if self.show_log else pygame.Rect(0, h, w, 0)
        self.right_rect   = pygame.Rect(w - RIGHT_W, TOOLBAR_H, RIGHT_W, h - TOOLBAR_H - bot_h)
        self.vscroll_rect = pygame.Rect(w - RIGHT_W - 8, TOOLBAR_H, 8, h - TOOLBAR_H - bot_h)
        self.vscrollbar   = Scrollbar(self.vscroll_rect)

    def _build_toolbar(self):
        bw, bh, gap = 88, 26, 6
        y = (TOOLBAR_H - bh) // 2
        x = 8
        self.btn_new    = Button(pygame.Rect(x, y, bw, bh), "  Novo",    tag="new",    tooltip="Novo arquivo")
        x += bw + gap
        self.btn_open   = Button(pygame.Rect(x, y, bw, bh), "  Abrir",   tag="open",   tooltip="Abrir .asm/.asmx")
        x += bw + gap
        self.btn_save   = Button(pygame.Rect(x, y, bw, bh), "  Salvar",  tag="save",   tooltip="Salvar")
        x += bw + gap
        self.btn_find   = Button(pygame.Rect(x, y, bw, bh), "  Buscar",  tag="find",   tooltip="Buscar (Ctrl+F)")
        x += bw + gap
        self.btn_snip   = Button(pygame.Rect(x, y, bw+20, bh), "  Templates", tag="snip", tooltip="Inserir template")
        x += bw + 20 + gap
        self.btn_log    = Button(pygame.Rect(x, y, bw, bh), "  Log",     tag="log",    tooltip="Alternar log")
        x += bw + gap
        # ── ASMX macro buttons ────────────────────────────────────────────
        mbw = 78   # largura dos botões macro (menor para caber na toolbar)
        self.btn_macro_fwd = Button(
            pygame.Rect(x, y, mbw, bh),
            "ASMX▶ASM",
            color_override=(255, 160, 60),
            tag="macro_fwd",
            tooltip="Expandir ASMX → NASM válido (Ctrl+M)"
        )
        x += mbw + gap
        self.btn_macro_rev = Button(
            pygame.Rect(x, y, mbw, bh),
            "ASM▶ASMX",
            color_override=(60, 200, 160),
            tag="macro_rev",
            tooltip="Anotar NASM → ASMX (heurístico)"
        )
        self.toolbar_buttons = [
            self.btn_new, self.btn_open, self.btn_save,
            self.btn_find, self.btn_snip, self.btn_log,
            self.btn_macro_fwd, self.btn_macro_rev,
        ]

    def _build_compile_buttons(self):
        """Botões de compilação — lado direito da toolbar."""
        w = self.screen.get_width()
        bw, bh, gap = 100, 26, 6
        y  = (TOOLBAR_H - bh) // 2
        modes = [
            ("obj_win64",  "Win64 OBJ",   (60, 140, 255),  "COFF/PE obj para linker"),
            ("exe_win64",  "Win64 EXE*",  (60, 200, 120),  "OBJ p/ linker manual"),
            ("bios",       "BIOS BIN",    (255, 160, 60),  "Boot sector 512b flat bin"),
            ("uefi",       "UEFI OBJ",    (200, 100, 255), "UEFI PE+ object"),
            ("bin",        "Flat BIN",    (180, 180, 60),  "Binary flat output"),
            ("obj_elf64",  "ELF64 OBJ",   (80, 200, 180),  "ELF64 object (Linux)"),
        ]
        self.compile_buttons: List[Tuple[Button, str]] = []
        x = w - len(modes) * (bw + gap) - 8
        for mode_id, label, color, tip in modes:
            rect = pygame.Rect(x, y, bw, bh)
            btn  = Button(rect, label, color_override=color, tag=f"compile_{mode_id}",
                          tooltip=tip, active=(mode_id == self.build_mode))
            self.compile_buttons.append((btn, mode_id))
            x += bw + gap

    def _build_right_panel(self):
        """Painel direito: sugestões de completions."""
        pass  # Desenhado dinamicamente

    # ─────────────────────────────────────────────────────────────────────────
    # VISIBLE LINES
    # ─────────────────────────────────────────────────────────────────────────
    def visible_lines(self) -> int:
        return max(1, self.editor_rect.height // self.line_h)

    def visible_cols(self) -> int:
        return max(1, self.editor_rect.width // self.char_w)

    def ensure_cursor_visible(self):
        ed = self.editor
        vis = self.visible_lines()
        if ed.cursor_row < ed.scroll_row:
            ed.scroll_row = ed.cursor_row
        elif ed.cursor_row >= ed.scroll_row + vis - 1:
            ed.scroll_row = ed.cursor_row - vis + 2
        ed.scroll_row = clamp(ed.scroll_row, 0, max(0, len(ed.lines) - 1))

        vcols = self.visible_cols()
        if ed.cursor_col < ed.scroll_col:
            ed.scroll_col = ed.cursor_col
        elif ed.cursor_col >= ed.scroll_col + vcols - 4:
            ed.scroll_col = ed.cursor_col - vcols + 5

    # ─────────────────────────────────────────────────────────────────────────
    # DRAW
    # ─────────────────────────────────────────────────────────────────────────
    def draw(self):
        w, h = self.screen.get_size()
        surf = self.screen
        surf.fill(T["bg"])
        self._draw_toolbar(surf)
        self._draw_gutter(surf)
        self._draw_editor(surf)
        self._draw_right_panel(surf)
        self._draw_log(surf)
        self._draw_vscrollbar(surf)
        if self.ac_active and self.ac_items:
            self._draw_autocomplete(surf)
        if self.find_mode:
            self._draw_find_bar(surf)
        if self.snippet_menu:
            self._draw_snippet_menu(surf)
        self._draw_statusbar(surf)
        pygame.display.flip()

    def _draw_toolbar(self, surf):
        pygame.draw.rect(surf, T["bg2"], self.toolbar_rect)
        pygame.draw.line(surf, T["border"],
                         (0, TOOLBAR_H-1), (surf.get_width(), TOOLBAR_H-1))
        mouse = pygame.mouse.get_pos()
        for btn in self.toolbar_buttons:
            btn.draw(surf, self.font_ui, mouse)

        # Compile buttons
        for btn, mode_id in self.compile_buttons:
            btn.active = (mode_id == self.build_mode)
            btn.draw(surf, self.font_ui, mouse)

        # Building indicator
        if self.building:
            t = int(time.time() * 4) % 4
            dots = "." * (t + 1)
            lbl = self.font_ui.render(f"Compilando{dots}", True, T["warn_fg"])
            surf.blit(lbl, (self.compile_buttons[0][0].rect.left - 120,
                            (TOOLBAR_H - lbl.get_height()) // 2))

    def _draw_gutter(self, surf):
        gr = self.gutter_rect
        pygame.draw.rect(surf, T["gutter"], gr)
        pygame.draw.line(surf, T["border"],
                         (gr.right-1, gr.top), (gr.right-1, gr.bottom))
        ed = self.editor
        vis = self.visible_lines()
        for i in range(vis):
            row = ed.scroll_row + i
            if row >= len(ed.lines):
                break
            y = gr.top + i * self.line_h + 2
            color = T["lineno_cur"] if row == ed.cursor_row else T["gutter_fg"]
            lbl = self.font_mono.render(str(row + 1), True, color)
            surf.blit(lbl, (gr.right - lbl.get_width() - 6, y))

            # Error / warning indicator
            if self.build_result:
                for err in self.build_result.errors:
                    if err["line"] == row + 1:
                        ec = T["error_fg"] if err["sev"] == "error" else T["warn_fg"]
                        pygame.draw.circle(surf, ec, (gr.left + 6, y + self.line_h // 2), 4)

    def _draw_editor(self, surf):
        er   = self.editor_rect
        ed   = self.editor
        vis  = self.visible_lines()
        vcols= self.visible_cols()

        pygame.draw.rect(surf, T["bg"], er)

        # Error line backgrounds
        err_lines = {}
        if self.build_result:
            for e in self.build_result.errors:
                ln = e["line"] - 1
                err_lines[ln] = "error" if e["sev"] == "error" else "warn"

        for i in range(vis):
            row = ed.scroll_row + i
            if row >= len(ed.lines):
                break
            y = er.top + i * self.line_h
            line = ed.lines[row]

            # Error/warn background
            if row in err_lines:
                bg_col = T["error_line"] if err_lines[row] == "error" else T["warn_line"]
                pygame.draw.rect(surf, bg_col, (er.left, y, er.width, self.line_h))

            # Selection background
            a, b = ed.selection_ordered()
            if a is not None:
                if a[0] <= row <= b[0]:
                    if a[0] == b[0]:
                        x1 = er.left + (a[1] - ed.scroll_col) * self.char_w
                        x2 = er.left + (b[1] - ed.scroll_col) * self.char_w
                    elif row == a[0]:
                        x1 = er.left + (a[1] - ed.scroll_col) * self.char_w
                        x2 = er.left + len(line) * self.char_w + self.char_w
                    elif row == b[0]:
                        x1 = er.left
                        x2 = er.left + (b[1] - ed.scroll_col) * self.char_w
                    else:
                        x1 = er.left
                        x2 = er.left + len(line) * self.char_w + self.char_w
                    x1 = max(er.left, x1)
                    x2 = min(er.right, x2)
                    if x2 > x1:
                        pygame.draw.rect(surf, T["sel"], (x1, y, x2-x1, self.line_h))

            # Syntax highlighted text
            visible_line = line[ed.scroll_col:ed.scroll_col + vcols + 4]
            spans = tokenize_line(line)
            x_off = ed.scroll_col
            drawn_up_to = 0
            for span_start, span_end, kind in spans:
                # Plain text before span
                seg_plain = line[drawn_up_to:span_start]
                if seg_plain:
                    vs = max(0, x_off - drawn_up_to)
                    seg_plain_vis = seg_plain[vs:vs + vcols]
                    if seg_plain_vis:
                        xp = er.left + (drawn_up_to + vs - x_off) * self.char_w
                        if er.left <= xp <= er.right:
                            s = self.font_mono.render(seg_plain_vis, True, T["text"])
                            surf.blit(s, (xp, y + 1))
                # Highlighted span
                seg = line[span_start:span_end]
                vs  = max(0, x_off - span_start)
                seg_vis = seg[vs:vs + vcols]
                if seg_vis:
                    xp = er.left + (span_start + vs - x_off) * self.char_w
                    if er.left <= xp <= er.right:
                        s = self.font_mono.render(seg_vis, True, T.get(kind, T["text"]))
                        surf.blit(s, (xp, y + 1))
                drawn_up_to = span_end

            # Remaining text after last span
            seg_tail = line[drawn_up_to:]
            if seg_tail:
                vs = max(0, x_off - drawn_up_to)
                seg_vis = seg_tail[vs:vs + vcols]
                if seg_vis:
                    xp = er.left + (drawn_up_to + vs - x_off) * self.char_w
                    if er.left <= xp <= er.right:
                        s = self.font_mono.render(seg_vis, True, T["text"])
                        surf.blit(s, (xp, y + 1))

            # Cursor
            if row == ed.cursor_row and self.cursor_visible:
                cx = er.left + (ed.cursor_col - ed.scroll_col) * self.char_w
                if er.left <= cx <= er.right:
                    pygame.draw.line(surf, T["cursor"],
                                     (cx, y + 1), (cx, y + self.line_h - 2), 2)

        # Clip border
        pygame.draw.rect(surf, T["border"], er, 1)

    def _draw_right_panel(self, surf):
        rr = self.right_rect
        draw_panel(surf, rr, T["bg2"], T["border"])
        y = rr.top + 8
        title = self.font_ui.render("Sugestões (Tab/Enter)", True, T["panel_title"])
        surf.blit(title, (rr.left + 8, y))
        y += title.get_height() + 6
        pygame.draw.line(surf, T["border"], (rr.left+4, y), (rr.right-4, y))
        y += 4

        # Show completions from autocomplete, or hints
        items = self.ac_items if self.ac_items else get_completions(
            self.editor.find_word_at_cursor(), 18)

        for i, item in enumerate(items[:18]):
            iy = y + i * (self.char_h + 2)
            if iy > rr.bottom - 20:
                break
            bg = T["ac_sel"] if (self.ac_active and i == self.ac_index) else None
            if bg:
                pygame.draw.rect(surf, bg, (rr.left+2, iy-1, rr.width-4, self.char_h+2))
            kc = KIND_COLOR.get(item["kind"], T["text"])
            kind_lbl = self.font_small.render(item["kind"][:4], True, kc)
            surf.blit(kind_lbl, (rr.left + 6, iy + 1))
            word_lbl = self.font_mono.render(item["word"], True, T["ac_text"])
            surf.blit(word_lbl, (rr.left + 44, iy + 1))

    def _draw_log(self, surf):
        if not self.show_log:
            return
        lr = self.log_rect
        pygame.draw.rect(surf, T["bg3"], lr)
        pygame.draw.line(surf, T["border"], (lr.left, lr.top), (lr.right, lr.top), 2)

        # Title
        title = "█ Saída / Log de Compilação"
        if self.build_result:
            icon = "✓" if self.build_result.ok else "✗"
            col  = T["ok"] if self.build_result.ok else T["error_fg"]
            t = self.font_ui.render(f"  {icon} [{self.build_result.mode.upper()}] {title}", True, col)
        else:
            t = self.font_ui.render(f"  {title}", True, T["panel_title"])
        surf.blit(t, (lr.left + 4, lr.top + 4))

        # Log text
        log_text = ""
        if self.build_result:
            log_text = self.build_result.log
        if not log_text:
            log_text = "Nenhuma compilação realizada. Use os botões acima para compilar."

        lines = log_text.splitlines()
        max_scroll = max(0, len(lines) - ((lr.height - 26) // self.char_h))
        self.log_scroll = clamp(self.log_scroll, 0, max_scroll)

        y = lr.top + 24
        for i, line in enumerate(lines[self.log_scroll:]):
            if y + self.char_h > lr.bottom:
                break
            # Colorize error/warning lines
            low = line.lower()
            if "error" in low:
                col = T["error_fg"]
            elif "warning" in low:
                col = T["warn_fg"]
            elif line.startswith("✓") or "ok" in low[:10]:
                col = T["ok"]
            elif line.startswith(";") or line.startswith("%define") or line.startswith("@"):
                col = T["asmx"]
            else:
                col = T["text"]
            lbl = self.font_small.render(line[:200], True, col)
            surf.blit(lbl, (lr.left + 8, y))
            y += self.char_h

    def _draw_vscrollbar(self, surf):
        ed  = self.editor
        vis = self.visible_lines()
        self.vscrollbar.draw(surf, ed.scroll_row, max(0, len(ed.lines) - vis), vis)

    def _draw_autocomplete(self, surf):
        ed  = self.editor
        er  = self.editor_rect
        row = ed.cursor_row
        col = self.ac_trigger_col
        ax  = er.left + (col - ed.scroll_col) * self.char_w
        ay  = er.top + (row - ed.scroll_row + 1) * self.line_h

        item_h = self.char_h + 4
        aw = 340
        ah = min(len(self.ac_items), 10) * item_h + 6

        # Flip up if near bottom
        w, h = surf.get_size()
        if ay + ah > h - 20:
            ay = er.top + (row - ed.scroll_row) * self.line_h - ah

        ax = clamp(ax, 0, w - aw - 4)
        rect = pygame.Rect(ax, ay, aw, ah)
        draw_panel(surf, rect, T["ac_bg"], T["ac_border"], 6)

        for i, item in enumerate(self.ac_items[:10]):
            iy = rect.top + 3 + i * item_h
            bg = T["ac_sel"] if i == self.ac_index else None
            if bg:
                pygame.draw.rect(surf, bg, (rect.left+2, iy, aw-4, item_h), border_radius=3)
            kc = KIND_COLOR.get(item["kind"], T["text"])
            kind_s = self.font_small.render(f"[{item['kind'][:5]}]", True, kc)
            surf.blit(kind_s, (rect.left + 6, iy + 2))
            word_s = self.font_mono.render(item["word"], True, T["ac_text"])
            surf.blit(word_s, (rect.left + 75, iy + 2))
            # Detail (truncated)
            detail = item.get("detail", "")[:40]
            if detail:
                det_s = self.font_small.render(detail, True, T["ac_detail"])
                surf.blit(det_s, (rect.left + 75 + word_s.get_width() + 8, iy + 4))

    def _draw_find_bar(self, surf):
        w, h = surf.get_size()
        bw, bh = 360, 32
        bx = (w - bw) // 2
        by = TOOLBAR_H + 8
        rect = pygame.Rect(bx, by, bw, bh)
        draw_panel(surf, rect, T["bg2"], T["ac_border"], 6)
        prompt = self.font_ui.render("Buscar: ", True, T["panel_title"])
        surf.blit(prompt, (bx + 8, by + (bh - prompt.get_height()) // 2))
        text_lbl = self.font_mono.render(self.find_text + "█", True, T["text"])
        surf.blit(text_lbl, (bx + 72, by + (bh - text_lbl.get_height()) // 2))
        if self.find_results:
            info = self.font_small.render(
                f"{self.find_idx+1}/{len(self.find_results)}  Enter:próx  Esc:sair",
                True, T["ok"])
        else:
            info = self.font_small.render("Não encontrado  Esc:sair", True, T["error_fg"])
        surf.blit(info, (bx + bw + 8, by + (bh - info.get_height()) // 2))

    def _draw_snippet_menu(self, surf):
        w, h = surf.get_size()
        mw, mh = 320, len(SNIPPETS) * 30 + 16
        mx = (w - mw) // 2
        my = TOOLBAR_H + 8
        rect = pygame.Rect(mx, my, mw, mh)
        draw_panel(surf, rect, T["bg2"], T["ac_border"], 8)
        title = self.font_ui.render("Templates — clique para inserir", True, T["panel_title"])
        surf.blit(title, (mx + 10, my + 6))
        mouse = pygame.mouse.get_pos()
        for i, (key, _) in enumerate(SNIPPETS.items()):
            iy = my + 30 + i * 28
            ir = pygame.Rect(mx + 6, iy, mw - 12, 26)
            if ir.collidepoint(mouse):
                pygame.draw.rect(surf, T["ac_sel"], ir, border_radius=4)
            lbl = self.font_ui.render(f"  {key}", True, T["text"])
            surf.blit(lbl, (mx + 12, iy + 4))

    def _draw_statusbar(self, surf):
        w, h = surf.get_size()
        sr   = pygame.Rect(0, h - 20, w, 20)
        pygame.draw.rect(surf, T["bg3"], sr)
        pygame.draw.line(surf, T["border"], (0, h-20), (w, h-20))
        ed   = self.editor
        info = (f"  Ln {ed.cursor_row+1}, Col {ed.cursor_col+1}"
                f"  |  {len(ed.lines)} linhas"
                f"  |  {'*Modificado' if ed.modified else 'Salvo'}"
                f"  |  {ed.file_path or 'Sem arquivo'}"
                f"  |  NASM: {find_nasm()}")
        lbl  = self.font_small.render(info, True, T["gutter_fg"])
        surf.blit(lbl, (0, h - 18))
        msg  = self.font_small.render(self.status_msg, True, self.status_color)
        surf.blit(msg, (w - msg.get_width() - 10, h - 18))

    # ─────────────────────────────────────────────────────────────────────────
    # EVENT HANDLING
    # ─────────────────────────────────────────────────────────────────────────
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._try_quit()
                return

            if event.type == pygame.VIDEORESIZE:
                self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                self._calc_layout()
                self._build_toolbar()
                self._build_compile_buttons()
                continue

            if event.type == pygame.MOUSEWHEEL:
                if self.log_rect.collidepoint(pygame.mouse.get_pos()):
                    self.log_scroll -= event.y * SCROLL_SPEED
                else:
                    self.editor.scroll_row -= event.y * SCROLL_SPEED
                    self.editor.scroll_row = clamp(
                        self.editor.scroll_row, 0,
                        max(0, len(self.editor.lines) - self.visible_lines()))
                continue

            if event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_mouse_down(event)
                continue

            if event.type == pygame.MOUSEMOTION:
                if event.buttons[0]:
                    self._handle_mouse_drag(event)
                continue

            if event.type == pygame.KEYDOWN:
                self._handle_key(event)
                continue

    def _handle_mouse_down(self, event):
        pos = event.pos

        # Snippet menu
        if self.snippet_menu:
            w, h = self.screen.get_size()
            mw = 320
            mx = (w - mw) // 2
            my = TOOLBAR_H + 8
            for i, (key, code) in enumerate(SNIPPETS.items()):
                iy = my + 30 + i * 28
                ir = pygame.Rect(mx + 6, iy, mw - 12, 26)
                if ir.collidepoint(pos):
                    self.editor.insert_text(code)
                    self.snippet_menu = False
                    return
            self.snippet_menu = False
            return

        # Autocomplete
        if self.ac_active and self.ac_items:
            ed = self.editor
            er = self.editor_rect
            col = self.ac_trigger_col
            ax  = er.left + (col - ed.scroll_col) * self.char_w
            ay  = er.top + (ed.cursor_row - ed.scroll_row + 1) * self.line_h
            aw  = 340
            item_h = self.char_h + 4
            ah  = min(len(self.ac_items), 10) * item_h + 6
            w, h = self.screen.get_size()
            if ay + ah > h - 20:
                ay = er.top + (ed.cursor_row - ed.scroll_row) * self.line_h - ah
            ax = clamp(ax, 0, w - aw - 4)
            rect = pygame.Rect(ax, ay, aw, ah)
            if rect.collidepoint(pos):
                idx = (pos[1] - rect.top - 3) // (self.char_h + 4)
                if 0 <= idx < len(self.ac_items):
                    self.ac_index = idx
                    self._accept_completion()
                return
            self.ac_active = False

        # Toolbar buttons
        for btn in self.toolbar_buttons:
            if btn.clicked(event):
                self._toolbar_action(btn.tag)
                return

        # Compile buttons
        for btn, mode_id in self.compile_buttons:
            if btn.clicked(event):
                self.build_mode = mode_id
                self._start_build(mode_id)
                return

        # Editor click → move cursor
        if self.editor_rect.collidepoint(pos):
            self._click_to_cursor(pos, event)

    def _click_to_cursor(self, pos, event):
        er = self.editor_rect
        ed = self.editor
        row = ed.scroll_row + (pos[1] - er.top) // self.line_h
        col = ed.scroll_col + (pos[0] - er.left + self.char_w // 2) // self.char_w
        row = clamp(row, 0, len(ed.lines) - 1)
        col = clamp(col, 0, len(ed.lines[row]))
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            if ed.sel_start is None:
                ed.sel_start = (ed.cursor_row, ed.cursor_col)
            ed.sel_end = (row, col)
        else:
            ed.sel_start = (row, col)
            ed.sel_end   = (row, col)
        ed.cursor_row = row
        ed.cursor_col = col

    def _handle_mouse_drag(self, event):
        pos = event.pos
        if self.editor_rect.collidepoint(pos):
            er = self.editor_rect
            ed = self.editor
            row = ed.scroll_row + (pos[1] - er.top) // self.line_h
            col = ed.scroll_col + (pos[0] - er.left + self.char_w // 2) // self.char_w
            row = clamp(row, 0, len(ed.lines) - 1)
            col = clamp(col, 0, len(ed.lines[row]))
            ed.sel_end = (row, col)
            ed.cursor_row = row
            ed.cursor_col = col

    def _handle_key(self, event):
        ed   = self.editor
        ctrl = event.mod & pygame.KMOD_CTRL
        shift= event.mod & pygame.KMOD_SHIFT
        alt  = event.mod & pygame.KMOD_ALT

        # Find bar
        if self.find_mode:
            self._handle_find_key(event)
            return

        # Snippet menu
        if self.snippet_menu:
            if event.key == pygame.K_ESCAPE:
                self.snippet_menu = False
            return

        # Autocomplete navigation
        if self.ac_active and self.ac_items:
            if event.key == pygame.K_DOWN:
                self.ac_index = (self.ac_index + 1) % len(self.ac_items)
                return
            if event.key == pygame.K_UP:
                self.ac_index = (self.ac_index - 1) % len(self.ac_items)
                return
            if event.key in (pygame.K_RETURN, pygame.K_TAB):
                self._accept_completion()
                return
            if event.key == pygame.K_ESCAPE:
                self.ac_active = False
                return

        # Global shortcuts
        if ctrl:
            if event.key == pygame.K_z:
                ed.undo(); return
            if event.key == pygame.K_y:
                ed.redo(); return
            if event.key == pygame.K_s:
                self._save_file(); return
            if event.key == pygame.K_o:
                self._open_file(); return
            if event.key == pygame.K_n:
                self._new_file(); return
            if event.key == pygame.K_f:
                self.find_mode = True; self.find_text = ""; self.find_results = []; return
            if event.key == pygame.K_a:
                ed.sel_start = (0, 0)
                ed.sel_end = (len(ed.lines)-1, len(ed.lines[-1]))
                return
            if event.key == pygame.K_c:
                t = ed.get_selected_text()
                if t: pygame.scrap.put(pygame.SCRAP_TEXT, t.encode())
                return
            if event.key == pygame.K_x:
                t = ed.get_selected_text()
                if t:
                    pygame.scrap.put(pygame.SCRAP_TEXT, t.encode())
                    ed.push_undo(); ed.delete_selection()
                return
            if event.key == pygame.K_v:
                try:
                    data = pygame.scrap.get(pygame.SCRAP_TEXT)
                    if data:
                        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
                        ed.insert_text(text)
                except:
                    pass
                return
            if event.key == pygame.K_d:  # duplicate line
                ed.push_undo()
                line = ed.lines[ed.cursor_row]
                ed.lines.insert(ed.cursor_row + 1, line)
                ed.cursor_row += 1
                return
            if event.key == pygame.K_SLASH:  # toggle comment
                ed.push_undo()
                line = ed.lines[ed.cursor_row]
                stripped = line.lstrip()
                indent   = line[:len(line)-len(stripped)]
                if stripped.startswith(";"):
                    ed.lines[ed.cursor_row] = indent + stripped[1:].lstrip()
                else:
                    ed.lines[ed.cursor_row] = indent + "; " + stripped
                return
            if event.key == pygame.K_b:  # compile with current mode
                self._start_build(self.build_mode)
                return
            if event.key == pygame.K_F5:
                self._start_build(self.build_mode)
                return
            # Ctrl+M → ASMX→ASM expand
            if event.key == pygame.K_m:
                self._toolbar_action("macro_fwd")
                return

        # Navigation
        if event.key == pygame.K_LEFT:
            if shift:
                if ed.sel_start is None: ed.sel_start = (ed.cursor_row, ed.cursor_col)
            else:
                ed.sel_start = ed.sel_end = None
            if ctrl:
                self._move_word_left()
            elif ed.cursor_col > 0:
                ed.cursor_col -= 1
            elif ed.cursor_row > 0:
                ed.cursor_row -= 1
                ed.cursor_col = len(ed.lines[ed.cursor_row])
            if shift: ed.sel_end = (ed.cursor_row, ed.cursor_col)
        elif event.key == pygame.K_RIGHT:
            if shift:
                if ed.sel_start is None: ed.sel_start = (ed.cursor_row, ed.cursor_col)
            else:
                ed.sel_start = ed.sel_end = None
            if ctrl:
                self._move_word_right()
            elif ed.cursor_col < len(ed.lines[ed.cursor_row]):
                ed.cursor_col += 1
            elif ed.cursor_row < len(ed.lines) - 1:
                ed.cursor_row += 1
                ed.cursor_col = 0
            if shift: ed.sel_end = (ed.cursor_row, ed.cursor_col)
        elif event.key == pygame.K_UP:
            if shift:
                if ed.sel_start is None: ed.sel_start = (ed.cursor_row, ed.cursor_col)
            else:
                ed.sel_start = ed.sel_end = None
            if ed.cursor_row > 0:
                ed.cursor_row -= 1
                ed.cursor_col = min(ed.cursor_col, len(ed.lines[ed.cursor_row]))
            if shift: ed.sel_end = (ed.cursor_row, ed.cursor_col)
        elif event.key == pygame.K_DOWN:
            if shift:
                if ed.sel_start is None: ed.sel_start = (ed.cursor_row, ed.cursor_col)
            else:
                ed.sel_start = ed.sel_end = None
            if ed.cursor_row < len(ed.lines) - 1:
                ed.cursor_row += 1
                ed.cursor_col = min(ed.cursor_col, len(ed.lines[ed.cursor_row]))
            if shift: ed.sel_end = (ed.cursor_row, ed.cursor_col)
        elif event.key == pygame.K_HOME:
            if shift:
                if ed.sel_start is None: ed.sel_start = (ed.cursor_row, ed.cursor_col)
            else:
                ed.sel_start = ed.sel_end = None
            line = ed.lines[ed.cursor_row]
            indent = len(line) - len(line.lstrip())
            if not ctrl:
                ed.cursor_col = indent if ed.cursor_col != indent else 0
            if shift: ed.sel_end = (ed.cursor_row, ed.cursor_col)
        elif event.key == pygame.K_END:
            if shift:
                if ed.sel_start is None: ed.sel_start = (ed.cursor_row, ed.cursor_col)
            else:
                ed.sel_start = ed.sel_end = None
            ed.cursor_col = len(ed.lines[ed.cursor_row])
            if shift: ed.sel_end = (ed.cursor_row, ed.cursor_col)
        elif event.key == pygame.K_PAGEUP:
            ed.cursor_row = max(0, ed.cursor_row - self.visible_lines())
            ed.scroll_row = max(0, ed.scroll_row - self.visible_lines())
            ed.cursor_col = min(ed.cursor_col, len(ed.lines[ed.cursor_row]))
        elif event.key == pygame.K_PAGEDOWN:
            ed.cursor_row = min(len(ed.lines)-1, ed.cursor_row + self.visible_lines())
            ed.scroll_row = min(max(0,len(ed.lines)-self.visible_lines()),
                                ed.scroll_row + self.visible_lines())
            ed.cursor_col = min(ed.cursor_col, len(ed.lines[ed.cursor_row]))

        # Editing
        elif event.key == pygame.K_BACKSPACE:
            ed.backspace()
            self._update_autocomplete()
        elif event.key == pygame.K_DELETE:
            ed.delete_forward()
        elif event.key == pygame.K_RETURN:
            ed.push_undo()
            if ed.sel_start and ed.sel_end:
                ed.delete_selection()
            # Auto-indent
            line = ed.lines[ed.cursor_row]
            indent = len(line) - len(line.lstrip())
            indent_str = line[:indent]
            # Extra indent after ":" label or macro open
            stripped = line.strip()
            if stripped.endswith(":") or stripped.startswith("%macro") or stripped.startswith("@macro"):
                indent_str += "    "
            rest = line[ed.cursor_col:]
            ed.lines[ed.cursor_row] = line[:ed.cursor_col]
            ed.lines.insert(ed.cursor_row + 1, indent_str + rest)
            ed.cursor_row += 1
            ed.cursor_col = len(indent_str)
            self.ac_active = False
        elif event.key == pygame.K_TAB:
            ed.handle_tab(reverse=shift)
            self.ac_active = False
        elif event.key == pygame.K_ESCAPE:
            ed.sel_start = ed.sel_end = None
            self.ac_active = False
        else:
            ch = event.unicode
            if ch and ch.isprintable():
                ed.insert_text(ch)
                self._update_autocomplete()

        self.ensure_cursor_visible()

    def _move_word_left(self):
        ed = self.editor
        line = ed.lines[ed.cursor_row]
        col  = ed.cursor_col
        while col > 0 and not line[col-1].isalnum() and line[col-1] != '_':
            col -= 1
        while col > 0 and (line[col-1].isalnum() or line[col-1] == '_'):
            col -= 1
        ed.cursor_col = col

    def _move_word_right(self):
        ed = self.editor
        line = ed.lines[ed.cursor_row]
        col  = ed.cursor_col
        n    = len(line)
        while col < n and not line[col].isalnum() and line[col] != '_':
            col += 1
        while col < n and (line[col].isalnum() or line[col] == '_'):
            col += 1
        ed.cursor_col = col

    def _handle_find_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self.find_mode = False
            return
        if event.key == pygame.K_BACKSPACE:
            self.find_text = self.find_text[:-1]
        elif event.key == pygame.K_RETURN:
            if self.find_results:
                self.find_idx = (self.find_idx + 1) % len(self.find_results)
                row, col = self.find_results[self.find_idx]
                self.editor.cursor_row = row
                self.editor.cursor_col = col
                self.editor.sel_start  = (row, col)
                self.editor.sel_end    = (row, col + len(self.find_text))
                self.ensure_cursor_visible()
            return
        elif event.unicode and event.unicode.isprintable():
            self.find_text += event.unicode

        # Update results
        self.find_results = []
        if self.find_text:
            low = self.find_text.lower()
            for r, line in enumerate(self.editor.lines):
                idx = 0
                while True:
                    pos = line.lower().find(low, idx)
                    if pos == -1:
                        break
                    self.find_results.append((r, pos))
                    idx = pos + 1
        self.find_idx = 0
        if self.find_results:
            row, col = self.find_results[0]
            self.editor.cursor_row = row
            self.editor.cursor_col = col
            self.editor.sel_start  = (row, col)
            self.editor.sel_end    = (row, col + len(self.find_text))
            self.ensure_cursor_visible()

    # ─────────────────────────────────────────────────────────────────────────
    # AUTOCOMPLETE
    # ─────────────────────────────────────────────────────────────────────────
    def _update_autocomplete(self):
        ed   = self.editor
        line = ed.lines[ed.cursor_row]
        col  = ed.cursor_col
        # Extract prefix — inclui '@' para ASMX keywords
        m = re.search(r'[@\w.%]+$', line[:col])
        if m:
            prefix = m.group()
            if len(prefix) >= 1:
                items = get_completions(prefix)
                if items:
                    self.ac_items   = items
                    self.ac_index   = 0
                    self.ac_prefix  = prefix
                    self.ac_active  = True
                    self.ac_trigger_col = m.start()
                    return
        self.ac_active = False
        self.ac_items  = []

    def _accept_completion(self):
        if not self.ac_items:
            return
        item = self.ac_items[self.ac_index]
        word = item["word"]
        ed   = self.editor
        line = ed.lines[ed.cursor_row]
        col  = ed.cursor_col
        # Remove prefix
        prefix_start = self.ac_trigger_col
        ed.push_undo()
        ed.lines[ed.cursor_row] = line[:prefix_start] + word + line[col:]
        ed.cursor_col = prefix_start + len(word)
        self.ac_active = False
        self.ac_items  = []

    # ─────────────────────────────────────────────────────────────────────────
    # TOOLBAR ACTIONS
    # ─────────────────────────────────────────────────────────────────────────
    def _toolbar_action(self, tag: str):
        if tag == "new":
            self._new_file()
        elif tag == "open":
            self._open_file()
        elif tag == "save":
            self._save_file()
        elif tag == "find":
            self.find_mode = True
            self.find_text = ""
            self.find_results = []
        elif tag == "snip":
            self.snippet_menu = not self.snippet_menu
        elif tag == "log":
            self.show_log = not self.show_log
            self._calc_layout()
        # ── ASMX → ASM: expande macros estruturais no editor ──────────────
        elif tag == "macro_fwd":
            try:
                src = self.editor.get_text()
                out = asmx_to_asm(src)
                # preserva undo
                self.editor.push_undo()
                new_lines = out.splitlines() or [""]
                self.editor.lines = new_lines
                self.editor.cursor_row = min(self.editor.cursor_row, len(new_lines) - 1)
                self.editor.cursor_col = min(
                    self.editor.cursor_col,
                    len(new_lines[self.editor.cursor_row]))
                self.editor.modified = True
                self.status_msg = "ASMX expandido → NASM válido  (Ctrl+Z para desfazer)"
                self.status_color = T["ok"]
                # Exibe preview no log
                self.build_result = BuildResult(
                    ok=True, mode="ASMX▶ASM",
                    log="; === NASM expandido ===\n" + out[:4000] +
                        ("\n; [truncado...]" if len(out) > 4000 else ""))
            except Exception as e:
                self.status_msg = f"Erro ASMX→ASM: {e}"
                self.status_color = T["error_fg"]
                self.build_result = BuildResult(ok=False, mode="ASMX▶ASM", log=str(e))
        # ── ASM → ASMX: anota NASM com sintaxe ASMX ─────────────────────
        elif tag == "macro_rev":
            try:
                src = self.editor.get_text()
                out = asm_to_asmx(src)
                self.editor.push_undo()
                new_lines = out.splitlines() or [""]
                self.editor.lines = new_lines
                self.editor.cursor_row = min(self.editor.cursor_row, len(new_lines) - 1)
                self.editor.cursor_col = min(
                    self.editor.cursor_col,
                    len(new_lines[self.editor.cursor_row]))
                self.editor.modified = True
                self.status_msg = "NASM anotado → ASMX  (Ctrl+Z para desfazer)"
                self.status_color = T["asmx"]
                self.build_result = BuildResult(
                    ok=True, mode="ASM▶ASMX",
                    log="; === ASMX anotado ===\n" + out[:4000] +
                        ("\n; [truncado...]" if len(out) > 4000 else ""))
            except Exception as e:
                self.status_msg = f"Erro ASM→ASMX: {e}"
                self.status_color = T["error_fg"]
                self.build_result = BuildResult(ok=False, mode="ASM▶ASMX", log=str(e))

    def _new_file(self):
        self.editor.set_text(SNIPPETS["win64_exe"])
        self.editor.file_path = None
        self.build_result = None
        self.status_msg = "Novo arquivo criado."
        self.status_color = T["ok"]

    def _open_file(self):
        # Minimal cross-platform file open via tkinter
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            path = filedialog.askopenfilename(
                title="Abrir arquivo Assembly / ASMX",
                filetypes=[
                    ("Assembly / ASMX", "*.asm *.asmx *.s *.inc *.nasm"),
                    ("All", "*.*")
                ])
            root.destroy()
            if path:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                self.editor.set_text(text)
                self.editor.file_path = path
                self.build_result = None
                self.status_msg = f"Aberto: {path}"
                self.status_color = T["ok"]
        except Exception as e:
            self.status_msg = f"Erro ao abrir: {e}"
            self.status_color = T["error_fg"]

    def _save_file(self):
        if not self.editor.file_path:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                path = filedialog.asksaveasfilename(
                    title="Salvar arquivo Assembly / ASMX",
                    defaultextension=".asmx",
                    filetypes=[
                        ("ASMX", "*.asmx"),
                        ("Assembly", "*.asm"),
                        ("All", "*.*")
                    ])
                root.destroy()
                if not path:
                    return
                self.editor.file_path = path
            except Exception as e:
                self.status_msg = f"Erro ao salvar: {e}"
                self.status_color = T["error_fg"]
                return
        try:
            with open(self.editor.file_path, "w", encoding="utf-8") as f:
                f.write(self.editor.get_text())
            self.editor.modified = False
            self.status_msg = f"Salvo: {self.editor.file_path}"
            self.status_color = T["ok"]
        except Exception as e:
            self.status_msg = f"Erro ao salvar: {e}"
            self.status_color = T["error_fg"]

    # ─────────────────────────────────────────────────────────────────────────
    # BUILD  —  preprocessa ASMX antes de invocar nasm.exe
    # ─────────────────────────────────────────────────────────────────────────
    def _start_build(self, mode: str):
        if self.building:
            return
        self.building = True
        self.build_mode = mode
        self.status_msg = f"Compilando [{mode.upper()}]..."
        self.status_color = T["warn_fg"]
        # ASMX → NASM antes de enviar ao assembler
        raw_source = self.editor.get_text()
        try:
            source = asmx_to_asm(raw_source)
        except Exception as e:
            source = raw_source
            self.status_msg = f"Aviso ASMX: {e} — compilando fonte bruto"
        path = self.editor.file_path

        def _run():
            result = run_nasm(source, mode, path)
            self.build_result = result
            self.building = False
            if result.ok:
                self.status_msg = f"[{mode.upper()}] OK → {result.output_file}"
                self.status_color = T["ok"]
            else:
                n_err = len([e for e in result.errors if e["sev"] == "error"])
                n_wrn = len([e for e in result.errors if e["sev"] == "warning"])
                self.status_msg = f"[{mode.upper()}] FALHOU — {n_err} erros, {n_wrn} avisos"
                self.status_color = T["error_fg"]

        self.build_thread = threading.Thread(target=_run, daemon=True)
        self.build_thread.start()

    # ─────────────────────────────────────────────────────────────────────────
    # QUIT
    # ─────────────────────────────────────────────────────────────────────────
    def _try_quit(self):
        if self.editor.modified:
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                ans = messagebox.askyesnocancel(
                    "NASM Editor", "Arquivo modificado. Salvar antes de sair?")
                root.destroy()
                if ans is True:
                    self._save_file()
                    pygame.quit()
                    sys.exit()
                elif ans is False:
                    pygame.quit()
                    sys.exit()
                # Cancel → stay
            except:
                pygame.quit()
                sys.exit()
        else:
            pygame.quit()
            sys.exit()

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ─────────────────────────────────────────────────────────────────────────
    def run(self):
        try:
            pygame.scrap.init()
        except:
            pass

        while True:
            dt = self.clock.tick(FPS) / 1000.0
            self.cursor_blink += dt
            if self.cursor_blink >= 0.53:
                self.cursor_blink = 0.0
                self.cursor_visible = not self.cursor_visible

            self.handle_events()
            self.draw()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = NasmEditor()
    app.run()

if __name__ == "__main__":
    main()
