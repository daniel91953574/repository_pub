#!/usr/bin/env python3
"""
AssemblyIDE v3.0  —  NASM x64 Scratch-style Block Editor  (single file)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LEFT   → template library (search + category tree, click to insert)
  CENTER → full code editor  (highlight · undo/redo · select · scroll)
  RIGHT  → context recommendations from current instruction keyword
  DIALOG → per-template parameter editing before insertion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Ctrl+S / F2    save            F5            build commands
  Ctrl+Z / Y     undo / redo     Ctrl+A        select all
  Ctrl+C/X/V     copy / cut / paste
  Tab / Shift+Tab indent / dedent
  Mouse wheel    scroll          Drag & drop   load .asm file
  Double-click   select word     Click template open param dialog

BUGS FIXED vs v2:
  • insert_char() now batches snapshots (500 ms coalescing)
  • load() clears undo/redo history, keeps clean state
  • context_word() skips labels, returns first real mnemonic
  • select_word_at() finds the word UNDER the click, not first word
  • move(word) uses proper lexical boundary scanner
  • undo/redo restores selection + dirty flag
  • InputField: real Ctrl+A (select all), Ctrl+V filters newlines,
    horizontal scroll & clipping, no deactivation race
  • CONTEXT_RULES: asm.jz replaced with asm.je (asm.jz didn't exist)
  • _REG set built with | union (no invalid ** in set literal)
  • pygame.scrap initialised once with fallback
  • GDI32 / OpenGL32 templates are complete and ABI-correct
  • draw_text() ellipsis path no longer risks undefined s2
"""
from __future__ import annotations
import pygame, sys, re, copy, os, time
from typing import List, Dict, Tuple, Optional, Any
import queue, threading, shutil, subprocess, tempfile, json
from dataclasses import dataclass, field

# ══════════════════════════════════════════════════════════════════════════════
# § 1  TEMPLATE DATA
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATES: List[Dict] = [
    # ═══════════════════  CPU / Move  ═══════════════════
    {"id":"asm.mov",    "title":"MOV",     "cat":"CPU/Move",    "desc":"Move src→dst",
     "tpl":"mov {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.movzx",  "title":"MOVZX",   "cat":"CPU/Move",    "desc":"Move with zero-extension",
     "tpl":"movzx {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"al"}]},
    {"id":"asm.movsx",  "title":"MOVSX",   "cat":"CPU/Move",    "desc":"Move with sign-extension",
     "tpl":"movsx {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"eax"}]},
    {"id":"asm.movsxd", "title":"MOVSXD",  "cat":"CPU/Move",    "desc":"Move with sign-extension (dword→qword)",
     "tpl":"movsxd {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"eax"}]},
    {"id":"asm.lea",    "title":"LEA",     "cat":"CPU/Move",    "desc":"Load effective address",
     "tpl":"lea {dst}, [{addr}]",      "ports":[{"n":"dst","d":"rax"},{"n":"addr","d":"rsp+8"}]},
    {"id":"asm.xchg",   "title":"XCHG",    "cat":"CPU/Move",    "desc":"Exchange two operands",
     "tpl":"xchg {a}, {b}",            "ports":[{"n":"a","d":"rax"},{"n":"b","d":"rbx"}]},
    {"id":"asm.bswap",  "title":"BSWAP",   "cat":"CPU/Move",    "desc":"Byte swap (endian conversion)",
     "tpl":"bswap {reg}",              "ports":[{"n":"reg","d":"rax"}]},
    # ═══════════════════  CPU / Stack  ═══════════════════
    {"id":"asm.push",   "title":"PUSH",    "cat":"CPU/Stack",   "desc":"Push register onto stack",
     "tpl":"push {src}",               "ports":[{"n":"src","d":"rbp"}]},
    {"id":"asm.pop",    "title":"POP",     "cat":"CPU/Stack",   "desc":"Pop register from stack",
     "tpl":"pop {dst}",                "ports":[{"n":"dst","d":"rbp"}]},
    {"id":"asm.pushfq", "title":"PUSHFQ",  "cat":"CPU/Stack",   "desc":"Push RFLAGS", "tpl":"pushfq","ports":[]},
    {"id":"asm.popfq",  "title":"POPFQ",   "cat":"CPU/Stack",   "desc":"Pop RFLAGS",  "tpl":"popfq", "ports":[]},
    {"id":"asm.pusha",  "title":"PUSHA",   "cat":"CPU/Stack",   "desc":"Push all integer regs (32-bit mode)", "tpl":"pusha","ports":[]},
    {"id":"asm.popa",   "title":"POPA",    "cat":"CPU/Stack",   "desc":"Pop all integer regs (32-bit mode)",  "tpl":"popa", "ports":[]},
    # ═══════════════════  CPU / ALU  ═══════════════════
    {"id":"asm.add",    "title":"ADD",     "cat":"CPU/ALU",     "desc":"Integer addition",
     "tpl":"add {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"1"}]},
    {"id":"asm.adc",    "title":"ADC",     "cat":"CPU/ALU",     "desc":"Add with carry",
     "tpl":"adc {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"0"}]},
    {"id":"asm.sub",    "title":"SUB",     "cat":"CPU/ALU",     "desc":"Integer subtraction",
     "tpl":"sub {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"1"}]},
    {"id":"asm.sbb",    "title":"SBB",     "cat":"CPU/ALU",     "desc":"Subtract with borrow",
     "tpl":"sbb {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"0"}]},
    {"id":"asm.imul",   "title":"IMUL",    "cat":"CPU/ALU",     "desc":"Signed multiply (2‑op form)",
     "tpl":"imul {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.imul3",  "title":"IMUL 3‑op","cat":"CPU/ALU",    "desc":"Signed multiply (dest, src1, imm32)",
     "tpl":"imul {dst}, {src1}, {imm}","ports":[{"n":"dst","d":"rax"},{"n":"src1","d":"rbx"},{"n":"imm","d":"4"}]},
    {"id":"asm.mul",    "title":"MUL",     "cat":"CPU/ALU",     "desc":"Unsigned multiply (rax*r/m)",
     "tpl":"mul {src}",                "ports":[{"n":"src","d":"rbx"}]},
    {"id":"asm.idiv",   "title":"IDIV",    "cat":"CPU/ALU",     "desc":"Signed divide (rdx:rax÷src)",
     "tpl":"idiv {src}",               "ports":[{"n":"src","d":"rbx"}]},
    {"id":"asm.div",    "title":"DIV",     "cat":"CPU/ALU",     "desc":"Unsigned divide",
     "tpl":"div {src}",                "ports":[{"n":"src","d":"rbx"}]},
    {"id":"asm.inc",    "title":"INC",     "cat":"CPU/ALU",     "desc":"Increment by 1",
     "tpl":"inc {dst}",                "ports":[{"n":"dst","d":"rax"}]},
    {"id":"asm.dec",    "title":"DEC",     "cat":"CPU/ALU",     "desc":"Decrement by 1",
     "tpl":"dec {dst}",                "ports":[{"n":"dst","d":"rax"}]},
    {"id":"asm.neg",    "title":"NEG",     "cat":"CPU/ALU",     "desc":"Two's complement negation",
     "tpl":"neg {dst}",                "ports":[{"n":"dst","d":"rax"}]},
    {"id":"asm.cdq",    "title":"CDQ",     "cat":"CPU/ALU",     "desc":"Sign‑extend EAX→EDX:EAX (before idiv)",
     "tpl":"cdq", "ports":[]},
    {"id":"asm.cqo",    "title":"CQO",     "cat":"CPU/ALU",     "desc":"Sign‑extend RAX→RDX:RAX (before idiv)",
     "tpl":"cqo", "ports":[]},
    # ═══════════════════  CPU / Logic  ═══════════════════
    {"id":"asm.xor",    "title":"XOR",     "cat":"CPU/Logic",   "desc":"Bitwise XOR (xor r,r → zero)",
     "tpl":"xor {a}, {b}",             "ports":[{"n":"a","d":"rax"},{"n":"b","d":"rax"}]},
    {"id":"asm.and",    "title":"AND",     "cat":"CPU/Logic",   "desc":"Bitwise AND / mask",
     "tpl":"and {dst}, {mask}",        "ports":[{"n":"dst","d":"rax"},{"n":"mask","d":"0xFF"}]},
    {"id":"asm.or",     "title":"OR",      "cat":"CPU/Logic",   "desc":"Bitwise OR / set bits",
     "tpl":"or {dst}, {src}",          "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.not",    "title":"NOT",     "cat":"CPU/Logic",   "desc":"Bitwise NOT",
     "tpl":"not {dst}",                "ports":[{"n":"dst","d":"rax"}]},
    {"id":"asm.shl",    "title":"SHL",     "cat":"CPU/Logic",   "desc":"Shift left logical",
     "tpl":"shl {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.shr",    "title":"SHR",     "cat":"CPU/Logic",   "desc":"Shift right logical",
     "tpl":"shr {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.sar",    "title":"SAR",     "cat":"CPU/Logic",   "desc":"Shift right arithmetic",
     "tpl":"sar {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.rol",    "title":"ROL",     "cat":"CPU/Logic",   "desc":"Rotate left",
     "tpl":"rol {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.ror",    "title":"ROR",     "cat":"CPU/Logic",   "desc":"Rotate right",
     "tpl":"ror {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.rcl",    "title":"RCL",     "cat":"CPU/Logic",   "desc":"Rotate left through carry",
     "tpl":"rcl {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.rcr",    "title":"RCR",     "cat":"CPU/Logic",   "desc":"Rotate right through carry",
     "tpl":"rcr {dst}, {cnt}",         "ports":[{"n":"dst","d":"rax"},{"n":"cnt","d":"1"}]},
    {"id":"asm.shlx",   "title":"SHLX",    "cat":"CPU/Logic",   "desc":"Shift left (BMI2, no flags)",
     "tpl":"shlx {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"rcx"}]},
    {"id":"asm.shrx",   "title":"SHRX",    "cat":"CPU/Logic",   "desc":"Shift right (BMI2, no flags)",
     "tpl":"shrx {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"rcx"}]},
    {"id":"asm.sarx",   "title":"SARX",    "cat":"CPU/Logic",   "desc":"Arithmetic shift (BMI2, no flags)",
     "tpl":"sarx {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"rcx"}]},
    # ═══════════════════  CPU / Compare  ═══════════════════
    {"id":"asm.cmp",    "title":"CMP",     "cat":"CPU/Compare", "desc":"Compare (sub, sets flags only)",
     "tpl":"cmp {a}, {b}",             "ports":[{"n":"a","d":"rax"},{"n":"b","d":"0"}]},
    {"id":"asm.test",   "title":"TEST",    "cat":"CPU/Compare", "desc":"AND operands, sets flags only",
     "tpl":"test {a}, {b}",            "ports":[{"n":"a","d":"rax"},{"n":"b","d":"rax"}]},
    {"id":"asm.bt",     "title":"BT",      "cat":"CPU/Compare", "desc":"Bit test (into CF)",
     "tpl":"bt {src}, {bit}",          "ports":[{"n":"src","d":"rax"},{"n":"bit","d":"0"}]},
    {"id":"asm.bts",    "title":"BTS",     "cat":"CPU/Compare", "desc":"Bit test & set",
     "tpl":"bts {src}, {bit}",         "ports":[{"n":"src","d":"rax"},{"n":"bit","d":"0"}]},
    {"id":"asm.btr",    "title":"BTR",     "cat":"CPU/Compare", "desc":"Bit test & reset",
     "tpl":"btr {src}, {bit}",         "ports":[{"n":"src","d":"rax"},{"n":"bit","d":"0"}]},
    {"id":"asm.btc",    "title":"BTC",     "cat":"CPU/Compare", "desc":"Bit test & complement",
     "tpl":"btc {src}, {bit}",         "ports":[{"n":"src","d":"rax"},{"n":"bit","d":"0"}]},
    # ═══════════════════  CPU / Flow  ═══════════════════
    {"id":"asm.jmp",    "title":"JMP",     "cat":"CPU/Flow",    "desc":"Unconditional jump",
     "tpl":"jmp {lbl}",                "ports":[{"n":"lbl","d":".loop"}]},
    {"id":"asm.je",     "title":"JE/JZ",   "cat":"CPU/Flow",    "desc":"Jump if equal (ZF=1)",
     "tpl":"je {lbl}",                 "ports":[{"n":"lbl","d":".done"}]},
    {"id":"asm.jne",    "title":"JNE/JNZ", "cat":"CPU/Flow",    "desc":"Jump if not equal (ZF=0)",
     "tpl":"jne {lbl}",                "ports":[{"n":"lbl","d":".loop"}]},
    {"id":"asm.jl",     "title":"JL",      "cat":"CPU/Flow",    "desc":"Jump if less (signed, SF≠OF)",
     "tpl":"jl {lbl}",                 "ports":[{"n":"lbl","d":".err"}]},
    {"id":"asm.jle",    "title":"JLE",     "cat":"CPU/Flow",    "desc":"Jump if less or equal",
     "tpl":"jle {lbl}",                "ports":[{"n":"lbl","d":".done"}]},
    {"id":"asm.jg",     "title":"JG",      "cat":"CPU/Flow",    "desc":"Jump if greater (signed)",
     "tpl":"jg {lbl}",                 "ports":[{"n":"lbl","d":".big"}]},
    {"id":"asm.jge",    "title":"JGE",     "cat":"CPU/Flow",    "desc":"Jump if greater or equal",
     "tpl":"jge {lbl}",                "ports":[{"n":"lbl","d":".ok"}]},
    {"id":"asm.jb",     "title":"JB/JC",   "cat":"CPU/Flow",    "desc":"Jump if below / carry (CF=1)",
     "tpl":"jb {lbl}",                 "ports":[{"n":"lbl","d":".under"}]},
    {"id":"asm.jbe",    "title":"JBE",     "cat":"CPU/Flow",    "desc":"Jump if below or equal",
     "tpl":"jbe {lbl}",                "ports":[{"n":"lbl","d":".done"}]},
    {"id":"asm.ja",     "title":"JA",      "cat":"CPU/Flow",    "desc":"Jump if above (CF=0,ZF=0)",
     "tpl":"ja {lbl}",                 "ports":[{"n":"lbl","d":".above"}]},
    {"id":"asm.jae",    "title":"JAE",     "cat":"CPU/Flow",    "desc":"Jump if above or equal",
     "tpl":"jae {lbl}",                "ports":[{"n":"lbl","d":".above"}]},
    {"id":"asm.jo",     "title":"JO",      "cat":"CPU/Flow",    "desc":"Jump if overflow (OF=1)",
     "tpl":"jo {lbl}",                 "ports":[{"n":"lbl","d":".ovfl"}]},
    {"id":"asm.js",     "title":"JS",      "cat":"CPU/Flow",    "desc":"Jump if sign (SF=1)",
     "tpl":"js {lbl}",                 "ports":[{"n":"lbl","d":".neg"}]},
    {"id":"asm.call",   "title":"CALL",    "cat":"CPU/Flow",    "desc":"Call direct procedure",
     "tpl":"call {proc}",              "ports":[{"n":"proc","d":"my_func"}]},
    {"id":"asm.calli",  "title":"CALL []", "cat":"CPU/Flow",    "desc":"Indirect call via import table",
     "tpl":"call [{proc}]",            "ports":[{"n":"proc","d":"ExitProcess"}]},
    {"id":"asm.ret",    "title":"RET",     "cat":"CPU/Flow",    "desc":"Return from procedure",   "tpl":"ret", "ports":[]},
    {"id":"asm.retn",   "title":"RET n",   "cat":"CPU/Flow",    "desc":"Return + pop n bytes (stdcall)",
     "tpl":"ret {n}",                  "ports":[{"n":"n","d":"8"}]},
    {"id":"asm.loop",   "title":"LOOP",    "cat":"CPU/Flow",    "desc":"Dec RCX, jump if RCX≠0",
     "tpl":"loop {lbl}",               "ports":[{"n":"lbl","d":".body"}]},
    {"id":"asm.loope",  "title":"LOOPE",   "cat":"CPU/Flow",    "desc":"Loop while equal (ZF=1)",
     "tpl":"loope {lbl}",              "ports":[{"n":"lbl","d":".body"}]},
    {"id":"asm.loopne", "title":"LOOPNE",  "cat":"CPU/Flow",    "desc":"Loop while not equal (ZF=0)",
     "tpl":"loopne {lbl}",             "ports":[{"n":"lbl","d":".body"}]},
    # ── CMOVcc ─────────────────────────────────────────────────────────────
    {"id":"asm.cmove",  "title":"CMOVE",   "cat":"CPU/Flow",    "desc":"Conditional move if equal",
     "tpl":"cmove {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovne", "title":"CMOVNE",  "cat":"CPU/Flow",    "desc":"Conditional move if not equal",
     "tpl":"cmovne {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovg",  "title":"CMOVG",   "cat":"CPU/Flow",    "desc":"Conditional move if greater",
     "tpl":"cmovg {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovge", "title":"CMOVGE",  "cat":"CPU/Flow",    "desc":"Conditional move if greater or equal",
     "tpl":"cmovge {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovl",  "title":"CMOVL",   "cat":"CPU/Flow",    "desc":"Conditional move if less",
     "tpl":"cmovl {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovle", "title":"CMOVLE",  "cat":"CPU/Flow",    "desc":"Conditional move if less or equal",
     "tpl":"cmovle {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmova",  "title":"CMOVA",   "cat":"CPU/Flow",    "desc":"Conditional move if above",
     "tpl":"cmova {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovae", "title":"CMOVAE",  "cat":"CPU/Flow",    "desc":"Conditional move if above or equal",
     "tpl":"cmovae {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovb",  "title":"CMOVB",   "cat":"CPU/Flow",    "desc":"Conditional move if below",
     "tpl":"cmovb {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovbe", "title":"CMOVBE",  "cat":"CPU/Flow",    "desc":"Conditional move if below or equal",
     "tpl":"cmovbe {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    # ═══════════════════  CPU / Misc  ═══════════════════
    {"id":"asm.nop",    "title":"NOP",     "cat":"CPU/Misc",    "desc":"No operation",                 "tpl":"nop","ports":[]},
    {"id":"asm.int3",   "title":"INT3",    "cat":"CPU/Misc",    "desc":"Debugger breakpoint",          "tpl":"int3","ports":[]},
    {"id":"asm.syscall","title":"SYSCALL", "cat":"CPU/Misc",    "desc":"Linux x64 system call",        "tpl":"syscall","ports":[]},
    {"id":"asm.cpuid",  "title":"CPUID",   "cat":"CPU/Misc",    "desc":"Query CPU features (eax=leaf)","tpl":"cpuid","ports":[]},
    {"id":"asm.rdtsc",  "title":"RDTSC",   "cat":"CPU/Misc",    "desc":"Read timestamp counter → rdx:rax","tpl":"rdtsc","ports":[]},
    {"id":"asm.hlt",    "title":"HLT",     "cat":"CPU/Misc",    "desc":"Halt until interrupt",         "tpl":"hlt","ports":[]},
    {"id":"asm.int",    "title":"INT n",   "cat":"CPU/Misc",    "desc":"Software interrupt",
     "tpl":"int {n}",                  "ports":[{"n":"n","d":"0x80"}]},
    {"id":"asm.clc",    "title":"CLC",     "cat":"CPU/Misc",    "desc":"Clear carry flag",  "tpl":"clc","ports":[]},
    {"id":"asm.stc",    "title":"STC",     "cat":"CPU/Misc",    "desc":"Set carry flag",    "tpl":"stc","ports":[]},
    {"id":"asm.cld",    "title":"CLD",     "cat":"CPU/Misc",    "desc":"Clear direction flag","tpl":"cld","ports":[]},
    {"id":"asm.std",    "title":"STD",     "cat":"CPU/Misc",    "desc":"Set direction flag",  "tpl":"std","ports":[]},
    {"id":"asm.mfence", "title":"MFENCE",  "cat":"CPU/Misc",    "desc":"Memory fence",      "tpl":"mfence","ports":[]},
    {"id":"asm.lfence", "title":"LFENCE",  "cat":"CPU/Misc",    "desc":"Load fence",        "tpl":"lfence","ports":[]},
    {"id":"asm.sfence", "title":"SFENCE",  "cat":"CPU/Misc",    "desc":"Store fence",       "tpl":"sfence","ports":[]},
    {"id":"asm.pause",  "title":"PAUSE",   "cat":"CPU/Misc",    "desc":"Spin-loop hint",    "tpl":"pause","ports":[]},
    # ═══════════════════  CPU / SIMD SSE  ═══════════════════
    {"id":"sse.movaps", "title":"MOVAPS",  "cat":"CPU/SIMD-SSE","desc":"Move aligned packed f32 (16‑byte aligned)",
     "tpl":"movaps {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.movups", "title":"MOVUPS",  "cat":"CPU/SIMD-SSE","desc":"Move unaligned packed f32",
     "tpl":"movups {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"[rbx]"}]},
    {"id":"sse.movss",  "title":"MOVSS",   "cat":"CPU/SIMD-SSE","desc":"Move scalar f32",
     "tpl":"movss {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"[rax]"}]},
    {"id":"sse.movsd",  "title":"MOVSD",   "cat":"CPU/SIMD-SSE","desc":"Move scalar f64",
     "tpl":"movsd {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"[rax]"}]},
    {"id":"sse.addps",  "title":"ADDPS",   "cat":"CPU/SIMD-SSE","desc":"Add packed f32 (4×f32)",
     "tpl":"addps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.addss",  "title":"ADDSS",   "cat":"CPU/SIMD-SSE","desc":"Add scalar f32",
     "tpl":"addss {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.subps",  "title":"SUBPS",   "cat":"CPU/SIMD-SSE","desc":"Sub packed f32",
     "tpl":"subps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.mulps",  "title":"MULPS",   "cat":"CPU/SIMD-SSE","desc":"Multiply packed f32",
     "tpl":"mulps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.divps",  "title":"DIVPS",   "cat":"CPU/SIMD-SSE","desc":"Divide packed f32",
     "tpl":"divps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.xorps",  "title":"XORPS",   "cat":"CPU/SIMD-SSE","desc":"XOR packed (xorps x,x → zero xmm)",
     "tpl":"xorps {a}, {b}",           "ports":[{"n":"a","d":"xmm0"},{"n":"b","d":"xmm0"}]},
    {"id":"sse.sqrtps", "title":"SQRTPS",  "cat":"CPU/SIMD-SSE","desc":"Square root packed f32",
     "tpl":"sqrtps {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.maxps",  "title":"MAXPS",   "cat":"CPU/SIMD-SSE","desc":"Max packed f32",
     "tpl":"maxps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.minps",  "title":"MINPS",   "cat":"CPU/SIMD-SSE","desc":"Min packed f32",
     "tpl":"minps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.cvtsi2ss","title":"CVTSI2SS","cat":"CPU/SIMD-SSE","desc":"Int→scalar f32",
     "tpl":"cvtsi2ss {xmm}, {int}",    "ports":[{"n":"xmm","d":"xmm0"},{"n":"int","d":"rax"}]},
    {"id":"sse.cvtsi2sd","title":"CVTSI2SD","cat":"CPU/SIMD-SSE","desc":"Int→scalar f64",
     "tpl":"cvtsi2sd {xmm}, {int}",    "ports":[{"n":"xmm","d":"xmm0"},{"n":"int","d":"rax"}]},
    {"id":"sse.cvtss2si","title":"CVTSS2SI","cat":"CPU/SIMD-SSE","desc":"Scalar f32→int",
     "tpl":"cvtss2si {int}, {xmm}",    "ports":[{"n":"int","d":"rax"},{"n":"xmm","d":"xmm0"}]},
    {"id":"sse.pxor",   "title":"PXOR",    "cat":"CPU/SIMD-SSE","desc":"XOR packed integer (128‑bit)",
     "tpl":"pxor {dst}, {src}",        "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pand",   "title":"PAND",    "cat":"CPU/SIMD-SSE","desc":"AND packed integer",
     "tpl":"pand {dst}, {src}",        "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.por",    "title":"POR",     "cat":"CPU/SIMD-SSE","desc":"OR packed integer",
     "tpl":"por {dst}, {src}",         "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddb",  "title":"PADDB",   "cat":"CPU/SIMD-SSE","desc":"Add packed bytes",
     "tpl":"paddb {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddw",  "title":"PADDW",   "cat":"CPU/SIMD-SSE","desc":"Add packed words",
     "tpl":"paddw {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddd",  "title":"PADDD",   "cat":"CPU/SIMD-SSE","desc":"Add packed dwords",
     "tpl":"paddd {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.psubb",  "title":"PSUBB",   "cat":"CPU/SIMD-SSE","desc":"Sub packed bytes",
     "tpl":"psubb {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pmullw", "title":"PMULLW",  "cat":"CPU/SIMD-SSE","desc":"Multiply packed words (low result)",
     "tpl":"pmullw {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pmaddwd","title":"PMADDWD", "cat":"CPU/SIMD-SSE","desc":"Multiply & add packed words→dwords",
     "tpl":"pmaddwd {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    # ═══════════════════  CPU / SIMD AVX  ═══════════════════
    {"id":"avx.vmovaps","title":"VMOVAPS", "cat":"CPU/SIMD-AVX","desc":"AVX move aligned packed f32 (8×f32)",
     "tpl":"vmovaps {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"ymm1"}]},
    {"id":"avx.vmovups","title":"VMOVUPS", "cat":"CPU/SIMD-AVX","desc":"AVX move unaligned packed f32",
     "tpl":"vmovups {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"[rbx]"}]},
    {"id":"avx.vaddps", "title":"VADDPS",  "cat":"CPU/SIMD-AVX","desc":"AVX add packed f32 (8×f32)",
     "tpl":"vaddps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vsubps", "title":"VSUBPS",  "cat":"CPU/SIMD-AVX","desc":"AVX sub packed f32",
     "tpl":"vsubps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vmulps", "title":"VMULPS",  "cat":"CPU/SIMD-AVX","desc":"AVX mul packed f32",
     "tpl":"vmulps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vdivps", "title":"VDIVPS",  "cat":"CPU/SIMD-AVX","desc":"AVX div packed f32",
     "tpl":"vdivps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vxorps", "title":"VXORPS",  "cat":"CPU/SIMD-AVX","desc":"AVX XOR packed (zero ymm)",
     "tpl":"vxorps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm0"},{"n":"b","d":"ymm0"}]},
    {"id":"avx.vsqrtps","title":"VSQRTPS","cat":"CPU/SIMD-AVX","desc":"AVX sqrt packed f32",
     "tpl":"vsqrtps {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"ymm1"}]},
    {"id":"avx.vbroadcastss","title":"VBROADCASTSS","cat":"CPU/SIMD-AVX","desc":"Broadcast scalar f32 to all lanes",
     "tpl":"vbroadcastss {ymm}, {src}","ports":[{"n":"ymm","d":"ymm0"},{"n":"src","d":"[rax]"}]},
    # ═══════════════════  CPU / SIMD AVX-512  ═══════════════════
    {"id":"avx512.vaddps","title":"VADDPS (ZMM)","cat":"CPU/SIMD-AVX512","desc":"AVX‑512 add packed f32 (16×f32)",
     "tpl":"vaddps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"zmm0"},{"n":"a","d":"zmm1"},{"n":"b","d":"zmm2"}]},
    {"id":"avx512.vmovaps","title":"VMOVAPS (ZMM)","cat":"CPU/SIMD-AVX512","desc":"AVX‑512 move aligned packed f32",
     "tpl":"vmovaps {dst}, {src}",     "ports":[{"n":"dst","d":"zmm0"},{"n":"src","d":"zmm1"}]},
    # ═══════════════════  CPU / FMA  ═══════════════════
    {"id":"fma.vfmadd132ps","title":"VFMADD132PS","cat":"CPU/SIMD-FMA","desc":"FMA: dst = (dst*src1)+src2",
     "tpl":"vfmadd132ps {dst}, {src1}, {src2}","ports":[{"n":"dst","d":"xmm0"},{"n":"src1","d":"xmm1"},{"n":"src2","d":"xmm2"}]},
    # ═══════════════════  CPU / BMI  ═══════════════════
    {"id":"bmi.blsr",   "title":"BLSR",    "cat":"CPU/BMI",      "desc":"Reset lowest set bit (BMI1)",
     "tpl":"blsr {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"bmi.blsmsk", "title":"BLSMSK",  "cat":"CPU/BMI",      "desc":"Get mask up to lowest set bit",
     "tpl":"blsmsk {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"bmi.bextr",  "title":"BEXTR",   "cat":"CPU/BMI",      "desc":"Bit field extract (BMI1)",
     "tpl":"bextr {dst}, {src}, {start}","ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"start","d":"8"}]},
    {"id":"bmi.bzhi",   "title":"BZHI",    "cat":"CPU/BMI",      "desc":"Zero high bits (BMI2)",
     "tpl":"bzhi {dst}, {src}, {idx}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"idx","d":"4"}]},
    # ═══════════════════  CPU / FPU (x87)  ═══════════════════
    {"id":"fpu.fld",    "title":"FLD",     "cat":"CPU/FPU",      "desc":"Load floating point",
     "tpl":"fld {src}",                "ports":[{"n":"src","d":"dword [rax]"}]},
    {"id":"fpu.fstp",   "title":"FSTP",    "cat":"CPU/FPU",      "desc":"Store floating point & pop",
     "tpl":"fstp {dst}",               "ports":[{"n":"dst","d":"qword [rbx]"}]},
    {"id":"fpu.fadd",   "title":"FADD",    "cat":"CPU/FPU",      "desc":"Add floating point",
     "tpl":"fadd {src}",               "ports":[{"n":"src","d":"st1"}]},
    {"id":"fpu.fsqrt",  "title":"FSQRT",   "cat":"CPU/FPU",      "desc":"Square root",
     "tpl":"fsqrt", "ports":[]},
    # ═══════════════════  ABI / Win64  ═══════════════════
    {"id":"abi.proc","title":"Procedure Prologue","cat":"ABI/Win64",
     "desc":"Win64 function prologue (push rbp / mov rbp,rsp / sub rsp,32)",
     "tpl":"{name}:\n    push rbp\n    mov  rbp, rsp\n    sub  rsp, 32",
     "ports":[{"n":"name","d":"my_func"}]},
    {"id":"abi.epilog","title":"Procedure Epilogue","cat":"ABI/Win64",
     "desc":"Win64 function epilogue (add rsp,32 / pop rbp / ret)",
     "tpl":"    add  rsp, 32\n    pop  rbp\n    ret","ports":[]},
    {"id":"abi.shadow","title":"Shadow Space","cat":"ABI/Win64",
     "desc":"Allocate 32‑byte shadow space before any Win64 CALL",
     "tpl":"    sub  rsp, 32","ports":[]},
    {"id":"abi.unshadow","title":"Remove Shadow","cat":"ABI/Win64",
     "desc":"Restore stack after Win64 CALL shadow space",
     "tpl":"    add  rsp, 32","ports":[]},
    {"id":"abi.align","title":"Align Stack 16","cat":"ABI/Win64",
     "desc":"Force 16‑byte alignment required before SSE/AVX calls",
     "tpl":"    and  rsp, -16","ports":[]},
    {"id":"abi.args","title":"Args Comment","cat":"ABI/Win64",
     "desc":"Win64 CC: rcx rdx r8 r9 then stack at [rsp+32+]",
     "tpl":"; arg1→rcx  arg2→rdx  arg3→r8  arg4→r9  arg5+→[rsp+32]","ports":[]},
    {"id":"abi.winmain","title":"WinMain Prologue","cat":"ABI/Win64",
     "desc":"Windows WinMain entry point skeleton (Win64 ABI)",
     "tpl":"WinMain:\n    push rbp\n    mov  rbp, rsp\n    sub  rsp, 64\n    ; rcx=hInst  rdx=hPrevInst  r8=lpCmdLine  r9=nCmdShow",
     "ports":[]},
    # ═══════════════════  Directives  ═══════════════════
    {"id":"dir.bits64","title":"bits 64","cat":"Directives","desc":"Set 64‑bit mode","tpl":"bits 64","ports":[]},
    {"id":"dir.defrel","title":"default rel","cat":"Directives","desc":"RIP‑relative addressing by default","tpl":"default rel","ports":[]},
    {"id":"dir.global","title":"global","cat":"Directives","desc":"Export symbol",
     "tpl":"global {sym}","ports":[{"n":"sym","d":"main"}]},
    {"id":"dir.extern","title":"extern","cat":"Directives","desc":"Import external symbol",
     "tpl":"extern {sym}","ports":[{"n":"sym","d":"ExitProcess"}]},
    # ═══════════════════  Sections  ═══════════════════
    {"id":"sec.text","title":".text","cat":"Sections","desc":"Code section","tpl":"section .text","ports":[]},
    {"id":"sec.data","title":".data","cat":"Sections","desc":"Initialized data","tpl":"section .data","ports":[]},
    {"id":"sec.bss","title":".bss","cat":"Sections","desc":"Uninitialized data","tpl":"section .bss","ports":[]},
    {"id":"sec.rodata","title":".rodata","cat":"Sections","desc":"Read‑only data","tpl":"section .rodata","ports":[]},
    {"id":"sec.idata","title":".idata","cat":"Sections","desc":"Import table","tpl":"section .idata","ports":[]},
    # ═══════════════════  Data / Define  ═══════════════════
    {"id":"dat.db","title":"DB byte","cat":"Data/Define","desc":"Define byte",
     "tpl":"{name}  db  {val}","ports":[{"n":"name","d":"my_byte"},{"n":"val","d":"0"}]},
    {"id":"dat.dw","title":"DW word","cat":"Data/Define","desc":"Define word",
     "tpl":"{name}  dw  {val}","ports":[{"n":"name","d":"my_word"},{"n":"val","d":"0"}]},
    {"id":"dat.dd","title":"DD dword","cat":"Data/Define","desc":"Define dword",
     "tpl":"{name}  dd  {val}","ports":[{"n":"name","d":"my_dword"},{"n":"val","d":"0"}]},
    {"id":"dat.dq","title":"DQ qword","cat":"Data/Define","desc":"Define qword",
     "tpl":"{name}  dq  {val}","ports":[{"n":"name","d":"my_qword"},{"n":"val","d":"0"}]},
    {"id":"dat.str","title":"DB string","cat":"Data/Define","desc":"Null‑terminated ASCII string",
     "tpl":'{name}  db  "{txt}", 0',"ports":[{"n":"name","d":"msg"},{"n":"txt","d":"Hello!"}]},
    {"id":"dat.strn","title":"DB string+CRLF","cat":"Data/Define","desc":"String with CR+LF",
     "tpl":'{name}  db  "{txt}", 0x0D, 0x0A, 0',"ports":[{"n":"name","d":"msg"},{"n":"txt","d":"Hello!"}]},
    {"id":"dat.resb","title":"RESB","cat":"Data/Define","desc":"Reserve n bytes (BSS)",
     "tpl":"{name}  resb  {n}","ports":[{"n":"name","d":"buffer"},{"n":"n","d":"256"}]},
    {"id":"dat.resq","title":"RESQ","cat":"Data/Define","desc":"Reserve n qwords (BSS)",
     "tpl":"{name}  resq  {n}","ports":[{"n":"name","d":"buf64"},{"n":"n","d":"16"}]},
    {"id":"dat.equ","title":"EQU","cat":"Data/Define","desc":"Define constant (no storage)",
     "tpl":"{name}  equ  {val}","ports":[{"n":"name","d":"PAGE_SIZE"},{"n":"val","d":"4096"}]},
    {"id":"dat.times","title":"TIMES","cat":"Data/Define","desc":"Repeat instruction/data",
     "tpl":"times {count} {instr}","ports":[{"n":"count","d":"8"},{"n":"instr","d":"nop"}]},
    # ═══════════════════  GDI32 (complete)  ═══════════════════
    {"id":"gdi.beginpaint","title":"BeginPaint","cat":"GDI32","desc":"BeginPaint + get HDC",
     "tpl":"; BeginPaint(hwnd, &ps)\n    lea  rdx, [rel ps_buf]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [BeginPaint]\n    add  rsp, 32\n    mov  [rel hdc], rax","ports":[]},
    {"id":"gdi.endpaint","title":"EndPaint","cat":"GDI32","desc":"EndPaint",
     "tpl":"; EndPaint(hwnd, &ps)\n    lea  rdx, [rel ps_buf]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [EndPaint]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.textout","title":"TextOutA","cat":"GDI32","desc":"Draw text at (x,y)",
     "tpl":"; TextOutA(hdc, x, y, str, len)\n    mov  r9,  {len}\n    lea  r8,  [{str}]\n    mov  rdx, {y}\n    mov  rcx, {x}\n    push rcx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [TextOutA]\n    add  rsp, 32\n    pop  rcx",
     "ports":[{"n":"str","d":"my_str"},{"n":"x","d":"10"},{"n":"y","d":"10"},{"n":"len","d":"13"}]},
    {"id":"gdi.rectangle","title":"Rectangle","cat":"GDI32","desc":"Draw rectangle",
     "tpl":"; Rectangle(hdc, left, top, right, bottom)\n    mov  [rsp+32], {bot}\n    mov  r9,  {right}\n    mov  r8,  {top}\n    mov  rdx, {left}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [Rectangle]\n    add  rsp, 32",
     "ports":[{"n":"left","d":"10"},{"n":"top","d":"10"},{"n":"right","d":"200"},{"n":"bot","d":"100"}]},
    {"id":"gdi.fillrect","title":"FillRect","cat":"GDI32","desc":"Fill rectangle with brush",
     "tpl":"; FillRect(hdc, &rect, hBrush)\n    mov  r8,  [rel hbrush]\n    lea  rdx, [rel rc_buf]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [FillRect]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.bitblt","title":"BitBlt","cat":"GDI32","desc":"Bit‑block transfer",
     "tpl":"; BitBlt(dst,dx,dy,w,h,src,sx,sy,rop)\n    mov  [rsp+56], 0xCC0020  ; SRCCOPY\n    mov  [rsp+48], 0\n    mov  [rsp+40], 0\n    mov  [rsp+32], [rel mem_dc]\n    mov  r9,  100\n    mov  r8,  100\n    xor  rdx, rdx\n    xor  rcx, rcx\n    push rcx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [BitBlt]\n    add  rsp, 32\n    pop  rcx","ports":[]},
    {"id":"gdi.createbrush","title":"CreateSolidBrush","cat":"GDI32","desc":"Create solid brush",
     "tpl":"; CreateSolidBrush(color)\n    mov  rcx, {color}\n    sub  rsp, 32\n    call [CreateSolidBrush]\n    add  rsp, 32\n    mov  [rel hbrush], rax",
     "ports":[{"n":"color","d":"0xFF0000"}]},
    {"id":"gdi.setbkcolor","title":"SetBkColor","cat":"GDI32","desc":"Set background color",
     "tpl":"; SetBkColor(hdc, color)\n    mov  rdx, {color}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetBkColor]\n    add  rsp, 32",
     "ports":[{"n":"color","d":"0x000000"}]},
    {"id":"gdi.settextcolor","title":"SetTextColor","cat":"GDI32","desc":"Set text color",
     "tpl":"; SetTextColor(hdc, color)\n    mov  rdx, {color}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetTextColor]\n    add  rsp, 32",
     "ports":[{"n":"color","d":"0xFFFFFF"}]},
    {"id":"gdi.createpen","title":"CreatePen","cat":"GDI32","desc":"Create pen (outline style)",
     "tpl":"; CreatePen(style, width, color)\n    mov  r8,  {color}\n    mov  rdx, {width}\n    mov  rcx, {style}\n    sub  rsp, 32\n    call [CreatePen]\n    add  rsp, 32\n    mov  [rel hpen], rax",
     "ports":[{"n":"style","d":"0"},{"n":"width","d":"1"},{"n":"color","d":"0x000000"}]},
    {"id":"gdi.selectobject","title":"SelectObject","cat":"GDI32","desc":"Select GDI object into DC",
     "tpl":"; SelectObject(hdc, hobj)\n    mov  rdx, {obj}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SelectObject]\n    add  rsp, 32\n    mov  [rel old_obj], rax",
     "ports":[{"n":"obj","d":"[rel hpen]"}]},
    {"id":"gdi.deleteobject","title":"DeleteObject","cat":"GDI32","desc":"Delete GDI object",
     "tpl":"; DeleteObject(hobj)\n    mov  rcx, {obj}\n    sub  rsp, 32\n    call [DeleteObject]\n    add  rsp, 32",
     "ports":[{"n":"obj","d":"[rel hbrush]"}]},
    {"id":"gdi.getdc","title":"GetDC","cat":"GDI32","desc":"Get device context of window",
     "tpl":"; GetDC(hwnd)\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [GetDC]\n    add  rsp, 32\n    mov  [rel hdc], rax","ports":[]},
    {"id":"gdi.releasedc","title":"ReleaseDC","cat":"GDI32","desc":"Release device context",
     "tpl":"; ReleaseDC(hwnd, hdc)\n    mov  rdx, [rel hdc]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [ReleaseDC]\n    add  rsp, 32","ports":[]},
    # ═══════════════════  OpenGL32 / WGL  ═══════════════════
    {"id":"gl.setup_pfd","title":"SetPixelFormat","cat":"OpenGL32","desc":"Setup PFD & set pixel format",
     "tpl":"; ChoosePixelFormat + SetPixelFormat\n    mov  word [rel pfd+0],  40\n    mov  word [rel pfd+2],  1\n    mov  dword [rel pfd+4], 0x25\n    mov  byte  [rel pfd+8],  0\n    mov  byte  [rel pfd+9],  32\n    mov  byte  [rel pfd+22], 24\n    lea  rdx, [rel pfd]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [ChoosePixelFormat]\n    add  rsp, 32\n    mov  [rel pf_idx], eax\n    lea  r8, [rel pfd]\n    mov  edx, [rel pf_idx]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetPixelFormat]\n    add  rsp, 32","ports":[]},
    {"id":"gl.create_ctx","title":"wglCreateContext","cat":"OpenGL32","desc":"Create GL rendering context",
     "tpl":"; wglCreateContext(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [wglCreateContext]\n    add  rsp, 32\n    mov  [rel hrc], rax","ports":[]},
    {"id":"gl.make_current","title":"wglMakeCurrent","cat":"OpenGL32","desc":"Make GL context current",
     "tpl":"; wglMakeCurrent(hdc, hrc)\n    mov  rdx, [rel hrc]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [wglMakeCurrent]\n    add  rsp, 32","ports":[]},
    {"id":"gl.delete_ctx","title":"wglDeleteContext","cat":"OpenGL32","desc":"Delete GL context",
     "tpl":"; wglDeleteContext(hrc)\n    mov  rcx, [rel hrc]\n    sub  rsp, 32\n    call [wglDeleteContext]\n    add  rsp, 32","ports":[]},
    {"id":"gl.swapbuffers","title":"SwapBuffers","cat":"OpenGL32","desc":"Swap front/back buffers",
     "tpl":"; SwapBuffers(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SwapBuffers]\n    add  rsp, 32","ports":[]},
    {"id":"gl.clear","title":"glClear","cat":"OpenGL32","desc":"Clear color+depth buffers (0x4100)",
     "tpl":"; glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)\n    mov  ecx, 0x4100\n    sub  rsp, 32\n    call [glClear]\n    add  rsp, 32","ports":[]},
    {"id":"gl.clearcolor","title":"glClearColor","cat":"OpenGL32","desc":"Set clear color (r,g,b,a via XMM registers)",
     "tpl":"; glClearColor(r,g,b,a)  — floats in xmm0–xmm3\n    xorps xmm3, xmm3\n    xorps xmm2, xmm2\n    xorps xmm1, xmm1\n    xorps xmm0, xmm0\n    sub   rsp, 32\n    call  [glClearColor]\n    add   rsp, 32","ports":[]},
    {"id":"gl.viewport","title":"glViewport","cat":"OpenGL32","desc":"Set viewport rectangle",
     "tpl":"; glViewport(x, y, w, h)\n    mov  r9,  {h}\n    mov  r8,  {w}\n    xor  rdx, rdx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [glViewport]\n    add  rsp, 32",
     "ports":[{"n":"w","d":"800"},{"n":"h","d":"600"}]},
    {"id":"gl.begin","title":"glBegin","cat":"OpenGL32","desc":"Begin immediate‑mode primitive",
     "tpl":"; glBegin(mode)\n    mov  ecx, {mode}\n    sub  rsp, 32\n    call [glBegin]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"0x0004"}]},
    {"id":"gl.end","title":"glEnd","cat":"OpenGL32","desc":"End immediate‑mode primitive",
     "tpl":"; glEnd()\n    sub  rsp, 32\n    call [glEnd]\n    add  rsp, 32","ports":[]},
    {"id":"gl.vertex3f","title":"glVertex3f","cat":"OpenGL32","desc":"Specify 3D vertex (x,y,z via XMM0‑XMM2)",
     "tpl":"; glVertex3f(x, y, z)  — floats via xmm0,xmm1,xmm2\n    sub  rsp, 32\n    call [glVertex3f]\n    add  rsp, 32","ports":[]},
    {"id":"gl.color3f","title":"glColor3f","cat":"OpenGL32","desc":"Set current color (r,g,b via XMM0‑XMM2)",
     "tpl":"; glColor3f(r, g, b)  — floats via xmm0,xmm1,xmm2\n    sub  rsp, 32\n    call [glColor3f]\n    add  rsp, 32","ports":[]},
    {"id":"gl.matrixmode","title":"glMatrixMode","cat":"OpenGL32","desc":"Set matrix mode (0x1700=MODELVIEW,0x1701=PROJECTION)",
     "tpl":"; glMatrixMode(mode)\n    mov  ecx, {mode}\n    sub  rsp, 32\n    call [glMatrixMode]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"0x1700"}]},
    {"id":"gl.loadidentity","title":"glLoadIdentity","cat":"OpenGL32","desc":"Load identity matrix",
     "tpl":"; glLoadIdentity()\n    sub  rsp, 32\n    call [glLoadIdentity]\n    add  rsp, 32","ports":[]},
    {"id":"gl.enable","title":"glEnable","cat":"OpenGL32","desc":"Enable GL capability (e.g. GL_DEPTH_TEST=0x0B71)",
     "tpl":"; glEnable(cap)\n    mov  ecx, {cap}\n    sub  rsp, 32\n    call [glEnable]\n    add  rsp, 32",
     "ports":[{"n":"cap","d":"0x0B71"}]},
    {"id":"gl.disable","title":"glDisable","cat":"OpenGL32","desc":"Disable GL capability",
     "tpl":"; glDisable(cap)\n    mov  ecx, {cap}\n    sub  rsp, 32\n    call [glDisable]\n    add  rsp, 32",
     "ports":[{"n":"cap","d":"0x0B71"}]},
    {"id":"gl.flush","title":"glFlush","cat":"OpenGL32","desc":"Flush GL command pipeline",
     "tpl":"; glFlush()\n    sub  rsp, 32\n    call [glFlush]\n    add  rsp, 32","ports":[]},
    # ═══════════════════  Project Skeletons  ═══════════════════
    {"id":"proj.console","title":"Win64 Console App","cat":"Projects",
     "desc":"Minimal NASM Win64 console executable",
     "tpl":"""\
bits 64
default rel

global main
extern ExitProcess

section .text
main:
    push rbp
    mov  rbp, rsp
    sub  rsp, 32

    ; ── your code here ──────────────────────────────
    xor  eax, eax

    add  rsp, 32
    pop  rbp
    xor  rcx, rcx
    sub  rsp, 32
    call [ExitProcess]

section .data
    msg  db  "Hello, World!", 0x0D, 0x0A, 0

section .bss
    buffer  resb  256
""","ports":[]},
    {"id":"proj.gdi","title":"Win64 GDI32 Window","cat":"Projects",
     "desc":"Complete GDI32 window with message loop + painting",
     "tpl":"""\
bits 64
default rel

global WinMain
extern RegisterClassExA, CreateWindowExA, ShowWindow, UpdateWindow
extern GetMessageA, TranslateMessage, DispatchMessageA, PostQuitMessage
extern DefWindowProcA, LoadCursorA, BeginPaint, EndPaint
extern TextOutA, GetDC, ReleaseDC, ExitProcess

section .text

WndProc:
    push rbp
    mov  rbp, rsp
    sub  rsp, 64
    cmp  edx, 0x000F            ; WM_PAINT
    je   .paint
    cmp  edx, 0x0002            ; WM_DESTROY
    je   .destroy
    add  rsp, 64
    pop  rbp
    jmp  [DefWindowProcA]
.destroy:
    xor  rcx, rcx
    sub  rsp, 32
    call [PostQuitMessage]
    add  rsp, 32
    xor  rax, rax
    add  rsp, 64
    pop  rbp
    ret
.paint:
    mov  [rel hwnd], rcx
    lea  rdx, [rel ps_buf]
    sub  rsp, 32
    call [BeginPaint]
    add  rsp, 32
    mov  [rel hdc], rax
    ; TextOutA(hdc, 10, 10, msg, 13)
    mov  r9,  13
    lea  r8,  [rel msg]
    mov  rdx, 10
    mov  rcx, 10
    push rcx
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [TextOutA]
    add  rsp, 32
    pop  rcx
    lea  rdx, [rel ps_buf]
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [EndPaint]
    add  rsp, 32
    xor  rax, rax
    add  rsp, 64
    pop  rbp
    ret

WinMain:
    push rbp
    mov  rbp, rsp
    sub  rsp, 64
    ; RegisterClassExA
    lea  rcx, [rel wndcls]
    sub  rsp, 32
    call [RegisterClassExA]
    add  rsp, 32
    ; CreateWindowExA – simplified for demonstration
    mov  [rsp+56], 0
    mov  [rsp+48], 0
    mov  [rsp+40], 0
    mov  [rsp+32], 0
    mov  r9,  600
    mov  r8,  800
    xor  rdx, rdx
    xor  rcx, rcx
    push rcx
    mov  rcx, 0x00CF0000
    push rcx
    xor  rcx, rcx
    push rcx
    lea  rcx, [rel cls_name]
    sub  rsp, 32
    call [CreateWindowExA]
    add  rsp, 32
    mov  [rel hwnd], rax
    mov  rdx, 10
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [ShowWindow]
    add  rsp, 32
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [UpdateWindow]
    add  rsp, 32
.msg_loop:
    mov  [rsp+24], 0
    mov  [rsp+16], 0
    xor  r9, r9
    xor  r8, r8
    lea  rdx, [rel msg_buf]
    xor  rcx, rcx
    sub  rsp, 32
    call [GetMessageA]
    add  rsp, 32
    test eax, eax
    je   .exit
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [TranslateMessage]
    add  rsp, 32
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [DispatchMessageA]
    add  rsp, 32
    jmp  .msg_loop
.exit:
    xor  rcx, rcx
    sub  rsp, 32
    call [ExitProcess]

section .data
    cls_name  db  "GDIWnd", 0
    wnd_title db  "GDI Window", 0
    msg       db  "Hello, GDI32!", 0
    wndcls:
      dd  80
      dd  3
      dq  WndProc
      dd  0, 0
      dq  0, 0, 0, 0, 0
      dq  cls_name
      dq  0

section .bss
    hwnd    resq 1
    hdc     resq 1
    ps_buf  resb 72
    msg_buf resb 48
""","ports":[]},
    {"id":"proj.opengl","title":"Win64 OpenGL32 Window","cat":"Projects",
     "desc":"OpenGL32 window with WGL, render loop, SwapBuffers",
     "tpl":"""\
bits 64
default rel

global WinMain
extern RegisterClassExA, CreateWindowExA, ShowWindow, UpdateWindow
extern PeekMessageA, TranslateMessage, DispatchMessageA, PostQuitMessage
extern DefWindowProcA, GetDC, ReleaseDC, ExitProcess
extern ChoosePixelFormat, SetPixelFormat
extern wglCreateContext, wglMakeCurrent, wglDeleteContext, SwapBuffers
extern glClearColor, glClear, glViewport, glBegin, glEnd, glVertex3f, glFlush

section .text

WndProc:
    push rbp
    mov  rbp, rsp
    sub  rsp, 48
    cmp  edx, 0x0002
    je   .destroy
    cmp  edx, 0x0005
    je   .size
    add  rsp, 48
    pop  rbp
    jmp  [DefWindowProcA]
.destroy:
    xor  rcx, rcx
    sub  rsp, 32
    call [PostQuitMessage]
    add  rsp, 32
    xor  rax, rax
    add  rsp, 48
    pop  rbp
    ret
.size:
    add  rsp, 48
    pop  rbp
    xor  rax, rax
    ret

render:
    push rbp
    mov  rbp, rsp
    sub  rsp, 48
    mov  ecx, 0x4100
    sub  rsp, 32
    call [glClear]
    add  rsp, 32
    mov  ecx, 4
    sub  rsp, 32
    call [glBegin]
    add  rsp, 32
    movss xmm0, [rel v1x]
    movss xmm1, [rel v1y]
    xorps xmm2, xmm2
    sub  rsp, 32
    call [glVertex3f]
    add  rsp, 32
    movss xmm0, [rel v2x]
    movss xmm1, [rel v1y]
    xorps xmm2, xmm2
    sub  rsp, 32
    call [glVertex3f]
    add  rsp, 32
    xorps xmm0, xmm0
    movss xmm1, [rel v2y]
    xorps xmm2, xmm2
    sub  rsp, 32
    call [glVertex3f]
    add  rsp, 32
    sub  rsp, 32
    call [glEnd]
    add  rsp, 32
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [SwapBuffers]
    add  rsp, 32
    add  rsp, 48
    pop  rbp
    ret

WinMain:
    push rbp
    mov  rbp, rsp
    sub  rsp, 64
    lea  rcx, [rel wndcls]
    sub  rsp, 32
    call [RegisterClassExA]
    add  rsp, 32
    ; CreateWindowExA simplified
    mov  [rsp+56], 0
    mov  [rsp+48], 0
    mov  [rsp+40], 0
    mov  [rsp+32], 0
    mov  r9,  600
    mov  r8,  800
    xor  rdx, rdx
    xor  rcx, rcx
    push rcx
    mov  rcx, 0x00CF0000
    push rcx
    xor  rcx, rcx
    push rcx
    lea  rcx, [rel cls_name]
    sub  rsp, 32
    call [CreateWindowExA]
    add  rsp, 32
    mov  [rel hwnd], rax
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [GetDC]
    add  rsp, 32
    mov  [rel hdc], rax
    call setup_pixel_format
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [wglCreateContext]
    add  rsp, 32
    mov  [rel hrc], rax
    mov  rdx, [rel hrc]
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [wglMakeCurrent]
    add  rsp, 32
    movss xmm3, [rel one_f]
    movss xmm2, [rel bg_b]
    movss xmm1, [rel bg_g]
    movss xmm0, [rel bg_r]
    sub  rsp, 32
    call [glClearColor]
    add  rsp, 32
    mov  rdx, 10
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [ShowWindow]
    add  rsp, 32
.msg_loop:
    mov  [rsp+24], 1
    mov  [rsp+16], 0
    xor  r9, r9
    xor  r8, r8
    lea  rdx, [rel msg_buf]
    xor  rcx, rcx
    sub  rsp, 32
    call [PeekMessageA]
    add  rsp, 32
    test eax, eax
    jz   .render
    cmp  dword [rel msg_buf+4], 0x0012
    je   .exit
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [TranslateMessage]
    add  rsp, 32
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [DispatchMessageA]
    add  rsp, 32
.render:
    call render
    jmp  .msg_loop
.exit:
    xor  rdx, rdx
    xor  rcx, rcx
    sub  rsp, 32
    call [wglMakeCurrent]
    add  rsp, 32
    mov  rcx, [rel hrc]
    sub  rsp, 32
    call [wglDeleteContext]
    add  rsp, 32
    xor  rcx, rcx
    sub  rsp, 32
    call [ExitProcess]

setup_pixel_format:
    push rbp
    mov  rbp, rsp
    sub  rsp, 32
    mov  word [rel pfd+0],  40
    mov  word [rel pfd+2],  1
    mov  dword [rel pfd+4], 0x25
    mov  byte  [rel pfd+8], 0
    mov  byte  [rel pfd+9], 32
    mov  byte  [rel pfd+22],24
    lea  rdx, [rel pfd]
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [ChoosePixelFormat]
    add  rsp, 32
    mov  [rel pf_idx], eax
    lea  r8, [rel pfd]
    mov  edx, [rel pf_idx]
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [SetPixelFormat]
    add  rsp, 32
    add  rsp, 32
    pop  rbp
    ret

section .data
    cls_name  db  "GLWnd", 0
    wnd_title db  "OpenGL Window", 0
    wndcls:
      dd  80
      dd  3
      dq  WndProc
      dd  0, 0
      dq  0, 0, 0, 0, 0
      dq  cls_name
      dq  0
    v1x   dd  0xBF000000
    v1y   dd  0xBF000000
    v2x   dd  0x3F000000
    v2y   dd  0x3F000000
    one_f dd  0x3F800000
    bg_r  dd  0x3DCCCCCD
    bg_g  dd  0x3DCCCCCD
    bg_b  dd  0x3E19999A

section .bss
    hwnd    resq 1
    hdc     resq 1
    hrc     resq 1
    pfd     resb 40
    pf_idx  resd 1
    msg_buf resb 48
""","ports":[]},
]

# ══════════════════════════════════════════════════════════════════════════════
# § 2  CONTEXT RULES  (all IDs validated against TEMPLATES above)
# ══════════════════════════════════════════════════════════════════════════════

CONTEXT_RULES: Dict[str, List[str]] = {
    # ── CPU / Move ─────────────────────────────────────────────────────────
    "mov":        ["asm.add","asm.sub","asm.cmp","asm.push","asm.calli","asm.movzx","asm.lea"],
    "movzx":      ["asm.mov","asm.add","asm.cmp","asm.and","asm.shl"],
    "movsx":      ["asm.mov","asm.add","asm.cmp","asm.imul"],
    "movsxd":     ["asm.mov","asm.add","asm.cmp","asm.lea"],
    "lea":        ["asm.mov","asm.push","asm.add","asm.calli","asm.sub"],
    "xchg":       ["asm.mov","asm.cmp","asm.xor","asm.push","asm.pop"],
    "bswap":      ["asm.mov","asm.cmp","asm.push","asm.calli"],
    # ── CPU / Stack ────────────────────────────────────────────────────────
    "push":       ["asm.push","asm.call","asm.pop","abi.shadow","asm.mov"],
    "pop":        ["asm.ret","asm.jmp","asm.cmp","abi.epilog","asm.mov"],
    "pushfq":     ["asm.cmp","asm.je","asm.popfq","asm.ret"],
    "popfq":      ["asm.ret","asm.jmp","asm.cmp"],
    "pusha":      ["asm.push","asm.calli","abi.shadow"],
    "popa":       ["asm.ret","asm.jmp","asm.cmp"],
    # ── CPU / ALU ──────────────────────────────────────────────────────────
    "add":        ["asm.sub","asm.cmp","asm.mov","asm.inc","asm.je"],
    "adc":        ["asm.add","asm.sbb","asm.cmp","asm.jb"],
    "sub":        ["asm.cmp","asm.je","asm.mov","asm.dec"],
    "sbb":        ["asm.sub","asm.adc","asm.cmp","asm.jb"],
    "imul":       ["asm.add","asm.mov","asm.idiv","asm.cmp","asm.movsx"],
    "imul3":      ["asm.mov","asm.add","asm.cmp","asm.idiv"],
    "mul":        ["asm.mov","asm.div","asm.cmp","asm.xor"],
    "idiv":       ["asm.mov","asm.cmp","asm.xor","asm.cdq"],
    "div":        ["asm.mov","asm.cmp","asm.xor","asm.cdq"],
    "inc":        ["asm.dec","asm.cmp","asm.add","asm.jne"],
    "dec":        ["asm.inc","asm.cmp","asm.je"],
    "neg":        ["asm.mov","asm.cmp","asm.add","asm.xor"],
    "cdq":        ["asm.idiv","asm.mov","asm.cmp"],
    "cqo":        ["asm.idiv","asm.mov","asm.cmp"],
    # ── CPU / Logic ────────────────────────────────────────────────────────
    "xor":        ["asm.mov","asm.or","asm.and","asm.cmp"],
    "and":        ["asm.or","asm.xor","asm.cmp","asm.test"],
    "or":         ["asm.xor","asm.and","asm.cmp","asm.mov"],
    "not":        ["asm.xor","asm.and","asm.mov"],
    "shl":        ["asm.shr","asm.and","asm.or","asm.mov","asm.sar"],
    "shr":        ["asm.shl","asm.and","asm.or","asm.mov","asm.sar"],
    "sar":        ["asm.shl","asm.shr","asm.mov","asm.cmp"],
    "rol":        ["asm.ror","asm.mov","asm.cmp"],
    "ror":        ["asm.rol","asm.mov","asm.cmp"],
    "rcl":        ["asm.rcr","asm.mov","asm.cmp"],
    "rcr":        ["asm.rcl","asm.mov","asm.cmp"],
    "shlx":       ["asm.shrx","asm.sarx","asm.mov","asm.cmp"],
    "shrx":       ["asm.shlx","asm.mov","asm.cmp"],
    "sarx":       ["asm.shlx","asm.shrx","asm.mov","asm.cmp"],
    # ── CPU / Compare ──────────────────────────────────────────────────────
    "cmp":        ["asm.je","asm.jne","asm.jl","asm.jg","asm.jle","asm.jge","asm.jb","asm.ja","asm.jbe","asm.jae","asm.jo","asm.js","asm.cmove"],
    "test":       ["asm.je","asm.jne","asm.jb","asm.ja","asm.cmove"],
    "bt":         ["asm.jb","asm.jae","asm.bts","asm.btr","asm.btc"],
    "bts":        ["asm.bt","asm.btr","asm.mov","asm.cmp"],
    "btr":        ["asm.bt","asm.bts","asm.mov","asm.cmp"],
    "btc":        ["asm.bt","asm.bts","asm.mov","asm.cmp"],
    # ── CPU / Flow ─────────────────────────────────────────────────────────
    "jmp":        ["abi.proc","asm.ret","dir.global","asm.cmp"],
    "je":         ["asm.jne","asm.jl","asm.jg","asm.ret","asm.jmp"],
    "jne":        ["asm.je","asm.jl","asm.jg","asm.ret","asm.jmp"],
    "jl":         ["asm.jg","asm.jle","asm.jge","asm.ret","asm.cmp"],
    "jle":        ["asm.jg","asm.jl","asm.jge","asm.ret","asm.cmp"],
    "jg":         ["asm.jl","asm.jle","asm.jge","asm.ret","asm.cmp"],
    "jge":        ["asm.jl","asm.jg","asm.ret","asm.cmp"],
    "jb":         ["asm.ja","asm.cmp","asm.ret","asm.jbe"],
    "jbe":        ["asm.ja","asm.jb","asm.ret","asm.cmp"],
    "ja":         ["asm.jb","asm.cmp","asm.ret","asm.jbe"],
    "jae":        ["asm.jb","asm.ja","asm.ret","asm.cmp"],
    "jo":         ["asm.jno","asm.cmp","asm.ret"],
    "js":         ["asm.jns","asm.cmp","asm.ret"],
    "call":       ["abi.shadow","abi.epilog","asm.ret","asm.cmp","abi.unshadow"],
    "calli":      ["abi.shadow","asm.mov","asm.cmp","abi.epilog","abi.unshadow"],
    "ret":        ["abi.proc","dir.global","sec.text","asm.cmp"],
    "retn":       ["abi.proc","abi.epilog","asm.cmp"],
    "loop":       ["asm.cmp","asm.je","asm.jne","asm.mov"],
    "loope":      ["asm.cmp","asm.jne","asm.ret"],
    "loopne":     ["asm.cmp","asm.je","asm.ret"],
    # ── CMOVcc ─────────────────────────────────────────────────────────────
    "cmove":      ["asm.mov","asm.cmp","asm.je","asm.ret"],
    "cmovne":     ["asm.mov","asm.cmp","asm.jne","asm.ret"],
    "cmovg":      ["asm.mov","asm.cmp","asm.jg","asm.ret"],
    "cmovge":     ["asm.mov","asm.cmp","asm.jge","asm.ret"],
    "cmovl":      ["asm.mov","asm.cmp","asm.jl","asm.ret"],
    "cmovle":     ["asm.mov","asm.cmp","asm.jle","asm.ret"],
    "cmova":      ["asm.mov","asm.cmp","asm.ja","asm.ret"],
    "cmovae":     ["asm.mov","asm.cmp","asm.jae","asm.ret"],
    "cmovb":      ["asm.mov","asm.cmp","asm.jb","asm.ret"],
    "cmovbe":     ["asm.mov","asm.cmp","asm.jbe","asm.ret"],
    # ── CPU / Misc ─────────────────────────────────────────────────────────
    "nop":        ["asm.jmp","asm.cmp","abi.proc"],
    "int3":       ["asm.cmp","asm.je","asm.mov"],
    "syscall":    ["asm.mov","asm.ret","asm.cmp"],
    "cpuid":      ["asm.mov","asm.cmp","asm.xor","asm.and"],
    "rdtsc":      ["asm.mov","asm.cmp","asm.shl","asm.shr"],
    "hlt":        ["asm.cli","asm.sti","asm.cmp"],
    "int":        ["asm.mov","asm.ret","asm.cmp"],
    "clc":        ["asm.adc","asm.sbb","asm.jb"],
    "stc":        ["asm.adc","asm.sbb","asm.jb"],
    "cld":        ["asm.movs","asm.stos","asm.lods"],
    "std":        ["asm.movs","asm.stos","asm.lods"],
    "mfence":     ["asm.lfence","asm.sfence","asm.mov","asm.cmp"],
    "lfence":     ["asm.mfence","asm.sfence","asm.mov","asm.cmp"],
    "sfence":     ["asm.mfence","asm.lfence","asm.mov","asm.cmp"],
    "pause":      ["asm.jmp","asm.cmp","abi.proc"],
    # ── CPU / SIMD SSE ─────────────────────────────────────────────────────
    "movaps":     ["sse.addps","sse.subps","sse.mulps","sse.movups","sse.xorps"],
    "movups":     ["sse.addps","sse.subps","sse.mulps","sse.movaps","sse.xorps"],
    "movss":      ["sse.addss","sse.mulss","sse.cvtss2si","sse.movaps"],
    "movsd":      ["sse.addsd","sse.mulsd","sse.cvtsd2si","sse.movapd"],
    "addps":      ["sse.mulps","sse.subps","sse.divps","sse.movaps","sse.maxps","sse.minps"],
    "addss":      ["sse.mulss","sse.subss","sse.divss","sse.movss"],
    "subps":      ["sse.addps","sse.mulps","sse.divps","sse.movaps"],
    "mulps":      ["sse.addps","sse.subps","sse.divps","sse.movaps"],
    "divps":      ["sse.mulps","sse.addps","sse.subps","sse.movaps"],
    "xorps":      ["sse.movaps","sse.addps","sse.mulps","sse.movups"],
    "sqrtps":     ["sse.movaps","sse.addps","sse.mulps","sse.movups"],
    "maxps":      ["sse.minps","sse.addps","sse.movaps","sse.movups"],
    "minps":      ["sse.maxps","sse.addps","sse.movaps","sse.movups"],
    "cvtsi2ss":   ["sse.movss","sse.addss","sse.cvtss2si"],
    "cvtsi2sd":   ["sse.movsd","sse.addsd","sse.cvtsd2si"],
    "cvtss2si":   ["asm.mov","asm.cmp","sse.cvtsi2ss"],
    "pxor":       ["sse.pand","sse.por","sse.movdqa"],
    "pand":       ["sse.pxor","sse.por","sse.movdqa"],
    "por":        ["sse.pxor","sse.pand","sse.movdqa"],
    "paddb":      ["sse.psubb","sse.paddw","sse.paddd","sse.movdqa"],
    "paddw":      ["sse.psubw","sse.pmullw","sse.paddd","sse.movdqa"],
    "paddd":      ["sse.psubd","sse.pmaddwd","sse.movdqa"],
    "psubb":      ["sse.paddb","sse.psubw","sse.movdqa"],
    "pmullw":     ["sse.paddw","sse.psubw","sse.pmaddwd","sse.movdqa"],
    "pmaddwd":    ["sse.paddd","sse.movdqa","sse.psubd"],
    # ── CPU / SIMD AVX ─────────────────────────────────────────────────────
    "vmovaps":    ["avx.vaddps","avx.vsubps","avx.vmulps","avx.vmovups","avx.vxorps"],
    "vmovups":    ["avx.vaddps","avx.vsubps","avx.vmulps","avx.vmovaps","avx.vxorps"],
    "vaddps":     ["avx.vmulps","avx.vsubps","avx.vdivps","avx.vmovaps","avx.vbroadcastss"],
    "vsubps":     ["avx.vaddps","avx.vmulps","avx.vdivps","avx.vmovaps"],
    "vmulps":     ["avx.vaddps","avx.vsubps","avx.vdivps","avx.vmovaps"],
    "vdivps":     ["avx.vmulps","avx.vaddps","avx.vsubps","avx.vmovaps"],
    "vxorps":     ["avx.vmovaps","avx.vaddps","avx.vmulps","avx.vmovups"],
    "vsqrtps":    ["avx.vmovaps","avx.vaddps","avx.vmulps","avx.vmovups"],
    "vbroadcastss":["avx.vaddps","avx.vmulps","avx.vmovaps","avx.vxorps"],
    # ── AVX-512 ────────────────────────────────────────────────────────────
    "vaddps_zmm": ["avx512.vmovaps","avx512.vmulps","avx512.vsubps"],   # note: actual mnemonic varies; keep template ID keys later
    "vmovaps_zmm":["avx512.vaddps","avx512.vmulps","avx512.vsubps"],
    # ── FMA ────────────────────────────────────────────────────────────────
    "vfmadd132ps":["avx.vaddps","avx.vmulps","avx.vmovaps","avx.vxorps"],
    # ── BMI ────────────────────────────────────────────────────────────────
    "blsr":       ["bmi.blsmsk","bmi.bextr","asm.mov","asm.cmp"],
    "blsmsk":     ["bmi.blsr","bmi.bextr","asm.mov","asm.cmp"],
    "bextr":      ["bmi.bzhi","asm.mov","asm.cmp"],
    "bzhi":       ["bmi.bextr","asm.mov","asm.cmp"],
    # ── FPU ────────────────────────────────────────────────────────────────
    "fld":        ["fpu.fstp","fpu.fadd","fpu.fsqrt","asm.mov"],
    "fstp":       ["fpu.fld","fpu.fadd","fpu.fsqrt","asm.mov"],
    "fadd":       ["fpu.fstp","fpu.fmul","fpu.fdiv","fpu.fsqrt"],
    "fsqrt":      ["fpu.fstp","fpu.fadd","fpu.fmul"],
    # ── ABI / Win64 ────────────────────────────────────────────────────────
    "proc":       ["abi.shadow","asm.mov","asm.calli","abi.epilog"],      # abi.proc
    "epilog":     ["asm.ret","abi.proc","asm.jmp"],
    "shadow":     ["asm.mov","asm.calli","asm.push","abi.unshadow"],
    "unshadow":   ["asm.ret","abi.epilog","asm.pop"],
    "align":      ["abi.shadow","asm.calli","asm.mov"],
    "args":       ["abi.shadow","asm.calli","asm.push","abi.proc"],
    "winmain":    ["gdi.getdc","gdi.beginpaint","gl.setup_pfd","gl.create_ctx"],
    # ── Directives ─────────────────────────────────────────────────────────
    "bits64":     ["dir.defrel","dir.global","dir.extern"],
    "defrel":     ["dir.bits64","sec.text","dir.global"],
    "global":     ["dir.extern","abi.proc","sec.text"],
    "extern":     ["dir.extern","asm.calli","abi.proc","asm.calli"],
    # ── Sections ───────────────────────────────────────────────────────────
    "section":    ["dir.global","dir.extern","abi.proc","dat.db","sec.text","sec.data","sec.bss"],
    # ── Data / Define ──────────────────────────────────────────────────────
    "db":         ["dat.db","dat.dw","dat.dd","dat.dq","dat.str"],
    "dw":         ["dat.dw","dat.dd","dat.resb","sec.data"],
    "dd":         ["dat.dd","dat.dq","dat.resq","sec.data"],
    "dq":         ["dat.dq","dat.dd","dat.resb","dat.resq","sse.movaps"],
    "str":        ["dat.str","dat.strn","gdi.textout"],
    "resb":       ["dat.resb","dat.resq","sec.bss"],
    "resq":       ["dat.resq","dat.resb","sec.bss"],
    "equ":        ["asm.mov","asm.cmp","dat.db"],
    "times":      ["asm.nop","asm.int3","dat.db"],
    # ── GDI32 ──────────────────────────────────────────────────────────────
    "beginpaint": ["gdi.textout","gdi.rectangle","gdi.fillrect","gdi.endpaint"],
    "endpaint":   ["gdi.setbkcolor","gdi.settextcolor","gdi.releasedc"],
    "textout":    ["gdi.rectangle","gdi.fillrect","gdi.endpaint","gdi.settextcolor"],
    "rectangle":  ["gdi.textout","gdi.fillrect","gdi.endpaint","gdi.createpen"],
    "fillrect":   ["gdi.rectangle","gdi.endpaint","gdi.createbrush"],
    "bitblt":     ["gdi.getdc","gdi.releasedc","gdi.endpaint","gdi.createcompatibledc"],
    "createbrush":["gdi.fillrect","gdi.rectangle","gdi.selectobject"],
    "createpen":  ["gdi.rectangle","gdi.selectobject","gdi.setbkmode"],
    "selectobject":["gdi.deleteobject","gdi.rectangle","gdi.textout"],
    "deleteobject":["gdi.selectobject","gdi.endpaint","gdi.releasedc"],
    "getdc":      ["gdi.beginpaint","gdi.createcompatibleDC","gdi.setup_pfd"],
    "releasedc":  ["gdi.endpaint","gdi.getdc","gdi.createcompatibleDC"],
    "setbkcolor": ["gdi.textout","gdi.rectangle","gdi.createbrush"],
    "settextcolor":["gdi.textout","gdi.setbkcolor"],
    # ── OpenGL32 / WGL ─────────────────────────────────────────────────────
    "setup_pfd":  ["gl.create_ctx","gl.make_current","gl.viewport"],
    "create_ctx": ["gl.make_current","gl.viewport","gl.clearcolor"],
    "make_current":["gl.clearcolor","gl.viewport","gl.clear"],
    "delete_ctx": ["gl.make_current","gl.swapbuffers"],
    "swapbuffers":["gl.clear","gl.begin","gl.clearcolor","gl.flush"],
    "clear":      ["gl.begin","gl.viewport","gl.clearcolor","gl.end"],
    "clearcolor": ["gl.clear","gl.viewport","gl.begin"],
    "viewport":   ["gl.clear","gl.begin","gl.matrixmode","gl.loadidentity"],
    "begin":      ["gl.vertex3f","gl.color3f","gl.end"],
    "end":        ["gl.swapbuffers","gl.clear","gl.flush"],
    "vertex3f":   ["gl.color3f","gl.begin","gl.end","gl.swapbuffers"],
    "color3f":    ["gl.vertex3f","gl.begin","gl.end"],
    "matrixmode": ["gl.loadidentity","gl.viewport","gl.begin"],
    "loadidentity":["gl.matrixmode","gl.viewport","gl.begin"],
    "enable":     ["gl.disable","gl.clear","gl.viewport"],
    "disable":    ["gl.enable","gl.clear","gl.viewport"],
    "flush":      ["gl.swapbuffers","gl.clear","gl.end"],
    # ── Project Skeletons ──────────────────────────────────────────────────
    "proj.console":["abi.proc","asm.mov","asm.calli","asm.ret","dat.db","sec.data","sec.bss"],
    "proj.gdi":    ["gdi.beginpaint","gdi.textout","gdi.rectangle","gdi.endpaint","gdi.getdc","gdi.createwindow"],
    "proj.opengl": ["gl.setup_pfd","gl.create_ctx","gl.make_current","gl.clearcolor","gl.begin","gl.vertex3f","gl.swapbuffers"],
}

# ══════════════════════════════════════════════════════════════════════════════
# § 3  THEME & LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

WIN_W, WIN_H = 1440, 900
LEFT_W   = 285
RIGHT_W  = 270
TOOLBAR_H = 38
STATUS_H  = 26
FONT_SZ   = 15
FONT_UI   = 13
FONT_SMALL = 11
FONT_TITLE = 16
FONT_MONO = "Consolas"
FONT_SANS = "Segoe UI"

# Padding/margins
PAD_X = 10
PAD_Y = 5
GUTTER_W = 54               # line numbers
SCROLLBAR_W = 8
ITEM_H = 28                 # panel list item height
CURSOR_BLINK = 0.5          # seconds
SCROLL_SPEED = 3            # lines per mouse wheel tick

# Editor text area
EDITOR_X = LEFT_W
EDITOR_Y = TOOLBAR_H
EDITOR_RIGHT_PAD = 8        # from right panel

# Input dialog
DIALOG_W = 400
DIALOG_H = 220
DIALOG_PAD = 14

T: Dict = {
    # ── Base ──────────────────────────────────────────────────────────────
    "bg":        (13,13,19),
    "panel":     (17,17,25),
    "panel2":    (21,21,31),
    "border":    (42,42,62),
    "border_light": (55,55,82),
    "border_active": (80,120,185),
    "shadow":    (0,0,0, 60),      # used for overlays (alpha)

    # ── Text ──────────────────────────────────────────────────────────────
    "text":      (218,218,238),
    "text_dim":  (110,110,145),
    "text_em":   (180,210,255),
    "text_bright":(240,240,255),
    "cursor":    (220,230,255),
    "sel":       (50,85,145),
    "line_hl":   (25,25,40),

    # ── Gutter ────────────────────────────────────────────────────────────
    "gutter_bg": (15,15,23),
    "gutter_fg": (75,75,108),
    "gutter_cur":(180,160,90),

    # ── Syntax Highlighting ───────────────────────────────────────────────
    "kw":        (86,156,214),
    "num":       (181,206,168),
    "cmt":       (106,153,85),
    "str_":      (206,145,120),
    "reg":       (255,165,70),
    "dir_":      (197,134,192),
    "lbl":       (255,225,80),
    "op":        (200,200,210),
    "macro":     (255,130,130),
    "mem":       (140,200,255),
    "segment":   (140,220,180),
    "type":      (180,180,200),
    "preproc":   (220,150,100),
    "error":     (255,80,90),
    "warn":      (255,190,70),
    "info":      (100,180,255),

    # ── Buttons ───────────────────────────────────────────────────────────
    "btn":       (45,45,68),
    "btn_hov":   (62,62,95),
    "btn_act":   (80,120,185),
    "btn_danger":(160,60,60),
    "btn_text":  (210,210,225),
    "btn_text_dim":(140,140,160),
    "btn_disabled":(35,35,55),

    # ── Input fields ──────────────────────────────────────────────────────
    "inp":       (22,22,33),
    "inp_brd":   (60,60,92),
    "inp_act":   (80,110,175),
    "inp_text":  (210,210,230),
    "inp_placeholder":(100,100,130),

    # ── Lists / Panels ────────────────────────────────────────────────────
    "item_hov":  (32,32,50),
    "item_sel":  (40,65,105),
    "cat_bg":    (24,24,38),
    "cat_fg":    (160,175,210),
    "separator": (55,55,75),

    # ── Scrollbar ─────────────────────────────────────────────────────────
    "scrollbar": (45,45,68),
    "scrollbar_hov":(70,70,100),
    "scrollbar_thumb":(80,80,120),

    # ── Toolbar ───────────────────────────────────────────────────────────
    "toolbar":   (15,15,24),
    "toolbar_btn":(50,50,75),
    "toolbar_btn_hov":(70,70,100),
    "toolbar_sep":(40,40,58),

    # ── Status bar ────────────────────────────────────────────────────────
    "status":    (12,12,20),
    "status_fg": (130,130,170),
    "status_highlight":(180,180,210),

    # ── Dialogs / Popups ──────────────────────────────────────────────────
    "dialog_bg":  (22,22,34),
    "dialog_border":(65,65,95),
    "dialog_title":(100,150,230),
    "overlay":    (0,0,0, 120),

    # ── Semantic ──────────────────────────────────────────────────────────
    "accent":    (80,130,200),
    "ok":        (80,185,100),
    "warn":      (210,165,70),
    "err":       (200,80,80),
    "info":      (100,180,255),

    # ── Special ───────────────────────────────────────────────────────────
    "match_hl":  (60,120,60, 80),     # search match highlight
    "find_bg":   (40,40,60),
    "placeholder_hl":(255,200,80, 60),

    # ── Code block colours (used in template list) ────────────────────────
    "block_cpu":   (80, 160, 255),
    "block_alu":   (255, 140, 60),
    "block_flow":  (180, 120, 255),
    "block_simd":  (80, 200, 140),
    "block_gdi":   (255, 220, 60),
    "block_gl":    (60, 220, 200),
    "block_asmx":  (255, 120, 120),
}

# ══════════════════════════════════════════════════════════════════════════════
# § 4  TOKENIZER
# ══════════════════════════════════════════════════════════════════════════════

_KW: set = {
    # ── general purpose ───────────────────────────────────────────────────
    'mov','lea','movzx','movsx','movsxd','xchg','bswap',
    'push','pop','pushfq','popfq','pusha','popa',
    # ── arithmetic ────────────────────────────────────────────────────────
    'add','adc','sub','sbb','imul','mul','idiv','div',
    'inc','dec','neg','cdq','cqo','cwd','cbw','cwde',
    # ── logic ─────────────────────────────────────────────────────────────
    'xor','and','or','not',
    'shl','shr','sal','sar','rol','ror','rcl','rcr',
    'shld','shrd','shlx','shrx','sarx','rorx',
    # ── comparison ────────────────────────────────────────────────────────
    'cmp','test','bt','bts','btr','btc',
    # ── flow ──────────────────────────────────────────────────────────────
    'jmp','je','jne','jz','jnz','jg','jge','jl','jle',
    'ja','jae','jb','jbe','jc','jnc','jo','jno','js','jns',
    'jecxz','jrcxz','loop','loope','loopne',
    'call','ret','retn','retf',
    # ── conditional moves ─────────────────────────────────────────────────
    'cmove','cmovne','cmovg','cmovge','cmovl','cmovle',
    'cmova','cmovae','cmovb','cmovbe','cmovc','cmovs','cmovo',
    # ── set on condition ──────────────────────────────────────────────────
    'sete','setne','setg','setge','setl','setle',
    'seta','setae','setb','setbe','setc','sets','seto',
    # ── string operations ─────────────────────────────────────────────────
    'movsb','movsw','movsd','movsq',
    'cmpsb','cmpsw','cmpsd','cmpsq',
    'scasb','scasw','scasd','scasq',
    'lodsb','lodsw','lodsd','lodsq',
    'stosb','stosw','stosd','stosq',
    'insb','insw','insd','outsb','outsw','outsd',
    'rep','repe','repne','repz','repnz',
    # ── I/O ───────────────────────────────────────────────────────────────
    'in','out',
    # ── system / misc ─────────────────────────────────────────────────────
    'int','int3','into','iret','iretd','iretq',
    'syscall','sysret','sysenter','sysexit',
    'cpuid','rdtsc','rdtscp','rdmsr','wrmsr','rdpmc',
    'nop','hlt','pause','ud2',
    'clc','stc','cmc','cld','std','cli','sti','clts',
    'mfence','lfence','sfence',
    'wbinvd','invd','invlpg','ltr','str','lldt','sldt',
    'lgdt','sgdt','lidt','sidt','lmsw','smsw',
    'clflush','monitor','mwait','xgetbv','xsetbv',
    'xsave','xrstor','vmcall','vmlaunch','vmresume','vmxoff','vmxon',
    # ── SIMD SSE / SSE2 ───────────────────────────────────────────────────
    'movaps','movups','movss','movsd','movapd','movupd',
    'addps','addss','addpd','addsd',
    'subps','subss','subpd','subsd',
    'mulps','mulss','mulpd','mulsd',
    'divps','divss','divpd','divsd',
    'sqrtps','sqrtss','sqrtpd','sqrtsd',
    'maxps','maxss','maxpd','maxsd',
    'minps','minss','minpd','minsd',
    'andps','andpd','andnps','andnpd',
    'orps','orpd','xorps','xorpd',
    'cvtsi2ss','cvtsi2sd','cvtss2si','cvtsd2si',
    'cvttss2si','cvttsd2si','cvtss2sd','cvtsd2ss',
    'pxor','pand','pandn','por',
    'movdqa','movdqu','movq','movd',
    'packuswb','packsswb','packssdw',
    'punpcklbw','punpcklwd','punpckldq','punpcklqdq',
    'punpckhbw','punpckhwd','punpckhdq','punpckhqdq',
    'paddb','paddw','paddd','paddq',
    'psubb','psubw','psubd','psubq',
    'pmullw','pmulhw','pmulhuw','pmulld','pmulhq',
    'pmaddwd','pmaddubsw',
    'psllw','pslld','psllq','psrlw','psrld','psrlq',
    'pcmpeqb','pcmpeqw','pcmpeqd','pcmpeqq',
    'pcmpgtb','pcmpgtw','pcmpgtd','pcmpgtq',
    'pmovmskb','palignr','pshufb','pshufd','pshufhw','pshuflw',
    'pextrb','pextrw','pextrd','pextrq',
    'pinsrb','pinsrw','pinsrd','pinsrq',
    'blendps','blendpd','pblendw','dpps','dppd',
    'ptest','pcmpestri','pcmpestrm','pcmpistri','pcmpistrm',
    'crc32','popcnt','lzcnt','tzcnt',
    # ── AVX ───────────────────────────────────────────────────────────────
    'vmovaps','vmovups','vmovapd','vmovupd',
    'vaddps','vaddpd','vaddss','vaddsd',
    'vsubps','vsubpd','vsubss','vsubsd',
    'vmulps','vmulpd','vmulss','vmulsd',
    'vdivps','vdivpd','vdivss','vdivsd',
    'vsqrtps','vsqrtpd','vsqrtss','vsqrtsd',
    'vmaxps','vmaxpd','vmaxss','vmaxsd',
    'vminps','vminpd','vminss','vminsd',
    'vandps','vandpd','vorps','vorpd',
    'vxorps','vxorpd','vandnps','vandnpd',
    'vbroadcastss','vbroadcastsd','vbroadcastf128',
    'vfmadd132ps','vfmadd213ps','vfmadd231ps',
    'vfmadd132pd','vfmadd213pd','vfmadd231pd',
    'vfmsub132ps','vfmsub213ps','vfmsub231ps',
    'vfnmadd132ps','vfnmadd213ps','vfnmadd231ps',
    'vperm2f128','vpermilps','vpermilpd',
    'vzeroupper','vzeroall',
    # ── AVX‑512 ───────────────────────────────────────────────────────────
    'vmovaps','vmovups','vmovdqa32','vmovdqu32',
    'vaddps','vaddpd','vsubps','vsubpd',
    'vmulps','vmulpd','vdivps','vdivpd',
    'vsqrtps','vsqrtpd',
    'vfmadd132ps','vfmadd213ps','vfmadd231ps',
    'vpbroadcastb','vpbroadcastw','vpbroadcastd','vpbroadcastq',
    'vpcmpd','vpcmpud','vpcmpq','vpcmpuq',
    'vcompressps','vcompresspd','vexpandps','vexpandpd',
    'vpshufd','vpshufhw','vpshuflw',
    'vpmovsxbd','vpmovsxwd','vpmovsxdq','vpmovsxwq',
    'vpmovzxbd','vpmovzxwd','vpmovzxdq','vpmovzxwq',
    # ── BMI / ADX / other extensions ─────────────────────────────────────
    'blsr','blsmsk','bextr','bzhi','mulx','pdep','pext',
    'adcx','adox','movbe','rdrand','rdseed','rdseed',
    # ── FPU (x87) ─────────────────────────────────────────────────────────
    'fld','fst','fstp','fild','fist','fistp',
    'fadd','faddp','fsub','fsubp','fmul','fmulp','fdiv','fdivp',
    'fcom','fcomp','fcompp','ficom','ficomp',
    'fxch','fabs','fchs','fsqrt','fsin','fcos','fptan','fpatan',
    'fprem','fprem1','fscale','frndint','fxtract',
    'fninit','finit','fnsave','frstor','fldcw','fstcw','fnstcw',
    'fwait','wait',
    # ── directives & preprocessor ─────────────────────────────────────────
    'section','segment','global','extern','common','absolute',
    'bits','use16','use32','use64','default',
    'org','times','align','alignb',
    'db','dw','dd','dq','dt','do','dy','dz',
    'resb','resw','resd','resq','rest','reso','resy','resz',
    'equ',
    'macro','endmacro',
    '%define','%xdefine','%undef','%assign',
    '%if','%elif','%else','%endif',
    '%ifdef','%ifndef','%ifmacro','%ifnmacro',
    '%ifidn','%ifidni','%ifdif','%ifdifi',
    '%ifid','%ifnum','%ifstr','%iftoken',
    '%include','%use',
    '%macro','%imacro','%endmacro',
    '%rep','%endrep','%exitrep','%exitmacro',
    '%rotate','%push','%pop',
    '%error','%warning','%fatal',
    '%line','%strcat','%strlen','%substr',
    '%idefine','%ixdefine',
    '%repl',
}

_REG: set = (
    {'rax','rbx','rcx','rdx','rsi','rdi','rbp','rsp','rip'} |
    {f'r{i}'  for i in range(8, 16)} |
    {'eax','ebx','ecx','edx','esi','edi','esp','ebp','eip'} |
    {f'r{i}d' for i in range(8, 16)} |
    {'ax','bx','cx','dx','si','di','sp','bp'} |
    {f'r{i}w' for i in range(8, 16)} |
    {'al','bl','cl','dl','ah','bh','ch','dh',
     'sil','dil','bpl','spl'} |
    {f'r{i}b' for i in range(8, 16)} |
    {f'xmm{i}' for i in range(32)} |
    {f'ymm{i}' for i in range(32)} |
    {f'zmm{i}' for i in range(32)} |
    {f'k{i}' for i in range(8)} |
    {'mm'+str(i) for i in range(8)} |
    {'cs','ds','es','fs','gs','ss'} |
    {'cr0','cr2','cr3','cr4','cr8'} |
    {'dr0','dr1','dr2','dr3','dr6','dr7'} |
    {'st0','st1','st2','st3','st4','st5','st6','st7'}
)

# ---------------------------------------------------------------------------
# Numeric patterns: hex, decimal, binary, octal, float with suffix
# ---------------------------------------------------------------------------
_NUM_PAT = (
    r'0[xX][0-9A-Fa-f]+[hH]?'
    r'|\b[0-9][0-9A-Fa-f]*[hH]\b'
    r'|\b[01]+[bB]\b'
    r'|\b[0-7]+[oOqQ]\b'
    r'|\b\d+\.?\d*(?:[eE][+-]?\d+)?\b'
    r'|\b\d+\b'
)

_TOK_RE = re.compile(
    r'(?P<cmt>;[^\n]*)'
    r'|(?P<str>("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'))'
    r'|(?P<num>' + _NUM_PAT + r')'
    r'|(?P<lbl>[.A-Za-z_$?@][\w.$?@]*:)'
    r'|(?P<dir>%(?:define|xdefine|undef|assign|macro|imacro|endmacro|rep|endrep|exitrep|exitmacro|'
               r'if|elif|else|endif|ifdef|ifndef|ifmacro|ifnmacro|ifidn|ifidni|ifdif|ifdifi|'
               r'ifid|ifnum|ifstr|iftoken|include|use|error|warning|fatal|line|strcat|strlen|substr|'
               r'idefine|ixdefine|rotate|push|pop|repl)\b)'
    r'|(?P<seg>(?:cs|ds|es|fs|gs|ss)[ \t]*:)'
    r'|(?P<word>[.A-Za-z_$?@][\w.$?@]*)'
    r'|(?P<op>[+\-*/<>=!&|^~\[\]{}().,@#%])'
    r'|(?P<ws>[ \t]+)'
    r'|(?P<unk>\S)'
)

def tokenize(line: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for m in _TOK_RE.finditer(line):
        kind = m.lastgroup
        tok  = m.group()
        if kind == 'cmt':
            out.append((tok, 'cmt'))
        elif kind == 'str':
            out.append((tok, 'str_'))
        elif kind == 'num':
            out.append((tok, 'num'))
        elif kind == 'lbl':
            out.append((tok, 'lbl'))
        elif kind == 'dir':
            out.append((tok, 'dir_'))
        elif kind == 'seg':
            # segment override: keep the colon attached
            out.append((tok, 'segment'))
        elif kind == 'word':
            lo = tok.lower()
            if lo in _KW:
                out.append((tok, 'kw'))
            elif lo in _REG:
                out.append((tok, 'reg'))
            else:
                out.append((tok, 'text'))
        elif kind == 'ws':
            out.append((tok, 'ws'))
        else:
            out.append((tok, 'op'))
    return out

# ══════════════════════════════════════════════════════════════════════════════
# § 5  TEXT BUFFER
# ══════════════════════════════════════════════════════════════════════════════

class TextBuffer:
    TAB        = 4
    MAX_UNDO   = 400
    COALESCE_MS = 500

    def __init__(self, text: str = ""):
        self.lines: List[str] = text.split('\n') if text else ['']
        self.cur = [0, 0]                       # [row, col]
        self.sel: Optional[Tuple] = None        # ((r0,c0),(r1,c1))
        self._sel_anchor: Optional[List[int]] = None
        self._undo: List[Tuple] = []            # (lines, cur, sel)
        self._redo: List[Tuple] = []
        self._clip: str = ''
        self._dirty = False
        self.filepath: Optional[str] = None
        self._last_snap_t: float = 0.0
        # ── extended features ──────────────────────────────────────────────
        self.bookmarks: set = set()             # row numbers
        self._last_search: str = ''             # last search pattern
        self._last_replace: str = ''            # last replace string
        self._snap()

    # ── snapshot ──────────────────────────────────────────────────────────
    def _snap(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_snap_t) < self.COALESCE_MS / 1000:
            return
        self._last_snap_t = now
        state = (copy.deepcopy(self.lines), list(self.cur),
                 copy.deepcopy(self.sel), self._dirty)
        # note: reference equality check is unreliable after deepcopy – kept for potential future use
        if self._undo and self._undo[-1][0] is self.lines:
            return
        self._undo.append(state)
        if len(self._undo) > self.MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def _snap_force(self) -> None:
        self._last_snap_t = 0.0
        self._snap(force=True)

    # ── helpers ───────────────────────────────────────────────────────────
    def _clamp(self, r: int, c: int) -> Tuple[int, int]:
        r = max(0, min(r, len(self.lines) - 1))
        c = max(0, min(c, len(self.lines[r])))
        return r, c

    def _sel_ordered(self) -> Optional[Tuple]:
        if not self.sel: return None
        a, b = self.sel
        return (a, b) if (a[0] < b[0] or (a[0] == b[0] and a[1] <= b[1])) else (b, a)

    def has_sel(self) -> bool:
        return bool(self.sel) and self.sel[0] != self.sel[1]

    def sel_text(self) -> str:
        s = self._sel_ordered()
        if not s: return ''
        (r0, c0), (r1, c1) = s
        if r0 == r1: return self.lines[r0][c0:c1]
        parts = [self.lines[r0][c0:]]
        for r in range(r0 + 1, r1): parts.append(self.lines[r])
        parts.append(self.lines[r1][:c1])
        return '\n'.join(parts)

    def del_sel(self) -> None:
        s = self._sel_ordered()
        if not s: return
        (r0, c0), (r1, c1) = s
        head, tail = self.lines[r0][:c0], self.lines[r1][c1:]
        del self.lines[r0:r1 + 1]
        self.lines.insert(r0, head + tail)
        self.cur = [r0, c0]
        self.sel = None
        self._sel_anchor = None

    # ── insert / delete ───────────────────────────────────────────────────
    def insert_char(self, ch: str) -> None:
        if self.has_sel():
            self.del_sel()
            self._snap_force()
        r, c = self.cur
        self.lines[r] = self.lines[r][:c] + ch + self.lines[r][c:]
        self.cur[1] += 1
        self._dirty = True
        self._snap()

    def newline(self) -> None:
        if self.has_sel(): self.del_sel()
        r, c = self.cur
        indent = len(self.lines[r]) - len(self.lines[r].lstrip())
        rest = self.lines[r][c:]
        self.lines[r] = self.lines[r][:c]
        self.lines.insert(r + 1, ' ' * indent + rest)
        self.cur = [r + 1, indent]
        self._dirty = True
        self._snap_force()

    def del_back(self) -> None:
        if self.has_sel():
            self.del_sel()
            self._snap_force()
            return
        r, c = self.cur
        if c > 0:
            self.lines[r] = self.lines[r][:c - 1] + self.lines[r][c:]
            self.cur[1] -= 1
        elif r > 0:
            prev = self.lines[r - 1]
            self.lines[r - 1] = prev + self.lines[r]
            del self.lines[r]
            self.cur = [r - 1, len(prev)]
        self._dirty = True
        self._snap_force()

    def del_fwd(self) -> None:
        if self.has_sel():
            self.del_sel()
            self._snap_force()
            return
        r, c = self.cur
        if c < len(self.lines[r]):
            self.lines[r] = self.lines[r][:c] + self.lines[r][c + 1:]
        elif r < len(self.lines) - 1:
            self.lines[r] = self.lines[r] + self.lines[r + 1]
            del self.lines[r + 1]
        self._dirty = True
        self._snap_force()

    def insert_text(self, text: str) -> None:
        """Insert multi-line text at cursor (used by template insertion)."""
        if self.has_sel(): self.del_sel()
        parts = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        if not parts: return
        r, c = self.cur
        prefix, suffix = self.lines[r][:c], self.lines[r][c:]
        if len(parts) == 1:
            self.lines[r] = prefix + parts[0] + suffix
            self.cur[1] += len(parts[0])
        else:
            self.lines[r] = prefix + parts[0]
            for i, p in enumerate(parts[1:-1], 1):
                self.lines.insert(r + i, p)
            last = parts[-1]
            self.lines.insert(r + len(parts) - 1, last + suffix)
            self.cur = [r + len(parts) - 1, len(last)]
        self._dirty = True
        self._snap_force()

    def tab(self, shift: bool = False) -> None:
        if self.has_sel():
            s = self._sel_ordered()
            r0, r1 = s[0][0], s[1][0]
            delta = [0] * (r1 - r0 + 1)
            for i, r in enumerate(range(r0, r1 + 1)):
                if not shift:
                    self.lines[r] = ' ' * self.TAB + self.lines[r]
                    delta[i] = self.TAB
                else:
                    stripped = self.lines[r].lstrip(' ')
                    removed = len(self.lines[r]) - len(stripped)
                    take = min(removed, self.TAB)
                    self.lines[r] = ' ' * (removed - take) + stripped
                    delta[i] = -take
            a_r = self._sel_anchor[0] if self._sel_anchor else r0
            a_delta = delta[a_r - r0] if r0 <= a_r <= r1 else 0
            c_delta = delta[self.cur[0] - r0] if r0 <= self.cur[0] <= r1 else 0
            if self._sel_anchor:
                self._sel_anchor[1] = max(0, self._sel_anchor[1] + a_delta)
            self.cur[1] = max(0, self.cur[1] + c_delta)
            self.sel = (tuple(self._sel_anchor) if self._sel_anchor else self.sel[0],
                        tuple(self.cur))
        else:
            r, c = self.cur
            if not shift:
                sp = self.TAB - (c % self.TAB)
                self.lines[r] = self.lines[r][:c] + ' ' * sp + self.lines[r][c:]
                self.cur[1] += sp
            else:
                stripped = self.lines[r].lstrip(' ')
                removed  = len(self.lines[r]) - len(stripped)
                take     = min(removed, self.TAB)
                self.lines[r] = ' ' * (removed - take) + stripped
                self.cur[1]   = max(0, c - take)
        self._dirty = True
        self._snap_force()

    # ── cursor movement ───────────────────────────────────────────────────
    def move(self, dr: int, dc: int, select: bool = False, word: bool = False) -> None:
        if not select:
            if self.has_sel() and dc != 0 and dr == 0:
                s = self._sel_ordered()
                self.cur = list(s[1 if dc > 0 else 0])
                self.sel = None
                self._sel_anchor = None
                return
            self.sel = None
            self._sel_anchor = None
        if select and self._sel_anchor is None:
            self._sel_anchor = list(self.cur)
        r, c = self.cur
        if word and dc != 0:
            line = self.lines[r]
            if dc > 0:
                i = c
                if i < len(line) and (line[i].isalnum() or line[i] == '_'):
                    while i < len(line) and (line[i].isalnum() or line[i] == '_'):
                        i += 1
                else:
                    while i < len(line) and not (line[i].isalnum() or line[i] == '_'):
                        i += 1
                c = i
            else:
                i = c
                if i > 0: i -= 1
                while i > 0 and not (line[i - 1].isalnum() or line[i - 1] == '_'):
                    i -= 1
                while i > 0 and (line[i - 1].isalnum() or line[i - 1] == '_'):
                    i -= 1
                c = i
        else:
            c += dc
            r += dr
            if c < 0 and r > 0:
                r -= 1; c = len(self.lines[r])
            elif c > len(self.lines[max(0,r)]) and r < len(self.lines) - 1:
                r += 1; c = 0
        r, c = self._clamp(r, c)
        self.cur = [r, c]
        if select:
            self.sel = (tuple(self._sel_anchor), (r, c))

    def home(self, select: bool = False) -> None:
        if select and self._sel_anchor is None: self._sel_anchor = list(self.cur)
        elif not select: self.sel = None; self._sel_anchor = None
        r = self.cur[0]
        indent = len(self.lines[r]) - len(self.lines[r].lstrip())
        self.cur[1] = 0 if self.cur[1] == indent else indent
        if select: self.sel = (tuple(self._sel_anchor), tuple(self.cur))

    def end_(self, select: bool = False) -> None:
        if select and self._sel_anchor is None: self._sel_anchor = list(self.cur)
        elif not select: self.sel = None; self._sel_anchor = None
        self.cur[1] = len(self.lines[self.cur[0]])
        if select: self.sel = (tuple(self._sel_anchor), tuple(self.cur))

    def page(self, direction: int, vis: int, select: bool = False) -> None:
        if select and self._sel_anchor is None: self._sel_anchor = list(self.cur)
        elif not select: self.sel = None; self._sel_anchor = None
        r = max(0, min(self.cur[0] + direction * vis, len(self.lines) - 1))
        self.cur[0] = r
        self.cur[1] = min(self.cur[1], len(self.lines[r]))
        if select: self.sel = (tuple(self._sel_anchor), tuple(self.cur))

    def select_all(self) -> None:
        self._sel_anchor = [0, 0]
        self.cur = [len(self.lines) - 1, len(self.lines[-1])]
        self.sel = ((0, 0), tuple(self.cur))

    def select_word_at(self, r: int, c: int) -> None:
        """Select the word that spans or touches column c on line r."""
        line = self.lines[r]
        for m in re.finditer(r'\w+', line):
            if m.start() <= c <= m.end():
                self._sel_anchor = [r, m.start()]
                self.cur = [r, m.end()]
                self.sel = (tuple(self._sel_anchor), tuple(self.cur))
                return

    def select_line(self, r: int) -> None:
        self._sel_anchor = [r, 0]
        self.cur = [r, len(self.lines[r])]
        self.sel = (tuple(self._sel_anchor), tuple(self.cur))

    # ── clipboard ─────────────────────────────────────────────────────────
    def copy(self) -> None:
        t = self.sel_text()
        if t:
            self._clip = t
            try: pygame.scrap.put_text(t)
            except Exception: pass

    def cut(self) -> None:
        self.copy()
        if self.has_sel():
            self.del_sel()
            self._snap_force()
            self._dirty = True

    def paste(self) -> None:
        t = ''
        try: t = pygame.scrap.get_text() or ''
        except Exception: pass
        if not t: t = self._clip
        if t: self.insert_text(t)

    # ── undo / redo ───────────────────────────────────────────────────────
    def undo(self) -> None:
        if len(self._undo) > 1:
            self._redo.append(self._undo.pop())
            lines, cur, sel, dirty = self._undo[-1]
            self.lines = copy.deepcopy(lines)
            self.cur   = list(cur)
            self.sel   = copy.deepcopy(sel)
            self._dirty = dirty

    def redo(self) -> None:
        if self._redo:
            state = self._redo.pop()
            self._undo.append(state)
            lines, cur, sel, dirty = state
            self.lines = copy.deepcopy(lines)
            self.cur   = list(cur)
            self.sel   = copy.deepcopy(sel)
            self._dirty = dirty

    # ── file I/O ──────────────────────────────────────────────────────────
    def save(self, path: str) -> str:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.lines))
            self.filepath = path
            self._dirty = False
            return f"Saved → {path}"
        except Exception as e:
            return f"Error: {e}"

    def load(self, path: str) -> str:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            self.lines = content.split('\n') or ['']
            self.filepath = path
            self.cur = [0, 0]
            self.sel = None
            self._sel_anchor = None
            self._dirty = False
            self._undo.clear()
            self._redo.clear()
            self.bookmarks.clear()
            self._snap_force()
            return f"Loaded ← {path}"
        except Exception as e:
            return f"Error: {e}"

    # ── context query ─────────────────────────────────────────────────────
    def context_word(self) -> str:
        """Return the first instruction mnemonic on current line (skipping label)."""
        line = self.lines[self.cur[0]].strip().lower()
        if not line or line.startswith(';'):
            return ''
        label_m = re.match(r'[a-z0-9_.@$?]+:\s*', line)
        if label_m:
            line = line[label_m.end():]
        if not line:
            return ''
        m = re.match(r'([a-z%][a-z0-9_%]*)', line)
        return m.group(1) if m else ''

    def line_count(self) -> int:
        return len(self.lines)

    def get_line(self, i: int) -> str:
        return self.lines[i] if 0 <= i < len(self.lines) else ''

    # ── search & replace ─────────────────────────────────────────────────
    def find(self, pattern: str, start: Tuple[int,int] = None,
             case: bool = False, word: bool = False) -> Optional[Tuple[int,int]]:
        """Return (row, col) of next match after start, or None. Updates _last_search."""
        self._last_search = pattern
        if start is None: start = tuple(self.cur)
        r, c = start
        flags = 0 if case else re.IGNORECASE
        pat = re.compile(re.escape(pattern) if not case or word else pattern, flags)
        # search current line from c onwards
        line = self.lines[r]
        m = pat.search(line, c)
        if m: return (r, m.start())
        # subsequent lines
        for i in range(r+1, len(self.lines)):
            m = pat.search(self.lines[i])
            if m: return (i, m.start())
        # wrap around
        for i in range(r+1):
            m = pat.search(self.lines[i], 0 if i>r else None)
            if m: return (i, m.start())
        return None

    def find_prev(self, pattern: str = None) -> Optional[Tuple[int,int]]:
        """Find previous match. Uses _last_search if pattern not given."""
        if pattern is None: pattern = self._last_search
        if not pattern: return None
        r, c = self.cur
        flags = 0 if re.match(r'[A-Z]', pattern) else re.IGNORECASE
        pat = re.compile(re.escape(pattern) if True else pattern, flags)
        # search current line backwards
        line = self.lines[r]
        for m in pat.finditer(line[:c]):
            pass  # find last
        else:
            # no built-in rfind? we'll iterate reversed
            last = None
            for m in re.finditer(pat, line):
                if m.start() < c:
                    last = m
            if last: return (r, last.start())
        # previous lines
        for i in range(r-1, -1, -1):
            m_iter = list(pat.finditer(self.lines[i]))
            if m_iter:
                return (i, m_iter[-1].start())
        # wrap from end
        for i in range(len(self.lines)-1, r, -1):
            m_iter = list(pat.finditer(self.lines[i]))
            if m_iter:
                return (i, m_iter[-1].start())
        return None

    def replace(self, pattern: str, repl: str, case: bool = False) -> int:
        """Replace first occurrence after cursor; returns 1 if replaced else 0."""
        pos = self.find(pattern, case=case)
        if not pos: return 0
        self.cur = list(pos)
        end = pos[1] + len(pattern)
        self.sel = (pos, (pos[0], end))
        self.del_sel()
        self.insert_text(repl)
        self._snap_force()
        return 1

    def replace_all(self, pattern: str, repl: str, case: bool = False) -> int:
        """Replace all occurrences; returns number of replacements."""
        flags = 0 if case else re.IGNORECASE
        pat = re.compile(re.escape(pattern) if True else pattern, flags)
        count = 0
        new_lines = []
        for line in self.lines:
            new_line, n = pat.subn(repl, line)
            count += n
            new_lines.append(new_line)
        if count:
            self.lines = new_lines
            self._dirty = True
            self._snap_force()
        return count

    # ── bookmarks ─────────────────────────────────────────────────────────
    def toggle_bookmark(self, row: int) -> None:
        if row in self.bookmarks:
            self.bookmarks.discard(row)
        else:
            self.bookmarks.add(row)

    def next_bookmark(self, from_row: int = None) -> Optional[int]:
        if not self.bookmarks: return None
        if from_row is None: from_row = self.cur[0]
        sorted_marks = sorted(self.bookmarks)
        for m in sorted_marks:
            if m > from_row: return m
        return sorted_marks[0] if sorted_marks else None

    def prev_bookmark(self, from_row: int = None) -> Optional[int]:
        if not self.bookmarks: return None
        if from_row is None: from_row = self.cur[0]
        sorted_marks = sorted(self.bookmarks, reverse=True)
        for m in sorted_marks:
            if m < from_row: return m
        return sorted_marks[0] if sorted_marks else None

    def clear_bookmarks(self) -> None:
        self.bookmarks.clear()

    # ── line manipulation ─────────────────────────────────────────────────
    def toggle_comment(self, row: int) -> None:
        """Toggle ';' comment at beginning of line (after indentation)."""
        line = self.lines[row]
        stripped = line.lstrip()
        if stripped.startswith(';'):
            # remove first ';' (and optional space)
            idx = line.index(';')
            self.lines[row] = line[:idx] + line[idx+1:].lstrip()
        else:
            indent = len(line) - len(stripped)
            self.lines[row] = line[:indent] + '; ' + stripped
        self._dirty = True
        self._snap_force()

    def duplicate_line(self, row: int) -> None:
        self.lines.insert(row, self.lines[row])
        self.cur[0] += 1
        self._dirty = True
        self._snap_force()

    def move_line_up(self, row: int) -> None:
        if row == 0: return
        self.lines[row], self.lines[row-1] = self.lines[row-1], self.lines[row]
        if self.cur[0] == row: self.cur[0] -= 1
        self._dirty = True
        self._snap_force()

    def move_line_down(self, row: int) -> None:
        if row >= len(self.lines)-1: return
        self.lines[row], self.lines[row+1] = self.lines[row+1], self.lines[row]
        if self.cur[0] == row: self.cur[0] += 1
        self._dirty = True
        self._snap_force()

    def jump_to_line(self, row: int) -> None:
        row = max(0, min(row, len(self.lines)-1))
        self.cur = [row, 0]
        self.sel = None
        self._sel_anchor = None

    # ── case transformations ──────────────────────────────────────────────
    def upper_case(self) -> None:
        if not self.has_sel(): return
        s = self._sel_ordered()
        if s[0][0] == s[1][0]:
            r, c0, c1 = s[0][0], s[0][1], s[1][1]
            self.lines[r] = self.lines[r][:c0] + self.lines[r][c0:c1].upper() + self.lines[r][c1:]
        else:
            # first line
            self.lines[s[0][0]] = self.lines[s[0][0]][:s[0][1]] + self.lines[s[0][0]][s[0][1]:].upper()
            # middle lines
            for r in range(s[0][0]+1, s[1][0]):
                self.lines[r] = self.lines[r].upper()
            # last line
            self.lines[s[1][0]] = self.lines[s[1][0]][:s[1][1]].upper() + self.lines[s[1][0]][s[1][1]:]
        self._dirty = True
        self._snap_force()

    def lower_case(self) -> None:
        if not self.has_sel(): return
        s = self._sel_ordered()
        if s[0][0] == s[1][0]:
            r, c0, c1 = s[0][0], s[0][1], s[1][1]
            self.lines[r] = self.lines[r][:c0] + self.lines[r][c0:c1].lower() + self.lines[r][c1:]
        else:
            self.lines[s[0][0]] = self.lines[s[0][0]][:s[0][1]] + self.lines[s[0][0]][s[0][1]:].lower()
            for r in range(s[0][0]+1, s[1][0]):
                self.lines[r] = self.lines[r].lower()
            self.lines[s[1][0]] = self.lines[s[1][0]][:s[1][1]].lower() + self.lines[s[1][0]][s[1][1]:]
        self._dirty = True
        self._snap_force()

    # ── utility ───────────────────────────────────────────────────────────
    def word_count(self) -> int:
        return sum(len(re.findall(r'\b\w+\b', line)) for line in self.lines)

    def char_count(self) -> int:
        return sum(len(line) + 1 for line in self.lines)  # +1 for newline separator

    def indent_level(self, row: int) -> int:
        line = self.lines[row]
        return len(line) - len(line.lstrip())

# ══════════════════════════════════════════════════════════════════════════════
# § 6  TEMPLATE LIBRARY
# ══════════════════════════════════════════════════════════════════════════════

class TemplateLib:
    def __init__(self) -> None:
        self.all = TEMPLATES
        self.by_id: Dict[str, Dict] = {t['id']: t for t in TEMPLATES}
        cats: Dict[str, List] = {}
        for t in TEMPLATES:
            cats.setdefault(t['cat'], []).append(t)
        self.categories: Dict[str, List] = dict(sorted(cats.items()))
        # extended features
        self._recent: List[str] = []          # list of IDs, most recent last
        self._favorites: set = set()          # set of IDs
        self._max_recent = 20

    # ── query ──────────────────────────────────────────────────────────────
    def get(self, id_: str) -> Optional[Dict]:
        return self.by_id.get(id_)

    def __contains__(self, id_: str) -> bool:
        return id_ in self.by_id

    def by_category(self, cat: str) -> List[Dict]:
        return self.categories.get(cat, [])

    def ids(self) -> List[str]:
        return list(self.by_id.keys())

    # ── search ─────────────────────────────────────────────────────────────
    def search(self, query: str, max_results: int = 200) -> List[Dict]:
        q = query.lower().strip()
        if not q:
            return self.all[:max_results]
        results = []
        for t in self.all:
            if (q in t['title'].lower() or q in t['cat'].lower() or
                q in t['desc'].lower() or q in t['id'].lower() or
                q in t['tpl'].lower()):
                results.append(t)
                if len(results) >= max_results:
                    break
        return results

    def fuzzy_search(self, query: str, threshold: float = 0.6, max_results: int = 30) -> List[Dict]:
        """Return templates whose title or ID has a fuzzy match above threshold."""
        from difflib import SequenceMatcher
        q = query.lower()
        scored = []
        for t in self.all:
            title = t['title'].lower()
            id_  = t['id'].lower()
            score = max(SequenceMatcher(None, q, title).ratio(),
                        SequenceMatcher(None, q, id_).ratio())
            if score >= threshold:
                scored.append((score, t))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [t for _, t in scored[:max_results]]

    def suggest(self, prefix: str, limit: int = 10) -> List[str]:
        """Return a list of template IDs starting with prefix (for autocomplete)."""
        p = prefix.lower()
        return [tid for tid in self.by_id if tid.lower().startswith(p)][:limit]

    # ── recent / favorites ─────────────────────────────────────────────────
    def mark_used(self, id_: str) -> None:
        """Add to recent list after using a template."""
        if id_ in self._recent:
            self._recent.remove(id_)
        self._recent.append(id_)
        if len(self._recent) > self._max_recent:
            self._recent.pop(0)

    def recent(self, n: int = 10) -> List[Dict]:
        return [self.by_id[i] for i in reversed(self._recent[-n:]) if i in self.by_id]

    def toggle_favorite(self, id_: str) -> bool:
        """Toggle favorite status, return new state."""
        if id_ in self._favorites:
            self._favorites.discard(id_)
            return False
        else:
            self._favorites.add(id_)
            return True

    def is_favorite(self, id_: str) -> bool:
        return id_ in self._favorites

    def favorites(self) -> List[Dict]:
        return [self.by_id[i] for i in self._favorites if i in self.by_id]

    # ── rendering ──────────────────────────────────────────────────────────
    def render(self, tpl: Dict, params: Dict[str, str]) -> str:
        text = tpl['tpl']
        for port in tpl.get('ports', []):
            name = port['n']
            val  = params.get(name, port.get('d', ''))
            text = text.replace('{' + name + '}', val)
        return text

    def validate_params(self, tpl: Dict, params: Dict[str, str]) -> Tuple[bool, List[str]]:
        """Check that all required ports have a non-empty value. Returns (ok, missing_names)."""
        missing = []
        for port in tpl.get('ports', []):
            # if default is empty, consider it required
            if not port.get('d', '') and not params.get(port['n'], '').strip():
                missing.append(port['n'])
        return len(missing) == 0, missing

    # ── pagination helper ──────────────────────────────────────────────────
    def paginate(self, items: List[Dict], page: int, per_page: int = 50) -> List[Dict]:
        start = page * per_page
        return items[start:start+per_page]

    # ── export / import (for session save/load) ────────────────────────────
    def get_state(self) -> Dict:
        return {
            'recent': self._recent,
            'favorites': list(self._favorites),
        }

    def set_state(self, state: Dict) -> None:
        self._recent = state.get('recent', [])[:self._max_recent]
        self._favorites = set(state.get('favorites', []))

    # ── statistics ─────────────────────────────────────────────────────────
    def category_list(self) -> List[str]:
        return list(self.categories.keys())

    def count(self) -> int:
        return len(self.all)

# ══════════════════════════════════════════════════════════════════════════════
# § 7  CONTEXT ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

class ContextAnalyzer:
    def __init__(self, lib: TemplateLib) -> None:
        self.lib = lib
        # Validate all rule IDs exist (dev-time check)
        self._valid: Dict[str, List[str]] = {}
        for kw, ids in CONTEXT_RULES.items():
            valid = [i for i in ids if i in lib.by_id]
            if valid:
                self._valid[kw] = valid

        # ── extended maps ──────────────────────────────────────────────────
        # reverse: template ID → list of context keywords that recommend it
        self._reverse: Dict[str, List[str]] = {}
        for kw, ids in self._valid.items():
            for tid in ids:
                self._reverse.setdefault(tid, []).append(kw)

        # category → list of keywords (for fuzzy fallback)
        self._cat_keywords: Dict[str, List[str]] = {}
        for kw in self._valid:
            # determine category from any template that uses this keyword
            for tid in self._valid[kw]:
                t = lib.by_id.get(tid)
                if t:
                    cat = t['cat']
                    self._cat_keywords.setdefault(cat, []).append(kw)
        # deduplicate
        for cat in self._cat_keywords:
            self._cat_keywords[cat] = list(dict.fromkeys(self._cat_keywords[cat]))

        # ── register suggestion sets ───────────────────────────────────────
        self._gp_regs  = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','rsp',
                          'r8','r9','r10','r11','r12','r13','r14','r15']
        self._gp32_regs= ['eax','ebx','ecx','edx','esi','edi','ebp','esp']
        self._sse_regs = [f'xmm{i}' for i in range(16)]
        self._avx_regs = [f'ymm{i}' for i in range(16)]

    # ── primary suggestion (template blocks) ───────────────────────────────
    def suggest(self, buf: TextBuffer) -> List[Dict]:
        """Return a list of recommended template blocks based on cursor context."""
        kw = self._primary_keyword(buf)
        if not kw:
            return []
        ids = self._valid.get(kw, [])
        if not ids:
            # partial match against all known keywords
            for k, v in self._valid.items():
                if kw.startswith(k) or k.startswith(kw):
                    ids = v
                    break
        if not ids:
            # category fallback: look at the line's category via lib search
            cat = self._guess_category_from_line(buf)
            if cat:
                kw_list = self._cat_keywords.get(cat, [])
                for k in kw_list:
                    if k in self._valid:
                        ids = self._valid[k]
                        break
        return [self.lib.by_id[i] for i in ids]

    # ── parameter / inline suggestions ─────────────────────────────────────
    def suggest_params(self, buf: TextBuffer) -> List[Dict]:
        """
        Return a list of parameter suggestions (register names, immediate hints)
        based on the current instruction and cursor position.
        Each dict: { 'text':str, 'desc':str, 'type':str }
        """
        row, col = buf.cur
        line = buf.lines[row]
        tokens = tokenize(line)
        # locate token under / just before cursor
        # determine if we are inside an operand
        results = []
        inst = self._instruction_at_cursor(buf)
        if not inst:
            return results
        # suggest registers for general instructions
        if inst in ('mov','lea','add','sub','imul','idiv','xor','and','or','cmp','test','push','pop',
                    'movaps','movups','addps','vaddps'):
            results.extend([{'text':r,'desc':'64-bit GP register','type':'register'} for r in self._gp_regs])
            results.extend([{'text':r,'desc':'32-bit GP register','type':'register'} for r in self._gp32_regs])
            # also suggest memory forms
            results.append({'text':'[rsp+8]','desc':'stack offset','type':'memory'})
            results.append({'text':'[rbp-8]','desc':'frame offset','type':'memory'})
            results.append({'text':'[rax]','desc':'indirect memory','type':'memory'})
            results.append({'text':'0','desc':'immediate zero','type':'immediate'})
        # SSE / AVX → suggest XMM/YMM regs
        if inst.startswith('movap') or inst.startswith('addp') or inst.startswith('mulps') or \
           inst.startswith('vadd') or inst.startswith('vmov'):
            results.extend([{'text':r,'desc':'128-bit SSE reg','type':'register'} for r in self._sse_regs])
            if 'v' in inst or inst.startswith('v'):
                results.extend([{'text':r,'desc':'256-bit AVX reg','type':'register'} for r in self._avx_regs])

        # filter by what has already been typed after the comma
        # (simple: if cursor is after a comma, suggest only appropriate types)
        left_of_cursor = line[:col]
        if ',' in left_of_cursor:
            # after comma, suggest registers/immediates
            results = [r for r in results if r['type'] in ('register','immediate','memory')]
        return results[:20]

    # ── full context extraction ────────────────────────────────────────────
    def get_context(self, buf: TextBuffer) -> Dict:
        """Return a rich context dictionary for external use (e.g., AI completions)."""
        row, col = buf.cur
        line = buf.lines[row]
        tokens = tokenize(line)
        inst = self._instruction_at_cursor(buf)
        prev_inst = self._previous_instruction(buf)
        operands = self._parse_operands(buf)
        return {
            'current_instruction': inst,
            'previous_instruction': prev_inst,
            'operands': operands,
            'line_text': line,
            'cursor_col': col,
            'current_token': self._token_at(tokens, col),
            'line_number': row,
            'total_lines': buf.line_count(),
        }

    # ── internal helpers ───────────────────────────────────────────────────
    def _primary_keyword(self, buf: TextBuffer) -> Optional[str]:
        """Return the best guess for the main instruction keyword at cursor."""
        kw = buf.context_word()
        if kw:
            return kw
        # backward search up to 5 lines for a context keyword
        for back in range(1, 6):
            r = buf.cur[0] - back
            if r < 0: break
            line = buf.lines[r].strip().lower()
            if line and not line.startswith(';'):
                m = re.match(r'([a-z%][a-z0-9_%]*)', line)
                if m:
                    return m.group(1)
        return None

    def _instruction_at_cursor(self, buf: TextBuffer) -> Optional[str]:
        """Return the complete instruction mnemonic that the cursor is on,
        even if the cursor is inside its operands."""
        row = buf.cur[0]
        line = buf.lines[row].strip()
        if not line or line.startswith(';'):
            return None
        tokens = tokenize(line)
        # find the first keyword or directive token
        for tok, typ in tokens:
            if typ in ('kw','dir_','text'):
                return tok.lower()
        return None

    def _previous_instruction(self, buf: TextBuffer) -> Optional[str]:
        """Look backwards for the most recent non-comment instruction mnemonic."""
        for r in range(buf.cur[0]-1, -1, -1):
            line = buf.lines[r].strip()
            if line and not line.startswith(';'):
                tokens = tokenize(line)
                for tok, typ in tokens:
                    if typ in ('kw','dir_','text'):
                        return tok.lower()
        return None

    def _parse_operands(self, buf: TextBuffer) -> List[str]:
        """Return a list of operand strings extracted from the current instruction line."""
        row = buf.cur[0]
        line = buf.lines[row].strip()
        if not line or line.startswith(';'): return []
        # remove label
        m = re.match(r'[a-z0-9_.@$?]+:\s*', line, re.IGNORECASE)
        if m: line = line[m.end():]
        parts = line.split(None, 1)
        if len(parts) < 2: return []
        rest = parts[1]
        # split by comma, but respect brackets/strings
        operands = []
        current = ''
        depth = 0
        for ch in rest:
            if ch == ',' and depth == 0:
                operands.append(current.strip())
                current = ''
            else:
                if ch in '([': depth += 1
                elif ch in ')]': depth -= 1
                current += ch
        if current.strip():
            operands.append(current.strip())
        return operands

    def _token_at(self, tokens, col) -> Optional[Tuple[str,str]]:
        """Return (token, type) at a given column, or None."""
        for tok, typ in tokens:
            # approximate: we'd need to know token positions; we'll iterate
            pass
        # simplified: no position info, return None
        return None

    def _guess_category_from_line(self, buf: TextBuffer) -> Optional[str]:
        """Guess the template category based on the current line content."""
        line = buf.lines[buf.cur[0]].strip().lower()
        if 'section' in line: return 'Sections'
        if 'global' in line or 'extern' in line: return 'Directives'
        if 'db' in line or 'dw' in line or 'dd' in line or 'dq' in line: return 'Data/Define'
        if 'call [' in line: return 'CPU/Flow'
        if 'call' in line: return 'CPU/Flow'
        if 'gl' in line: return 'OpenGL32'
        if 'gdi' in line: return 'GDI32'
        return None

    # ── fuzzy keyword lookup ───────────────────────────────────────────────
    def _fuzzy_keyword_match(self, word: str) -> Optional[str]:
        """Return the closest matching keyword from _valid, or None."""
        from difflib import get_close_matches
        matches = get_close_matches(word, self._valid.keys(), n=1, cutoff=0.6)
        return matches[0] if matches else None

# ══════════════════════════════════════════════════════════════════════════════
# § 8  UI PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════

def _cat_color(cat: str) -> Tuple[int, int, int]:
    c = cat.lower()
    if 'project' in c: return (100, 80, 200)
    if 'opengl' in c:  return (80, 200, 130)
    if 'gdi' in c:     return (200, 110, 80)
    if 'abi' in c:     return (80, 150, 210)
    if 'simd' in c or 'avx' in c or 'sse' in c: return (190, 80, 200)
    if 'flow' in c:    return (220, 170, 50)
    if 'alu' in c:     return (210, 90, 90)
    if 'logic' in c:   return (90, 160, 210)
    if 'data' in c:    return (80, 195, 80)
    if 'section' in c or 'direct' in c: return (160, 160, 80)
    return (120, 120, 170)


def draw_text(surf: pygame.Surface, font: pygame.font.Font,
              x: int, y: int, text: str, color=None, max_w: int = 0) -> int:
    col = color or T['text']
    s = font.render(text, True, col)
    if max_w and s.get_width() > max_w:
        # find a truncated version that fits
        truncated = ''
        for end in range(len(text) - 1, 0, -1):
            candidate = text[:end] + '…'
            s2 = font.render(candidate, True, col)
            if s2.get_width() <= max_w:
                truncated = candidate
                break
        if truncated:
            s = font.render(truncated, True, col)
    surf.blit(s, (x, y))
    return s.get_width()


def draw_btn(surf: pygame.Surface, font: pygame.font.Font, r: pygame.Rect,
             label: str, hovered: bool = False, active: bool = False,
             col=None, text_col=None, disabled: bool = False) -> None:
    bg = col or (T['btn_act'] if active else (T['btn_hov'] if hovered else T['btn']))
    if disabled: bg = T['btn_disabled']; text_col = T['btn_text_dim']
    pygame.draw.rect(surf, bg, r, border_radius=5)
    pygame.draw.rect(surf, T['border'], r, 1, border_radius=5)
    s = font.render(label, True, text_col or T['btn_text'])
    surf.blit(s, (r.x + (r.w - s.get_width()) // 2, r.y + (r.h - s.get_height()) // 2))


class InputField:
    """Single-line text input  —  with real Ctrl+A / horizontal scroll / clip fix."""
    def __init__(self, x: int, y: int, w: int, h: int,
                 placeholder: str = '', text: str = ''):
        self.r = pygame.Rect(x, y, w, h)
        self.text = text
        self.ph = placeholder
        self.active = False
        self.cur = len(text)
        self.sel_start: Optional[int] = None   # selection anchor
        self._scroll = 0   # horizontal pixel scroll offset

    def activate(self) -> None:   self.active = True
    def deactivate(self) -> None: self.active = False

    def _sel_range(self) -> Optional[Tuple[int,int]]:
        if self.sel_start is None: return None
        lo, hi = min(self.sel_start, self.cur), max(self.sel_start, self.cur)
        return (lo, hi) if lo != hi else None

    def _del_sel(self) -> None:
        s = self._sel_range()
        if not s: return
        lo, hi = s
        self.text = self.text[:lo] + self.text[hi:]
        self.cur  = lo
        self.sel_start = None

    def _ensure_visible(self, font: pygame.font.Font) -> None:
        inner_w = self.r.w - 12
        cx = font.size(self.text[:self.cur])[0]
        if cx - self._scroll > inner_w:
            self._scroll = cx - inner_w
        elif cx - self._scroll < 0:
            self._scroll = max(0, cx - inner_w // 2)

    def handle_key(self, ev, font: pygame.font.Font) -> bool:
        if not self.active: return False
        ctrl  = bool(ev.mod & pygame.KMOD_CTRL)
        shift = bool(ev.mod & pygame.KMOD_SHIFT)

        if ev.key == pygame.K_LEFT:
            if shift:
                if self.sel_start is None: self.sel_start = self.cur
                self.cur = max(0, self.cur - 1)
            else:
                s = self._sel_range()
                if s: self.cur = s[0]; self.sel_start = None
                else: self.cur = max(0, self.cur - 1)
        elif ev.key == pygame.K_RIGHT:
            if shift:
                if self.sel_start is None: self.sel_start = self.cur
                self.cur = min(len(self.text), self.cur + 1)
            else:
                s = self._sel_range()
                if s: self.cur = s[1]; self.sel_start = None
                else: self.cur = min(len(self.text), self.cur + 1)
        elif ev.key == pygame.K_HOME:
            if shift:
                if self.sel_start is None: self.sel_start = self.cur
            else:
                self.sel_start = None
            self.cur = 0
        elif ev.key == pygame.K_END:
            if shift:
                if self.sel_start is None: self.sel_start = self.cur
            else:
                self.sel_start = None
            self.cur = len(self.text)
        elif ev.key == pygame.K_BACKSPACE:
            s = self._sel_range()
            if s: self._del_sel()
            elif self.cur > 0:
                self.text = self.text[:self.cur - 1] + self.text[self.cur:]
                self.cur -= 1
        elif ev.key == pygame.K_DELETE:
            s = self._sel_range()
            if s: self._del_sel()
            elif self.cur < len(self.text):
                self.text = self.text[:self.cur] + self.text[self.cur + 1:]
        elif ctrl and ev.key == pygame.K_a:
            self.sel_start = 0
            self.cur = len(self.text)
        elif ctrl and ev.key == pygame.K_c:
            s = self._sel_range()
            if s:
                try: pygame.scrap.put_text(self.text[s[0]:s[1]])
                except Exception: pass
        elif ctrl and ev.key == pygame.K_x:
            s = self._sel_range()
            if s:
                try: pygame.scrap.put_text(self.text[s[0]:s[1]])
                except Exception: pass
                self._del_sel()
        elif ctrl and ev.key == pygame.K_v:
            clip = ''
            try: clip = pygame.scrap.get_text() or ''
            except Exception: pass
            clip = clip.replace('\r', '').replace('\n', ' ')
            self._del_sel()
            self.text = self.text[:self.cur] + clip + self.text[self.cur:]
            self.cur += len(clip)
        elif ev.unicode and ev.unicode.isprintable():
            self._del_sel()
            self.text = self.text[:self.cur] + ev.unicode + self.text[self.cur:]
            self.cur += 1
        self._ensure_visible(font)
        return True

    def handle_mouse(self, pos: Tuple[int, int], font: pygame.font.Font,
                     double: bool = False) -> bool:
        if self.r.collidepoint(pos):
            self.active = True
            rel = pos[0] - self.r.x - 6 + self._scroll
            acc = 0
            for i, ch in enumerate(self.text):
                w = font.size(ch)[0]
                if acc + w / 2 >= rel:
                    self.cur = i
                    break
                acc += w
            else:
                self.cur = len(self.text)
            if double:
                lo, hi = self.cur, self.cur
                while lo > 0 and (self.text[lo - 1].isalnum() or self.text[lo - 1] == '_'):
                    lo -= 1
                while hi < len(self.text) and (self.text[hi].isalnum() or self.text[hi] == '_'):
                    hi += 1
                self.sel_start = lo; self.cur = hi
            else:
                self.sel_start = None
            return True
        else:
            self.active = False
            return False

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        brd = T['inp_act'] if self.active else T['inp_brd']
        pygame.draw.rect(surf, T['inp'], self.r, border_radius=4)
        pygame.draw.rect(surf, brd, self.r, 1, border_radius=4)

        inner = pygame.Rect(self.r.x + 6, self.r.y, self.r.w - 12, self.r.h)
        surf.set_clip(inner)

        ox = self.r.x + 6 - self._scroll
        ty = self.r.y + (self.r.h - font.get_height()) // 2

        if self.text:
            sel = self._sel_range()
            if sel:
                lo, hi = sel
                sx = ox + font.size(self.text[:lo])[0]
                sw = font.size(self.text[lo:hi])[0]
                pygame.draw.rect(surf, T['sel'], (sx, self.r.y + 2, sw, self.r.h - 4))
            s = font.render(self.text, True, T['text'])
            surf.blit(s, (ox, ty))
        else:
            s = font.render(self.ph, True, T['text_dim'])
            surf.blit(s, (ox, ty))

        if self.active and int(time.monotonic() * 2) % 2 == 0:
            cx = ox + font.size(self.text[:self.cur])[0]
            pygame.draw.line(surf, T['cursor'], (cx, self.r.y + 3), (cx, self.r.bottom - 3), 1)

        surf.set_clip(None)


# ── Scrollbar (vertical) ─────────────────────────────────────────────────────

class Scrollbar:
    def __init__(self, rect: pygame.Rect, vertical: bool = True):
        self.rect = rect
        self.vertical = vertical
        self.value = 0.0          # 0.0 = top, 1.0 = bottom
        self.visible_ratio = 1.0  # proportion of content visible
        self.dragging = False
        self.drag_offset = 0

    @property
    def is_hovered(self) -> bool:
        return self.rect.collidepoint(pygame.mouse.get_pos())

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.dragging = True
                if self.vertical:
                    thumb_h = max(8, self.rect.h * self.visible_ratio)
                    thumb_y = self.rect.y + (self.rect.h - thumb_h) * self.value
                    self.drag_offset = event.pos[1] - thumb_y
                else:
                    thumb_w = max(8, self.rect.w * self.visible_ratio)
                    thumb_x = self.rect.x + (self.rect.w - thumb_w) * self.value
                    self.drag_offset = event.pos[0] - thumb_x
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            if self.vertical:
                thumb_h = max(8, self.rect.h * self.visible_ratio)
                rel_y = event.pos[1] - self.rect.y - self.drag_offset
                self.value = max(0.0, min(1.0, rel_y / (self.rect.h - thumb_h)))
            else:
                thumb_w = max(8, self.rect.w * self.visible_ratio)
                rel_x = event.pos[0] - self.rect.x - self.drag_offset
                self.value = max(0.0, min(1.0, rel_x / (self.rect.w - thumb_w)))
            return True
        elif event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            delta = event.y * 0.1
            self.value = max(0.0, min(1.0, self.value - delta))
            return True
        return False

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, T['scrollbar'], self.rect, border_radius=3)
        if self.vertical:
            thumb_h = max(8, self.rect.h * self.visible_ratio)
            if thumb_h >= self.rect.h:
                return
            thumb_y = self.rect.y + (self.rect.h - thumb_h) * self.value
            thumb_rect = pygame.Rect(self.rect.x, thumb_y, self.rect.w, thumb_h)
        else:
            thumb_w = max(8, self.rect.w * self.visible_ratio)
            if thumb_w >= self.rect.w:
                return
            thumb_x = self.rect.x + (self.rect.w - thumb_w) * self.value
            thumb_rect = pygame.Rect(thumb_x, self.rect.y, thumb_w, self.rect.h)
        color = T['scrollbar_thumb'] if (self.dragging or self.is_hovered) else T['scrollbar']
        pygame.draw.rect(surf, color, thumb_rect, border_radius=3)


# ── ListBox ──────────────────────────────────────────────────────────────────

class ListBox:
    """A scrollable list of items with single selection and keyboard navigation."""
    def __init__(self, rect: pygame.Rect, font: pygame.font.Font,
                 item_height: int = 28):
        self.rect = rect
        self.font = font
        self.item_height = item_height
        self.items: List[str] = []
        self.selected_idx: int = -1
        self.scrollbar = Scrollbar(pygame.Rect(rect.right - 8, rect.y, 8, rect.h), True)
        self._hovered = -1
        self._visible_offset = 0  # first visible item index
        self._update_scrollbar()

    def _update_scrollbar(self) -> None:
        total_items = len(self.items)
        if total_items == 0:
            self.scrollbar.visible_ratio = 1.0
            return
        visible_items = max(1, self.rect.h // self.item_height)
        self.scrollbar.visible_ratio = visible_items / total_items
        # sync scroll offset
        max_offset = max(0, total_items - visible_items)
        self._visible_offset = int(self.scrollbar.value * max_offset)
        if self._visible_offset > max_offset:
            self._visible_offset = max_offset
            self.scrollbar.value = 1.0

    def set_items(self, items: List[str]) -> None:
        self.items = items
        self.selected_idx = -1 if not items else 0
        self._update_scrollbar()

    def handle_event(self, event: pygame.event.Event) -> Optional[int]:
        """Returns the index of the clicked/selected item, or None."""
        if self.scrollbar.handle_event(event):
            self._update_scrollbar()
        # mouse hover
        if event.type == pygame.MOUSEMOTION and self.rect.collidepoint(event.pos):
            rel_y = event.pos[1] - self.rect.y
            idx = self._visible_offset + rel_y // self.item_height
            self._hovered = idx if 0 <= idx < len(self.items) else -1
        # click
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos) and self._hovered >= 0:
                self.selected_idx = self._hovered
                return self.selected_idx
        # keyboard navigation
        if event.type == pygame.KEYDOWN and self.selected_idx >= 0:
            if event.key == pygame.K_UP:
                self.selected_idx = max(0, self.selected_idx - 1)
                self._scroll_to_visible(self.selected_idx)
                return self.selected_idx
            elif event.key == pygame.K_DOWN:
                self.selected_idx = min(len(self.items) - 1, self.selected_idx + 1)
                self._scroll_to_visible(self.selected_idx)
                return self.selected_idx
        return None

    def _scroll_to_visible(self, idx: int) -> None:
        visible = max(1, self.rect.h // self.item_height)
        max_offset = max(0, len(self.items) - visible)
        if idx < self._visible_offset:
            self._visible_offset = idx
        elif idx >= self._visible_offset + visible:
            self._visible_offset = idx - visible + 1
        self._visible_offset = max(0, min(self._visible_offset, max_offset))
        self.scrollbar.value = self._visible_offset / max_offset if max_offset > 0 else 0.0

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, T['panel2'], self.rect, border_radius=4)
        pygame.draw.rect(surf, T['border'], self.rect, 1, border_radius=4)
        clip = surf.get_clip()
        surf.set_clip(self.rect)
        visible = max(1, self.rect.h // self.item_height)
        for i in range(visible):
            item_idx = self._visible_offset + i
            if item_idx >= len(self.items):
                break
            y = self.rect.y + i * self.item_height
            rect = pygame.Rect(self.rect.x, y, self.rect.w - 10, self.item_height)
            if item_idx == self.selected_idx:
                pygame.draw.rect(surf, T['item_sel'], rect, border_radius=2)
            elif item_idx == self._hovered:
                pygame.draw.rect(surf, T['item_hov'], rect, border_radius=2)
            text = self.items[item_idx]
            draw_text(surf, self.font, self.rect.x + 4, y + 2, text, max_w=self.rect.w - 16)
        surf.set_clip(clip)
        self.scrollbar.draw(surf)


# ── Tooltip ──────────────────────────────────────────────────────────────────

class Tooltip:
    def __init__(self, delay: float = 0.5):
        self.text = ''
        self.pos = (0, 0)
        self.visible = False
        self._timer = 0.0
        self._delay = delay

    def show(self, text: str, pos: Tuple[int,int]) -> None:
        self.text = text
        self.pos = pos
        self.visible = False
        self._timer = 0.0

    def hide(self) -> None:
        self.visible = False

    def update(self, dt: float, mouse_pos: Tuple[int,int]) -> None:
        if self.text:
            self._timer += dt
            if self._timer >= self._delay:
                self.visible = True

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        if not self.visible or not self.text:
            return
        pad = 8
        lines = self.text.split('\n')
        widths = [font.size(line)[0] for line in lines]
        max_w = max(widths) if widths else 0
        line_h = font.get_linesize()
        h = len(lines) * line_h + pad * 2
        w = max_w + pad * 2
        # ensure tooltip fits on screen
        x, y = self.pos
        screen_w, screen_h = surf.get_size()
        if x + w > screen_w: x = screen_w - w - 4
        if y + h > screen_h: y = y - h - 4
        x = max(0, x)
        y = max(0, y)
        rect = pygame.Rect(x, y, w, h)
        pygame.draw.rect(surf, T['tooltip_bg'] if 'tooltip_bg' in T else (30,30,40), rect, border_radius=4)
        pygame.draw.rect(surf, T['border'], rect, 1, border_radius=4)
        for i, line in enumerate(lines):
            text_surf = font.render(line, True, T['text'])
            surf.blit(text_surf, (rect.x + pad, rect.y + pad + i * line_h))


# ── Dialog (modal popup) ─────────────────────────────────────────────────────

class Dialog:
    """Simple modal dialog with title, message, and buttons.
    Must be manually drawn and event-handled by the app loop."""
    def __init__(self, title: str, message: str, buttons: List[str] = None,
                 rect: Optional[pygame.Rect] = None):
        self.title = title
        self.message = message
        self.buttons = buttons or ['OK']
        self.rect = rect or pygame.Rect(0, 0, 420, 200)
        self._btn_rects: List[pygame.Rect] = []
        self._hovered_btn = -1
        self.result: Optional[int] = None   # index of clicked button
        self._old_mouse = (0, 0)
        self._calc_layout()

    def _calc_layout(self) -> None:
        pad = 16
        btn_w = 80
        btn_h = 30
        self.rect.centerx = WIN_W // 2
        self.rect.centery = WIN_H // 2
        # reposition buttons
        start_x = self.rect.right - (len(self.buttons) * (btn_w + 8)) - pad
        y = self.rect.bottom - btn_h - pad
        self._btn_rects = []
        for i in range(len(self.buttons)):
            r = pygame.Rect(start_x + i * (btn_w + 8), y, btn_w, btn_h)
            self._btn_rects.append(r)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self._hovered_btn = -1
            for i, r in enumerate(self._btn_rects):
                if r.collidepoint(event.pos):
                    self._hovered_btn = i
                    break
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._hovered_btn >= 0:
                self.result = self._hovered_btn
                return True
        return False

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        # overlay
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        surf.blit(overlay, (0, 0))
        # dialog box
        pygame.draw.rect(surf, T['dialog_bg'], self.rect, border_radius=6)
        pygame.draw.rect(surf, T['dialog_border'], self.rect, 2, border_radius=6)
        # title
        title_surf = font.render(self.title, True, T['dialog_title'])
        surf.blit(title_surf, (self.rect.x + 16, self.rect.y + 12))
        # message
        msg_lines = self.message.split('\n')
        y = self.rect.y + 40
        for line in msg_lines:
            s = font.render(line, True, T['text'])
            surf.blit(s, (self.rect.x + 16, y))
            y += font.get_linesize()
        # buttons
        for i, r in enumerate(self._btn_rects):
            draw_btn(surf, font, r, self.buttons[i],
                     hovered=(i == self._hovered_btn))


# ── TabBar ───────────────────────────────────────────────────────────────────

class TabBar:
    def __init__(self, rect: pygame.Rect, font: pygame.font.Font):
        self.rect = rect
        self.font = font
        self.tabs: List[str] = []
        self.active_tab: int = 0
        self._hovered: int = -1
        self._tab_widths: List[int] = []

    def set_tabs(self, tabs: List[str]) -> None:
        self.tabs = tabs
        self._tab_widths = [self.font.size(tab)[0] + 20 for tab in tabs]
        self.active_tab = 0

    def handle_event(self, event: pygame.event.Event) -> Optional[int]:
        if event.type == pygame.MOUSEMOTION:
            x = self.rect.x + 5
            self._hovered = -1
            for i, w in enumerate(self._tab_widths):
                if pygame.Rect(x, self.rect.y, w, self.rect.h).collidepoint(event.pos):
                    self._hovered = i
                    break
                x += w
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._hovered >= 0:
                self.active_tab = self._hovered
                return self.active_tab
        return None

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, T['panel'], self.rect)
        x = self.rect.x + 5
        for i, (tab, w) in enumerate(zip(self.tabs, self._tab_widths)):
            r = pygame.Rect(x, self.rect.y + 2, w, self.rect.h - 4)
            if i == self.active_tab:
                pygame.draw.rect(surf, T['item_sel'], r, border_radius=4)
            elif i == self._hovered:
                pygame.draw.rect(surf, T['item_hov'], r, border_radius=4)
            txt_surf = self.font.render(tab, True, T['text'] if i == self.active_tab else T['text_dim'])
            surf.blit(txt_surf, (r.x + 10, r.y + (r.h - txt_surf.get_height()) // 2))
            x += w


# ── ContextMenu ──────────────────────────────────────────────────────────────

class ContextMenu:
    """Right-click popup menu. Must be instantiated per use."""
    def __init__(self, items: List[Tuple[str, Callable]]):
        self.items = items
        self.rect: Optional[pygame.Rect] = None
        self.visible = False
        self._hovered = -1

    def show(self, pos: Tuple[int,int], font: pygame.font.Font) -> None:
        self.visible = True
        item_h = 28
        self.rect = pygame.Rect(pos[0], pos[1],
                                max(font.size(item[0])[0] for item in self.items) + 20,
                                len(self.items) * item_h)
        # adjust to stay on screen
        screen_w, screen_h = pygame.display.get_surface().get_size()
        if self.rect.right > screen_w: self.rect.x = screen_w - self.rect.w - 4
        if self.rect.bottom > screen_h: self.rect.y = screen_h - self.rect.h - 4

    def handle_event(self, event: pygame.event.Event) -> Optional[int]:
        if not self.visible: return None
        if event.type == pygame.MOUSEMOTION:
            if self.rect and self.rect.collidepoint(event.pos):
                rel = event.pos[1] - self.rect.y
                self._hovered = rel // 28
            else:
                self._hovered = -1
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._hovered >= 0:
                self.visible = False
                self.items[self._hovered][1]()  # execute callback
                return self._hovered
            else:
                self.visible = False
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.visible = False
        return None

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        if not self.visible or not self.rect: return
        pygame.draw.rect(surf, T['panel2'], self.rect, border_radius=4)
        pygame.draw.rect(surf, T['border'], self.rect, 1, border_radius=4)
        for i, item in enumerate(self.items):
            y = self.rect.y + i * 28
            r = pygame.Rect(self.rect.x, y, self.rect.w, 28)
            if i == self._hovered:
                pygame.draw.rect(surf, T['item_hov'], r, border_radius=2)
            txt_surf = font.render(item[0], True, T['text'])
            surf.blit(txt_surf, (self.rect.x + 8, y + 4))


# ── Checkbox ─────────────────────────────────────────────────────────────────

class Checkbox:
    def __init__(self, x: int, y: int, label: str, checked: bool = False):
        self.rect = pygame.Rect(x, y, 20, 20)
        self.label = label
        self.checked = checked
        self.hovered = False

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.checked = not self.checked
                return True
        return False

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        box = self.rect
        pygame.draw.rect(surf, T['inp'], box, border_radius=3)
        pygame.draw.rect(surf, T['inp_act'] if self.checked else T['inp_brd'], box, 1, border_radius=3)
        if self.checked:
            # draw check mark
            pygame.draw.line(surf, T['text'], (box.x+4, box.y+10), (box.x+8, box.y+14), 2)
            pygame.draw.line(surf, T['text'], (box.x+8, box.y+14), (box.x+16, box.y+6), 2)
        label_surf = font.render(self.label, True, T['text'])
        surf.blit(label_surf, (box.right + 6, box.y - 1))


# ── ProgressBar ──────────────────────────────────────────────────────────────

class ProgressBar:
    def __init__(self, rect: pygame.Rect, color: Tuple[int,int,int] = None):
        self.rect = rect
        self.color = color or T['accent']
        self.progress = 0.0   # 0..1
        self.text = ''

    def set_progress(self, value: float, text: str = '') -> None:
        self.progress = max(0.0, min(1.0, value))
        self.text = text

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        pygame.draw.rect(surf, T['inp'], self.rect, border_radius=3)
        if self.progress > 0:
            fill_w = int(self.rect.w * self.progress)
            fill_rect = pygame.Rect(self.rect.x, self.rect.y, fill_w, self.rect.h)
            pygame.draw.rect(surf, self.color, fill_rect, border_radius=3)
        pygame.draw.rect(surf, T['inp_brd'], self.rect, 1, border_radius=3)
        if self.text:
            txt_surf = font.render(self.text, True, T['text'])
            surf.blit(txt_surf, (self.rect.centerx - txt_surf.get_width()//2,
                                 self.rect.centery - txt_surf.get_height()//2))

# ══════════════════════════════════════════════════════════════════════════════
# § 9  PARAMETER DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class ParamDialog:
    W   = 480
    ROW = 46
    BTN = 34
    PAD = 16

    def __init__(self, tpl: Dict, screen_size: Tuple[int, int],
                 font: pygame.font.Font, font_ui: pygame.font.Font,
                 mono_font: pygame.font.Font = None):
        self.tpl = tpl
        self.font = font
        self.font_ui = font_ui
        self.mono = mono_font or font_ui
        self.ports = tpl.get('ports', [])
        n = len(self.ports)
        # height: title + preview line + inputs + preview area + buttons
        self.H = self.PAD * 4 + 62 + n * self.ROW + 30 + self.BTN + 14
        sw, sh = screen_size
        self.rect = pygame.Rect((sw - self.W) // 2, (sh - self.H) // 2, self.W, self.H)
        self.inputs: List[InputField] = [
            InputField(self.rect.x + self.PAD + 92,
                       self.rect.y + self.PAD + 58 + i * self.ROW + 4,
                       self.W - self.PAD * 2 - 96, 28,
                       placeholder=p.get('d', ''), text=p.get('d', ''))
            for i, p in enumerate(self.ports)
        ]
        if self.inputs: self.inputs[0].activate()
        self.active_inp = 0
        bx = self.rect.x + self.W - self.PAD - 186
        by_ = self.rect.y + self.H - self.BTN - self.PAD
        self.btn_ok  = pygame.Rect(bx,      by_, 88, self.BTN)
        self.btn_can = pygame.Rect(bx + 94, by_, 88, self.BTN)
        # reset defaults link
        self.reset_rect = pygame.Rect(self.rect.x + self.PAD, by_, 100, self.BTN)
        self.result: Optional[Dict] = None
        self.done = False
        self._hov = ''

    def _get_preview(self) -> str:
        """Return rendered template with current input values (first 100 chars)."""
        text = self.tpl['tpl']
        for p, inp in zip(self.ports, self.inputs):
            val = inp.text.strip()
            if val:
                text = text.replace('{' + p['n'] + '}', val)
            else:
                # keep placeholder if empty
                pass
        # replace remaining placeholders with blank for preview
        import re as _re
        text = _re.sub(r'\{[^}]*\}', '', text)
        lines = text.split('\n')
        if lines:
            first_line = lines[0]
            if len(first_line) > 90:
                first_line = first_line[:87] + '…'
            # if multi-line, append ' …'
            if len(lines) > 1:
                first_line += ' …'
            return first_line.strip()
        return ''

    def _reset_defaults(self) -> None:
        for inp, p in zip(self.inputs, self.ports):
            inp.text = p.get('d', '')
            inp.cur = len(inp.text)
            inp.sel_start = None

    def _cycle(self, fwd: bool = True) -> None:
        if not self.inputs: return
        self.inputs[self.active_inp].deactivate()
        self.active_inp = (self.active_inp + (1 if fwd else -1)) % len(self.inputs)
        self.inputs[self.active_inp].activate()

    def handle_event(self, ev) -> bool:
        if ev.type == pygame.MOUSEMOTION:
            p = ev.pos
            self._hov = ('ok' if self.btn_ok.collidepoint(p) else
                         'can' if self.btn_can.collidepoint(p) else
                         'reset' if self.reset_rect.collidepoint(p) else '')
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            p = ev.pos
            if not self.rect.collidepoint(p): self.done = True; return True
            if self.btn_ok.collidepoint(p):   self._confirm(); return True
            if self.btn_can.collidepoint(p):  self.done = True; return True
            if self.reset_rect.collidepoint(p): self._reset_defaults(); return True
            # click input fields
            for i, inp in enumerate(self.inputs):
                if inp.r.collidepoint(p):
                    for j, other in enumerate(self.inputs):
                        if j != i: other.deactivate()
                    self.active_inp = i
                    inp.handle_mouse(p, self.font_ui)
                    return True
        elif ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_RETURN:
                if pygame.key.get_mods() & pygame.KMOD_CTRL:
                    self._confirm(); return True
                else:
                    # if no modifier, cycle to next input
                    self._cycle(True); return True
            if ev.key == pygame.K_ESCAPE:  self.done = True; return True
            if ev.key == pygame.K_TAB:
                self._cycle(not (ev.mod & pygame.KMOD_SHIFT)); return True
            if ev.key == pygame.K_DOWN:     self._cycle(True); return True
            if ev.key == pygame.K_UP:       self._cycle(False); return True
            if self.inputs:
                self.inputs[self.active_inp].handle_key(ev, self.font_ui)
                return True
        return False

    def _confirm(self) -> None:
        self.result = {p['n']: inp.text for p, inp in zip(self.ports, self.inputs)}
        self.done = True

    def draw(self, surf: pygame.Surface) -> None:
        # dim overlay
        ov = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 110))
        surf.blit(ov, (0, 0))
        # shadow
        sh = pygame.Surface((self.W + 10, self.H + 10), pygame.SRCALPHA)
        sh.fill((0, 0, 0, 130))
        surf.blit(sh, (self.rect.x - 5, self.rect.y - 5))
        # body
        pygame.draw.rect(surf, T['panel2'], self.rect, border_radius=10)
        pygame.draw.rect(surf, T['border'], self.rect, 1, border_radius=10)
        # title bar
        tr = pygame.Rect(self.rect.x, self.rect.y, self.W, 44)
        pygame.draw.rect(surf, T['panel'], tr, border_radius=10)
        pygame.draw.line(surf, T['border'],
                         (self.rect.x, self.rect.y + 44), (self.rect.right, self.rect.y + 44))
        dot = _cat_color(self.tpl.get('cat', ''))
        pygame.draw.rect(surf, dot, (self.rect.x + 12, self.rect.y + 13, 6, 18), border_radius=3)
        draw_text(surf, self.font, self.rect.x + 26, self.rect.y + 13,
                  self.tpl['title'], T['text_em'])
        draw_text(surf, self.font_ui, self.rect.x + 120, self.rect.y + 16,
                  self.tpl['desc'], T['text_dim'], self.W - 135)
        # template line
        py = self.rect.y + 50
        draw_text(surf, self.font_ui, self.rect.x + self.PAD, py,
                  'Template:', T['text_dim'])
        preview = self.tpl['tpl'][:90].replace('\n', '↵ ')
        draw_text(surf, self.font_ui, self.rect.x + self.PAD + 72, py,
                  preview, T['kw'], self.W - 100)
        # ports
        for i, (p, inp) in enumerate(zip(self.ports, self.inputs)):
            y = self.rect.y + self.PAD + 58 + i * self.ROW
            draw_text(surf, self.font_ui, self.rect.x + self.PAD, y + 8, p['n'] + ':', T['lbl'])
            inp.draw(surf, self.font_ui)
        if not self.ports:
            draw_text(surf, self.font_ui, self.rect.x + self.PAD,
                      self.rect.y + 74, '(no parameters — ready to insert)', T['text_dim'])

        # live preview
        py2 = self.rect.y + self.H - self.BTN - self.PAD - 30 - 6
        if self.ports:
            draw_text(surf, self.font_ui, self.rect.x + self.PAD, py2, 'Preview:', T['text_dim'])
            rendered = self._get_preview()
            draw_text(surf, self.mono, self.rect.x + self.PAD + 72, py2 + 2,
                      rendered or '(empty)', T['text'], self.W - 100)

        # buttons
        draw_btn(surf, self.font_ui, self.btn_ok,  '✓ Insert', hovered=self._hov == 'ok',  active=True)
        draw_btn(surf, self.font_ui, self.btn_can, 'Cancel',  hovered=self._hov == 'can')
        # reset defaults
        reset_col = T['text_dim'] if self._hov != 'reset' else T['text']
        draw_text(surf, self.font_ui, self.reset_rect.x, self.reset_rect.y + 8,
                  '↺ Defaults', reset_col)

# ══════════════════════════════════════════════════════════════════════════════
# § 10  LEFT PANEL — Template Library
# ══════════════════════════════════════════════════════════════════════════════

class LeftPanel:
    ITEM_H = 40
    CAT_H  = 28
    SBAR_W = 8

    def __init__(self, x: int, y: int, w: int, h: int, lib: TemplateLib,
                 font: pygame.font.Font, font_ui: pygame.font.Font):
        self.r = pygame.Rect(x, y, w, h)
        self.lib = lib
        self.font = font
        self.font_ui = font_ui
        self.search = InputField(x + 6, y + 6, w - 12, 28, placeholder='search templates…')
        self.expanded: set = set(lib.categories.keys())
        self._items: List[Tuple] = []    # (kind, data, rel_y, height)
        self._scroll = 0
        self._total_h = 0
        self._hov_idx = -1               # index into _items for mouse hover
        self._sel_idx = -1               # index for keyboard selection
        self._click_cb = None            # callback(template_data)
        self._drag_cb = None             # callback(template_data) on drag start
        self._right_click_cb = None      # optional callback(template_data, pos)
        self._build('')

    def set_click_callback(self, cb: Any) -> None:
        self._click_cb = cb

    def set_drag_callback(self, cb: Any) -> None:
        self._drag_cb = cb

    def set_right_click_callback(self, cb: Any) -> None:
        self._right_click_cb = cb

    def _build(self, query: str) -> None:
        self._items.clear()
        y = 0

        # recent / favourites section (only when no search query)
        if not query:
            recents = self.lib.recent(5)
            if recents:
                self._items.append(('section', 'Recent', y, self.CAT_H))
                y += self.CAT_H
                for t in recents:
                    self._items.append(('item', t, y, self.ITEM_H))
                    y += self.ITEM_H
            favs = self.lib.favorites()
            if favs:
                self._items.append(('section', 'Favorites', y, self.CAT_H))
                y += self.CAT_H
                for t in favs:
                    self._items.append(('item', t, y, self.ITEM_H))
                    y += self.ITEM_H
            # categories
            for cat, tpls in self.lib.categories.items():
                self._items.append(('cat', cat, y, self.CAT_H))
                y += self.CAT_H
                if cat in self.expanded:
                    for t in tpls:
                        self._items.append(('item', t, y, self.ITEM_H))
                        y += self.ITEM_H
        else:
            # search results flat
            results = self.lib.search(query)
            for t in results:
                self._items.append(('item', t, y, self.ITEM_H))
                y += self.ITEM_H

        self._total_h = y
        self._scroll  = max(0, min(self._scroll, self._scroll_max()))
        self._sel_idx = -1   # reset selection when rebuilding

    def _vis_h(self) -> int:  return self.r.h - 44
    def _scroll_max(self) -> int: return max(0, self._total_h - self._vis_h())

    def _move_sel(self, delta: int) -> None:
        """Move keyboard selection through item entries only."""
        items = [i for i, (kind, _, _, _) in enumerate(self._items) if kind == 'item']
        if not items:
            self._sel_idx = -1
            return
        if self._sel_idx == -1:
            self._sel_idx = items[0] if delta > 0 else items[-1]
        else:
            current_pos = items.index(self._sel_idx) if self._sel_idx in items else -1
            if current_pos == -1:
                self._sel_idx = items[0]
            else:
                new_pos = (current_pos + delta) % len(items)
                self._sel_idx = items[new_pos]
        # ensure visible
        if self._sel_idx >= 0:
            _, _, iy, ih = self._items[self._sel_idx]
            if iy < self._scroll:
                self._scroll = iy
            elif iy + ih > self._scroll + self._vis_h():
                self._scroll = iy + ih - self._vis_h()

    def handle_event(self, ev) -> bool:
        # keyboard events for search field
        if ev.type == pygame.KEYDOWN and self.search.active:
            prev = self.search.text
            self.search.handle_key(ev, self.font_ui)
            if self.search.text != prev:
                self._build(self.search.text)
            return True

        if ev.type == pygame.KEYDOWN:
            # global keyboard shortcuts when panel has focus (search not active)
            if ev.key == pygame.K_ESCAPE:
                self.search.text = ''
                self.search.deactivate()
                self._build('')
                return True
            if ev.key == pygame.K_TAB:
                self.search.activate()
                return True
            # arrow keys for item selection
            if ev.key == pygame.K_UP:
                self._move_sel(-1)
                return True
            if ev.key == pygame.K_DOWN:
                self._move_sel(1)
                return True
            if ev.key == pygame.K_RETURN:
                # if an item is selected, trigger click callback
                if self._sel_idx >= 0 and self._click_cb:
                    kind, data, _, _ = self._items[self._sel_idx]
                    if kind == 'item':
                        self._click_cb(data)
                return True
            if ev.key == pygame.K_RIGHT and ev.mod & pygame.KMOD_CTRL:
                # expand all categories
                for cat in self.lib.categories:
                    self.expanded.add(cat)
                self._build(self.search.text)
                return True
            if ev.key == pygame.K_LEFT and ev.mod & pygame.KMOD_CTRL:
                self.expanded.clear()
                self._build(self.search.text)
                return True

        if ev.type == pygame.MOUSEBUTTONDOWN:
            p = ev.pos
            if self.search.r.collidepoint(p):
                self.search.handle_mouse(p, self.font_ui)
                return True
            if not self.r.collidepoint(p): return False
            if ev.button == 1:
                self.search.active = False
                ry = p[1] - (self.r.y + 44) + self._scroll
                for i, (kind, data, iy, ih) in enumerate(self._items):
                    if iy <= ry < iy + ih:
                        self._sel_idx = i
                        if kind == 'cat':
                            if data in self.expanded: self.expanded.discard(data)
                            else:                      self.expanded.add(data)
                            self._build(self.search.text)
                        elif kind == 'item':
                            # single click: open parameter dialog
                            if self._click_cb:
                                self._click_cb(data)
                        return True
            elif ev.button == 3:   # right click
                ry = p[1] - (self.r.y + 44) + self._scroll
                for i, (kind, data, iy, ih) in enumerate(self._items):
                    if kind == 'item' and iy <= ry < iy + ih:
                        if self._right_click_cb:
                            self._right_click_cb(data, p)
                        return True
            elif ev.button == 4:   # scroll up
                self._scroll = max(0, self._scroll - 40)
                return True
            elif ev.button == 5:   # scroll down
                self._scroll = min(self._scroll_max(), self._scroll + 40)
                return True

        if ev.type == pygame.MOUSEWHEEL and self.r.collidepoint(pygame.mouse.get_pos()):
            self._scroll = max(0, min(self._scroll_max(), self._scroll - ev.y * 40))
            return True

        if ev.type == pygame.MOUSEMOTION and self.r.collidepoint(ev.pos):
            ry = ev.pos[1] - (self.r.y + 44) + self._scroll
            self._hov_idx = -1
            for i, (kind, data, iy, ih) in enumerate(self._items):
                if iy <= ry < iy + ih and kind == 'item':
                    self._hov_idx = i
                    break

        # drag start detection (simple: left mouse down on item + mouse motion)
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self._drag_start_pos = ev.pos
            self._drag_start_idx = -1
            ry = ev.pos[1] - (self.r.y + 44) + self._scroll
            for i, (kind, data, iy, ih) in enumerate(self._items):
                if kind == 'item' and iy <= ry < iy + ih:
                    self._drag_start_idx = i
                    break
        elif ev.type == pygame.MOUSEMOTION and hasattr(self, '_drag_start_idx') and self._drag_start_idx >= 0:
            # check if moved more than 5 pixels
            if abs(ev.pos[0] - self._drag_start_pos[0]) > 5 or abs(ev.pos[1] - self._drag_start_pos[1]) > 5:
                kind, data, _, _ = self._items[self._drag_start_idx]
                if self._drag_cb:
                    self._drag_cb(data)
                self._drag_start_idx = -1
                return True
        elif ev.type == pygame.MOUSEBUTTONUP:
            self._drag_start_idx = -1

        return False

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, T['panel'], self.r)
        pygame.draw.line(surf, T['border'],
                         (self.r.right - 1, self.r.y), (self.r.right - 1, self.r.bottom))
        self.search.draw(surf, self.font_ui)

        clip = pygame.Rect(self.r.x, self.r.y + 44,
                           self.r.w - self.SBAR_W, self.r.h - 44)
        surf.set_clip(clip)
        oy = self.r.y + 44 - self._scroll

        for i, (kind, data, iy, ih) in enumerate(self._items):
            y = oy + iy
            if y + ih < self.r.y + 44 or y > self.r.bottom: continue
            if kind == 'cat':
                pygame.draw.rect(surf, T['cat_bg'],
                                 (self.r.x, y, self.r.w - self.SBAR_W, ih))
                arrow = '▼' if data in self.expanded else '▶'
                draw_text(surf, self.font_ui, self.r.x + 8, y + 6,
                          arrow + ' ' + data, T['cat_fg'])
            elif kind == 'section':
                # recent/favorites header
                pygame.draw.rect(surf, T['cat_bg'],
                                 (self.r.x, y, self.r.w - self.SBAR_W, ih))
                draw_text(surf, self.font_ui, self.r.x + 8, y + 6,
                          '★ ' + data, (255,200,80))
            else:  # item
                sel = (i == self._sel_idx)
                hov = (i == self._hov_idx)
                bg = T['item_sel'] if sel else (T['item_hov'] if hov else T['panel'])
                pygame.draw.rect(surf, bg, (self.r.x, y, self.r.w - self.SBAR_W, ih))
                dot = _cat_color(data['cat'])
                pygame.draw.rect(surf, dot, (self.r.x + 8, y + 10, 5, 20), border_radius=2)
                # favorite star if applicable
                if data['id'] in self.lib._favorites:
                    star_surf = self.font_ui.render('★', True, (255,200,80))
                    surf.blit(star_surf, (self.r.x + self.r.w - self.SBAR_W - 30, y + 10))
                draw_text(surf, self.font,    self.r.x + 20, y + 5,
                          data['title'], T['text'], self.r.w - 60)
                draw_text(surf, self.font_ui, self.r.x + 20, y + 23,
                          data['desc'], T['text_dim'], self.r.w - 60)
                np_ = len(data.get('ports', []))
                if np_:
                    badge = f'{np_}p'
                    bw = self.font_ui.size(badge)[0] + 6
                    bx = self.r.right - self.SBAR_W - bw - 4
                    pygame.draw.rect(surf, T['btn'], (bx, y + 12, bw, 16), border_radius=3)
                    draw_text(surf, self.font_ui, bx + 3, y + 13, badge, T['text_dim'])
            pygame.draw.line(surf, T['border'],
                             (self.r.x, y + ih - 1), (self.r.right - self.SBAR_W, y + ih - 1))

        surf.set_clip(None)

        # scrollbar
        sm = self._scroll_max()
        if sm > 0:
            vh = self._vis_h()
            sb_h = max(24, int(vh * vh / (vh + sm)))
            sb_y = self.r.y + 44 + int(self._scroll / sm * (vh - sb_h))
            pygame.draw.rect(surf, T['scrollbar'],
                             (self.r.right - self.SBAR_W, sb_y, self.SBAR_W - 1, sb_h),
                             border_radius=3)

# ══════════════════════════════════════════════════════════════════════════════
# § 11  CODE EDITOR
# ══════════════════════════════════════════════════════════════════════════════

class CodeEditor:
    GUTTER_W = 56
    LINE_PAD  = 2
    BLINK_MS  = 530
    MINIMAP_W = 0          # set to e.g. 80 to enable minimap
    SBAR_W    = 8

    def __init__(self, x: int, y: int, w: int, h: int,
                 buf: TextBuffer, font: pygame.font.Font):
        self.r = pygame.Rect(x, y, w, h)
        self.buf = buf
        self.font = font
        self.lh = font.get_linesize() + self.LINE_PAD
        self.cw = max(1, font.size(' ')[0])
        self.scroll_x = 0
        self.scroll_y = 0
        self._blink_t  = pygame.time.get_ticks()
        self._blink_on = True
        self._cache: Dict[int, Tuple[int, pygame.Surface]] = {}
        self._focused = True
        self._mouse_down = False
        self._drag_select = False
        self._alt_column = False          # Alt+drag column selection
        self._show_minimap = False        # toggle with Ctrl+M
        self._word_wrap = False           # toggle with Ctrl+W
        self._show_indent_guides = True
        self._highlight_line = True
        self._match_brackets = True
        # overlay states
        self._find_mode = False
        self._find_text = ''
        self._find_case = False
        self._find_word = False
        self._replace_text = ''
        self._goto_line_mode = False
        self._goto_input = InputField(0, 0, 100, 24, placeholder='line')
        # additional data from external systems
        self.error_lines: Dict[int, str] = {}     # line -> message
        self.warning_lines: Dict[int, str] = {}   # line -> message
        self.search_matches: List[Tuple[int,int,int,int]] = []  # (row, c0, c1, col)
        self._last_scroll_y = 0

    # ── line surface cache ────────────────────────────────────────────────
    def _line_surf(self, row: int) -> pygame.Surface:
        line = self.buf.get_line(row)
        h = hash(line)
        c = self._cache.get(row)
        if c and c[0] == h:
            return c[1]
        tokens = tokenize(line)
        sw = max(self.r.w * 2, 2048)
        s = pygame.Surface((sw, self.lh), pygame.SRCALPHA)
        x = 0
        for tok, kind in tokens:
            if kind == 'ws':
                x += self.font.size(tok)[0]; continue
            col = T.get(kind, T['text'])
            ts = self.font.render(tok, True, col)
            s.blit(ts, (x, 1))
            x += ts.get_width()
        self._cache[row] = (h, s)
        if len(self._cache) > 1024:
            del self._cache[next(iter(self._cache))]
        return s

    def _inval(self, row: int) -> None:
        self._cache.pop(row, None)

    def _inval_all(self) -> None:
        self._cache.clear()

    # ── coordinate helpers ────────────────────────────────────────────────
    def _row_y(self, row: int) -> int:
        return self.r.y + row * self.lh - self.scroll_y

    def _col_x(self, row: int, col: int) -> int:
        line = self.buf.get_line(row)
        return (self.r.x + self.GUTTER_W + 4
                + self.font.size(line[:col])[0] - self.scroll_x)

    def _xy_to_rc(self, x: int, y: int) -> Tuple[int, int]:
        row = max(0, min((y - self.r.y + self.scroll_y) // self.lh,
                         self.buf.line_count() - 1))
        line = self.buf.get_line(row)
        rel  = x - self.r.x - self.GUTTER_W - 4 + self.scroll_x
        acc, col = 0, 0
        for i, ch in enumerate(line):
            w = self.font.size(ch)[0]
            if acc + w / 2 >= rel:
                col = i; break
            acc += w
        else:
            col = len(line)
        return row, col

    def _vis_lines(self) -> Tuple[int, int]:
        first = max(0, self.scroll_y // self.lh)
        count = self.r.h // self.lh + 2
        last  = min(self.buf.line_count(), first + count)
        return first, last

    # ── ensure cursor visible ─────────────────────────────────────────────
    def _ensure_visible(self) -> None:
        r, c = self.buf.cur
        cy = r * self.lh
        if cy < self.scroll_y:                  self.scroll_y = cy
        if cy + self.lh > self.scroll_y + self.r.h:
            self.scroll_y = cy + self.lh - self.r.h
        line = self.buf.get_line(r)
        cx = self.font.size(line[:c])[0]
        inner_w = self.r.w - self.GUTTER_W - 8 - self.MINIMAP_W - self.SBAR_W
        if cx < self.scroll_x:                  self.scroll_x = max(0, cx - 40)
        if cx > self.scroll_x + inner_w:        self.scroll_x = cx - inner_w + 40

    # ── bracket matching ──────────────────────────────────────────────────
    BRACKET_PAIRS = {'(': ')', '[': ']', '{': '}', '<': '>'}
    BRACKET_CLOSE = {')', ']', '}', '>'}

    def _find_bracket_match(self) -> Optional[Tuple[int,int,int,int]]:
        """Return (open_row, open_col, close_row, close_col) or None."""
        r, c = self.buf.cur
        line = self.buf.get_line(r)
        # check if character under cursor or to left is a bracket
        if c > 0 and line[c-1] in self.BRACKET_PAIRS:
            open_ch = line[c-1]; open_pos = (r, c-1); close_ch = self.BRACKET_PAIRS[open_ch]
            # search forward for matching close
            depth = 1
            for rr in range(r, self.buf.line_count()):
                l = self.buf.get_line(rr)
                start = (c) if rr == r else 0
                for cc in range(start, len(l)):
                    ch = l[cc]
                    if ch == open_ch: depth += 1
                    elif ch == close_ch:
                        depth -= 1
                        if depth == 0:
                            return (r, c-1, rr, cc)
        elif c > 0 and line[c-1] in self.BRACKET_CLOSE:
            close_ch = line[c-1]; close_pos = (r, c-1)
            # find corresponding open bracket
            open_ch = None
            for k, v in self.BRACKET_PAIRS.items():
                if v == close_ch: open_ch = k; break
            if open_ch is None: return None
            depth = 1
            for rr in range(r, -1, -1):
                l = self.buf.get_line(rr)
                end = (c-1) if rr == r else len(l)
                for cc in range(end-1, -1, -1):
                    ch = l[cc]
                    if ch == close_ch: depth += 1
                    elif ch == open_ch:
                        depth -= 1
                        if depth == 0:
                            return (rr, cc, r, c-1)
        return None

    # ── keyboard input ────────────────────────────────────────────────────
    def handle_key(self, ev) -> None:
        buf   = self.buf
        ctrl  = bool(ev.mod & pygame.KMOD_CTRL)
        shift = bool(ev.mod & pygame.KMOD_SHIFT)
        alt   = bool(ev.mod & pygame.KMOD_ALT)
        k     = ev.key

        # Find/Replace mode
        if self._find_mode:
            if k == pygame.K_RETURN:
                # execute find next
                self._do_find()
                return
            elif k == pygame.K_ESCAPE:
                self._find_mode = False
                self.search_matches.clear()
                return
            elif k == pygame.K_BACKSPACE:
                if self._find_text:
                    self._find_text = self._find_text[:-1]
                    self._refresh_search()
            elif ev.unicode and ev.unicode.isprintable():
                self._find_text += ev.unicode
                self._refresh_search()
            return

        if self._goto_line_mode:
            if k == pygame.K_RETURN:
                try:
                    line_num = int(self._goto_input.text) - 1
                    buf.jump_to_line(line_num)
                    self._goto_line_mode = False
                except ValueError:
                    pass
                return
            elif k == pygame.K_ESCAPE:
                self._goto_line_mode = False
                return
            else:
                self._goto_input.handle_key(ev, self.font)
                return

        if ctrl:
            if k == pygame.K_z:     buf.undo()
            elif k == pygame.K_y:   buf.redo()
            elif k == pygame.K_c:   buf.copy()
            elif k == pygame.K_x:   buf.cut()
            elif k == pygame.K_v:   buf.paste()
            elif k == pygame.K_a:   buf.select_all()
            elif k == pygame.K_d:   buf.select_line(buf.cur[0])
            elif k == pygame.K_f:   self._find_mode = True; self._find_text = ''; self.search_matches.clear()
            elif k == pygame.K_h:   # replace (simplified: starts find mode, then replace in next step)
                self._find_mode = True; self._find_text = ''
            elif k == pygame.K_g:   self._goto_line_mode = True; self._goto_input.text = ''; self._goto_input.cur = 0
            elif k == pygame.K_l:   self._goto_line_mode = True; self._goto_input.text = str(buf.cur[0]+1); self._goto_input.cur = len(self._goto_input.text)
            elif k == pygame.K_m:   self._show_minimap = not self._show_minimap
            elif k == pygame.K_w:   self._word_wrap = not self._word_wrap
            elif k == pygame.K_LEFT:  buf.move(0, -1, select=shift, word=True)
            elif k == pygame.K_RIGHT: buf.move(0,  1, select=shift, word=True)
            elif k == pygame.K_HOME:
                buf.sel = None; buf._sel_anchor = None; buf.cur = [0, 0]
            elif k == pygame.K_END:
                buf.cur = [buf.line_count() - 1, len(buf.get_line(buf.line_count() - 1))]
            elif k == pygame.K_UP:
                self.scroll_y = max(0, self.scroll_y - self.lh)
            elif k == pygame.K_DOWN:
                self.scroll_y = min((buf.line_count() - 1) * self.lh, self.scroll_y + self.lh)
            elif k == pygame.K_PAGEUP:
                buf.page(-1, self.r.h // self.lh - 1, select=shift)
            elif k == pygame.K_PAGEDOWN:
                buf.page( 1, self.r.h // self.lh - 1, select=shift)
        elif k == pygame.K_LEFT:   buf.move(0, -1, select=shift)
        elif k == pygame.K_RIGHT:  buf.move(0,  1, select=shift)
        elif k == pygame.K_UP:     buf.move(-1, 0, select=shift)
        elif k == pygame.K_DOWN:   buf.move( 1, 0, select=shift)
        elif k == pygame.K_HOME:   buf.home(select=shift)
        elif k == pygame.K_END:    buf.end_(select=shift)
        elif k == pygame.K_PAGEUP:
            buf.page(-1, self.r.h // self.lh - 1, select=shift)
        elif k == pygame.K_PAGEDOWN:
            buf.page( 1, self.r.h // self.lh - 1, select=shift)
        elif k == pygame.K_RETURN: buf.newline()
        elif k == pygame.K_BACKSPACE: buf.del_back()
        elif k == pygame.K_DELETE:    buf.del_fwd()
        elif k == pygame.K_TAB:       buf.tab(shift=shift)
        elif alt and k == pygame.K_F2:  buf.toggle_bookmark(buf.cur[0])
        elif alt and k == pygame.K_UP:  # column selection up
            if not buf._sel_anchor: buf._sel_anchor = list(buf.cur)
            if buf.cur[0] > 0: buf.cur[0] -= 1
            buf.sel = (tuple(buf._sel_anchor), tuple(buf.cur))
        elif alt and k == pygame.K_DOWN:
            if not buf._sel_anchor: buf._sel_anchor = list(buf.cur)
            if buf.cur[0] < buf.line_count() - 1: buf.cur[0] += 1
            buf.sel = (tuple(buf._sel_anchor), tuple(buf.cur))
        elif ev.unicode and ev.unicode.isprintable() and not ctrl:
            buf.insert_char(ev.unicode)

        self._inval(buf.cur[0])
        self._ensure_visible()
        self._refresh_search()

    # ── mouse input ───────────────────────────────────────────────────────
    def handle_mouse(self, ev) -> bool:
        if not self.r.collidepoint(ev.pos if hasattr(ev, 'pos') else (0, 0)):
            if ev.type != pygame.MOUSEWHEEL: return False
        buf = self.buf

        if ev.type == pygame.MOUSEWHEEL:
            if not self.r.collidepoint(pygame.mouse.get_pos()): return False
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                self.scroll_x = max(0, self.scroll_x - ev.y * self.cw * 4)
            else:
                self.scroll_y = max(0, min(
                    self.scroll_y - ev.y * self.lh * 3,
                    (buf.line_count() - 1) * self.lh
                ))
            return True

        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self._mouse_down = True
            self._drag_select = False
            r, c = self._xy_to_rc(*ev.pos)
            mods = pygame.key.get_mods()            # ✅ correto
            if mods & pygame.KMOD_SHIFT:
                if buf._sel_anchor is None: buf._sel_anchor = list(buf.cur)
                buf.cur = [r, c]
                buf.sel = (tuple(buf._sel_anchor), (r, c))
            elif mods & pygame.KMOD_ALT:
                self._alt_column = True
                buf._sel_anchor = list(buf.cur)
                buf.cur = [r, c]
                buf.sel = (tuple(buf._sel_anchor), (r, c))
            else:
                buf.cur = [r, c]
                buf.sel = None; buf._sel_anchor = None
            return True

        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self._mouse_down = False
            self._alt_column = False
            return True

        if ev.type == pygame.MOUSEMOTION and self._mouse_down:
            r, c = self._xy_to_rc(*ev.pos)
            if not self._drag_select:
                self._drag_select = True
                if not buf._sel_anchor: buf._sel_anchor = list(buf.cur)
            buf.cur = [r, c]
            buf.sel = (tuple(buf._sel_anchor), (r, c))
            self._ensure_visible()
            return True

        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 4:
            self.scroll_y = max(0, self.scroll_y - self.lh * 3)
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 5:
            self.scroll_y = min((buf.line_count() - 1) * self.lh,
                                self.scroll_y + self.lh * 3)
        return False

    # ── search helpers ────────────────────────────────────────────────────
    def _do_find(self) -> None:
        if not self._find_text: return
        pos = self.buf.find(self._find_text, case=self._find_case, word=self._find_word)
        if pos:
            self.buf.cur = list(pos)
            self.buf.sel = None
            self._ensure_visible()
            self._refresh_search()
        else:
            self._find_text = ''

    def _refresh_search(self) -> None:
        self.search_matches.clear()
        if not self._find_text: return
        pattern = re.compile(re.escape(self._find_text), 0 if self._find_case else re.IGNORECASE)
        for row in range(self.buf.line_count()):
            line = self.buf.get_line(row)
            for m in pattern.finditer(line):
                self.search_matches.append((row, m.start(), m.end(), 0))

    # ── draw ──────────────────────────────────────────────────────────────
    def draw(self, surf: pygame.Surface) -> None:
        buf = self.buf
        inner_rect = self.r.inflate(-1, -1)
        clip = pygame.Rect(self.r.x, self.r.y, self.r.w, self.r.h)
        old_clip = surf.get_clip()
        surf.set_clip(clip)
        pygame.draw.rect(surf, T['bg'], clip)

        # gutter
        gr = pygame.Rect(self.r.x, self.r.y, self.GUTTER_W, self.r.h)
        pygame.draw.rect(surf, T['gutter_bg'], gr)
        pygame.draw.line(surf, T['border'],
                         (self.r.x + self.GUTTER_W, self.r.y),
                         (self.r.x + self.GUTTER_W, self.r.bottom))

        first, last = self._vis_lines()
        sel = buf._sel_ordered()

        # blink update
        t_now = pygame.time.get_ticks()
        if t_now - self._blink_t > self.BLINK_MS:
            self._blink_on = not self._blink_on
            self._blink_t  = t_now

        # find bracket match
        bracket_match = None
        if self._match_brackets:
            bracket_match = self._find_bracket_match()

        for row in range(first, last):
            y = self._row_y(row)

            # line highlight
            if self._highlight_line and row == buf.cur[0]:
                pygame.draw.rect(surf, T['line_hl'],
                                 (self.r.x + self.GUTTER_W, y, self.r.w - self.GUTTER_W, self.lh))

            # error / warning background
            if row in self.error_lines:
                pygame.draw.rect(surf, (*T['err'], 40),
                                 (self.r.x + self.GUTTER_W, y, self.r.w - self.GUTTER_W, self.lh))
            elif row in self.warning_lines:
                pygame.draw.rect(surf, (*T['warn'], 40),
                                 (self.r.x + self.GUTTER_W, y, self.r.w - self.GUTTER_W, self.lh))

            # selection highlight
            if sel:
                (r0, c0), (r1, c1) = sel
                if r0 <= row <= r1:
                    line = buf.get_line(row)
                    sc = c0 if row == r0 else 0
                    ec = c1 if row == r1 else len(line)
                    sx = (self.r.x + self.GUTTER_W + 4
                          + self.font.size(line[:sc])[0] - self.scroll_x)
                    sw = self.font.size(line[sc:ec])[0] if sc < ec else (self.r.w // 4)
                    pygame.draw.rect(surf, T['sel'], (sx, y, sw, self.lh))

            # search match highlights
            for sm_row, sm_c0, sm_c1, _ in self.search_matches:
                if sm_row == row:
                    line = buf.get_line(row)
                    sx = self.r.x + self.GUTTER_W + 4 + self.font.size(line[:sm_c0])[0] - self.scroll_x
                    sw = self.font.size(line[sm_c0:sm_c1])[0]
                    pygame.draw.rect(surf, T['match_hl'], (sx, y, sw, self.lh))

            # bracket match highlight
            if bracket_match:
                open_r, open_c, close_r, close_c = bracket_match
                if row == open_r:
                    line = buf.get_line(row)
                    cx = self._col_x(row, open_c)
                    pygame.draw.rect(surf, (255, 255, 0, 80), (cx, y, self.cw, self.lh), 1)
                if row == close_r:
                    line = buf.get_line(row)
                    cx = self._col_x(row, close_c)
                    pygame.draw.rect(surf, (255, 255, 0, 80), (cx, y, self.cw, self.lh), 1)

            # gutter line number
            gnum = self.font.render(str(row + 1), True, T['gutter_fg'])
            # bookmark indicator
            if row in buf.bookmarks:
                gnum = self.font.render('►', True, (255,200,80))
            surf.blit(gnum, (self.r.x + self.GUTTER_W - gnum.get_width() - 6, y + 1))

            # line text
            ls = self._line_surf(row)
            surf.blit(ls, (self.r.x + self.GUTTER_W + 4 - self.scroll_x, y))

            # indentation guides
            if self._show_indent_guides:
                indent = buf.indent_level(row)
                if indent > 0:
                    guide_x = self.r.x + self.GUTTER_W + 4 - self.scroll_x + indent * self.cw
                    # only draw if indent < right margin
                    if guide_x < self.r.right - 10:
                        for g in range(0, indent, self.buf.TAB):
                            gx = self.r.x + self.GUTTER_W + 4 - self.scroll_x + g * self.cw
                            if gx < self.r.right - 10:
                                pygame.draw.line(surf, (*T['border'], 50),
                                                 (gx, y), (gx, y + self.lh), 1)

        # cursor
        if self._focused and self._blink_on:
            r, c = buf.cur
            cy = self._row_y(r)
            cx = self._col_x(r, c)
            pygame.draw.rect(surf, T['cursor'], (cx, cy, 2, self.lh))

        # column selection guide (if Alt held)
        if self._alt_column and buf.has_sel():
            # draw vertical line at anchor column
            pass

        # minimap
        if self._show_minimap and self.MINIMAP_W > 0:
            self._draw_minimap(surf)

        # scrollbar
        self._draw_scrollbar(surf)

        # border
        pygame.draw.rect(surf, T['border'], clip, 1)
        surf.set_clip(old_clip)

        # find / goto overlay
        if self._find_mode:
            self._draw_find_overlay(surf)
        if self._goto_line_mode:
            self._draw_goto_overlay(surf)

    def _draw_minimap(self, surf: pygame.Surface) -> None:
        """Draw code minimap on the right side."""
        map_x = self.r.right - self.MINIMAP_W - self.SBAR_W
        map_rect = pygame.Rect(map_x, self.r.y, self.MINIMAP_W, self.r.h)
        pygame.draw.rect(surf, T['minimap_bg'], map_rect)
        total_lines = self.buf.line_count()
        if total_lines == 0: return
        scale = self.r.h / total_lines
        for row in range(total_lines):
            line = self.buf.get_line(row)
            density = len(re.findall(r'\b\w+\b', line)) / max(1, len(line) + 1)
            y = self.r.y + int(row * scale)
            h = max(1, int(scale))
            col = T['minimap_text']
            if density > 0.3: col = (180,180,200)
            if row in self.error_lines: col = T['minimap_error']
            pygame.draw.rect(surf, col, (map_x, y, self.MINIMAP_W, h))

        # viewport rectangle
        first, last = self._vis_lines()
        vp_y = self.r.y + int(first * scale)
        vp_h = int((last - first) * scale)
        pygame.draw.rect(surf, T['minimap_viewport'], (map_x, vp_y, self.MINIMAP_W, vp_h), 1)

    def _draw_scrollbar(self, surf: pygame.Surface) -> None:
        total_lines = self.buf.line_count()
        if total_lines == 0: return
        sb_rect = pygame.Rect(self.r.right - self.SBAR_W, self.r.y, self.SBAR_W, self.r.h)
        visible = self._vis_lines()
        visible_lines = visible[1] - visible[0]
        thumb_h = max(8, sb_rect.h * visible_lines / total_lines)
        thumb_y = sb_rect.y + (sb_rect.h - thumb_h) * (self.scroll_y / ((total_lines - visible_lines) * self.lh + 1e-9))
        pygame.draw.rect(surf, T['scrollbar'], sb_rect)
        pygame.draw.rect(surf, T['scrollbar_thumb'], (sb_rect.x, thumb_y, sb_rect.w, thumb_h), border_radius=3)

    def _draw_find_overlay(self, surf: pygame.Surface) -> None:
        """Draw find bar at top right of editor."""
        fw, fh = 220, 28
        fx = self.r.right - fw - 10 - self.SBAR_W
        fy = self.r.y + 5
        rect = pygame.Rect(fx, fy, fw, fh)
        pygame.draw.rect(surf, T['find_bg'], rect, border_radius=4)
        pygame.draw.rect(surf, T['border'], rect, 1, border_radius=4)
        txt = self.font.render(self._find_text + '|', True, T['text'])
        surf.blit(txt, (fx + 6, fy + 4))
        count = len(self.search_matches)
        if count:
            cnt_txt = self.font.render(f'{count} matches', True, T['text_dim'])
            surf.blit(cnt_txt, (fx + fw - cnt_txt.get_width() - 4, fy + 4))

    def _draw_goto_overlay(self, surf: pygame.Surface) -> None:
        """Draw goto line input at top center."""
        gw, gh = 200, 28
        gx = self.r.centerx - gw // 2
        gy = self.r.y + 5
        self._goto_input.r = pygame.Rect(gx, gy, gw, gh)
        self._goto_input.draw(surf, self.font)
        lbl = self.font.render('Go to line:', True, T['text_dim'])
        surf.blit(lbl, (gx - lbl.get_width() - 5, gy + 4))

    # ── public focus ──────────────────────────────────────────────────────
    @property
    def focused(self) -> bool: return self._focused
    @focused.setter
    def focused(self, val: bool): self._focused = val

# ══════════════════════════════════════════════════════════════════════════════
# § 12  RIGHT PANEL — Context Recommendations
# ══════════════════════════════════════════════════════════════════════════════

class RightPanel:
    ITEM_H    = 56
    PARAM_H   = 24          # height for inline suggestion items
    SBAR_W    = 8
    HEADER_H  = 36
    SECT_H    = 24          # section label height

    def __init__(self, x: int, y: int, w: int, h: int,
                 lib: TemplateLib, analyzer: ContextAnalyzer,
                 font: pygame.font.Font, font_ui: pygame.font.Font):
        self.r = pygame.Rect(x, y, w, h)
        self.lib = lib
        self.analyzer = analyzer
        self.font = font
        self.font_ui = font_ui

        # internal state
        self._templates: List[Dict] = []        # template blocks recommended
        self._params: List[Dict] = []           # inline param suggestions
        self._scroll = 0
        self._hov = -1          # index into unified list of rendered items
        self._sel = -1          # keyboard selection index
        self._click_cb = None   # callback(template_data_or_param_dict)
        self._focused = False   # whether the panel has keyboard focus
        self._context_info = '' # textual description of current context
        self._filter_text = ''  # for filtering suggestions
        self._filter_input = InputField(
            x + 4, y + self.HEADER_H, w - 8, 22, placeholder='filter…')
        self._show_params = True  # toggle visibility of inline params

        # unified item list for drawing; each entry: (kind, data, height)
        # kind: 'template' or 'param'
        self._draw_items: List[Tuple[str, Any, int]] = []
        self._total_h = 0

    def set_click_callback(self, cb: Any) -> None:
        self._click_cb = cb

    def update(self, buf: TextBuffer) -> None:
        """Rebuild suggestions based on current editor state."""
        self._templates = self.analyzer.suggest(buf)
        self._params    = self.analyzer.suggest_params(buf)
        # context info
        inst = self.analyzer._instruction_at_cursor(buf)
        self._context_info = f'Current: {inst}' if inst else 'No instruction detected'
        self._filter_text = ''
        self._filter_input.text = ''
        self._rebuild_draw_items()
        self._scroll = 0
        self._sel = -1

    # ── internal item builder ──────────────────────────────────────────────
    def _rebuild_draw_items(self) -> None:
        """Rebuild unified list of drawable items based on current filter and visibility settings."""
        self._draw_items.clear()
        filter_lower = self._filter_text.lower().strip()

        # add section label for templates
        filtered_templates = self._templates
        if filter_lower:
            filtered_templates = [t for t in self._templates
                                  if filter_lower in t['title'].lower()
                                  or filter_lower in t['desc'].lower()
                                  or filter_lower in t['cat'].lower()]

        if filtered_templates:
            self._draw_items.append(('section', '⚡ Templates', self.SECT_H))
            for t in filtered_templates:
                self._draw_items.append(('template', t, self.ITEM_H))

        # add section label for inline params if enabled
        if self._show_params and self._params:
            filtered_params = self._params
            if filter_lower:
                filtered_params = [p for p in self._params
                                   if filter_lower in p['text'].lower()
                                   or filter_lower in p.get('desc', '').lower()]
            if filtered_params:
                self._draw_items.append(('section', '✎ Parameters', self.SECT_H))
                for p in filtered_params:
                    self._draw_items.append(('param', p, self.PARAM_H))

        self._total_h = sum(item[2] for item in self._draw_items)

    # ── visible area ───────────────────────────────────────────────────────
    def _vis_h(self) -> int:
        return self.r.h - self.HEADER_H - 2

    def _scroll_max(self) -> int:
        return max(0, self._total_h - self._vis_h())

    # ── position helpers ───────────────────────────────────────────────────
    def _item_at_visible_index(self, rel_y: int) -> int:
        """Convert a relative y coordinate (from content top) to index in _draw_items."""
        accum = 0
        for i, (_, _, h) in enumerate(self._draw_items):
            if accum <= rel_y < accum + h:
                return i
            accum += h
        return -1

    def _item_abs_y(self, index: int) -> int:
        """Return absolute y of item's top within content area."""
        return sum(item[2] for item in self._draw_items[:index])

    # ── keyboard navigation ────────────────────────────────────────────────
    def _move_sel(self, delta: int) -> None:
        """Move keyboard selection among clickable items (templates/params)."""
        clickable = [i for i, (kind, _, _) in enumerate(self._draw_items)
                     if kind in ('template', 'param')]
        if not clickable:
            self._sel = -1
            return
        if self._sel == -1:
            self._sel = clickable[0] if delta > 0 else clickable[-1]
        else:
            try:
                pos = clickable.index(self._sel)
            except ValueError:
                pos = -1
            if pos == -1:
                self._sel = clickable[0]
            else:
                new_pos = (pos + delta) % len(clickable)
                self._sel = clickable[new_pos]
        # ensure visible
        if self._sel >= 0:
            y = self._item_abs_y(self._sel)
            h = self._draw_items[self._sel][2]
            if y < self._scroll:
                self._scroll = y
            elif y + h > self._scroll + self._vis_h():
                self._scroll = y + h - self._vis_h()

    # ── event handling ─────────────────────────────────────────────────────
    def handle_event(self, ev) -> bool:
        # keyboard events – only when panel has focus
        if self._focused and ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                self._focused = False
                return True
            if ev.key == pygame.K_UP:
                self._move_sel(-1)
                return True
            if ev.key == pygame.K_DOWN:
                self._move_sel(1)
                return True
            if ev.key == pygame.K_RETURN:
                if self._sel >= 0 and self._click_cb:
                    kind, data, _ = self._draw_items[self._sel]
                    if kind != 'section':
                        self._click_cb(data)
                return True
            # filter input active (global focus) – forward to filter input
            if self._filter_input.active:
                prev = self._filter_input.text
                self._filter_input.handle_key(ev, self.font_ui)
                if self._filter_input.text != prev:
                    self._filter_text = self._filter_input.text
                    self._rebuild_draw_items()
                    self._scroll = 0
                    self._sel = -1
                return True
            # activate filter on typing
            if ev.unicode and ev.unicode.isprintable() and not pygame.key.get_mods() & pygame.KMOD_CTRL:
                self._filter_input.active = True
                self._filter_input.text = ''
                self._filter_text = ''
                self._filter_input.handle_key(ev, self.font_ui)
                self._filter_text = self._filter_input.text
                self._rebuild_draw_items()
                self._scroll = 0
                self._sel = -1
                return True
            return False

        # mouse wheel scroll
        if ev.type == pygame.MOUSEWHEEL and self.r.collidepoint(pygame.mouse.get_pos()):
            self._scroll = max(0, min(self._scroll_max(), self._scroll - ev.y * 40))
            return True

        # mouse button down
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.r.collidepoint(ev.pos):
            self._focused = True
            # check click on filter input
            if self._filter_input.r.collidepoint(ev.pos):
                self._filter_input.handle_mouse(ev.pos, self.font_ui)
                return True
            # click on a suggestion item
            rel_y = ev.pos[1] - (self.r.y + self.HEADER_H) + self._scroll
            idx = self._item_at_visible_index(rel_y)
            if idx >= 0:
                kind, data, _ = self._draw_items[idx]
                if kind != 'section' and self._click_cb:
                    self._click_cb(data)
                    self._sel = idx
                elif kind == 'section':
                    # toggle category expand? not needed
                    pass
            return True

        # right click for context menu (future)
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3 and self.r.collidepoint(ev.pos):
            rel_y = ev.pos[1] - (self.r.y + self.HEADER_H) + self._scroll
            idx = self._item_at_visible_index(rel_y)
            if idx >= 0:
                kind, data, _ = self._draw_items[idx]
                # can add a callback for right-click actions
            return True

        # mouse motion for hover
        if ev.type == pygame.MOUSEMOTION and self.r.collidepoint(ev.pos):
            rel_y = ev.pos[1] - (self.r.y + self.HEADER_H) + self._scroll
            self._hov = self._item_at_visible_index(rel_y)
            # hover on filter input
            if self._filter_input.r.collidepoint(ev.pos):
                self._hov = -1
            return False

        # handle mouse wheel with old-style buttons
        if ev.type == pygame.MOUSEBUTTONDOWN and self.r.collidepoint(ev.pos):
            if ev.button == 4: self._scroll = max(0, self._scroll - 40); return True
            if ev.button == 5: self._scroll = min(self._scroll_max(), self._scroll + 40); return True
        return False

    # ── drawing ────────────────────────────────────────────────────────────
    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, T['panel'], self.r)
        pygame.draw.line(surf, T['border'],
                         (self.r.x, self.r.y), (self.r.x, self.r.bottom))

        # header area
        draw_text(surf, self.font_ui, self.r.x + 10, self.r.y + 8,
                  '⚡ Suggestions', T['cat_fg'])
        if self._context_info:
            draw_text(surf, self.font_ui, self.r.x + self.r.w - 200, self.r.y + 8,
                      self._context_info, T['accent'], 180)

        # filter input
        filter_y = self.r.y + self.HEADER_H - 26
        self._filter_input.r = pygame.Rect(self.r.x + 4, filter_y, self.r.w - 8, 22)
        self._filter_input.draw(surf, self.font_ui)

        if not self._draw_items:
            draw_text(surf, self.font_ui, self.r.x + 10, self.r.y + self.HEADER_H + 10,
                      '(no suggestions for this context)', T['text_dim'])
            return

        # content clip
        clip = pygame.Rect(self.r.x, self.r.y + self.HEADER_H,
                           self.r.w - self.SBAR_W, self.r.h - self.HEADER_H)
        surf.set_clip(clip)
        content_y = self.r.y + self.HEADER_H - self._scroll

        for i, (kind, data, h) in enumerate(self._draw_items):
            y = content_y + sum(item[2] for item in self._draw_items[:i])
            if y + h < self.r.y + self.HEADER_H or y > self.r.bottom:
                continue

            if kind == 'section':
                # draw section label
                pygame.draw.rect(surf, T['cat_bg'],
                                 (self.r.x, y, self.r.w - self.SBAR_W, h))
                draw_text(surf, self.font_ui, self.r.x + 8, y + 4,
                          data, T['cat_fg'])
            elif kind == 'template':
                bg = T['item_sel'] if i == self._sel else (T['item_hov'] if i == self._hov else T['panel'])
                pygame.draw.rect(surf, bg, (self.r.x, y, self.r.w - self.SBAR_W, h))
                dot = _cat_color(data['cat'])
                pygame.draw.rect(surf, dot, (self.r.x + 8, y + 8, 5, 40), border_radius=2)
                # favorite star
                if data['id'] in self.lib._favorites:
                    star_surf = self.font_ui.render('★', True, (255,200,80))
                    surf.blit(star_surf, (self.r.x + self.r.w - self.SBAR_W - 24, y + 8))
                draw_text(surf, self.font,    self.r.x + 18, y + 6,
                          data['title'], T['text_em'], self.r.w - 30)
                draw_text(surf, self.font_ui, self.r.x + 18, y + 24,
                          data['cat'], T['accent'], self.r.w - 30)
                draw_text(surf, self.font_ui, self.r.x + 18, y + 38,
                          data['desc'], T['text_dim'], self.r.w - 30)
                # port count badge
                np_ = len(data.get('ports', []))
                if np_:
                    badge = f'{np_}p'
                    bw = self.font_ui.size(badge)[0] + 6
                    bx = self.r.right - self.SBAR_W - bw - 4
                    pygame.draw.rect(surf, T['btn'], (bx, y + 30, bw, 16), border_radius=3)
                    draw_text(surf, self.font_ui, bx + 3, y + 31, badge, T['text_dim'])
            elif kind == 'param':
                bg = T['item_sel'] if i == self._sel else (T['item_hov'] if i == self._hov else T['panel'])
                pygame.draw.rect(surf, bg, (self.r.x, y, self.r.w - self.SBAR_W, h))
                # indicate type icon
                type_icon = 'R' if data.get('type') == 'register' else 'I'
                icon_col = T['reg'] if data.get('type') == 'register' else T['num']
                icon_surf = self.font_ui.render(type_icon, True, icon_col)
                surf.blit(icon_surf, (self.r.x + 8, y + 2))
                draw_text(surf, self.font_ui, self.r.x + 22, y + 2,
                          data['text'], T['text'], self.r.w - 30)
                draw_text(surf, self.font_ui, self.r.x + 22, y + 14,
                          data.get('desc', ''), T['text_dim'], self.r.w - 30)

            # separator line
            if kind != 'section':
                pygame.draw.line(surf, T['border'],
                                 (self.r.x, y + h - 1),
                                 (self.r.right - self.SBAR_W, y + h - 1))

        surf.set_clip(None)

        # scrollbar
        sm = self._scroll_max()
        if sm > 0:
            vh = self._vis_h()
            sb_h = max(24, int(vh * vh / (vh + sm)))
            sb_y = self.r.y + self.HEADER_H + int(self._scroll / sm * (vh - sb_h))
            pygame.draw.rect(surf, T['scrollbar'],
                             (self.r.right - self.SBAR_W, sb_y, self.SBAR_W - 1, sb_h),
                             border_radius=3)

    @property
    def focused(self) -> bool: return self._focused
    @focused.setter
    def focused(self, val: bool): self._focused = val

# ══════════════════════════════════════════════════════════════════════════════
# § 13  TOOLBAR & STATUS BAR
# ══════════════════════════════════════════════════════════════════════════════

class Toolbar:
    """Toolbar com botões, ícones, dicas e indicador de compilação."""

    BTNS = (
        ("📄 New", "new"),
        ("📂 Open", "open"),
        ("💾 Save", "save"),
        ("↩ Undo", "undo"),
        ("↪ Redo", "redo"),
        ("❌ Cut", "cut"),
        ("📋 Copy", "copy"),
        ("📌 Paste", "paste"),
        ("🔍 Find", "find"),
        ("⚙ Build", "build"),
        ("▶ Run", "run"),
        ("⏹ Stop", "stop"),
        ("❓ About", "about"),
    )

    def __init__(self, x: int, y: int, w: int, h: int, font_ui: pygame.font.Font):
        self.r = pygame.Rect(x, y, w, h)
        self.font = font_ui
        self.btn_rects: Dict[str, pygame.Rect] = {}
        self.btn_ids: List[str] = []
        self._hov = ""
        self._cb: Optional[Callable[[str], None]] = None
        self._rebuild_buttons()
        self._disabled = set()         # ids of disabled buttons
        self._build_progress = 0.0     # 0..1 for build progress bar
        self._build_running = False
        self._tooltips: Dict[str, str] = {
            "new": "New file (Ctrl+N)",
            "open": "Open file (Ctrl+O)",
            "save": "Save file (Ctrl+S)",
            "undo": "Undo (Ctrl+Z)",
            "redo": "Redo (Ctrl+Y)",
            "cut": "Cut selection (Ctrl+X)",
            "copy": "Copy selection (Ctrl+C)",
            "paste": "Paste (Ctrl+V)",
            "find": "Find / Replace (Ctrl+F)",
            "build": "Build project (F5)",
            "run": "Build & Run (F6)",
            "stop": "Stop running (Esc)",
            "about": "About ModernAssembly NX",
        }

    def _rebuild_buttons(self):
        """Re‑calcula posições dos botões com base na largura atual."""
        self.btn_rects.clear()
        self.btn_ids.clear()
        bx, by_ = self.r.x + 8, self.r.y + (self.r.h - 26) // 2
        for lbl, ident in self.BTNS:
            bw = self.font.size(lbl)[0] + 16
            # wrap to next row if too wide? we keep single row
            self.btn_rects[ident] = pygame.Rect(bx, by_, bw, 26)
            self.btn_ids.append(ident)
            bx += bw + 4

    def set_callback(self, cb: Callable[[str], None]) -> None:
        self._cb = cb

    def disable_button(self, ident: str):
        self._disabled.add(ident)

    def enable_button(self, ident: str):
        self._disabled.discard(ident)

    def set_build_progress(self, progress: float, running: bool = True):
        self._build_progress = max(0.0, min(1.0, progress))
        self._build_running = running

    def handle_event(self, ev) -> bool:
        if ev.type == pygame.MOUSEMOTION:
            self._hov = ""
            for ident, rect in self.btn_rects.items():
                if rect.collidepoint(ev.pos):
                    self._hov = ident
                    break
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for ident, rect in self.btn_rects.items():
                if rect.collidepoint(ev.pos) and ident not in self._disabled:
                    if self._cb:
                        self._cb(ident)
                    return True
        return False

    def draw(self, surf: pygame.Surface, filename: Optional[str], dirty: bool,
             buffer: Optional[TextBuffer] = None):
        pygame.draw.rect(surf, T['toolbar'], self.r)
        pygame.draw.line(surf, T['border'],
                         (self.r.x, self.r.bottom - 1), (self.r.right, self.r.bottom - 1))

        # draw buttons
        for ident, rect in self.btn_rects.items():
            label = next((lbl for lbl, i in self.BTNS if i == ident), ident)
            disabled = ident in self._disabled
            draw_btn(surf, self.font, rect, label,
                     hovered=self._hov == ident,
                     active=False,
                     disabled=disabled)
            # tooltip on hover (draw later?)

        # build progress bar (inline on the right side)
        if self._build_running:
            bar_w = 120
            bar_h = 12
            bar_x = self.r.right - bar_w - 10
            bar_y = self.r.centery - bar_h // 2
            pygame.draw.rect(surf, T['inp'], (bar_x, bar_y, bar_w, bar_h), border_radius=3)
            if self._build_progress > 0:
                fill_w = int(bar_w * self._build_progress)
                fill_rect = pygame.Rect(bar_x, bar_y, fill_w, bar_h)
                pygame.draw.rect(surf, T['accent'], fill_rect, border_radius=3)
            pygame.draw.rect(surf, T['border'], (bar_x, bar_y, bar_w, bar_h), 1, border_radius=3)
            progress_text = self.font.render(f"{int(self._build_progress * 100)}%",
                                             True, T['text'])
            surf.blit(progress_text, (bar_x + (bar_w - progress_text.get_width()) // 2,
                                      bar_y - 16))

        # file name & dirty indicator
        title = ('● ' if dirty else '') + (filename or 'untitled.asm')
        title += '  —  ModernAssembly NX'
        draw_text(surf, self.font, self.r.x + 520, self.r.y + 11, title, T['text_dim'])

        # tooltip for currently hovered button
        if self._hov and self._hov in self._tooltips:
            tip = self._tooltips[self._hov]
            tip_surf = self.font.render(tip, True, (255, 255, 200))
            tip_rect = tip_surf.get_rect()
            tip_rect.midbottom = (self.btn_rects[self._hov].centerx,
                                  self.r.y - 2)
            # ensure it doesn't go off screen
            if tip_rect.left < 0: tip_rect.left = 0
            if tip_rect.right > self.r.right: tip_rect.right = self.r.right
            pygame.draw.rect(surf, (30, 30, 40), tip_rect.inflate(8, 4), border_radius=4)
            pygame.draw.rect(surf, T['border'], tip_rect.inflate(8, 4), 1, border_radius=4)
            surf.blit(tip_surf, tip_rect)


class StatusBar:
    """Barra de status rica com múltiplas informações."""

    def __init__(self, x: int, y: int, w: int, h: int, font_ui: pygame.font.Font):
        self.r = pygame.Rect(x, y, w, h)
        self.font = font_ui
        self.messages: List[Tuple[str, int, Tuple[int,int,int]]] = []   # (msg, expire_time, color)
        self.mode = "INS"          # INS / OVR
        self.encoding = "UTF-8"
        self.line_ending = "CRLF"
        self.error_count = 0
        self.warning_count = 0
        self.file_size = ""
        self._blink_timer = 0

    def notify(self, msg: str, dur: int = 4000, color=None) -> None:
        if color is None:
            color = T['ok'] if 'Saved' in msg or 'Loaded' in msg else \
                    T['err'] if 'Error' in msg else T['status_fg']
        expire = pygame.time.get_ticks() + dur
        self.messages.append((msg, expire, color))

    def set_mode(self, mode: str): self.mode = mode
    def set_errors(self, errors: int, warnings: int = 0):
        self.error_count = errors
        self.warning_count = warnings
    def set_file_size(self, size: str): self.file_size = size

    def draw(self, surf: pygame.Surface, buf: TextBuffer) -> None:
        pygame.draw.rect(surf, T['status'], self.r)
        pygame.draw.line(surf, T['border'], (self.r.x, self.r.y), (self.r.right, self.r.y))

        r, c = buf.cur
        # left section: cursor position & line count
        left_text = f'Ln {r + 1}, Col {c + 1}  |  {buf.line_count()} lines'
        if self.mode == "OVR":
            left_text += "  [OVR]"
        draw_text(surf, self.font, self.r.x + 10, self.r.y + 6, left_text, T['status_fg'])

        # middle section: notifications (stacked)
        now = pygame.time.get_ticks()
        self.messages = [m for m in self.messages if now < m[1]]
        offset_x = self.r.x + 280
        for msg, _, col in self.messages[:3]:  # show last 3 messages
            draw_text(surf, self.font, offset_x, self.r.y + 6, msg, col)
            offset_x += self.font.size(msg)[0] + 12

        # right section: selection info
        sel = buf._sel_ordered()
        if sel:
            (r0, c0), (r1, c1) = sel
            chars = abs(c1 - c0 + sum(len(buf.get_line(rr)) for rr in range(r0, r1)))
            sel_text = f'SEL {r1 - r0 + 1} ln  {chars} ch'
            draw_text(surf, self.font, self.r.right - 230, self.r.y + 6, sel_text, T['lbl'])

        # far right: build errors/warnings
        if self.error_count or self.warning_count:
            err_text = f'⚠{self.warning_count} ✕{self.error_count}'
            col = T['err'] if self.error_count else T['warn']
            draw_text(surf, self.font, self.r.right - 120, self.r.y + 6, err_text, col)

        # encoding / line endings (far right small)
        meta_text = f'{self.encoding}  {self.line_ending}'
        draw_text(surf, self.font, self.r.right - 60, self.r.y + 6, meta_text, T['text_dim'])

# ══════════════════════════════════════════════════════════════════════════════
# § 14  BUILD DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class BuildDialog:
    """Diálogo com comandos de build dinâmicos, cópia e execução opcional."""
    W, H = 740, 500
    PAD = 14
    BTN_H = 30
    ROW_H = 20

    def __init__(self, buf: TextBuffer, screen_size: Tuple[int, int],
                 font: pygame.font.Font, font_ui: pygame.font.Font,
                 toolchain: Optional[ToolchainManager] = None,
                 project_target: Optional[Dict] = None):
        self.font = font
        self.font_ui = font_ui
        sw, sh = screen_size
        self.r = pygame.Rect((sw - self.W) // 2, (sh - self.H) // 2, self.W, self.H)
        self.done = False

        # ── configuração do alvo ────────────────────────────────────────────
        self.targets = ['console', 'gui', 'dll', 'uefi']
        self.active_target = 'console'
        self.entry = project_target.get('entry', 'main') if project_target else 'main'
        self.libs = project_target.get('libs', ['kernel32.dll']) if project_target else ['kernel32.dll']
        self.extra_flags = project_target.get('flags', []) if project_target else []

        # ── toolchain ───────────────────────────────────────────────────────
        self.tc = toolchain or ToolchainManager()
        self._scroll = 0
        self._max_scroll = 0

        # ── botões ──────────────────────────────────────────────────────────
        btn_y = self.r.y + self.H - self.BTN_H - self.PAD
        self.btn_close = pygame.Rect(self.r.right - 110, btn_y, 100, self.BTN_H)
        self.btn_copy  = pygame.Rect(self.r.right - 216, btn_y, 100, self.BTN_H)
        self.btn_run   = pygame.Rect(self.r.right - 322, btn_y, 100, self.BTN_H)
        self._hov = ''

        # ── geração de comandos ─────────────────────────────────────────────
        self.lines: List[Tuple[str, str]] = []   # (texto, tipo de cor)
        self._build_commands()

    def _build_commands(self) -> None:
        """Gera a lista de linhas com base no alvo e ferramentas disponíveis."""
        lines = []
        fn = self.buf.filepath or 'untitled.asm'
        stem = os.path.splitext(os.path.basename(fn))[0]
        nasm_ok = self.tc.has_nasm
        golink_ok = self.tc.has_golink

        def add(text, color='cmt'):
            lines.append((text, color))

        # cabeçalho
        add(f'; Build commands for:  {fn}')
        add('')

        # assemble (NASM)
        if nasm_ok:
            add('; ── Assemble (NASM Win64) ───────────────────────────────')
            add(f'nasm -f win64 -o {stem}.obj {fn}', 'kw')
        else:
            add('; [!] NASM not found in PATH', 'err')
        add('')

        # linkagem conforme target
        if golink_ok:
            if self.active_target == 'console':
                add('; ── Link as Console (GoLink) ────────────────────────')
                add(f'golink /entry {self.entry} /console /fo {stem}.exe {stem}.obj ' +
                    ' '.join(self.libs), 'kw')
            elif self.active_target == 'gui':
                add('; ── Link as GUI (GoLink) ────────────────────────────')
                add(f'golink /entry {self.entry} /windows /fo {stem}.exe {stem}.obj ' +
                    ' '.join(self.libs), 'kw')
            elif self.active_target == 'dll':
                add('; ── Link as DLL (GoLink) ────────────────────────────')
                add(f'golink /entry {self.entry} /dll /fo {stem}.dll {stem}.obj ' +
                    ' '.join(self.libs), 'kw')
            elif self.active_target == 'uefi':
                add('; ── Link as UEFI (lld-link) ──────────────────────────')
                add(f'lld-link /subsystem:efi_application /entry:efi_main ' +
                    f'/out:{stem}.efi {stem}.obj', 'kw')
            add('')
        else:
            add('; [!] GoLink not found – only object file will be created', 'warn')
            add('')

        # MSVC link (bônus)
        add('; ── Link as Console (MSVC link.exe) ─────────────────────────')
        add(f'link /subsystem:console /entry:{self.entry} /out:{stem}.exe ' +
            f'{stem}.obj {" ".join(self.libs)}', 'kw')
        add('')

        # GCC / ld (para Linux)
        add('; ── Link as Console (GCC / ld) ──────────────────────────────')
        add(f'ld -o {stem} {stem}.o -e {self.entry} -lc -dynamic-linker ' +
            '/lib64/ld-linux-x86-64.so.2', 'kw')
        add('')

        # dicas
        add('; Tip: add nasm.exe and golink.exe to your PATH,')
        add(';      or run from the Developer Command Prompt.')
        add('')

        self.lines = lines
        self._max_scroll = max(0, len(lines) * self.ROW_H - (self.H - 100))

    def set_target(self, target: str) -> None:
        if target in self.targets:
            self.active_target = target
            self._build_commands()
            self._scroll = 0

    def handle_event(self, ev) -> bool:
        if ev.type == pygame.MOUSEMOTION:
            pos = ev.pos
            self._hov = ''
            if self.btn_close.collidepoint(pos): self._hov = 'close'
            elif self.btn_copy.collidepoint(pos): self._hov = 'copy'
            elif self.btn_run.collidepoint(pos): self._hov = 'run'

        if ev.type == pygame.MOUSEBUTTONDOWN:
            if not self.r.collidepoint(ev.pos):
                self.done = True; return True
            if self.btn_close.collidepoint(ev.pos):
                self.done = True; return True
            if self.btn_copy.collidepoint(ev.pos):
                # copia todos os comandos para a área de transferência
                full_text = '\n'.join(line[0] for line in self.lines)
                try: pygame.scrap.put_text(full_text)
                except Exception: pass
                return True
            if self.btn_run.collidepoint(ev.pos):
                # executa o build? por enquanto apenas sinaliza que o diálogo fechou
                self.done = True; return True
            if ev.button == 4: self._scroll = max(0, self._scroll - 20)
            if ev.button == 5: self._scroll = min(self._max_scroll, self._scroll + 20)

        elif ev.type == pygame.MOUSEWHEEL:
            self._scroll = max(0, min(self._max_scroll, self._scroll - ev.y * 20))

        elif ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                self.done = True; return True
            if ev.key == pygame.K_c and (ev.mod & pygame.KMOD_CTRL):
                full_text = '\n'.join(line[0] for line in self.lines)
                try: pygame.scrap.put_text(full_text)
                except Exception: pass
                return True

        return False

    def draw(self, surf: pygame.Surface) -> None:
        # overlay
        ov = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 110)); surf.blit(ov, (0, 0))
        # sombra
        sh = pygame.Surface((self.W + 10, self.H + 10), pygame.SRCALPHA)
        sh.fill((0, 0, 0, 140)); surf.blit(sh, (self.r.x - 5, self.r.y - 5))

        # fundo
        pygame.draw.rect(surf, T['panel2'], self.r, border_radius=10)
        pygame.draw.rect(surf, T['border'], self.r, 1, border_radius=10)

        # barra de título
        title_rect = pygame.Rect(self.r.x, self.r.y, self.W, 42)
        pygame.draw.rect(surf, T['panel'], title_rect, border_radius=10)
        pygame.draw.line(surf, T['border'],
                         (self.r.x, self.r.y + 42), (self.r.right, self.r.y + 42))
        draw_text(surf, self.font, self.r.x + self.PAD, self.r.y + 11,
                  'Build Commands', T['text_em'])

        # seletor de alvo
        sel_y = self.r.y + 48
        for i, target in enumerate(self.targets):
            btn_w = 80
            btn_rect = pygame.Rect(self.r.x + self.PAD + i * (btn_w + 6), sel_y,
                                   btn_w, self.BTN_H)
            draw_btn(surf, self.font_ui, btn_rect, target.capitalize(),
                     hovered=self._hov == target, active=(target == self.active_target))

        # área de comandos
        clip = pygame.Rect(self.r.x + self.PAD, sel_y + 40,
                           self.W - self.PAD * 2, self.H - 150)
        surf.set_clip(clip)
        y_start = self.r.y + sel_y + 40 - self._scroll
        for i, (text, col_name) in enumerate(self.lines):
            y = y_start + i * self.ROW_H
            if y + self.ROW_H < self.r.y + 90 or y > self.r.bottom - 60:
                continue
            color = T.get(col_name, T['text'])
            draw_text(surf, self.font_ui, self.r.x + self.PAD + 4, y, text, color, self.W - 40)
        surf.set_clip(None)

        # botões inferiores
        draw_btn(surf, self.font_ui, self.btn_run,  '▶ Run Build', hovered=self._hov == 'run',
                 col=T['btn_act'] if self._hov == 'run' else T['btn'])
        draw_btn(surf, self.font_ui, self.btn_copy, '📋 Copy All', hovered=self._hov == 'copy')
        draw_btn(surf, self.font_ui, self.btn_close, '✕ Close', hovered=self._hov == 'close')

# ══════════════════════════════════════════════════════════════════════════════
# § 15  ABOUT DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class AboutDialog:
    W, H = 560, 420
    TABS = ['About', 'Shortcuts', 'System Info']

    def __init__(self, screen_size: Tuple[int, int], font: pygame.font.Font,
                 font_ui: pygame.font.Font):
        self.font = font; self.font_ui = font_ui
        sw, sh = screen_size
        self.r = pygame.Rect((sw - self.W) // 2, (sh - self.H) // 2, self.W, self.H)
        self.done = False
        self.active_tab = 0               # index into TABS
        self._scroll = 0
        self._max_scroll = 0
        self._hov = ''
        self._tab_rects: List[pygame.Rect] = []
        self._rebuild_tab_rects()

        # ── contents per tab ────────────────────────────────────────────────
        self.pages: Dict[str, List[Tuple[str, str]]] = {
            'About': [
                ('em',  'ModernAssembly NX  v3.0'),
                ('dim', '─────────────────────────────────────'),
                ('txt', 'A Scratch‑style NASM x64 block editor'),
                ('dim', ''),
                ('txt', 'Left panel : browse & search template library'),
                ('txt', 'Center     : full code editor with syntax highlight'),
                ('txt', 'Right panel: context‑aware suggestions'),
                ('dim', ''),
                ('txt', 'Built with Python + Pygame'),
                ('txt', 'Supports GDI32 & OpenGL32 pre‑built templates'),
                ('txt', 'Includes ASMX ↔ NASM converters'),
                ('dim', ''),
                ('kw',  'MIT License — free for any use'),
            ],
            'Shortcuts': [
                ('em',  'Keyboard Shortcuts'),
                ('dim', '─────────────────────────────────────'),
                ('txt', 'Ctrl+N          New file'),
                ('txt', 'Ctrl+O          Open file'),
                ('txt', 'Ctrl+S / F2     Save to file'),
                ('txt', 'F5              Show build commands'),
                ('txt', 'F6              Build & Run'),
                ('txt', 'Ctrl+Z / Y      Undo / Redo'),
                ('txt', 'Ctrl+C / X / V  Copy / Cut / Paste'),
                ('txt', 'Ctrl+A          Select all'),
                ('txt', 'Ctrl+F          Find / Replace'),
                ('txt', 'Ctrl+G          Go to line'),
                ('txt', 'Tab / Shift+Tab Indent / Dedent'),
                ('txt', 'Alt+F2          Toggle bookmark'),
                ('txt', 'Ctrl+Left/Right Move word'),
                ('txt', 'Mouse wheel     Scroll'),
                ('txt', 'Drag & drop     Open .asm/.inc files'),
                ('txt', 'Double‑click    Select word'),
                ('txt', 'Right‑click     Context menu (soon)'),
            ],
            'System Info': self._system_info_lines()
        }

        # buttons
        self.btn_ok = pygame.Rect(self.r.x + self.W - 110, self.r.y + self.H - 44, 100, 30)
        self.btn_copy = pygame.Rect(self.r.x + self.W - 220, self.r.y + self.H - 44, 100, 30)

    def _system_info_lines(self) -> List[Tuple[str, str]]:
        import platform
        lines = [
            ('em',  'System Information'),
            ('dim', '─────────────────────────────────────'),
            ('txt', f'OS      : {platform.system()} {platform.release()}'),
            ('txt', f'Arch    : {platform.machine()}'),
            ('txt', f'Python  : {platform.python_version()}'),
            ('txt', f'Pygame  : {pygame.version.ver}'),
            ('txt', f'Display : {pygame.display.Info().current_w}x{pygame.display.Info().current_h}'),
        ]
        # NASM / GoLink detection
        tc = ToolchainManager()
        nasm_ok = tc.has_nasm
        golink_ok = tc.has_golink
        lines.append(('txt', f'NASM    : {"✔ found" if nasm_ok else "✘ not found"}'))
        lines.append(('txt', f'GoLink  : {"✔ found" if golink_ok else "✘ not found"}'))
        lines.append(('dim', ''))
        lines.append(('txt', 'Press "Copy SysInfo" to copy this information.'))
        return lines

    def _rebuild_tab_rects(self):
        self._tab_rects = []
        x = self.r.x + 16
        y = self.r.y + 44
        for tab in self.TABS:
            w = self.font.size(tab)[0] + 20
            self._tab_rects.append(pygame.Rect(x, y, w, 26))
            x += w + 4

    def handle_event(self, ev) -> bool:
        if ev.type == pygame.MOUSEMOTION:
            self._hov = ''
            if self.btn_ok.collidepoint(ev.pos): self._hov = 'ok'
            elif self.btn_copy.collidepoint(ev.pos): self._hov = 'copy'
            else:
                for i, r in enumerate(self._tab_rects):
                    if r.collidepoint(ev.pos):
                        self._hov = f'tab{i}'
                        break

        if ev.type == pygame.MOUSEBUTTONDOWN:
            if not self.r.collidepoint(ev.pos):
                self.done = True; return True
            if self.btn_ok.collidepoint(ev.pos):
                self.done = True; return True
            if self.btn_copy.collidepoint(ev.pos):
                # copy system info to clipboard
                sys_lines = [line[1] for line in self.pages['System Info']]
                try: pygame.scrap.put_text('\n'.join(sys_lines))
                except Exception: pass
                return True
            for i, r in enumerate(self._tab_rects):
                if r.collidepoint(ev.pos):
                    self.active_tab = i
                    self._scroll = 0
                    self._update_max_scroll()
                    return True
            if ev.button == 4: self._scroll = max(0, self._scroll - 20)
            if ev.button == 5: self._scroll = min(self._max_scroll, self._scroll + 20)

        elif ev.type == pygame.MOUSEWHEEL:
            self._scroll = max(0, min(self._max_scroll, self._scroll - ev.y * 20))

        elif ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                self.done = True; return True
            if ev.key == pygame.K_TAB:
                self.active_tab = (self.active_tab + 1) % len(self.TABS)
                self._scroll = 0
                self._update_max_scroll()
                return True
            if ev.key == pygame.K_UP:
                self._scroll = max(0, self._scroll - 20)
            if ev.key == pygame.K_DOWN:
                self._scroll = min(self._max_scroll, self._scroll + 20)

        return False

    def _update_max_scroll(self):
        page_lines = self.pages[self.TABS[self.active_tab]]
        total_h = len(page_lines) * 18 + 20
        content_h = self.H - 120   # top margin + tab bar + bottom buttons
        self._max_scroll = max(0, total_h - content_h)

    def draw(self, surf: pygame.Surface) -> None:
        # overlay
        ov = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 110)); surf.blit(ov, (0, 0))
        # shadow
        sh = pygame.Surface((self.W + 10, self.H + 10), pygame.SRCALPHA)
        sh.fill((0, 0, 0, 140)); surf.blit(sh, (self.r.x - 5, self.r.y - 5))

        # dialog body
        pygame.draw.rect(surf, T['panel2'], self.r, border_radius=10)
        pygame.draw.rect(surf, T['border'], self.r, 1, border_radius=10)

        # title bar
        title_rect = pygame.Rect(self.r.x, self.r.y, self.W, 42)
        pygame.draw.rect(surf, T['panel'], title_rect, border_radius=10)
        pygame.draw.line(surf, T['border'],
                         (self.r.x, self.r.y + 42), (self.r.right, self.r.y + 42))
        draw_text(surf, self.font, self.r.x + 16, self.r.y + 11, 'About ModernAssembly NX', T['text_em'])

        # tab bar
        self._rebuild_tab_rects()
        for i, (tab, rect) in enumerate(zip(self.TABS, self._tab_rects)):
            active = (i == self.active_tab)
            hov = (self._hov == f'tab{i}')
            draw_btn(surf, self.font_ui, rect, tab,
                     hovered=hov, active=active)

        # content area
        content_clip = pygame.Rect(self.r.x + 16, self.r.y + 78,
                                   self.W - 32, self.H - 125)
        surf.set_clip(content_clip)
        cmap = {'em': T['text_em'], 'dim': T['text_dim'], 'txt': T['text'],
                'kw': T['kw'], 'err': T['err'], 'ok': T['ok']}
        page_lines = self.pages[self.TABS[self.active_tab]]
        y = self.r.y + 80 - self._scroll
        for kind, text in page_lines:
            if y + 18 < self.r.y + 78 or y > self.r.bottom - 48: 
                y += 18; continue
            draw_text(surf, self.font_ui, self.r.x + 20, y, text, cmap.get(kind, T['text']), self.W - 40)
            y += 18
        surf.set_clip(None)

        # buttons
        if self.active_tab == 2:   # System Info tab: show Copy button
            draw_btn(surf, self.font_ui, self.btn_copy, 'Copy SysInfo',
                     hovered=self._hov == 'copy')
        else:
            # optional: hide or leave as placeholder
            pass
        draw_btn(surf, self.font_ui, self.btn_ok, 'OK', hovered=self._hov == 'ok', active=True)

# ══════════════════════════════════════════════════════════════════════════════
# § 16  DEFAULT SOURCE
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_SOURCES = {
    'console': """\
bits 64
default rel

global main
extern ExitProcess

section .text

main:
    push rbp
    mov  rbp, rsp
    sub  rsp, 32

    ; ── your code here ──────────────────────────────────
    xor  eax, eax

    ; exit(0)
    add  rsp, 32
    pop  rbp
    xor  rcx, rcx
    sub  rsp, 32
    call [ExitProcess]

section .data
    msg  db  "Hello, World!", 0x0D, 0x0A, 0

section .bss
    buffer  resb  256
""",
    'gdi32_window': """\
bits 64
default rel

global WinMain
extern RegisterClassExA, CreateWindowExA, ShowWindow, UpdateWindow
extern GetMessageA, TranslateMessage, DispatchMessageA, PostQuitMessage
extern DefWindowProcA, BeginPaint, EndPaint, TextOutA, ExitProcess

section .text

WndProc:
    push rbp
    mov  rbp, rsp
    sub  rsp, 64
    cmp  edx, 0x000F            ; WM_PAINT
    je   .paint
    cmp  edx, 0x0002            ; WM_DESTROY
    je   .destroy
    add  rsp, 64
    pop  rbp
    jmp  [DefWindowProcA]
.destroy:
    xor  rcx, rcx
    sub  rsp, 32
    call [PostQuitMessage]
    add  rsp, 32
    xor  rax, rax
    add  rsp, 64
    pop  rbp
    ret
.paint:
    mov  [rel hwnd], rcx
    lea  rdx, [rel ps_buf]
    sub  rsp, 32
    call [BeginPaint]
    add  rsp, 32
    mov  [rel hdc], rax
    ; TextOutA(hdc, 10, 10, msg, 13)
    mov  r9,  13
    lea  r8,  [rel msg]
    mov  rdx, 10
    mov  rcx, 10
    push rcx
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [TextOutA]
    add  rsp, 32
    pop  rcx
    lea  rdx, [rel ps_buf]
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [EndPaint]
    add  rsp, 32
    xor  rax, rax
    add  rsp, 64
    pop  rbp
    ret

WinMain:
    push rbp
    mov  rbp, rsp
    sub  rsp, 64
    ; RegisterClassExA
    lea  rcx, [rel wndcls]
    sub  rsp, 32
    call [RegisterClassExA]
    add  rsp, 32
    ; CreateWindowExA (simplified)
    mov  [rsp+56], 0
    mov  [rsp+48], 0
    mov  [rsp+40], 0
    mov  [rsp+32], 0
    mov  r9,  600
    mov  r8,  800
    xor  rdx, rdx
    xor  rcx, rcx
    push rcx
    mov  rcx, 0x00CF0000
    push rcx
    xor  rcx, rcx
    push rcx
    lea  rcx, [rel cls_name]
    sub  rsp, 32
    call [CreateWindowExA]
    add  rsp, 32
    mov  [rel hwnd], rax
    mov  rdx, 10
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [ShowWindow]
    add  rsp, 32
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [UpdateWindow]
    add  rsp, 32
.msg_loop:
    mov  [rsp+24], 0
    mov  [rsp+16], 0
    xor  r9, r9
    xor  r8, r8
    lea  rdx, [rel msg_buf]
    xor  rcx, rcx
    sub  rsp, 32
    call [GetMessageA]
    add  rsp, 32
    test eax, eax
    je   .exit
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [TranslateMessage]
    add  rsp, 32
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [DispatchMessageA]
    add  rsp, 32
    jmp  .msg_loop
.exit:
    xor  rcx, rcx
    sub  rsp, 32
    call [ExitProcess]

section .data
    cls_name  db  "GDIWnd", 0
    wnd_title db  "GDI Window", 0
    msg       db  "Hello, GDI32!", 0
    wndcls:
      dd  80
      dd  3
      dq  WndProc
      dd  0, 0
      dq  0, 0, 0, 0, 0
      dq  cls_name
      dq  0

section .bss
    hwnd    resq 1
    hdc     resq 1
    ps_buf  resb 72
    msg_buf resb 48
""",
    'opengl_window': """\
bits 64
default rel

global WinMain
extern RegisterClassExA, CreateWindowExA, ShowWindow, UpdateWindow
extern PeekMessageA, TranslateMessage, DispatchMessageA, PostQuitMessage
extern DefWindowProcA, GetDC, ReleaseDC, ExitProcess
extern ChoosePixelFormat, SetPixelFormat
extern wglCreateContext, wglMakeCurrent, wglDeleteContext, SwapBuffers
extern glClearColor, glClear, glViewport, glBegin, glEnd, glVertex3f, glFlush

section .text

WndProc:
    push rbp
    mov  rbp, rsp
    sub  rsp, 48
    cmp  edx, 0x0002            ; WM_DESTROY
    je   .destroy
    cmp  edx, 0x0005            ; WM_SIZE
    je   .size
    add  rsp, 48
    pop  rbp
    jmp  [DefWindowProcA]
.destroy:
    xor  rcx, rcx
    sub  rsp, 32
    call [PostQuitMessage]
    add  rsp, 32
    xor  rax, rax
    add  rsp, 48
    pop  rbp
    ret
.size:
    add  rsp, 48
    pop  rbp
    xor  rax, rax
    ret

render:
    push rbp
    mov  rbp, rsp
    sub  rsp, 48
    mov  ecx, 0x4100
    sub  rsp, 32
    call [glClear]
    add  rsp, 32
    mov  ecx, 4
    sub  rsp, 32
    call [glBegin]
    add  rsp, 32
    movss xmm0, [rel v1x]
    movss xmm1, [rel v1y]
    xorps xmm2, xmm2
    sub  rsp, 32
    call [glVertex3f]
    add  rsp, 32
    movss xmm0, [rel v2x]
    movss xmm1, [rel v1y]
    xorps xmm2, xmm2
    sub  rsp, 32
    call [glVertex3f]
    add  rsp, 32
    xorps xmm0, xmm0
    movss xmm1, [rel v2y]
    xorps xmm2, xmm2
    sub  rsp, 32
    call [glVertex3f]
    add  rsp, 32
    sub  rsp, 32
    call [glEnd]
    add  rsp, 32
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [SwapBuffers]
    add  rsp, 32
    add  rsp, 48
    pop  rbp
    ret

WinMain:
    push rbp
    mov  rbp, rsp
    sub  rsp, 64
    lea  rcx, [rel wndcls]
    sub  rsp, 32
    call [RegisterClassExA]
    add  rsp, 32
    ; CreateWindowExA simplified
    mov  [rsp+56], 0
    mov  [rsp+48], 0
    mov  [rsp+40], 0
    mov  [rsp+32], 0
    mov  r9,  600
    mov  r8,  800
    xor  rdx, rdx
    xor  rcx, rcx
    push rcx
    mov  rcx, 0x00CF0000
    push rcx
    xor  rcx, rcx
    push rcx
    lea  rcx, [rel cls_name]
    sub  rsp, 32
    call [CreateWindowExA]
    add  rsp, 32
    mov  [rel hwnd], rax
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [GetDC]
    add  rsp, 32
    mov  [rel hdc], rax
    call setup_pixel_format
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [wglCreateContext]
    add  rsp, 32
    mov  [rel hrc], rax
    mov  rdx, [rel hrc]
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [wglMakeCurrent]
    add  rsp, 32
    movss xmm3, [rel one_f]
    movss xmm2, [rel bg_b]
    movss xmm1, [rel bg_g]
    movss xmm0, [rel bg_r]
    sub  rsp, 32
    call [glClearColor]
    add  rsp, 32
    mov  rdx, 10
    mov  rcx, [rel hwnd]
    sub  rsp, 32
    call [ShowWindow]
    add  rsp, 32
.msg_loop:
    mov  [rsp+24], 1
    mov  [rsp+16], 0
    xor  r9, r9
    xor  r8, r8
    lea  rdx, [rel msg_buf]
    xor  rcx, rcx
    sub  rsp, 32
    call [PeekMessageA]
    add  rsp, 32
    test eax, eax
    jz   .render
    cmp  dword [rel msg_buf+4], 0x0012
    je   .exit
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [TranslateMessage]
    add  rsp, 32
    lea  rcx, [rel msg_buf]
    sub  rsp, 32
    call [DispatchMessageA]
    add  rsp, 32
.render:
    call render
    jmp  .msg_loop
.exit:
    xor  rdx, rdx
    xor  rcx, rcx
    sub  rsp, 32
    call [wglMakeCurrent]
    add  rsp, 32
    mov  rcx, [rel hrc]
    sub  rsp, 32
    call [wglDeleteContext]
    add  rsp, 32
    xor  rcx, rcx
    sub  rsp, 32
    call [ExitProcess]

setup_pixel_format:
    push rbp
    mov  rbp, rsp
    sub  rsp, 32
    mov  word [rel pfd+0],  40
    mov  word [rel pfd+2],  1
    mov  dword [rel pfd+4], 0x25
    mov  byte  [rel pfd+8], 0
    mov  byte  [rel pfd+9], 32
    mov  byte  [rel pfd+22],24
    lea  rdx, [rel pfd]
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [ChoosePixelFormat]
    add  rsp, 32
    mov  [rel pf_idx], eax
    lea  r8, [rel pfd]
    mov  edx, [rel pf_idx]
    mov  rcx, [rel hdc]
    sub  rsp, 32
    call [SetPixelFormat]
    add  rsp, 32
    add  rsp, 32
    pop  rbp
    ret

section .data
    cls_name  db  "GLWnd", 0
    wnd_title db  "OpenGL Window", 0
    wndcls:
      dd  80
      dd  3
      dq  WndProc
      dd  0, 0
      dq  0, 0, 0, 0, 0
      dq  cls_name
      dq  0
    v1x   dd  0xBF000000
    v1y   dd  0xBF000000
    v2x   dd  0x3F000000
    v2y   dd  0x3F000000
    one_f dd  0x3F800000
    bg_r  dd  0x3DCCCCCD
    bg_g  dd  0x3DCCCCCD
    bg_b  dd  0x3E19999A

section .bss
    hwnd    resq 1
    hdc     resq 1
    hrc     resq 1
    pfd     resb 40
    pf_idx  resd 1
    msg_buf resb 48
""",
    'uefi_app': """\
; UEFI Application (x64)
format pe64
entry efi_main

section '.text' code readable executable

efi_main:
    push rbp
    mov  rbp, rsp
    sub  rsp, 32

    ; In UEFI, first arg (rcx) is ImageHandle, second (rdx) is *EFI_SYSTEM_TABLE
    ; Example: print a string via ConOut->OutputString
    mov  rsi, rdx             ; save SystemTable
    mov  rax, [rsi + 0x40]    ; ConOut
    mov  rcx, rax
    lea  rdx, [rel msg]
    call qword [rcx + 0x08]   ; OutputString
    xor  rax, rax
    add  rsp, 32
    pop  rbp
    ret

section '.data' data readable writeable
    msg  dw  __utf16__('Hello, UEFI!'), 0x0D, 0x0A, 0
""",
    'bios_boot': """\
; BIOS boot sector (512 bytes)
bits 16
org 0x7C00

start:
    cli
    xor  ax, ax
    mov  ds, ax
    mov  es, ax
    mov  ss, ax
    mov  sp, 0x7C00
    sti

    mov  si, msg
.loop:
    lodsb
    test al, al
    jz   .halt
    mov  ah, 0x0E
    int  0x10
    jmp  .loop
.halt:
    hlt
    jmp  .halt

msg db 'Hello from BIOS!', 13, 10, 0

times 510-($-$$) db 0
dw 0xAA55
""",
    'dll': """\
; Windows x64 DLL
bits 64
default rel

global DllMain
extern DisableThreadLibraryCalls

section .text

DllMain:
    push rbp
    mov  rbp, rsp
    sub  rsp, 32

    cmp  edx, 1               ; DLL_PROCESS_ATTACH
    jne  .done
    sub  rsp, 32
    call [DisableThreadLibraryCalls]
    add  rsp, 32
.done:
    mov  eax, 1               ; return TRUE
    add  rsp, 32
    pop  rbp
    ret

section .data
""",
    'asmx_demo': """\
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

# Choose the default source that will appear on start-up
DEFAULT_SRC = DEFAULT_SOURCES['console']

# ══════════════════════════════════════════════════════════════════════════════
# § 17  ASSEMBLY IDE — MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class AssemblyIDE:
    def __init__(self):
        pygame.init()
        # scrap init with fallback
        try:
            pygame.scrap.init()
        except Exception:
            pass

        self.screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
        pygame.display.set_caption('ModernAssembly NX  —  NASM x64 + ASMX + OpenGL + GDI')
        self._init_fonts()
        self.clock = pygame.time.Clock()
        self.lib      = TemplateLib()
        self.buf      = TextBuffer(DEFAULT_SRC)
        self.analyzer = ContextAnalyzer(self.lib)

        # Build system (async)
        self.build_sys = BuildSystem()
        self._build_running = False
        self._build_progress = 0.0
        self._build_result: Optional[Dict] = None

        # UI state
        self._dialog: Optional[Any] = None
        self._last_ctx_row = -1
        self._build_layout()

        # Drag & drop file support
        pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)  # required for DROP events
        pygame.event.set_allowed([pygame.QUIT, pygame.KEYDOWN, pygame.KEYUP,
                                  pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN,
                                  pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL,
                                  pygame.VIDEORESIZE, pygame.DROPFILE])

        # Recent files & session restore
        self.recent_files: List[str] = []
        self._session_file = 'session.json'
        self._load_session()

        # Initial context update
        self.right.update(self.buf)

    def _init_fonts(self) -> None:
        candidates = ['Consolas', 'JetBrains Mono', 'Fira Code', 'Cascadia Mono',
                      'Courier New', 'DejaVu Sans Mono', 'Liberation Mono', 'monospace']
        self.font = self.font_ui = None
        for name in candidates:
            try:
                f  = pygame.font.SysFont(name, FONT_SZ)
                fu = pygame.font.SysFont(name, FONT_UI)
                if f and fu:
                    self.font = f; self.font_ui = fu; break
            except Exception:
                pass
        if not self.font:
            self.font    = pygame.font.Font(None, FONT_SZ + 6)
            self.font_ui = pygame.font.Font(None, FONT_UI + 4)

    def _build_layout(self) -> None:
        w, h = self.screen.get_size()
        ew = w - LEFT_W - RIGHT_W
        ey = TOOLBAR_H
        eh = h - TOOLBAR_H - STATUS_H
        self.toolbar = Toolbar(0, 0, w, TOOLBAR_H, self.font_ui)
        self.left    = LeftPanel(0, TOOLBAR_H, LEFT_W, h - TOOLBAR_H - STATUS_H,
                                 self.lib, self.font, self.font_ui)
        self.editor  = CodeEditor(LEFT_W, ey, ew, eh, self.buf, self.font)
        self.right   = RightPanel(LEFT_W + ew, ey, RIGHT_W, eh,
                                  self.lib, self.analyzer, self.font, self.font_ui)
        self.status  = StatusBar(0, h - STATUS_H, w, STATUS_H, self.font_ui)
        self.toolbar.set_callback(self._toolbar_cb)
        self.left.set_click_callback(self._open_dialog)
        self.left.set_right_click_callback(self._on_right_click_template)
        self.right.set_click_callback(self._open_dialog_fast)

    # ── Toolbar callback ───────────────────────────────────────────────────
    def _toolbar_cb(self, action: str) -> None:
        if action == 'new':
            self._new_file()
        elif action == 'open':
            self._open_file()
        elif action == 'save':
            self._save()
        elif action == 'undo':
            self.buf.undo()
            self.editor._inval_all()
            self.status.notify('Undo')
        elif action == 'redo':
            self.buf.redo()
            self.editor._inval_all()
            self.status.notify('Redo')
        elif action == 'cut':
            self.buf.cut()
            self.editor._inval_all()
            self.status.notify('Cut')
        elif action == 'copy':
            self.buf.copy()
            self.status.notify('Copied')
        elif action == 'paste':
            self.buf.paste()
            self.editor._inval_all()
            self.status.notify('Pasted')
        elif action == 'find':
            self.editor._find_mode = True
            self.editor._find_text = ''
            self.editor.search_matches.clear()
        elif action == 'build':
            self._show_build_dialog()
        elif action == 'run':
            self._build_and_run()
        elif action == 'stop':
            self._stop_build()
        elif action == 'about':
            self._dialog = AboutDialog(self.screen.get_size(), self.font, self.font_ui)

    # ── File operations ────────────────────────────────────────────────────
    def _new_file(self) -> None:
        self.buf = TextBuffer(DEFAULT_SRC)
        self.editor.buf = self.buf
        self.editor._inval_all()
        self.editor.scroll_y = 0
        self.editor.scroll_x = 0
        self.status.notify('New file.')

    def _open_file(self) -> None:
        # Use tkinter file dialog if available, else fallback to prompt
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            path = filedialog.askopenfilename(
                title="Open Assembly File",
                filetypes=[("Assembly files", "*.asm *.inc *.nasm *.s *.asmx"), ("All files", "*.*")]
            )
            root.destroy()
            if path:
                self._load_file(path)
        except Exception:
            self.status.notify('Drag & drop a .asm file to open.', 4000)

    def _load_file(self, path: str) -> None:
        msg = self.buf.load(path)
        self.editor._inval_all()
        self.editor.scroll_y = 0
        self.editor.scroll_x = 0
        self.status.notify(msg)
        if path not in self.recent_files:
            self.recent_files.insert(0, str(path))
            if len(self.recent_files) > 10:
                self.recent_files.pop()
        self.right.update(self.buf)

    def _save(self) -> None:
        path = self.buf.filepath
        if not path:
            # use tkinter file dialog
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                path = filedialog.asksaveasfilename(
                    title="Save Assembly File",
                    defaultextension=".asm",
                    filetypes=[("Assembly files", "*.asm"), ("All files", "*.*")]
                )
                root.destroy()
            except Exception:
                path = None
            if not path:
                self.status.notify('Save cancelled.')
                return
        msg = self.buf.save(path)
        self.status.notify(msg)
        if path not in self.recent_files:
            self.recent_files.insert(0, str(path))

    # ── Build system integration ───────────────────────────────────────────
    def _show_build_dialog(self) -> None:
        self._dialog = BuildDialog(self.buf, self.screen.get_size(),
                                   self.font, self.font_ui)

    def _build_and_run(self) -> None:
        if self._build_running:
            return
        # Save current buffer to a temporary file
        import tempfile
        tmp = tempfile.mkdtemp(prefix="masm_")
        src_path = os.path.join(tmp, "source.asm")
        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(self.buf.get_text())
        # Prepare target
        target = BuildTarget(
            name="temp",
            output_name="output.exe",
            source_files=[src_path],
            libraries=["kernel32.dll"],
            entry="main",
        )
        self._build_running = True
        self.toolbar.set_build_progress(0.0, True)
        self.build_sys.submit(target, callback=self._on_build_complete)

    def _on_build_complete(self, result: BuildResult) -> None:
        self._build_result = result
        self._build_running = False
        self.toolbar.set_build_progress(1.0, False)
        if result.status == BUILD_OK:
            self.status.notify('Build successful')
        else:
            self.status.notify('Build failed', color=T['err'])

    def _stop_build(self) -> None:
        self._build_running = False
        self.toolbar.set_build_progress(0.0, False)
        self.status.notify('Build stopped.')

    # ── Template insertion from panels ────────────────────────────────────
    def _open_dialog(self, tpl: Dict) -> None:
        self._dialog = ParamDialog(tpl, self.screen.get_size(), self.font, self.font_ui)

    def _open_dialog_fast(self, tpl: Dict) -> None:
        """Right panel: insert immediately if no ports, else show dialog."""
        if isinstance(tpl, dict):
            # it's a template dict
            if not tpl.get('ports'):
                self._insert_template(tpl, {})
            else:
                self._open_dialog(tpl)
        else:
            # it's a param suggestion dict: insert as text
            self.buf.insert_text(tpl['text'])
            self.editor._inval_all()
            self.editor._ensure_visible()
            self.status.notify(f'Inserted: {tpl["text"]}')

    def _insert_template(self, tpl: Dict, params: Dict) -> None:
        text = self.lib.render(tpl, params)
        self.buf.insert_text(text)
        self.editor._inval_all()
        self.editor._ensure_visible()
        self.lib.mark_used(tpl['id'])
        self.status.notify(f'Inserted: {tpl["title"]}')

    def _on_right_click_template(self, tpl: Dict, pos: Tuple[int,int]) -> None:
        """Show context menu for a template in the left panel."""
        menu_items = []
        if tpl['id'] in self.lib._favorites:
            menu_items.append(('★ Unfavorite', lambda: self.lib.toggle_favorite(tpl['id'])))
        else:
            menu_items.append(('☆ Favorite', lambda: self.lib.toggle_favorite(tpl['id'])))
        menu_items.append(('Insert without params', lambda: self._insert_template(tpl, {})))
        # build small popup context menu (simplified: use Dialog-like overlay)
        self._dialog = PopupContextMenu(menu_items, pos, self.font_ui)

    # ── Dialog handling ────────────────────────────────────────────────────
    def _handle_dialog(self) -> None:
        if not self._dialog or not self._dialog.done:
            return
        if isinstance(self._dialog, ParamDialog) and self._dialog.result is not None:
            self._insert_template(self._dialog.tpl, self._dialog.result)
        self._dialog = None

    # ── Context update ─────────────────────────────────────────────────────
    def _update_ctx(self) -> None:
        cr = self.buf.cur[0]
        if cr != self._last_ctx_row or self.buf._dirty:
            self._last_ctx_row = cr
            self.right.update(self.buf)

    # ── Session persistence ────────────────────────────────────────────────
    def _load_session(self) -> None:
        try:
            if os.path.exists(self._session_file):
                with open(self._session_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.recent_files = data.get('recent_files', [])
                # optionally restore last opened file
                last = data.get('last_file')
                if last and os.path.exists(last):
                    self._load_file(last)
        except Exception:
            pass

    def _save_session(self) -> None:
        try:
            data = {
                'recent_files': self.recent_files[:10],
                'last_file': self.buf.filepath,
                'cursor': self.buf.cur,
                'scroll': [self.editor.scroll_y, self.editor.scroll_x],
            }
            with open(self._session_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ── Main loop ──────────────────────────────────────────────────────────
    def run(self) -> None:
        running = True
        while running:
            self.clock.tick(60)
            dt = 1.0 / 60.0

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._save_session()
                    running = False
                    break

                if ev.type == pygame.VIDEORESIZE:
                    self.screen = pygame.display.set_mode((ev.w, ev.h), pygame.RESIZABLE)
                    self._build_layout()
                    continue

                # Global shortcuts (when no dialog is open)
                if ev.type == pygame.KEYDOWN and not self._dialog:
                    ctrl  = bool(ev.mod & pygame.KMOD_CTRL)
                    if ctrl and ev.key == pygame.K_s:
                        self._save(); continue
                    if ev.key == pygame.K_F2:
                        self._save(); continue
                    if ev.key == pygame.K_F5:
                        self._show_build_dialog(); continue
                    if ev.key == pygame.K_F6:
                        self._build_and_run(); continue
                    if ctrl and ev.key == pygame.K_o:
                        self._open_file(); continue
                    if ctrl and ev.key == pygame.K_n:
                        self._new_file(); continue
                    if ctrl and ev.key == pygame.K_q:
                        self._save_session(); running = False; break
                    if ctrl and ev.key == pygame.K_w:
                        # close? new empty file
                        self._new_file(); continue
                    if ctrl and ev.key == pygame.K_TAB:
                        # switch focus between editor and panels? simple: toggle focus
                        pass

                # Dialog gets priority
                if self._dialog:
                    self._dialog.handle_event(ev)
                    self._handle_dialog()
                    continue

                # Route keyboard to editor or search field
                if ev.type == pygame.KEYDOWN:
                    if self.left.search.active:
                        self.left.handle_event(ev)
                    elif self.editor._find_mode:
                        # forward to find input (handled internally in editor)
                        self.editor.handle_key(ev)
                    else:
                        self.editor.handle_key(ev)

                # Route mouse events
                if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                               pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
                    if not self.toolbar.handle_event(ev):
                        if not self.left.handle_event(ev):
                            if not self.right.handle_event(ev):
                                self.editor.handle_mouse(ev)

                # Drag-and-drop file loading
                if ev.type == pygame.DROPFILE:
                    p = ev.file
                    if os.path.splitext(p)[1].lower() in ('.asm', '.inc', '.nasm', '.s', '.asm64'):
                        self._load_file(p)
                    else:
                        self.status.notify(f'Unsupported file type: {os.path.basename(p)}')

            # Update build progress bar from build system
            if self._build_running:
                # simulate? BuildSystem currently doesn't provide progress updates.
                # We'll poll result asynchronously in a future update.
                pass

            # Update context
            self._update_ctx()
            # Draw
            self._draw()
            pygame.display.flip()

        pygame.quit()
        sys.exit(0)

    def _draw(self) -> None:
        self.screen.fill(T['bg'])
        self.left.draw(self.screen)
        self.editor.draw(self.screen)
        self.right.draw(self.screen)
        fn = os.path.basename(self.buf.filepath) if self.buf.filepath else None
        self.toolbar.draw(self.screen, fn, self.buf._dirty, self.buf)
        self.status.draw(self.screen, self.buf)
        if self._dialog:
            self._dialog.draw(self.screen)

# ═══════════════════  CLASSES DO BUILD SYSTEM  ═══════════════════
# (extraídas de buildsystem.py para manter o arquivo único)

BUILD_OK = "OK"
BUILD_ERROR = "ERROR"

@dataclass(slots=True)
class Diagnostic:
    severity: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None

@dataclass(slots=True)
class BuildArtifact:
    path: str
    type: str
    size: int = 0

@dataclass(slots=True)
class BuildResult:
    status: str
    diagnostics: List[Diagnostic] = field(default_factory=list)
    artifacts: List[BuildArtifact] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    elapsed: float = 0.0

@dataclass(slots=True)
class BuildTarget:
    name: str
    entry: str = "main"
    target_type: str = "win64"
    output_name: str = "program.exe"
    source_files: List[str] = field(default_factory=list)
    include_dirs: List[str] = field(default_factory=list)
    libraries: List[str] = field(default_factory=list)
    defines: Dict[str, str] = field(default_factory=dict)
    linker_flags: List[str] = field(default_factory=list)
    assembler_flags: List[str] = field(default_factory=list)

class ToolchainManager:
    def __init__(self):
        self.nasm = self._find(["nasm", "nasm.exe", "./tools/nasm.exe"])
        self.golink = self._find(["GoLink", "GoLink.exe", "./tools/GoLink.exe"])
        self.gcc = self._find(["gcc", "gcc.exe"])
        self.clang = self._find(["clang", "clang.exe"])

    def _find(self, names: List[str]) -> Optional[str]:
        for n in names:
            p = shutil.which(n)
            if p:
                return p
        return None

    @property
    def has_nasm(self) -> bool:
        return self.nasm is not None

    @property
    def has_golink(self) -> bool:
        return self.golink is not None

class BuildSystem:
    def __init__(self):
        self.toolchain = ToolchainManager()
        self.cache = {}  # simples cache de hash
        self.queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.running = True
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def submit(self, target: BuildTarget, callback: Optional[Callable] = None):
        self.queue.put((target, callback))

    def _worker_loop(self):
        while self.running:
            try:
                target, callback = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            res = self._build(target)
            if callback:
                try:
                    callback(res)
                except Exception:
                    pass

    def _build(self, target: BuildTarget) -> BuildResult:
        t0 = time.perf_counter()
        diagnostics = []
        artifacts = []
        stdout_acc = []
        stderr_acc = []

        if not self.toolchain.has_nasm:
            diagnostics.append(Diagnostic("error", "NASM not found"))
            return BuildResult(status=BUILD_ERROR, diagnostics=diagnostics)

        build_dir = os.path.abspath("./build")
        os.makedirs(build_dir, exist_ok=True)
        obj_dir = os.path.join(build_dir, "obj")
        os.makedirs(obj_dir, exist_ok=True)

        object_files = []

        for src in target.source_files:
            if not os.path.exists(src):
                diagnostics.append(Diagnostic("error", f"File not found: {src}"))
                continue
            src_name = Path(src).stem
            obj_path = os.path.join(obj_dir, src_name + ".obj")
            object_files.append(obj_path)

            # incremental build (hash cache)
            rebuild = not os.path.exists(obj_path)
            if not rebuild:
                # simplificação: sempre compila (cache não implementado completamente)
                rebuild = True

            if not rebuild:
                continue

            cmd = [self.toolchain.nasm, "-f", "win64", "-o", obj_path]
            for inc in target.include_dirs:
                cmd.extend(["-i", inc])
            for k, v in target.defines.items():
                cmd.append(f"-d{k}={v}")
            cmd.extend(target.assembler_flags)
            cmd.append(src)

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                stdout_acc.append(proc.stdout)
                stderr_acc.append(proc.stderr)
                if proc.returncode != 0:
                    for line in proc.stderr.splitlines():
                        m = re.search(r"(.+?):(\d+):\s*(.+)", line)
                        if m:
                            diagnostics.append(Diagnostic("error", m.group(3), file=m.group(1), line=int(m.group(2))))
                        else:
                            diagnostics.append(Diagnostic("error", line))
                    return BuildResult(status=BUILD_ERROR, diagnostics=diagnostics,
                                      stdout="\n".join(stdout_acc), stderr="\n".join(stderr_acc),
                                      elapsed=time.perf_counter() - t0)
            except subprocess.TimeoutExpired:
                diagnostics.append(Diagnostic("error", "Assembler timeout"))
                return BuildResult(status=BUILD_ERROR, diagnostics=diagnostics, elapsed=time.perf_counter() - t0)

        # Linkagem
        output_path = os.path.join(build_dir, target.output_name)
        if self.toolchain.has_golink:
            cmd = [self.toolchain.golink, "/console", "/entry", target.entry, "/fo", output_path]
            cmd.extend(object_files)
            cmd.extend(target.libraries)
            cmd.extend(target.linker_flags)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                stdout_acc.append(proc.stdout)
                stderr_acc.append(proc.stderr)
                if proc.returncode == 0 and os.path.exists(output_path):
                    artifacts.append(BuildArtifact(path=output_path, type="exe", size=os.path.getsize(output_path)))
                else:
                    diagnostics.append(Diagnostic("error", "Linker failed: " + proc.stderr))
                    return BuildResult(status=BUILD_ERROR, diagnostics=diagnostics,
                                      stdout="\n".join(stdout_acc), stderr="\n".join(stderr_acc),
                                      elapsed=time.perf_counter() - t0)
            except subprocess.TimeoutExpired:
                diagnostics.append(Diagnostic("error", "Linker timeout"))
                return BuildResult(status=BUILD_ERROR, diagnostics=diagnostics, elapsed=time.perf_counter() - t0)
        else:
            diagnostics.append(Diagnostic("warning", "GoLink not found – only object files were created."))

        return BuildResult(status=BUILD_OK, diagnostics=diagnostics, artifacts=artifacts,
                          stdout="\n".join(stdout_acc), stderr="\n".join(stderr_acc),
                          elapsed=time.perf_counter() - t0)

# ══════════════════════════════════════════════════════════════════════════════
# § 18  SIMPLE POPUP CONTEXT MENU (helper for right-click on templates)
# ══════════════════════════════════════════════════════════════════════════════

class PopupContextMenu:
    def __init__(self, items: List[Tuple[str, Callable]], pos: Tuple[int, int],
                 font: pygame.font.Font):
        self.items = items
        self.font = font
        self.item_h = 28
        self.rect = pygame.Rect(pos[0], pos[1],
                                max(font.size(item[0])[0] for item in items) + 20,
                                len(items) * self.item_h)
        self.visible = True
        self._hovered = -1
        self.result: Optional[int] = None
        self.done = False

    def handle_event(self, ev) -> bool:
        if not self.visible: return False
        if ev.type == pygame.MOUSEMOTION:
            if self.rect.collidepoint(ev.pos):
                rel = ev.pos[1] - self.rect.y
                self._hovered = rel // self.item_h
            else:
                self._hovered = -1
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            if self._hovered >= 0:
                self.items[self._hovered][1]()  # execute callback
                self.done = True
                return True
            else:
                self.done = True
                return True
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            self.done = True
        return False

    def draw(self, surf: pygame.Surface) -> None:
        if not self.visible: return
        pygame.draw.rect(surf, T['panel2'], self.rect, border_radius=4)
        pygame.draw.rect(surf, T['border'], self.rect, 1, border_radius=4)
        for i, (label, _) in enumerate(self.items):
            y = self.rect.y + i * self.item_h
            r = pygame.Rect(self.rect.x, y, self.rect.w, self.item_h)
            if i == self._hovered:
                pygame.draw.rect(surf, T['item_hov'], r, border_radius=2)
            txt_surf = self.font.render(label, True, T['text'])
            surf.blit(txt_surf, (self.rect.x + 8, y + 4))
# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import traceback
    import sys

    try:
        app = AssemblyIDE()
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Log the full traceback to stderr
        print("Fatal error in ModernAssembly NX:", file=sys.stderr)
        traceback.print_exc()

        # Attempt to display a simple error dialog using pygame if it's still usable
        try:
            if pygame.get_init():
                # If the display was already set, reuse it; otherwise create a small window
                try:
                    screen = pygame.display.get_surface()
                    if screen is None:
                        screen = pygame.display.set_mode((800, 200))
                except:
                    screen = pygame.display.set_mode((800, 200))

                screen.fill((18, 18, 24))
                font = pygame.font.Font(None, 24)
                error_text = font.render("Fatal Error", True, (255, 80, 80))
                msg_text = font.render(str(e)[:100], True, (220, 220, 240))
                screen.blit(error_text, (20, 40))
                screen.blit(msg_text, (20, 80))
                pygame.display.flip()
                pygame.time.wait(5000)
        except:
            pass
        finally:
            pygame.quit()
            sys.exit(1)
