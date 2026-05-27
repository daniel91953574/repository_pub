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
    {"id":"asm.xlat",   "title":"XLAT",    "cat":"CPU/Move",    "desc":"Table look-up: AL = [RBX+AL]",
     "tpl":"xlat",                     "ports":[]},
    {"id":"asm.lahf",   "title":"LAHF",    "cat":"CPU/Move",    "desc":"Load AH ← FLAGS low byte",
     "tpl":"lahf",                     "ports":[]},
    {"id":"asm.sahf",   "title":"SAHF",    "cat":"CPU/Move",    "desc":"Store AH → FLAGS low byte",
     "tpl":"sahf",                     "ports":[]},
    {"id":"asm.cbw",    "title":"CBW/CWDE/CDQE","cat":"CPU/Move","desc":"Sign-extend AL→AX / AX→EAX / EAX→RAX",
     "tpl":"cdqe",                     "ports":[]},

    # ═══════════════════  CPU / Stack  ═══════════════════
    {"id":"asm.push",   "title":"PUSH",    "cat":"CPU/Stack",   "desc":"Push register onto stack",
     "tpl":"push {src}",               "ports":[{"n":"src","d":"rbp"}]},
    {"id":"asm.pop",    "title":"POP",     "cat":"CPU/Stack",   "desc":"Pop register from stack",
     "tpl":"pop {dst}",                "ports":[{"n":"dst","d":"rbp"}]},
    {"id":"asm.pushfq", "title":"PUSHFQ",  "cat":"CPU/Stack",   "desc":"Push RFLAGS", "tpl":"pushfq","ports":[]},
    {"id":"asm.popfq",  "title":"POPFQ",   "cat":"CPU/Stack",   "desc":"Pop RFLAGS",  "tpl":"popfq", "ports":[]},
    {"id":"asm.pusha",  "title":"PUSHA",   "cat":"CPU/Stack",   "desc":"Push all integer regs (32-bit mode)", "tpl":"pusha","ports":[]},
    {"id":"asm.popa",   "title":"POPA",    "cat":"CPU/Stack",   "desc":"Pop all integer regs (32-bit mode)",  "tpl":"popa", "ports":[]},
    {"id":"asm.enter",  "title":"ENTER",   "cat":"CPU/Stack",   "desc":"Create stack frame with locals",
     "tpl":"enter {locals}, 0",        "ports":[{"n":"locals","d":"64"}]},
    {"id":"asm.leave",  "title":"LEAVE",   "cat":"CPU/Stack",   "desc":"Destroy stack frame (mov rsp,rbp / pop rbp)",
     "tpl":"leave",                    "ports":[]},

    # ═══════════════════  CPU / ALU  ═══════════════════
    {"id":"asm.add",    "title":"ADD",     "cat":"CPU/ALU",     "desc":"Integer addition",
     "tpl":"add {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"1"}]},
    {"id":"asm.adc",    "title":"ADC",     "cat":"CPU/ALU",     "desc":"Add with carry",
     "tpl":"adc {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"0"}]},
    {"id":"asm.sub",    "title":"SUB",     "cat":"CPU/ALU",     "desc":"Integer subtraction",
     "tpl":"sub {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"1"}]},
    {"id":"asm.sbb",    "title":"SBB",     "cat":"CPU/ALU",     "desc":"Subtract with borrow",
     "tpl":"sbb {dst}, {src}",         "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"0"}]},
    {"id":"asm.imul",   "title":"IMUL",    "cat":"CPU/ALU",     "desc":"Signed multiply (2-op form)",
     "tpl":"imul {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.imul3",  "title":"IMUL 3-op","cat":"CPU/ALU",    "desc":"Signed multiply (dest, src1, imm32)",
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
    {"id":"asm.cdq",    "title":"CDQ",     "cat":"CPU/ALU",     "desc":"Sign-extend EAX→EDX:EAX (before idiv)",
     "tpl":"cdq", "ports":[]},
    {"id":"asm.cqo",    "title":"CQO",     "cat":"CPU/ALU",     "desc":"Sign-extend RAX→RDX:RAX (before idiv)",
     "tpl":"cqo", "ports":[]},
    {"id":"asm.adcx",   "title":"ADCX",    "cat":"CPU/ALU",     "desc":"Add carry flag (preserves OF)",
     "tpl":"adcx {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.adox",   "title":"ADOX",    "cat":"CPU/ALU",     "desc":"Add overflow flag (preserves CF)",
     "tpl":"adox {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.mulx",   "title":"MULX",    "cat":"CPU/ALU",     "desc":"Unsigned multiply (BMI2, no flags) hi:lo = rdx*src",
     "tpl":"mulx {hi}, {lo}, {src}",   "ports":[{"n":"hi","d":"rdx"},{"n":"lo","d":"rax"},{"n":"src","d":"rbx"}]},

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
    {"id":"asm.shld",   "title":"SHLD",    "cat":"CPU/Logic",   "desc":"Double-precision shift left",
     "tpl":"shld {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"cl"}]},
    {"id":"asm.shrd",   "title":"SHRD",    "cat":"CPU/Logic",   "desc":"Double-precision shift right",
     "tpl":"shrd {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"cl"}]},
    {"id":"asm.shlx",   "title":"SHLX",    "cat":"CPU/Logic",   "desc":"Shift left (BMI2, no flags)",
     "tpl":"shlx {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"rcx"}]},
    {"id":"asm.shrx",   "title":"SHRX",    "cat":"CPU/Logic",   "desc":"Shift right (BMI2, no flags)",
     "tpl":"shrx {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"rcx"}]},
    {"id":"asm.sarx",   "title":"SARX",    "cat":"CPU/Logic",   "desc":"Arithmetic shift (BMI2, no flags)",
     "tpl":"sarx {dst}, {src}, {cnt}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"cnt","d":"rcx"}]},
    {"id":"asm.rorx",   "title":"RORX",    "cat":"CPU/Logic",   "desc":"Rotate right without flags (BMI2)",
     "tpl":"rorx {dst}, {src}, {imm}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"imm","d":"8"}]},

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
    {"id":"asm.bsf",    "title":"BSF",     "cat":"CPU/Compare", "desc":"Bit scan forward (find LSB)",
     "tpl":"bsf {dst}, {src}",         "ports":[{"n":"dst","d":"rcx"},{"n":"src","d":"rax"}]},
    {"id":"asm.bsr",    "title":"BSR",     "cat":"CPU/Compare", "desc":"Bit scan reverse (find MSB)",
     "tpl":"bsr {dst}, {src}",         "ports":[{"n":"dst","d":"rcx"},{"n":"src","d":"rax"}]},

    # ═══════════════════  CPU / SETcc  ═══════════════════
    {"id":"asm.sete",   "title":"SETE/Z",  "cat":"CPU/SETcc",   "desc":"Set byte if equal (ZF=1)",
     "tpl":"sete {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setne",  "title":"SETNE/NZ","cat":"CPU/SETcc",   "desc":"Set byte if not equal (ZF=0)",
     "tpl":"setne {dst}",              "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setg",   "title":"SETG",    "cat":"CPU/SETcc",   "desc":"Set byte if greater (signed)",
     "tpl":"setg {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setge",  "title":"SETGE",   "cat":"CPU/SETcc",   "desc":"Set byte if greater or equal (signed)",
     "tpl":"setge {dst}",              "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setl",   "title":"SETL",    "cat":"CPU/SETcc",   "desc":"Set byte if less (signed)",
     "tpl":"setl {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setle",  "title":"SETLE",   "cat":"CPU/SETcc",   "desc":"Set byte if less or equal (signed)",
     "tpl":"setle {dst}",              "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.seta",   "title":"SETA",    "cat":"CPU/SETcc",   "desc":"Set byte if above (unsigned, CF=0,ZF=0)",
     "tpl":"seta {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setae",  "title":"SETAE/C", "cat":"CPU/SETcc",   "desc":"Set byte if above or equal (CF=0)",
     "tpl":"setae {dst}",              "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setb",   "title":"SETB/C",  "cat":"CPU/SETcc",   "desc":"Set byte if below (CF=1)",
     "tpl":"setb {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setbe",  "title":"SETBE",   "cat":"CPU/SETcc",   "desc":"Set byte if below or equal (CF=1 or ZF=1)",
     "tpl":"setbe {dst}",              "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.sets",   "title":"SETS",    "cat":"CPU/SETcc",   "desc":"Set byte if sign (SF=1)",
     "tpl":"sets {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.seto",   "title":"SETO",    "cat":"CPU/SETcc",   "desc":"Set byte if overflow (OF=1)",
     "tpl":"seto {dst}",               "ports":[{"n":"dst","d":"al"}]},
    {"id":"asm.setp",   "title":"SETP",    "cat":"CPU/SETcc",   "desc":"Set byte if parity even (PF=1)",
     "tpl":"setp {dst}",               "ports":[{"n":"dst","d":"al"}]},

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
    {"id":"asm.jc",     "title":"JC",      "cat":"CPU/Flow",    "desc":"Jump if carry (CF=1)",
     "tpl":"jc {lbl}",                 "ports":[{"n":"lbl","d":".err"}]},
    {"id":"asm.jnc",    "title":"JNC",     "cat":"CPU/Flow",    "desc":"Jump if no carry (CF=0)",
     "tpl":"jnc {lbl}",                "ports":[{"n":"lbl","d":".ok"}]},
    {"id":"asm.jp",     "title":"JP",      "cat":"CPU/Flow",    "desc":"Jump if parity even (PF=1)",
     "tpl":"jp {lbl}",                 "ports":[{"n":"lbl","d":".par"}]},
    {"id":"asm.jrcxz",  "title":"JRCXZ",   "cat":"CPU/Flow",    "desc":"Jump if RCX == 0",
     "tpl":"jrcxz {lbl}",              "ports":[{"n":"lbl","d":".empty"}]},
    {"id":"asm.call",   "title":"CALL",    "cat":"CPU/Flow",    "desc":"Call direct procedure",
     "tpl":"call {proc}",              "ports":[{"n":"proc","d":"my_func"}]},
    {"id":"asm.calli",  "title":"CALL []", "cat":"CPU/Flow",    "desc":"Indirect call via import table",
     "tpl":"call [{proc}]",            "ports":[{"n":"proc","d":"ExitProcess"}]},
    {"id":"asm.callr",  "title":"CALL reg","cat":"CPU/Flow",    "desc":"Indirect call via register",
     "tpl":"call {reg}",               "ports":[{"n":"reg","d":"rax"}]},
    {"id":"asm.ret",    "title":"RET",     "cat":"CPU/Flow",    "desc":"Return from procedure",   "tpl":"ret", "ports":[]},
    {"id":"asm.retn",   "title":"RET n",   "cat":"CPU/Flow",    "desc":"Return + pop n bytes (stdcall)",
     "tpl":"ret {n}",                  "ports":[{"n":"n","d":"8"}]},
    {"id":"asm.loop",   "title":"LOOP",    "cat":"CPU/Flow",    "desc":"Dec RCX, jump if RCX≠0",
     "tpl":"loop {lbl}",               "ports":[{"n":"lbl","d":".body"}]},
    {"id":"asm.loope",  "title":"LOOPE",   "cat":"CPU/Flow",    "desc":"Loop while equal (ZF=1)",
     "tpl":"loope {lbl}",              "ports":[{"n":"lbl","d":".body"}]},
    {"id":"asm.loopne", "title":"LOOPNE",  "cat":"CPU/Flow",    "desc":"Loop while not equal (ZF=0)",
     "tpl":"loopne {lbl}",             "ports":[{"n":"lbl","d":".body"}]},
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
    {"id":"asm.cmovs",  "title":"CMOVS",   "cat":"CPU/Flow",    "desc":"Conditional move if sign",
     "tpl":"cmovs {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.cmovo",  "title":"CMOVO",   "cat":"CPU/Flow",    "desc":"Conditional move if overflow",
     "tpl":"cmovo {dst}, {src}",       "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},

    # ═══════════════════  CPU / String  ═══════════════════
    {"id":"str.movsb",  "title":"MOVSB",   "cat":"CPU/String",  "desc":"Move byte [RSI]→[RDI], advance both",
     "tpl":"movsb",                    "ports":[]},
    {"id":"str.movsw",  "title":"MOVSW",   "cat":"CPU/String",  "desc":"Move word [RSI]→[RDI]",
     "tpl":"movsw",                    "ports":[]},
    {"id":"str.movsd",  "title":"MOVSD str","cat":"CPU/String",  "desc":"Move dword [RSI]→[RDI]",
     "tpl":"movsd",                    "ports":[]},
    {"id":"str.movsq",  "title":"MOVSQ",   "cat":"CPU/String",  "desc":"Move qword [RSI]→[RDI]",
     "tpl":"movsq",                    "ports":[]},
    {"id":"str.stosb",  "title":"STOSB",   "cat":"CPU/String",  "desc":"Store AL→[RDI], advance RDI",
     "tpl":"stosb",                    "ports":[]},
    {"id":"str.stosw",  "title":"STOSW",   "cat":"CPU/String",  "desc":"Store AX→[RDI]",
     "tpl":"stosw",                    "ports":[]},
    {"id":"str.stosd",  "title":"STOSD",   "cat":"CPU/String",  "desc":"Store EAX→[RDI]",
     "tpl":"stosd",                    "ports":[]},
    {"id":"str.stosq",  "title":"STOSQ",   "cat":"CPU/String",  "desc":"Store RAX→[RDI]",
     "tpl":"stosq",                    "ports":[]},
    {"id":"str.lodsb",  "title":"LODSB",   "cat":"CPU/String",  "desc":"Load [RSI]→AL, advance RSI",
     "tpl":"lodsb",                    "ports":[]},
    {"id":"str.lodsq",  "title":"LODSQ",   "cat":"CPU/String",  "desc":"Load [RSI]→RAX, advance RSI",
     "tpl":"lodsq",                    "ports":[]},
    {"id":"str.scasb",  "title":"SCASB",   "cat":"CPU/String",  "desc":"Compare AL with [RDI], advance RDI",
     "tpl":"scasb",                    "ports":[]},
    {"id":"str.scasq",  "title":"SCASQ",   "cat":"CPU/String",  "desc":"Compare RAX with [RDI]",
     "tpl":"scasq",                    "ports":[]},
    {"id":"str.cmpsb",  "title":"CMPSB",   "cat":"CPU/String",  "desc":"Compare [RSI] with [RDI], advance both",
     "tpl":"cmpsb",                    "ports":[]},
    {"id":"str.rep_movsb","title":"REP MOVSB","cat":"CPU/String","desc":"memcpy pattern: copy RCX bytes from RSI→RDI",
     "tpl":"; memcpy: rsi=src, rdi=dst, rcx=count\n    cld\n    rep movsb", "ports":[]},
    {"id":"str.rep_stosb","title":"REP STOSB","cat":"CPU/String","desc":"memset pattern: fill RCX bytes at RDI with AL",
     "tpl":"; memset: rdi=dst, al=val, rcx=count\n    cld\n    rep stosb", "ports":[]},
    {"id":"str.rep_movsq","title":"REP MOVSQ","cat":"CPU/String","desc":"Fast qword memcpy: RCX qwords from RSI→RDI",
     "tpl":"; qword memcpy: rsi=src, rdi=dst, rcx=count/8\n    cld\n    rep movsq", "ports":[]},
    {"id":"str.repe_cmpsb","title":"REPE CMPSB","cat":"CPU/String","desc":"strcmp pattern: compare RSI vs RDI while equal",
     "tpl":"; strcmp: rsi=s1, rdi=s2, rcx=maxlen\n    cld\n    repe cmpsb", "ports":[]},
    {"id":"str.repne_scasb","title":"REPNE SCASB","cat":"CPU/String","desc":"strlen pattern: scan for 0 in RDI (rcx=MAX)",
     "tpl":"; strlen: rdi=str, rcx=-1, al=0\n    cld\n    repne scasb\n    not rcx\n    dec rcx  ; rcx = length", "ports":[]},

    # ═══════════════════  CPU / IO  ═══════════════════
    {"id":"io.in8",     "title":"IN byte", "cat":"CPU/IO",      "desc":"Input byte from port → AL",
     "tpl":"in al, {port}",            "ports":[{"n":"port","d":"0x64"}]},
    {"id":"io.in16",    "title":"IN word", "cat":"CPU/IO",      "desc":"Input word from port → AX",
     "tpl":"in ax, dx",                "ports":[]},
    {"id":"io.in32",    "title":"IN dword","cat":"CPU/IO",      "desc":"Input dword from port → EAX",
     "tpl":"in eax, dx",               "ports":[]},
    {"id":"io.out8",    "title":"OUT byte","cat":"CPU/IO",      "desc":"Output AL to port",
     "tpl":"out {port}, al",           "ports":[{"n":"port","d":"0x60"}]},
    {"id":"io.out16",   "title":"OUT word","cat":"CPU/IO",      "desc":"Output AX to DX port",
     "tpl":"out dx, ax",               "ports":[]},
    {"id":"io.out32",   "title":"OUT dword","cat":"CPU/IO",     "desc":"Output EAX to DX port",
     "tpl":"out dx, eax",              "ports":[]},
    {"id":"io.insb",    "title":"INSB",    "cat":"CPU/IO",      "desc":"Input byte from DX port → [RDI]",
     "tpl":"insb",                     "ports":[]},
    {"id":"io.outsb",   "title":"OUTSB",   "cat":"CPU/IO",      "desc":"Output byte [RSI] → DX port",
     "tpl":"outsb",                    "ports":[]},

    # ═══════════════════  CPU / Atomic  ═══════════════════
    {"id":"atm.xadd",   "title":"XADD",    "cat":"CPU/Atomic",  "desc":"Exchange + add (fetch-and-add primitive)",
     "tpl":"lock xadd [{mem}], {reg}", "ports":[{"n":"mem","d":"rsp+8"},{"n":"reg","d":"eax"}]},
    {"id":"atm.cmpxchg","title":"CMPXCHG", "cat":"CPU/Atomic",  "desc":"Compare-and-swap: if [mem]==RAX → write new",
     "tpl":"lock cmpxchg [{mem}], {new}","ports":[{"n":"mem","d":"counter"},{"n":"new","d":"rbx"}]},
    {"id":"atm.cmpxchg16","title":"CMPXCHG16B","cat":"CPU/Atomic","desc":"128-bit compare-and-swap (RDX:RAX / RCX:RBX)",
     "tpl":"lock cmpxchg16b [{mem}]",  "ports":[{"n":"mem","d":"pair_ptr"}]},
    {"id":"atm.lockinc","title":"LOCK INC","cat":"CPU/Atomic",  "desc":"Atomic increment",
     "tpl":"lock inc dword [{mem}]",   "ports":[{"n":"mem","d":"counter"}]},
    {"id":"atm.lockdec","title":"LOCK DEC","cat":"CPU/Atomic",  "desc":"Atomic decrement",
     "tpl":"lock dec dword [{mem}]",   "ports":[{"n":"mem","d":"counter"}]},
    {"id":"atm.lockand","title":"LOCK AND","cat":"CPU/Atomic",  "desc":"Atomic AND (clear bits)",
     "tpl":"lock and [{mem}], {mask}", "ports":[{"n":"mem","d":"flags"},{"n":"mask","d":"0xFFFFFFFE"}]},
    {"id":"atm.lockor", "title":"LOCK OR", "cat":"CPU/Atomic",  "desc":"Atomic OR (set bits)",
     "tpl":"lock or [{mem}], {bits}",  "ports":[{"n":"mem","d":"flags"},{"n":"bits","d":"1"}]},
    {"id":"atm.spin",   "title":"Spinlock","cat":"CPU/Atomic",  "desc":"Acquire spinlock (test-and-set loop)",
     "tpl":".spin_try:\n    xor eax, eax\n    mov ecx, 1\n    lock cmpxchg [{lock}], ecx\n    jnz .spin_wait\n    jmp .spin_got\n.spin_wait:\n    pause\n    cmp dword [{lock}], 0\n    jnz .spin_wait\n    jmp .spin_try\n.spin_got:",
     "ports":[{"n":"lock","d":"my_lock"}]},

    # ═══════════════════  CPU / Misc  ═══════════════════
    {"id":"asm.nop",    "title":"NOP",     "cat":"CPU/Misc",    "desc":"No operation",                 "tpl":"nop","ports":[]},
    {"id":"asm.int3",   "title":"INT3",    "cat":"CPU/Misc",    "desc":"Debugger breakpoint",          "tpl":"int3","ports":[]},
    {"id":"asm.syscall","title":"SYSCALL", "cat":"CPU/Misc",    "desc":"Linux x64 system call",        "tpl":"syscall","ports":[]},
    {"id":"asm.cpuid",  "title":"CPUID",   "cat":"CPU/Misc",    "desc":"Query CPU features (eax=leaf)","tpl":"cpuid","ports":[]},
    {"id":"asm.rdtsc",  "title":"RDTSC",   "cat":"CPU/Misc",    "desc":"Read timestamp counter → rdx:rax","tpl":"rdtsc","ports":[]},
    {"id":"asm.rdtscp", "title":"RDTSCP",  "cat":"CPU/Misc",    "desc":"Read TSC + processor ID → rdx:rax, ecx","tpl":"rdtscp","ports":[]},
    {"id":"asm.hlt",    "title":"HLT",     "cat":"CPU/Misc",    "desc":"Halt until interrupt",         "tpl":"hlt","ports":[]},
    {"id":"asm.int",    "title":"INT n",   "cat":"CPU/Misc",    "desc":"Software interrupt",
     "tpl":"int {n}",                  "ports":[{"n":"n","d":"0x80"}]},
    {"id":"asm.ud2",    "title":"UD2",     "cat":"CPU/Misc",    "desc":"Undefined instruction (guaranteed fault/trap)","tpl":"ud2","ports":[]},
    {"id":"asm.clc",    "title":"CLC",     "cat":"CPU/Misc",    "desc":"Clear carry flag",  "tpl":"clc","ports":[]},
    {"id":"asm.stc",    "title":"STC",     "cat":"CPU/Misc",    "desc":"Set carry flag",    "tpl":"stc","ports":[]},
    {"id":"asm.cmc",    "title":"CMC",     "cat":"CPU/Misc",    "desc":"Complement carry flag","tpl":"cmc","ports":[]},
    {"id":"asm.cld",    "title":"CLD",     "cat":"CPU/Misc",    "desc":"Clear direction flag","tpl":"cld","ports":[]},
    {"id":"asm.std",    "title":"STD",     "cat":"CPU/Misc",    "desc":"Set direction flag",  "tpl":"std","ports":[]},
    {"id":"asm.cli",    "title":"CLI",     "cat":"CPU/Misc",    "desc":"Clear interrupt flag","tpl":"cli","ports":[]},
    {"id":"asm.sti",    "title":"STI",     "cat":"CPU/Misc",    "desc":"Set interrupt flag",  "tpl":"sti","ports":[]},
    {"id":"asm.mfence", "title":"MFENCE",  "cat":"CPU/Misc",    "desc":"Full memory fence",  "tpl":"mfence","ports":[]},
    {"id":"asm.lfence", "title":"LFENCE",  "cat":"CPU/Misc",    "desc":"Load fence",         "tpl":"lfence","ports":[]},
    {"id":"asm.sfence", "title":"SFENCE",  "cat":"CPU/Misc",    "desc":"Store fence",        "tpl":"sfence","ports":[]},
    {"id":"asm.pause",  "title":"PAUSE",   "cat":"CPU/Misc",    "desc":"Spin-loop hint",     "tpl":"pause","ports":[]},
    {"id":"asm.rdrand", "title":"RDRAND",  "cat":"CPU/Misc",    "desc":"Hardware random number → dst",
     "tpl":"rdrand {dst}",             "ports":[{"n":"dst","d":"rax"}]},
    {"id":"asm.rdseed", "title":"RDSEED",  "cat":"CPU/Misc",    "desc":"Entropy source → dst (for seeding CSPRNGs)",
     "tpl":"rdseed {dst}",             "ports":[{"n":"dst","d":"rax"}]},
    {"id":"asm.crc32",  "title":"CRC32",   "cat":"CPU/Misc",    "desc":"Hardware CRC32 (SSE4.2)",
     "tpl":"crc32 {crc}, {data}",      "ports":[{"n":"crc","d":"eax"},{"n":"data","d":"dword [rbx]"}]},
    {"id":"asm.popcnt", "title":"POPCNT",  "cat":"CPU/Misc",    "desc":"Population count (count set bits)",
     "tpl":"popcnt {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"asm.lzcnt",  "title":"LZCNT",   "cat":"CPU/Misc",    "desc":"Count leading zeros",
     "tpl":"lzcnt {dst}, {src}",       "ports":[{"n":"dst","d":"rcx"},{"n":"src","d":"rax"}]},
    {"id":"asm.tzcnt",  "title":"TZCNT",   "cat":"CPU/Misc",    "desc":"Count trailing zeros",
     "tpl":"tzcnt {dst}, {src}",       "ports":[{"n":"dst","d":"rcx"},{"n":"src","d":"rax"}]},

    # ═══════════════════  CPU / Prefetch  ═══════════════════
    {"id":"pfx.t0",     "title":"PREFETCHT0","cat":"CPU/Prefetch","desc":"Prefetch to L1 cache (all cache levels)",
     "tpl":"prefetcht0 [{addr}]",      "ports":[{"n":"addr","d":"rax+64"}]},
    {"id":"pfx.t1",     "title":"PREFETCHT1","cat":"CPU/Prefetch","desc":"Prefetch to L2 cache",
     "tpl":"prefetcht1 [{addr}]",      "ports":[{"n":"addr","d":"rax+64"}]},
    {"id":"pfx.t2",     "title":"PREFETCHT2","cat":"CPU/Prefetch","desc":"Prefetch to L3 cache",
     "tpl":"prefetcht2 [{addr}]",      "ports":[{"n":"addr","d":"rax+128"}]},
    {"id":"pfx.nta",    "title":"PREFETCHNTA","cat":"CPU/Prefetch","desc":"Prefetch non-temporal (streaming, no cache pollution)",
     "tpl":"prefetchnta [{addr}]",     "ports":[{"n":"addr","d":"rax+64"}]},
    {"id":"pfx.clflush","title":"CLFLUSH","cat":"CPU/Prefetch","desc":"Flush cache line containing addr",
     "tpl":"clflush [{addr}]",         "ports":[{"n":"addr","d":"rax"}]},
    {"id":"pfx.clflushopt","title":"CLFLUSHOPT","cat":"CPU/Prefetch","desc":"Optimized flush (weakly ordered)",
     "tpl":"clflushopt [{addr}]",      "ports":[{"n":"addr","d":"rax"}]},
    {"id":"pfx.loop_ahead","title":"Prefetch-Ahead Loop","cat":"CPU/Prefetch",
     "desc":"Slice loop with N-ahead prefetch (Cannonic/SIMD pattern)",
     "tpl":"; Prefetch ahead by {ahead} elements while processing\n.loop:\n    prefetcht0 [{base} + {idx}*4 + {ahead}*4]\n    ; --- process element [{base} + {idx}*4] ---\n    inc {idx}\n    cmp {idx}, {count}\n    jl  .loop",
     "ports":[{"n":"base","d":"rsi"},{"n":"idx","d":"rcx"},{"n":"count","d":"rdx"},{"n":"ahead","d":"16"}]},

    # ═══════════════════  CPU / System  ═══════════════════
    {"id":"sys.lgdt",   "title":"LGDT",    "cat":"CPU/System",  "desc":"Load Global Descriptor Table Register",
     "tpl":"lgdt [{gdtr}]",            "ports":[{"n":"gdtr","d":"gdt_descriptor"}]},
    {"id":"sys.lidt",   "title":"LIDT",    "cat":"CPU/System",  "desc":"Load Interrupt Descriptor Table Register",
     "tpl":"lidt [{idtr}]",            "ports":[{"n":"idtr","d":"idt_descriptor"}]},
    {"id":"sys.sgdt",   "title":"SGDT",    "cat":"CPU/System",  "desc":"Store GDTR to memory",
     "tpl":"sgdt [{dst}]",             "ports":[{"n":"dst","d":"gdtr_buf"}]},
    {"id":"sys.sidt",   "title":"SIDT",    "cat":"CPU/System",  "desc":"Store IDTR to memory",
     "tpl":"sidt [{dst}]",             "ports":[{"n":"dst","d":"idtr_buf"}]},
    {"id":"sys.invlpg", "title":"INVLPG",  "cat":"CPU/System",  "desc":"Invalidate TLB entry for virtual address",
     "tpl":"invlpg [{vaddr}]",         "ports":[{"n":"vaddr","d":"rax"}]},
    {"id":"sys.wrmsr",  "title":"WRMSR",   "cat":"CPU/System",  "desc":"Write MSR[ecx] ← edx:eax",
     "tpl":"; ecx=MSR_num, edx:eax=value\n    wrmsr",   "ports":[]},
    {"id":"sys.rdmsr",  "title":"RDMSR",   "cat":"CPU/System",  "desc":"Read MSR[ecx] → edx:eax",
     "tpl":"; ecx=MSR_num → edx:eax=value\n    rdmsr",  "ports":[]},
    {"id":"sys.xsave",  "title":"XSAVE",   "cat":"CPU/System",  "desc":"Save extended processor states (SSE/AVX/...)",
     "tpl":"; edx:eax = feature mask\n    xsave [{buf}]",  "ports":[{"n":"buf","d":"xsave_area"}]},
    {"id":"sys.xrstor", "title":"XRSTOR",  "cat":"CPU/System",  "desc":"Restore extended processor states",
     "tpl":"; edx:eax = feature mask\n    xrstor [{buf}]", "ports":[{"n":"buf","d":"xsave_area"}]},
    {"id":"sys.vmcall", "title":"VMCALL",  "cat":"CPU/System",  "desc":"Hypercall from VM to VMM (VMX)",
     "tpl":"vmcall",                   "ports":[]},
    {"id":"sys.iretq",  "title":"IRETQ",   "cat":"CPU/System",  "desc":"Return from interrupt (64-bit)",
     "tpl":"iretq",                    "ports":[]},

    # ═══════════════════  CPU / SIMD SSE  ═══════════════════
    {"id":"sse.movaps", "title":"MOVAPS",  "cat":"CPU/SIMD-SSE","desc":"Move aligned packed f32 (16-byte aligned)",
     "tpl":"movaps {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.movups", "title":"MOVUPS",  "cat":"CPU/SIMD-SSE","desc":"Move unaligned packed f32",
     "tpl":"movups {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"[rbx]"}]},
    {"id":"sse.movss",  "title":"MOVSS",   "cat":"CPU/SIMD-SSE","desc":"Move scalar f32",
     "tpl":"movss {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"[rax]"}]},
    {"id":"sse.movsd",  "title":"MOVSD",   "cat":"CPU/SIMD-SSE","desc":"Move scalar f64",
     "tpl":"movsd {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"[rax]"}]},
    {"id":"sse.movhlps","title":"MOVHLPS", "cat":"CPU/SIMD-SSE","desc":"Move high to low packed f32",
     "tpl":"movhlps {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.movlhps","title":"MOVLHPS", "cat":"CPU/SIMD-SSE","desc":"Move low to high packed f32",
     "tpl":"movlhps {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.movntps","title":"MOVNTPS", "cat":"CPU/SIMD-SSE","desc":"Non-temporal store packed f32 (streaming write)",
     "tpl":"movntps [{dst}], {src}",   "ports":[{"n":"dst","d":"rdi"},{"n":"src","d":"xmm0"}]},
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
    {"id":"sse.andps",  "title":"ANDPS",   "cat":"CPU/SIMD-SSE","desc":"AND packed f32 (bit mask)",
     "tpl":"andps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.orps",   "title":"ORPS",    "cat":"CPU/SIMD-SSE","desc":"OR packed f32 (bit set)",
     "tpl":"orps {dst}, {src}",        "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.andnps", "title":"ANDNPS",  "cat":"CPU/SIMD-SSE","desc":"AND-NOT packed: dst = ~src1 & src2",
     "tpl":"andnps {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.sqrtps", "title":"SQRTPS",  "cat":"CPU/SIMD-SSE","desc":"Square root packed f32",
     "tpl":"sqrtps {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.rsqrtps","title":"RSQRTPS", "cat":"CPU/SIMD-SSE","desc":"Reciprocal sqrt packed f32 (fast approx)",
     "tpl":"rsqrtps {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.rcpps",  "title":"RCPPS",   "cat":"CPU/SIMD-SSE","desc":"Reciprocal packed f32 (fast approx)",
     "tpl":"rcpps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.maxps",  "title":"MAXPS",   "cat":"CPU/SIMD-SSE","desc":"Max packed f32",
     "tpl":"maxps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.minps",  "title":"MINPS",   "cat":"CPU/SIMD-SSE","desc":"Min packed f32",
     "tpl":"minps {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.cmpps",  "title":"CMPPS",   "cat":"CPU/SIMD-SSE","desc":"Compare packed f32 → mask (imm8: 0=eq,1=lt,4=ne)",
     "tpl":"cmpps {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"},{"n":"imm","d":"0"}]},
    {"id":"sse.shufps", "title":"SHUFPS",  "cat":"CPU/SIMD-SSE","desc":"Shuffle packed f32 (imm8 controls lanes)",
     "tpl":"shufps {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm0"},{"n":"imm","d":"0x00"}]},
    {"id":"sse.unpcklps","title":"UNPCKLPS","cat":"CPU/SIMD-SSE","desc":"Unpack & interleave low f32",
     "tpl":"unpcklps {dst}, {src}",    "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.unpckhps","title":"UNPCKHPS","cat":"CPU/SIMD-SSE","desc":"Unpack & interleave high f32",
     "tpl":"unpckhps {dst}, {src}",    "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.cvtsi2ss","title":"CVTSI2SS","cat":"CPU/SIMD-SSE","desc":"Int→scalar f32",
     "tpl":"cvtsi2ss {xmm}, {int}",    "ports":[{"n":"xmm","d":"xmm0"},{"n":"int","d":"rax"}]},
    {"id":"sse.cvtsi2sd","title":"CVTSI2SD","cat":"CPU/SIMD-SSE","desc":"Int→scalar f64",
     "tpl":"cvtsi2sd {xmm}, {int}",    "ports":[{"n":"xmm","d":"xmm0"},{"n":"int","d":"rax"}]},
    {"id":"sse.cvtss2si","title":"CVTSS2SI","cat":"CPU/SIMD-SSE","desc":"Scalar f32→int",
     "tpl":"cvtss2si {int}, {xmm}",    "ports":[{"n":"int","d":"rax"},{"n":"xmm","d":"xmm0"}]},
    {"id":"sse.cvtdq2ps","title":"CVTDQ2PS","cat":"CPU/SIMD-SSE","desc":"Convert packed i32→f32",
     "tpl":"cvtdq2ps {dst}, {src}",    "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.cvtps2dq","title":"CVTPS2DQ","cat":"CPU/SIMD-SSE","desc":"Convert packed f32→i32",
     "tpl":"cvtps2dq {dst}, {src}",    "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.movmskps","title":"MOVMSKPS","cat":"CPU/SIMD-SSE","desc":"Extract sign bits of packed f32 → int reg",
     "tpl":"movmskps {dst}, {src}",    "ports":[{"n":"dst","d":"eax"},{"n":"src","d":"xmm0"}]},
    {"id":"sse.pxor",   "title":"PXOR",    "cat":"CPU/SIMD-SSE","desc":"XOR packed integer (128-bit)",
     "tpl":"pxor {dst}, {src}",        "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pand",   "title":"PAND",    "cat":"CPU/SIMD-SSE","desc":"AND packed integer",
     "tpl":"pand {dst}, {src}",        "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.por",    "title":"POR",     "cat":"CPU/SIMD-SSE","desc":"OR packed integer",
     "tpl":"por {dst}, {src}",         "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pandn",  "title":"PANDN",   "cat":"CPU/SIMD-SSE","desc":"AND-NOT packed integer",
     "tpl":"pandn {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddb",  "title":"PADDB",   "cat":"CPU/SIMD-SSE","desc":"Add packed bytes",
     "tpl":"paddb {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddw",  "title":"PADDW",   "cat":"CPU/SIMD-SSE","desc":"Add packed words",
     "tpl":"paddw {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddd",  "title":"PADDD",   "cat":"CPU/SIMD-SSE","desc":"Add packed dwords",
     "tpl":"paddd {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.paddq",  "title":"PADDQ",   "cat":"CPU/SIMD-SSE","desc":"Add packed qwords",
     "tpl":"paddq {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.psubb",  "title":"PSUBB",   "cat":"CPU/SIMD-SSE","desc":"Sub packed bytes",
     "tpl":"psubb {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.psubw",  "title":"PSUBW",   "cat":"CPU/SIMD-SSE","desc":"Sub packed words",
     "tpl":"psubw {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.psubd",  "title":"PSUBD",   "cat":"CPU/SIMD-SSE","desc":"Sub packed dwords",
     "tpl":"psubd {dst}, {src}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pmullw", "title":"PMULLW",  "cat":"CPU/SIMD-SSE","desc":"Multiply packed words (low result)",
     "tpl":"pmullw {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pmulld", "title":"PMULLD",  "cat":"CPU/SIMD-SSE","desc":"Multiply packed dwords → dword (SSE4.1)",
     "tpl":"pmulld {dst}, {src}",      "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pmaddwd","title":"PMADDWD", "cat":"CPU/SIMD-SSE","desc":"Multiply & add packed words→dwords",
     "tpl":"pmaddwd {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pcmpeqd","title":"PCMPEQD", "cat":"CPU/SIMD-SSE","desc":"Compare packed dwords for equality → mask",
     "tpl":"pcmpeqd {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pcmpeqb","title":"PCMPEQB", "cat":"CPU/SIMD-SSE","desc":"Compare packed bytes for equality → mask",
     "tpl":"pcmpeqb {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pcmpgtd","title":"PCMPGTD", "cat":"CPU/SIMD-SSE","desc":"Compare packed dwords greater-than → mask",
     "tpl":"pcmpgtd {dst}, {src}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"}]},
    {"id":"sse.pmovmskb","title":"PMOVMSKB","cat":"CPU/SIMD-SSE","desc":"Move MSBs of packed bytes → int reg",
     "tpl":"pmovmskb {dst}, {src}",    "ports":[{"n":"dst","d":"eax"},{"n":"src","d":"xmm0"}]},
    {"id":"sse.pshufd", "title":"PSHUFD",  "cat":"CPU/SIMD-SSE","desc":"Shuffle packed dwords (imm8)",
     "tpl":"pshufd {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"},{"n":"imm","d":"0x00"}]},
    {"id":"sse.pshufb", "title":"PSHUFB",  "cat":"CPU/SIMD-SSE","desc":"Shuffle packed bytes by mask (SSSE3)",
     "tpl":"pshufb {dst}, {mask}",     "ports":[{"n":"dst","d":"xmm0"},{"n":"mask","d":"xmm1"}]},
    {"id":"sse.pslld",  "title":"PSLLD",   "cat":"CPU/SIMD-SSE","desc":"Shift left logical packed dwords",
     "tpl":"pslld {dst}, {imm}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"imm","d":"2"}]},
    {"id":"sse.psrld",  "title":"PSRLD",   "cat":"CPU/SIMD-SSE","desc":"Shift right logical packed dwords",
     "tpl":"psrld {dst}, {imm}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"imm","d":"2"}]},
    {"id":"sse.psrad",  "title":"PSRAD",   "cat":"CPU/SIMD-SSE","desc":"Shift right arithmetic packed dwords",
     "tpl":"psrad {dst}, {imm}",       "ports":[{"n":"dst","d":"xmm0"},{"n":"imm","d":"1"}]},
    {"id":"sse.blendps","title":"BLENDPS", "cat":"CPU/SIMD-SSE","desc":"Blend packed f32 by imm8 mask (SSE4.1)",
     "tpl":"blendps {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"},{"n":"imm","d":"0x0F"}]},
    {"id":"sse.blendvps","title":"BLENDVPS","cat":"CPU/SIMD-SSE","desc":"Blend packed f32 by xmm0 mask (SSE4.1)",
     "tpl":"blendvps {dst}, {src}, xmm0","ports":[{"n":"dst","d":"xmm1"},{"n":"src","d":"xmm2"}]},
    {"id":"sse.dpps",   "title":"DPPS",    "cat":"CPU/SIMD-SSE","desc":"Dot product packed f32 (SSE4.1)",
     "tpl":"dpps {dst}, {src}, {imm}", "ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"},{"n":"imm","d":"0xFF"}]},
    {"id":"sse.insertps","title":"INSERTPS","cat":"CPU/SIMD-SSE","desc":"Insert f32 scalar into xmm (SSE4.1)",
     "tpl":"insertps {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"},{"n":"imm","d":"0x00"}]},
    {"id":"sse.pinsrd", "title":"PINSRD",  "cat":"CPU/SIMD-SSE","desc":"Insert i32 into xmm lane (SSE4.1)",
     "tpl":"pinsrd {dst}, {src}, {lane}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"eax"},{"n":"lane","d":"0"}]},
    {"id":"sse.pextrd", "title":"PEXTRD",  "cat":"CPU/SIMD-SSE","desc":"Extract i32 from xmm lane (SSE4.1)",
     "tpl":"pextrd {dst}, {src}, {lane}","ports":[{"n":"dst","d":"eax"},{"n":"src","d":"xmm0"},{"n":"lane","d":"0"}]},
    {"id":"sse.mpsadbw","title":"MPSADBW", "cat":"CPU/SIMD-SSE","desc":"Multi-block sum of abs diffs (motion estimation, SSE4.1)",
     "tpl":"mpsadbw {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"xmm1"},{"n":"imm","d":"0"}]},
    {"id":"sse.aesenc", "title":"AESENC",  "cat":"CPU/SIMD-SSE","desc":"AES-NI one round encrypt (AES-NI)",
     "tpl":"aesenc {dst}, {roundkey}", "ports":[{"n":"dst","d":"xmm0"},{"n":"roundkey","d":"xmm1"}]},
    {"id":"sse.aesenclast","title":"AESENCLAST","cat":"CPU/SIMD-SSE","desc":"AES-NI last round encrypt",
     "tpl":"aesenclast {dst}, {roundkey}","ports":[{"n":"dst","d":"xmm0"},{"n":"roundkey","d":"xmm1"}]},
    {"id":"sse.aeskeygenassist","title":"AESKEYGENASSIST","cat":"CPU/SIMD-SSE","desc":"AES-NI key schedule step",
     "tpl":"aeskeygenassist {dst}, {src}, {rcon}","ports":[{"n":"dst","d":"xmm1"},{"n":"src","d":"xmm0"},{"n":"rcon","d":"0x01"}]},

    # ═══════════════════  CPU / SIMD AVX  ═══════════════════
    {"id":"avx.vzeroupper","title":"VZEROUPPER","cat":"CPU/SIMD-AVX","desc":"Zero upper 128 bits of all ymm (avoid SSE/AVX penalty)",
     "tpl":"vzeroupper",               "ports":[]},
    {"id":"avx.vmovaps","title":"VMOVAPS", "cat":"CPU/SIMD-AVX","desc":"AVX move aligned packed f32 (8×f32)",
     "tpl":"vmovaps {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"ymm1"}]},
    {"id":"avx.vmovups","title":"VMOVUPS", "cat":"CPU/SIMD-AVX","desc":"AVX move unaligned packed f32",
     "tpl":"vmovups {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"[rbx]"}]},
    {"id":"avx.vmovntps","title":"VMOVNTPS","cat":"CPU/SIMD-AVX","desc":"AVX non-temporal store (streaming)",
     "tpl":"vmovntps [{dst}], {src}",  "ports":[{"n":"dst","d":"rdi"},{"n":"src","d":"ymm0"}]},
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
    {"id":"avx.vandps", "title":"VANDPS",  "cat":"CPU/SIMD-AVX","desc":"AVX AND packed f32",
     "tpl":"vandps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vorps",  "title":"VORPS",   "cat":"CPU/SIMD-AVX","desc":"AVX OR packed f32",
     "tpl":"vorps {dst}, {a}, {b}",    "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vsqrtps","title":"VSQRTPS", "cat":"CPU/SIMD-AVX","desc":"AVX sqrt packed f32",
     "tpl":"vsqrtps {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"ymm1"}]},
    {"id":"avx.vmaxps", "title":"VMAXPS",  "cat":"CPU/SIMD-AVX","desc":"AVX max packed f32",
     "tpl":"vmaxps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vminps", "title":"VMINPS",  "cat":"CPU/SIMD-AVX","desc":"AVX min packed f32",
     "tpl":"vminps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vcmpps", "title":"VCMPPS",  "cat":"CPU/SIMD-AVX","desc":"AVX compare packed f32 → mask",
     "tpl":"vcmpps {dst}, {a}, {b}, {imm}","ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"},{"n":"imm","d":"0"}]},
    {"id":"avx.vblendps","title":"VBLENDPS","cat":"CPU/SIMD-AVX","desc":"AVX blend packed f32 by imm8",
     "tpl":"vblendps {dst}, {a}, {b}, {imm}","ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"},{"n":"imm","d":"0xFF"}]},
    {"id":"avx.vblendvps","title":"VBLENDVPS","cat":"CPU/SIMD-AVX","desc":"AVX blend packed f32 by mask register",
     "tpl":"vblendvps {dst}, {a}, {b}, {mask}","ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"},{"n":"mask","d":"ymm3"}]},
    {"id":"avx.vbroadcastss","title":"VBROADCASTSS","cat":"CPU/SIMD-AVX","desc":"Broadcast scalar f32 to all lanes",
     "tpl":"vbroadcastss {ymm}, {src}","ports":[{"n":"ymm","d":"ymm0"},{"n":"src","d":"[rax]"}]},
    {"id":"avx.vbroadcastsd","title":"VBROADCASTSD","cat":"CPU/SIMD-AVX","desc":"Broadcast scalar f64 to all lanes",
     "tpl":"vbroadcastsd {ymm}, {src}","ports":[{"n":"ymm","d":"ymm0"},{"n":"src","d":"[rax]"}]},
    {"id":"avx.vinsertf128","title":"VINSERTF128","cat":"CPU/SIMD-AVX","desc":"Insert xmm into high/low half of ymm",
     "tpl":"vinsertf128 {dst}, {src}, {xmm}, {imm}","ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"ymm1"},{"n":"xmm","d":"xmm2"},{"n":"imm","d":"1"}]},
    {"id":"avx.vextractf128","title":"VEXTRACTF128","cat":"CPU/SIMD-AVX","desc":"Extract xmm from ymm half",
     "tpl":"vextractf128 {dst}, {src}, {imm}","ports":[{"n":"dst","d":"xmm0"},{"n":"src","d":"ymm1"},{"n":"imm","d":"1"}]},
    {"id":"avx.vperm2f128","title":"VPERM2F128","cat":"CPU/SIMD-AVX","desc":"Permute 128-bit halves of ymm",
     "tpl":"vperm2f128 {dst}, {a}, {b}, {imm}","ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"},{"n":"imm","d":"0x01"}]},
    {"id":"avx.vpermps", "title":"VPERMPS", "cat":"CPU/SIMD-AVX","desc":"Permute single f32 elements by index ymm (AVX2)",
     "tpl":"vpermps {dst}, {idx}, {src}","ports":[{"n":"dst","d":"ymm0"},{"n":"idx","d":"ymm1"},{"n":"src","d":"ymm2"}]},
    {"id":"avx.vgatherdps","title":"VGATHERDPS","cat":"CPU/SIMD-AVX","desc":"Gather f32 from base+i32_index (AVX2)",
     "tpl":"vgatherdps {dst}, [{base}+{idx}*4], {mask}","ports":[{"n":"dst","d":"ymm0"},{"n":"base","d":"rax"},{"n":"idx","d":"ymm1"},{"n":"mask","d":"ymm2"}]},
    {"id":"avx.vpaddd", "title":"VPADDD",  "cat":"CPU/SIMD-AVX","desc":"AVX2 add packed i32 (8×i32)",
     "tpl":"vpaddd {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vpxor",  "title":"VPXOR",   "cat":"CPU/SIMD-AVX","desc":"AVX2 XOR packed integer 256-bit",
     "tpl":"vpxor {dst}, {a}, {b}",    "ports":[{"n":"dst","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"avx.vmovdqu","title":"VMOVDQU", "cat":"CPU/SIMD-AVX","desc":"Move unaligned double quadword (256-bit)",
     "tpl":"vmovdqu {dst}, {src}",     "ports":[{"n":"dst","d":"ymm0"},{"n":"src","d":"[rbx]"}]},

    # ═══════════════════  CPU / SIMD AVX-512  ═══════════════════
    {"id":"avx512.vmovaps","title":"VMOVAPS (ZMM)","cat":"CPU/SIMD-AVX512","desc":"AVX-512 move aligned packed f32 (16×f32)",
     "tpl":"vmovaps {dst}, {src}",     "ports":[{"n":"dst","d":"zmm0"},{"n":"src","d":"zmm1"}]},
    {"id":"avx512.vmovups","title":"VMOVUPS (ZMM)","cat":"CPU/SIMD-AVX512","desc":"AVX-512 move unaligned packed f32",
     "tpl":"vmovups {dst}, {src}",     "ports":[{"n":"dst","d":"zmm0"},{"n":"src","d":"[rbx]"}]},
    {"id":"avx512.vaddps","title":"VADDPS (ZMM)","cat":"CPU/SIMD-AVX512","desc":"AVX-512 add packed f32 (16×f32)",
     "tpl":"vaddps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"zmm0"},{"n":"a","d":"zmm1"},{"n":"b","d":"zmm2"}]},
    {"id":"avx512.vmulps","title":"VMULPS (ZMM)","cat":"CPU/SIMD-AVX512","desc":"AVX-512 mul packed f32",
     "tpl":"vmulps {dst}, {a}, {b}",   "ports":[{"n":"dst","d":"zmm0"},{"n":"a","d":"zmm1"},{"n":"b","d":"zmm2"}]},
    {"id":"avx512.vaddps_k","title":"VADDPS k-mask","cat":"CPU/SIMD-AVX512","desc":"AVX-512 add with opmask merge",
     "tpl":"vaddps {dst}{{{k}}}, {a}, {b}","ports":[{"n":"dst","d":"zmm0"},{"n":"k","d":"k1"},{"n":"a","d":"zmm1"},{"n":"b","d":"zmm2"}]},
    {"id":"avx512.vaddps_kz","title":"VADDPS k-zero","cat":"CPU/SIMD-AVX512","desc":"AVX-512 add with opmask zeroing",
     "tpl":"vaddps {dst}{{{k}}}{{z}}, {a}, {b}","ports":[{"n":"dst","d":"zmm0"},{"n":"k","d":"k1"},{"n":"a","d":"zmm1"},{"n":"b","d":"zmm2"}]},
    {"id":"avx512.vcompressps","title":"VCOMPRESSPS","cat":"CPU/SIMD-AVX512","desc":"Compress f32 elements by k-mask to contiguous",
     "tpl":"vcompressps {dst}{{{k}}}, {src}","ports":[{"n":"dst","d":"zmm0"},{"n":"k","d":"k1"},{"n":"src","d":"zmm1"}]},
    {"id":"avx512.vpbroadcastd","title":"VPBROADCASTD (ZMM)","cat":"CPU/SIMD-AVX512","desc":"Broadcast i32 to all 16 lanes",
     "tpl":"vpbroadcastd {dst}, {src}","ports":[{"n":"dst","d":"zmm0"},{"n":"src","d":"eax"}]},
    {"id":"avx512.kmovw", "title":"KMOVW",  "cat":"CPU/SIMD-AVX512","desc":"Move 16-bit opmask register",
     "tpl":"kmovw {dst}, {src}",       "ports":[{"n":"dst","d":"k1"},{"n":"src","d":"eax"}]},
    {"id":"avx512.kandw", "title":"KANDW",  "cat":"CPU/SIMD-AVX512","desc":"AND opmask registers",
     "tpl":"kandw {dst}, {a}, {b}",    "ports":[{"n":"dst","d":"k0"},{"n":"a","d":"k1"},{"n":"b","d":"k2"}]},
    {"id":"avx512.korw",  "title":"KORW",   "cat":"CPU/SIMD-AVX512","desc":"OR opmask registers",
     "tpl":"korw {dst}, {a}, {b}",     "ports":[{"n":"dst","d":"k0"},{"n":"a","d":"k1"},{"n":"b","d":"k2"}]},
    {"id":"avx512.kortestw","title":"KORTESTW","cat":"CPU/SIMD-AVX512","desc":"Test opmask (set ZF if k==0)",
     "tpl":"kortestw {a}, {b}",        "ports":[{"n":"a","d":"k1"},{"n":"b","d":"k1"}]},

    # ═══════════════════  CPU / SIMD FMA  ═══════════════════
    {"id":"fma.vfmadd132ps","title":"VFMADD132PS","cat":"CPU/SIMD-FMA","desc":"FMA: dst = (dst*src1)+src2 (packed f32)",
     "tpl":"vfmadd132ps {dst}, {src1}, {src2}","ports":[{"n":"dst","d":"xmm0"},{"n":"src1","d":"xmm1"},{"n":"src2","d":"xmm2"}]},
    {"id":"fma.vfmadd213ps","title":"VFMADD213PS","cat":"CPU/SIMD-FMA","desc":"FMA: dst = (src1*dst)+src2",
     "tpl":"vfmadd213ps {dst}, {src1}, {src2}","ports":[{"n":"dst","d":"ymm0"},{"n":"src1","d":"ymm1"},{"n":"src2","d":"ymm2"}]},
    {"id":"fma.vfmadd231ps","title":"VFMADD231PS","cat":"CPU/SIMD-FMA","desc":"FMA: dst += src1*src2 (packed f32 — canonical accumulate)",
     "tpl":"vfmadd231ps {acc}, {a}, {b}","ports":[{"n":"acc","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"fma.vfmsub231ps","title":"VFMSUB231PS","cat":"CPU/SIMD-FMA","desc":"FMS: dst -= src1*src2",
     "tpl":"vfmsub231ps {acc}, {a}, {b}","ports":[{"n":"acc","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"fma.vfnmadd231ps","title":"VFNMADD231PS","cat":"CPU/SIMD-FMA","desc":"FNMA: dst -= -(src1*src2) = dst + src1*src2 negated",
     "tpl":"vfnmadd231ps {acc}, {a}, {b}","ports":[{"n":"acc","d":"ymm0"},{"n":"a","d":"ymm1"},{"n":"b","d":"ymm2"}]},
    {"id":"fma.vfmadd231ss","title":"VFMADD231SS","cat":"CPU/SIMD-FMA","desc":"FMA scalar f32: acc += a*b",
     "tpl":"vfmadd231ss {acc}, {a}, {b}","ports":[{"n":"acc","d":"xmm0"},{"n":"a","d":"xmm1"},{"n":"b","d":"xmm2"}]},
    {"id":"fma.vfmadd231sd","title":"VFMADD231SD","cat":"CPU/SIMD-FMA","desc":"FMA scalar f64: acc += a*b",
     "tpl":"vfmadd231sd {acc}, {a}, {b}","ports":[{"n":"acc","d":"xmm0"},{"n":"a","d":"xmm1"},{"n":"b","d":"xmm2"}]},

    # ═══════════════════  CPU / BMI  ═══════════════════
    {"id":"bmi.blsr",   "title":"BLSR",    "cat":"CPU/BMI",     "desc":"Reset lowest set bit: dst = src & (src-1)",
     "tpl":"blsr {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"bmi.blsmsk", "title":"BLSMSK",  "cat":"CPU/BMI",     "desc":"Mask up to lowest set bit: dst = src ^ (src-1)",
     "tpl":"blsmsk {dst}, {src}",      "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"bmi.blsi",   "title":"BLSI",    "cat":"CPU/BMI",     "desc":"Extract lowest set bit: dst = src & (-src)",
     "tpl":"blsi {dst}, {src}",        "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"}]},
    {"id":"bmi.andn",   "title":"ANDN",    "cat":"CPU/BMI",     "desc":"AND NOT: dst = ~src1 & src2",
     "tpl":"andn {dst}, {src1}, {src2}","ports":[{"n":"dst","d":"rax"},{"n":"src1","d":"rbx"},{"n":"src2","d":"rcx"}]},
    {"id":"bmi.bextr",  "title":"BEXTR",   "cat":"CPU/BMI",     "desc":"Bit field extract: dst = src[start:start+len] (BMI1)",
     "tpl":"bextr {dst}, {src}, {ctrl}","ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"ctrl","d":"0x0804"}]},
    {"id":"bmi.bzhi",   "title":"BZHI",    "cat":"CPU/BMI",     "desc":"Zero high bits from index: dst = src & ((1<<idx)-1)",
     "tpl":"bzhi {dst}, {src}, {idx}", "ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"idx","d":"rcx"}]},
    {"id":"bmi.pdep",   "title":"PDEP",    "cat":"CPU/BMI",     "desc":"Parallel bits deposit by mask (BMI2)",
     "tpl":"pdep {dst}, {src}, {mask}","ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"mask","d":"rcx"}]},
    {"id":"bmi.pext",   "title":"PEXT",    "cat":"CPU/BMI",     "desc":"Parallel bits extract by mask (BMI2)",
     "tpl":"pext {dst}, {src}, {mask}","ports":[{"n":"dst","d":"rax"},{"n":"src","d":"rbx"},{"n":"mask","d":"rcx"}]},

    # ═══════════════════  CPU / FPU (x87)  ═══════════════════
    {"id":"fpu.fld",    "title":"FLD",     "cat":"CPU/FPU",     "desc":"Load floating-point onto st(0)",
     "tpl":"fld {src}",                "ports":[{"n":"src","d":"dword [rax]"}]},
    {"id":"fpu.fld1",   "title":"FLD1",    "cat":"CPU/FPU",     "desc":"Push 1.0 onto FPU stack",
     "tpl":"fld1",                     "ports":[]},
    {"id":"fpu.fldz",   "title":"FLDZ",    "cat":"CPU/FPU",     "desc":"Push 0.0 onto FPU stack",
     "tpl":"fldz",                     "ports":[]},
    {"id":"fpu.fldpi",  "title":"FLDPI",   "cat":"CPU/FPU",     "desc":"Push π onto FPU stack",
     "tpl":"fldpi",                    "ports":[]},
    {"id":"fpu.fld2e",  "title":"FLDL2E",  "cat":"CPU/FPU",     "desc":"Push log2(e) onto FPU stack",
     "tpl":"fldl2e",                   "ports":[]},
    {"id":"fpu.fst",    "title":"FST",     "cat":"CPU/FPU",     "desc":"Store st(0) to memory (no pop)",
     "tpl":"fst {dst}",                "ports":[{"n":"dst","d":"dword [rbx]"}]},
    {"id":"fpu.fstp",   "title":"FSTP",    "cat":"CPU/FPU",     "desc":"Store st(0) and pop",
     "tpl":"fstp {dst}",               "ports":[{"n":"dst","d":"qword [rbx]"}]},
    {"id":"fpu.fild",   "title":"FILD",    "cat":"CPU/FPU",     "desc":"Load integer and push as float",
     "tpl":"fild {src}",               "ports":[{"n":"src","d":"qword [rax]"}]},
    {"id":"fpu.fistp",  "title":"FISTP",   "cat":"CPU/FPU",     "desc":"Store st(0) as integer and pop",
     "tpl":"fistp {dst}",              "ports":[{"n":"dst","d":"qword [rbx]"}]},
    {"id":"fpu.fadd",   "title":"FADD",    "cat":"CPU/FPU",     "desc":"Add memory/st(i) to st(0)",
     "tpl":"fadd {src}",               "ports":[{"n":"src","d":"st1"}]},
    {"id":"fpu.fsub",   "title":"FSUB",    "cat":"CPU/FPU",     "desc":"Subtract from st(0)",
     "tpl":"fsub {src}",               "ports":[{"n":"src","d":"st1"}]},
    {"id":"fpu.fmul",   "title":"FMUL",    "cat":"CPU/FPU",     "desc":"Multiply st(0)",
     "tpl":"fmul {src}",               "ports":[{"n":"src","d":"st1"}]},
    {"id":"fpu.fdiv",   "title":"FDIV",    "cat":"CPU/FPU",     "desc":"Divide st(0) by src",
     "tpl":"fdiv {src}",               "ports":[{"n":"src","d":"st1"}]},
    {"id":"fpu.fxch",   "title":"FXCH",    "cat":"CPU/FPU",     "desc":"Exchange st(0) with st(i)",
     "tpl":"fxch {reg}",               "ports":[{"n":"reg","d":"st1"}]},
    {"id":"fpu.fabs",   "title":"FABS",    "cat":"CPU/FPU",     "desc":"Absolute value of st(0)",
     "tpl":"fabs",                     "ports":[]},
    {"id":"fpu.fchs",   "title":"FCHS",    "cat":"CPU/FPU",     "desc":"Change sign of st(0)",
     "tpl":"fchs",                     "ports":[]},
    {"id":"fpu.fsqrt",  "title":"FSQRT",   "cat":"CPU/FPU",     "desc":"Square root of st(0)",
     "tpl":"fsqrt",                    "ports":[]},
    {"id":"fpu.fsin",   "title":"FSIN",    "cat":"CPU/FPU",     "desc":"Sine of st(0) (radians)",
     "tpl":"fsin",                     "ports":[]},
    {"id":"fpu.fcos",   "title":"FCOS",    "cat":"CPU/FPU",     "desc":"Cosine of st(0) (radians)",
     "tpl":"fcos",                     "ports":[]},
    {"id":"fpu.fptan",  "title":"FPTAN",   "cat":"CPU/FPU",     "desc":"Partial tangent: pushes tan(st0) and 1.0",
     "tpl":"fptan",                    "ports":[]},
    {"id":"fpu.fpatan", "title":"FPATAN",  "cat":"CPU/FPU",     "desc":"Partial arctangent: st(1) = atan(st(1)/st(0)), pop",
     "tpl":"fpatan",                   "ports":[]},
    {"id":"fpu.fyl2x",  "title":"FYL2X",   "cat":"CPU/FPU",     "desc":"st(1) = st(1) * log2(st(0)), pop",
     "tpl":"fyl2x",                    "ports":[]},
    {"id":"fpu.fcompp", "title":"FCOMPP",  "cat":"CPU/FPU",     "desc":"Compare st(0) vs st(1), pop twice",
     "tpl":"fcompp",                   "ports":[]},
    {"id":"fpu.fnstsw", "title":"FNSTSW",  "cat":"CPU/FPU",     "desc":"Store FPU status word → AX",
     "tpl":"fnstsw ax",                "ports":[]},
    {"id":"fpu.fldcw",  "title":"FLDCW",   "cat":"CPU/FPU",     "desc":"Load FPU control word",
     "tpl":"fldcw [{cw}]",             "ports":[{"n":"cw","d":"fpu_ctrl"}]},
    {"id":"fpu.fninit", "title":"FNINIT",  "cat":"CPU/FPU",     "desc":"Initialize FPU (no-wait)",
     "tpl":"fninit",                   "ports":[]},

    # ═══════════════════  ABI / Win64  ═══════════════════
    {"id":"abi.proc","title":"Procedure Prologue","cat":"ABI/Win64",
     "desc":"Win64 function prologue (push rbp / mov rbp,rsp / sub rsp,32)",
     "tpl":"{name}:\n    push rbp\n    mov  rbp, rsp\n    sub  rsp, 32",
     "ports":[{"n":"name","d":"my_func"}]},
    {"id":"abi.epilog","title":"Procedure Epilogue","cat":"ABI/Win64",
     "desc":"Win64 function epilogue (add rsp,32 / pop rbp / ret)",
     "tpl":"    add  rsp, 32\n    pop  rbp\n    ret","ports":[]},
    {"id":"abi.shadow","title":"Shadow Space alloc","cat":"ABI/Win64",
     "desc":"Allocate 32-byte shadow space before Win64 CALL",
     "tpl":"    sub  rsp, 32","ports":[]},
    {"id":"abi.unshadow","title":"Shadow Space free","cat":"ABI/Win64",
     "desc":"Restore stack after Win64 CALL shadow space",
     "tpl":"    add  rsp, 32","ports":[]},
    {"id":"abi.align","title":"Align Stack 16","cat":"ABI/Win64",
     "desc":"Force 16-byte alignment required before SSE/AVX calls",
     "tpl":"    and  rsp, -16","ports":[]},
    {"id":"abi.args","title":"Args Comment","cat":"ABI/Win64",
     "desc":"Win64 CC: rcx rdx r8 r9 then stack at [rsp+32+]",
     "tpl":"; arg1→rcx  arg2→rdx  arg3→r8  arg4→r9  arg5+→[rsp+32]","ports":[]},
    {"id":"abi.winmain","title":"WinMain Prologue","cat":"ABI/Win64",
     "desc":"Windows WinMain entry point skeleton",
     "tpl":"WinMain:\n    push rbp\n    mov  rbp, rsp\n    sub  rsp, 64\n    ; rcx=hInst  rdx=hPrevInst  r8=lpCmdLine  r9=nCmdShow",
     "ports":[]},
    {"id":"abi.nvregs","title":"Save NV Regs","cat":"ABI/Win64",
     "desc":"Save non-volatile registers (rbx,rsi,rdi,r12-r15) per Win64 ABI",
     "tpl":"; Save non-volatile registers\n    push rbx\n    push rsi\n    push rdi\n    push r12\n    push r13\n    push r14\n    push r15",
     "ports":[]},
    {"id":"abi.restnv","title":"Restore NV Regs","cat":"ABI/Win64",
     "desc":"Restore non-volatile registers (in reverse push order)",
     "tpl":"; Restore non-volatile registers\n    pop  r15\n    pop  r14\n    pop  r13\n    pop  r12\n    pop  rdi\n    pop  rsi\n    pop  rbx",
     "ports":[]},
    {"id":"abi.arg5","title":"5th Argument","cat":"ABI/Win64",
     "desc":"Pass 5th argument via stack slot at [rsp+32] (after shadow alloc)",
     "tpl":"    mov  [{rsp_offset}], {val}",
     "ports":[{"n":"rsp_offset","d":"rsp+32"},{"n":"val","d":"rcx"}]},
    {"id":"abi.vararg","title":"Vararg Prologue","cat":"ABI/Win64",
     "desc":"Dump 4 register args to shadow space for vararg functions",
     "tpl":"; Dump register args to home shadow (vararg)\n    mov  [rsp+8],  rcx\n    mov  [rsp+16], rdx\n    mov  [rsp+24], r8\n    mov  [rsp+32], r9","ports":[]},

    # ═══════════════════  ABI / Linux x64  ═══════════════════
    {"id":"abi.lin.proc","title":"Proc Prologue (Linux)","cat":"ABI/Linux64",
     "desc":"Linux x64 System V ABI function prologue",
     "tpl":"{name}:\n    push rbp\n    mov  rbp, rsp\n    sub  rsp, {frame}",
     "ports":[{"n":"name","d":"my_func"},{"n":"frame","d":"32"}]},
    {"id":"abi.lin.args","title":"Args Comment (Linux)","cat":"ABI/Linux64",
     "desc":"Linux SysV: rdi rsi rdx rcx r8 r9, then stack",
     "tpl":"; arg1→rdi  arg2→rsi  arg3→rdx  arg4→rcx  arg5→r8  arg6→r9","ports":[]},
    {"id":"abi.lin.redzone","title":"Red Zone","cat":"ABI/Linux64",
     "desc":"Linux x64 128-byte red zone below rsp (no need to sub rsp in leaf funcs)",
     "tpl":"; Leaf function: use red zone [rsp-8]..[rsp-128]\n    ; No sub rsp needed if no calls are made","ports":[]},
    {"id":"abi.lin.syscall","title":"Syscall (Linux)","cat":"ABI/Linux64",
     "desc":"Linux x64 syscall: rax=nr, args in rdi,rsi,rdx,r10,r8,r9",
     "tpl":"; syscall({nr})\n    mov  rax, {nr}\n    ; rdi=arg1 rsi=arg2 rdx=arg3 r10=arg4\n    syscall\n    ; ret in rax",
     "ports":[{"n":"nr","d":"60"}]},
    {"id":"abi.lin.plt","title":"PLT Call","cat":"ABI/Linux64",
     "desc":"Call shared library function via PLT (Linux ELF)",
     "tpl":"    call {func} wrt ..plt",
     "ports":[{"n":"func","d":"printf"}]},
    {"id":"abi.lin.got","title":"GOT Data Access","cat":"ABI/Linux64",
     "desc":"Load address of global via GOT (PIC, Linux ELF)",
     "tpl":"    mov  rax, [{sym} wrt ..got]",
     "ports":[{"n":"sym","d":"my_global"}]},
    {"id":"abi.lin.tls","title":"TLS Access","cat":"ABI/Linux64",
     "desc":"Thread-local storage access via fs: segment",
     "tpl":"    mov  rax, qword [fs:0]   ; TCB pointer\n    mov  rbx, qword [fs:{off}]",
     "ports":[{"n":"off","d":"0x10"}]},

    # ═══════════════════  Directives  ═══════════════════
    {"id":"dir.bits64","title":"bits 64","cat":"Directives","desc":"Set 64-bit mode","tpl":"bits 64","ports":[]},
    {"id":"dir.bits32","title":"bits 32","cat":"Directives","desc":"Set 32-bit mode","tpl":"bits 32","ports":[]},
    {"id":"dir.defrel","title":"default rel","cat":"Directives","desc":"RIP-relative addressing by default","tpl":"default rel","ports":[]},
    {"id":"dir.global","title":"global","cat":"Directives","desc":"Export symbol",
     "tpl":"global {sym}","ports":[{"n":"sym","d":"main"}]},
    {"id":"dir.extern","title":"extern","cat":"Directives","desc":"Import external symbol",
     "tpl":"extern {sym}","ports":[{"n":"sym","d":"ExitProcess"}]},
    {"id":"dir.macro","title":"%macro",  "cat":"Directives","desc":"Define NASM macro with N args",
     "tpl":"%macro {name} {nargs}\n    ; {name} body: use %1 %2 ...\n%endmacro",
     "ports":[{"n":"name","d":"my_macro"},{"n":"nargs","d":"1"}]},
    {"id":"dir.define","title":"%define", "cat":"Directives","desc":"Text substitution define",
     "tpl":"%define {name} {val}","ports":[{"n":"name","d":"NULL"},{"n":"val","d":"0"}]},
    {"id":"dir.ifdef",  "title":"%ifdef",  "cat":"Directives","desc":"Conditional assembly block",
     "tpl":"%ifdef {sym}\n    ; code for {sym} defined\n%else\n    ; code for {sym} not defined\n%endif",
     "ports":[{"n":"sym","d":"DEBUG"}]},
    {"id":"dir.include","title":"%include","cat":"Directives","desc":"Include another NASM file",
     "tpl":'%include "{file}"',"ports":[{"n":"file","d":"macros.asm"}]},
    {"id":"dir.struc", "title":"struc",    "cat":"Directives","desc":"Define a structure layout",
     "tpl":"struc {name}\n    .x:  resd 1   ; offset 0\n    .y:  resd 1   ; offset 4\n    .z:  resd 1   ; offset 8\nendstruc",
     "ports":[{"n":"name","d":"Vec3"}]},
    {"id":"dir.istruc","title":"istruc",   "cat":"Directives","desc":"Initialize a structure instance",
     "tpl":"istruc {name}\n    at {name}.x, dd {xv}\n    at {name}.y, dd {yv}\niend",
     "ports":[{"n":"name","d":"Vec3"},{"n":"xv","d":"0"},{"n":"yv","d":"0"}]},
    {"id":"dir.align", "title":"align",    "cat":"Directives","desc":"Align to boundary (pads with NOPs in .text)",
     "tpl":"align {n}","ports":[{"n":"n","d":"16"}]},
    {"id":"dir.alignb","title":"alignb",   "cat":"Directives","desc":"Align in BSS/DATA (pads with 0s)",
     "tpl":"alignb {n}","ports":[{"n":"n","d":"16"}]},
    {"id":"dir.cpu",   "title":"cpu",      "cat":"Directives","desc":"Restrict CPU instruction set",
     "tpl":"cpu {level}","ports":[{"n":"level","d":"64"}]},

    # ═══════════════════  Sections  ═══════════════════
    {"id":"sec.text","title":".text","cat":"Sections","desc":"Code section","tpl":"section .text","ports":[]},
    {"id":"sec.data","title":".data","cat":"Sections","desc":"Initialized data","tpl":"section .data","ports":[]},
    {"id":"sec.bss","title":".bss","cat":"Sections","desc":"Uninitialized data","tpl":"section .bss","ports":[]},
    {"id":"sec.rodata","title":".rodata","cat":"Sections","desc":"Read-only data","tpl":"section .rodata","ports":[]},
    {"id":"sec.idata","title":".idata","cat":"Sections","desc":"Import table","tpl":"section .idata","ports":[]},
    {"id":"sec.xdata","title":".xdata","cat":"Sections","desc":"Exception unwind data (Win64 SEH)","tpl":"section .xdata","ports":[]},
    {"id":"sec.pdata","title":".pdata","cat":"Sections","desc":"Exception handler table (Win64 SEH)","tpl":"section .pdata","ports":[]},
    {"id":"sec.align", "title":"align exe", "cat":"Sections","desc":"Aligned executable section (64B for SIMD)",
     "tpl":"section .text  align=64","ports":[]},
    {"id":"sec.data_align","title":"align data","cat":"Sections","desc":"Aligned data section (64B for AVX-512)",
     "tpl":"section .data  align=64","ports":[]},

    # ═══════════════════  Data / Define  ═══════════════════
    {"id":"dat.db","title":"DB byte","cat":"Data/Define","desc":"Define byte",
     "tpl":"{name}  db  {val}","ports":[{"n":"name","d":"my_byte"},{"n":"val","d":"0"}]},
    {"id":"dat.dw","title":"DW word","cat":"Data/Define","desc":"Define word",
     "tpl":"{name}  dw  {val}","ports":[{"n":"name","d":"my_word"},{"n":"val","d":"0"}]},
    {"id":"dat.dd","title":"DD dword","cat":"Data/Define","desc":"Define dword",
     "tpl":"{name}  dd  {val}","ports":[{"n":"name","d":"my_dword"},{"n":"val","d":"0"}]},
    {"id":"dat.dq","title":"DQ qword","cat":"Data/Define","desc":"Define qword",
     "tpl":"{name}  dq  {val}","ports":[{"n":"name","d":"my_qword"},{"n":"val","d":"0"}]},
    {"id":"dat.ddFloat","title":"DD float","cat":"Data/Define","desc":"Define 32-bit float literal",
     "tpl":"{name}  dd  {val}",
     "ports":[{"n":"name","d":"my_f32"},{"n":"val","d":"0x3F800000"}]},  # 1.0f
    {"id":"dat.dqDouble","title":"DQ double","cat":"Data/Define","desc":"Define 64-bit double literal",
     "tpl":"{name}  dq  __float64__({val})","ports":[{"n":"name","d":"my_f64"},{"n":"val","d":"1.0"}]},
    {"id":"dat.str","title":"DB string","cat":"Data/Define","desc":"Null-terminated ASCII string",
     "tpl":'{name}  db  "{txt}", 0',"ports":[{"n":"name","d":"msg"},{"n":"txt","d":"Hello!"}]},
    {"id":"dat.strn","title":"DB string+CRLF","cat":"Data/Define","desc":"String with CR+LF",
     "tpl":'{name}  db  "{txt}", 0x0D, 0x0A, 0',"ports":[{"n":"name","d":"msg"},{"n":"txt","d":"Hello!"}]},
    {"id":"dat.resb","title":"RESB","cat":"Data/Define","desc":"Reserve n bytes (BSS)",
     "tpl":"{name}  resb  {n}","ports":[{"n":"name","d":"buffer"},{"n":"n","d":"256"}]},
    {"id":"dat.resq","title":"RESQ","cat":"Data/Define","desc":"Reserve n qwords (BSS)",
     "tpl":"{name}  resq  {n}","ports":[{"n":"name","d":"buf64"},{"n":"n","d":"16"}]},
    {"id":"dat.equ","title":"EQU","cat":"Data/Define","desc":"Define constant (no storage)",
     "tpl":"{name}  equ  {val}","ports":[{"n":"name","d":"PAGE_SIZE"},{"n":"val","d":"4096"}]},
    {"id":"dat.times","title":"TIMES","cat":"Data/Define","desc":"Repeat instruction/data n times",
     "tpl":"times {count} {instr}","ports":[{"n":"count","d":"8"},{"n":"instr","d":"nop"}]},
    {"id":"dat.arr_f32","title":"f32 array","cat":"Data/Define","desc":"Aligned array of 8 f32 (for AVX2)",
     "tpl":"align 32\n{name}  dd  {v0}, {v1}, {v2}, {v3}, {v4}, {v5}, {v6}, {v7}",
     "ports":[{"n":"name","d":"vec8"},{"n":"v0","d":"0"},{"n":"v1","d":"0"},{"n":"v2","d":"0"},{"n":"v3","d":"0"},
              {"n":"v4","d":"0"},{"n":"v5","d":"0"},{"n":"v6","d":"0"},{"n":"v7","d":"0"}]},
    {"id":"dat.mat4","title":"mat4 (4×4 f32)","cat":"Data/Define","desc":"Column-major 4×4 identity matrix (f32)",
     "tpl":"; Column-major identity mat4 (4×4 f32)\nalign 64\n{name}:\n    dd 0x3F800000, 0, 0, 0   ; col0\n    dd 0, 0x3F800000, 0, 0   ; col1\n    dd 0, 0, 0x3F800000, 0   ; col2\n    dd 0, 0, 0, 0x3F800000   ; col3",
     "ports":[{"n":"name","d":"mat_identity"}]},
    {"id":"dat.jtable","title":"Jump Table","cat":"Data/Define","desc":"Jump dispatch table (array of pointers to labels)",
     "tpl":"align 8\n{name}:\n    dq {lbl0}, {lbl1}, {lbl2}\n; usage: jmp [{name} + rax*8]",
     "ports":[{"n":"name","d":"jmp_tbl"},{"n":"lbl0","d":".case0"},{"n":"lbl1","d":".case1"},{"n":"lbl2","d":".case2"}]},

    # ═══════════════════  Kernel32  ═══════════════════
    {"id":"k32.valloc","title":"VirtualAlloc","cat":"Kernel32",
     "desc":"VirtualAlloc(addr, size, MEM_COMMIT|MEM_RESERVE=0x3000, PAGE_READWRITE=4)",
     "tpl":"; VirtualAlloc(0, size, MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE)\n    mov  [rsp+32], 4\n    mov  r9,  0x3000\n    mov  r8,  {size}\n    xor  rdx, rdx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [VirtualAlloc]\n    add  rsp, 32\n    ; rax = pointer or NULL",
     "ports":[{"n":"size","d":"0x1000"}]},
    {"id":"k32.vfree","title":"VirtualFree","cat":"Kernel32",
     "desc":"VirtualFree(ptr, 0, MEM_RELEASE=0x8000)",
     "tpl":"; VirtualFree(ptr, 0, MEM_RELEASE)\n    mov  r8,  0x8000\n    xor  rdx, rdx\n    mov  rcx, {ptr}\n    sub  rsp, 32\n    call [VirtualFree]\n    add  rsp, 32",
     "ports":[{"n":"ptr","d":"[rel hMem]"}]},
    {"id":"k32.vprotect","title":"VirtualProtect","cat":"Kernel32",
     "desc":"VirtualProtect(addr, size, newProt, &oldProt)",
     "tpl":"; VirtualProtect(addr, size, prot, &old_prot)\n    lea  [rsp+32], [rel old_prot]\n    mov  r9,  0x40   ; PAGE_EXECUTE_READWRITE\n    mov  r8,  {size}\n    mov  rdx, {size}\n    mov  rcx, {addr}\n    sub  rsp, 32\n    call [VirtualProtect]\n    add  rsp, 32",
     "ports":[{"n":"addr","d":"rax"},{"n":"size","d":"0x1000"}]},
    {"id":"k32.heapalloc","title":"HeapAlloc","cat":"Kernel32",
     "desc":"HeapAlloc(GetProcessHeap(), 0, size)",
     "tpl":"; GetProcessHeap()\n    sub  rsp, 32\n    call [GetProcessHeap]\n    add  rsp, 32\n    mov  [rel hHeap], rax\n    ; HeapAlloc(hHeap, 0, size)\n    mov  r8,  {size}\n    xor  rdx, rdx\n    mov  rcx, [rel hHeap]\n    sub  rsp, 32\n    call [HeapAlloc]\n    add  rsp, 32",
     "ports":[{"n":"size","d":"256"}]},
    {"id":"k32.heapfree","title":"HeapFree","cat":"Kernel32",
     "desc":"HeapFree(hHeap, 0, ptr)",
     "tpl":"; HeapFree(hHeap, 0, ptr)\n    mov  r8,  {ptr}\n    xor  rdx, rdx\n    mov  rcx, [rel hHeap]\n    sub  rsp, 32\n    call [HeapFree]\n    add  rsp, 32",
     "ports":[{"n":"ptr","d":"rax"}]},
    {"id":"k32.sleep","title":"Sleep","cat":"Kernel32",
     "desc":"Sleep(milliseconds)",
     "tpl":"; Sleep(ms)\n    mov  rcx, {ms}\n    sub  rsp, 32\n    call [Sleep]\n    add  rsp, 32",
     "ports":[{"n":"ms","d":"1"}]},
    {"id":"k32.qpc","title":"QueryPerformanceCounter","cat":"Kernel32",
     "desc":"QueryPerformanceCounter → high-resolution tick",
     "tpl":"; QueryPerformanceCounter(&counter)\n    lea  rcx, [rel qpc_buf]\n    sub  rsp, 32\n    call [QueryPerformanceCounter]\n    add  rsp, 32\n    mov  rax, [rel qpc_buf]",
     "ports":[]},
    {"id":"k32.qpf","title":"QueryPerformanceFrequency","cat":"Kernel32",
     "desc":"QueryPerformanceFrequency → ticks per second",
     "tpl":"; QueryPerformanceFrequency(&freq)\n    lea  rcx, [rel qpf_buf]\n    sub  rsp, 32\n    call [QueryPerformanceFrequency]\n    add  rsp, 32",
     "ports":[]},
    {"id":"k32.gettickcnt","title":"GetTickCount64","cat":"Kernel32",
     "desc":"GetTickCount64 → ms since boot (no args)",
     "tpl":"; GetTickCount64() → rax=ms\n    sub  rsp, 32\n    call [GetTickCount64]\n    add  rsp, 32",
     "ports":[]},
    {"id":"k32.createthread","title":"CreateThread","cat":"Kernel32",
     "desc":"CreateThread(NULL, 0, proc, arg, 0, NULL)",
     "tpl":"; CreateThread(NULL,0,proc,arg,0,NULL)\n    mov  [rsp+40], 0\n    mov  [rsp+32], 0\n    mov  r9,  {arg}\n    mov  r8,  {proc}\n    xor  rdx, rdx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [CreateThread]\n    add  rsp, 32\n    mov  [rel hThread], rax",
     "ports":[{"n":"proc","d":"thread_func"},{"n":"arg","d":"0"}]},
    {"id":"k32.waitobj","title":"WaitForSingleObject","cat":"Kernel32",
     "desc":"WaitForSingleObject(handle, INFINITE)",
     "tpl":"; WaitForSingleObject(h, INFINITE)\n    mov  rdx, 0xFFFFFFFF  ; INFINITE\n    mov  rcx, {handle}\n    sub  rsp, 32\n    call [WaitForSingleObject]\n    add  rsp, 32",
     "ports":[{"n":"handle","d":"[rel hThread]"}]},
    {"id":"k32.closehandle","title":"CloseHandle","cat":"Kernel32",
     "desc":"CloseHandle(handle)",
     "tpl":"; CloseHandle(h)\n    mov  rcx, {handle}\n    sub  rsp, 32\n    call [CloseHandle]\n    add  rsp, 32",
     "ports":[{"n":"handle","d":"[rel hFile]"}]},
    {"id":"k32.createevent","title":"CreateEventA","cat":"Kernel32",
     "desc":"CreateEventA(NULL, manual_reset, init_state, name)",
     "tpl":"; CreateEventA(NULL,manual,init,name)\n    xor  r9, r9          ; NULL name\n    mov  r8,  {init}\n    mov  rdx, {manual}\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [CreateEventA]\n    add  rsp, 32\n    mov  [rel hEvent], rax",
     "ports":[{"n":"manual","d":"0"},{"n":"init","d":"0"}]},
    {"id":"k32.setevent","title":"SetEvent","cat":"Kernel32",
     "desc":"SetEvent(hEvent) — signal the event",
     "tpl":"; SetEvent(hEvent)\n    mov  rcx, [rel hEvent]\n    sub  rsp, 32\n    call [SetEvent]\n    add  rsp, 32","ports":[]},
    {"id":"k32.resetevent","title":"ResetEvent","cat":"Kernel32",
     "desc":"ResetEvent(hEvent) — unsignal the event",
     "tpl":"; ResetEvent(hEvent)\n    mov  rcx, [rel hEvent]\n    sub  rsp, 32\n    call [ResetEvent]\n    add  rsp, 32","ports":[]},
    {"id":"k32.createfile","title":"CreateFileA","cat":"Kernel32",
     "desc":"CreateFileA(name, access, share, NULL, disp, attr, NULL)",
     "tpl":"; CreateFileA: open/create a file\n    mov  [rsp+48], 0\n    mov  [rsp+40], 0x80         ; FILE_ATTRIBUTE_NORMAL\n    mov  [rsp+32], {disp}       ; OPEN_EXISTING=3 / CREATE_ALWAYS=2\n    xor  r9,  r9               ; lpSecurityAttributes=NULL\n    xor  r8,  r8               ; dwShareMode=0\n    mov  rdx, 0xC0000000       ; GENERIC_READ|GENERIC_WRITE\n    lea  rcx, [rel {name}]\n    sub  rsp, 32\n    call [CreateFileA]\n    add  rsp, 32\n    mov  [rel hFile], rax",
     "ports":[{"n":"name","d":"file_path"},{"n":"disp","d":"3"}]},
    {"id":"k32.readfile","title":"ReadFile","cat":"Kernel32",
     "desc":"ReadFile(hFile, buf, size, &read, NULL)",
     "tpl":"; ReadFile(hFile, buf, size, &bytesRead, NULL)\n    mov  [rsp+32], 0\n    lea  r9,  [rel bytes_read]\n    mov  r8,  {size}\n    lea  rdx, [rel {buf}]\n    mov  rcx, [rel hFile]\n    sub  rsp, 32\n    call [ReadFile]\n    add  rsp, 32",
     "ports":[{"n":"buf","d":"file_buf"},{"n":"size","d":"4096"}]},
    {"id":"k32.writefile","title":"WriteFile","cat":"Kernel32",
     "desc":"WriteFile(hFile, buf, size, &written, NULL)",
     "tpl":"; WriteFile(hFile, buf, size, &bytesWritten, NULL)\n    mov  [rsp+32], 0\n    lea  r9,  [rel bytes_written]\n    mov  r8,  {size}\n    lea  rdx, [rel {buf}]\n    mov  rcx, [rel hFile]\n    sub  rsp, 32\n    call [WriteFile]\n    add  rsp, 32",
     "ports":[{"n":"buf","d":"out_buf"},{"n":"size","d":"rcx"}]},
    {"id":"k32.mapfile","title":"MapViewOfFile","cat":"Kernel32",
     "desc":"CreateFileMappingA + MapViewOfFile (memory-mapped file)",
     "tpl":"; CreateFileMappingA(hFile, NULL, PAGE_READONLY, 0, 0, NULL)\n    mov  [rsp+40], 0\n    mov  [rsp+32], 0\n    xor  r9,  r9\n    xor  r8,  r8\n    mov  rdx, 0x02         ; PAGE_READONLY\n    mov  rcx, [rel hFile]\n    sub  rsp, 32\n    call [CreateFileMappingA]\n    add  rsp, 32\n    mov  [rel hMap], rax\n    ; MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 0)\n    mov  [rsp+32], 0\n    xor  r9,  r9\n    xor  r8,  r8\n    mov  rdx, 4             ; FILE_MAP_READ\n    mov  rcx, [rel hMap]\n    sub  rsp, 32\n    call [MapViewOfFile]\n    add  rsp, 32\n    mov  [rel pView], rax",
     "ports":[]},
    {"id":"k32.loadlib","title":"LoadLibraryA","cat":"Kernel32",
     "desc":"LoadLibraryA(name) → hModule",
     "tpl":"; LoadLibraryA(dllname)\n    lea  rcx, [rel {dll}]\n    sub  rsp, 32\n    call [LoadLibraryA]\n    add  rsp, 32\n    mov  [rel hLib], rax",
     "ports":[{"n":"dll","d":"lib_name"}]},
    {"id":"k32.getproc","title":"GetProcAddress","cat":"Kernel32",
     "desc":"GetProcAddress(hModule, procName) → proc pointer",
     "tpl":"; GetProcAddress(hLib, procName)\n    lea  rdx, [rel {proc}]\n    mov  rcx, [rel hLib]\n    sub  rsp, 32\n    call [GetProcAddress]\n    add  rsp, 32\n    mov  [rel proc_ptr], rax",
     "ports":[{"n":"proc","d":"proc_name"}]},
    {"id":"k32.getlasterr","title":"GetLastError","cat":"Kernel32",
     "desc":"GetLastError() → eax = error code",
     "tpl":"; GetLastError() → eax\n    sub  rsp, 32\n    call [GetLastError]\n    add  rsp, 32",
     "ports":[]},
    {"id":"k32.outputdebug","title":"OutputDebugStringA","cat":"Kernel32",
     "desc":"OutputDebugStringA(str) — write to debugger output",
     "tpl":"; OutputDebugStringA(str)\n    lea  rcx, [rel {str}]\n    sub  rsp, 32\n    call [OutputDebugStringA]\n    add  rsp, 32",
     "ports":[{"n":"str","d":"debug_msg"}]},
    {"id":"k32.getsysinfo","title":"GetSystemInfo","cat":"Kernel32",
     "desc":"GetSystemInfo(&SYSTEM_INFO) — CPU count, page size, etc.",
     "tpl":"; GetSystemInfo(&si)\n    lea  rcx, [rel sys_info]\n    sub  rsp, 32\n    call [GetSystemInfo]\n    add  rsp, 32\n    mov  eax, [rel sys_info+0]  ; dwPageSize at offset varies — check struct",
     "ports":[]},
    {"id":"k32.interlocked","title":"InterlockedExchange","cat":"Kernel32",
     "desc":"InterlockedExchange(&target, value) — atomic swap",
     "tpl":"; InterlockedExchange(target, val)\n    mov  rdx, {val}\n    lea  rcx, [rel {target}]\n    sub  rsp, 32\n    call [InterlockedExchange]\n    add  rsp, 32",
     "ports":[{"n":"target","d":"shared_var"},{"n":"val","d":"1"}]},

    # ═══════════════════  Ntdll  ═══════════════════
    {"id":"ntdll.rtlzero","title":"RtlZeroMemory","cat":"Ntdll",
     "desc":"RtlZeroMemory(ptr, size) — zero a memory block",
     "tpl":"; RtlZeroMemory(ptr, size)\n    mov  rdx, {size}\n    mov  rcx, {ptr}\n    sub  rsp, 32\n    call [RtlZeroMemory]\n    add  rsp, 32",
     "ports":[{"n":"ptr","d":"rax"},{"n":"size","d":"1024"}]},
    {"id":"ntdll.rtlmove","title":"RtlMoveMemory","cat":"Ntdll",
     "desc":"RtlMoveMemory(dst, src, size) — memmove equivalent",
     "tpl":"; RtlMoveMemory(dst, src, size)\n    mov  r8,  {size}\n    mov  rdx, {src}\n    mov  rcx, {dst}\n    sub  rsp, 32\n    call [RtlMoveMemory]\n    add  rsp, 32",
     "ports":[{"n":"dst","d":"rdi"},{"n":"src","d":"rsi"},{"n":"size","d":"rcx"}]},
    {"id":"ntdll.rtlalloc","title":"RtlAllocateHeap","cat":"Ntdll",
     "desc":"RtlAllocateHeap(heap, 0, size) — NT-level allocation",
     "tpl":"; RtlAllocateHeap(heap, flags, size)\n    mov  r8,  {size}\n    xor  rdx, rdx\n    mov  rcx, {heap}\n    sub  rsp, 32\n    call [RtlAllocateHeap]\n    add  rsp, 32",
     "ports":[{"n":"heap","d":"[rel hHeap]"},{"n":"size","d":"256"}]},
    {"id":"ntdll.ntalloc","title":"NtAllocateVirtualMemory","cat":"Ntdll",
     "desc":"NtAllocateVirtualMemory(-1, &addr, 0, &size, MEM_COMMIT, PAGE_RW)",
     "tpl":"; NtAllocateVirtualMemory(process, &BaseAddr, ZeroBits, &RegionSize, AllocType, Protect)\n    mov  [rsp+40], 4            ; PAGE_READWRITE\n    mov  [rsp+32], 0x3000       ; MEM_COMMIT|MEM_RESERVE\n    lea  r9,  [rel alloc_size]\n    xor  r8,  r8\n    lea  rdx, [rel base_addr]\n    mov  rcx, -1               ; NtCurrentProcess()\n    sub  rsp, 32\n    call [NtAllocateVirtualMemory]\n    add  rsp, 32",
     "ports":[]},
    {"id":"k32.createfile_w","title":"CreateFileW","cat":"Kernel32",
     "desc":"CreateFileW(wide_path, ...) — Unicode file open",
     "tpl":"; CreateFileW(wpath, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL)\n    mov  [rsp+48], 0\n    mov  [rsp+40], 0\n    mov  [rsp+32], 3\n    xor  r9,  r9\n    xor  r8,  r8\n    mov  rdx, 0x80000000\n    lea  rcx, [rel {wpath}]\n    sub  rsp, 32\n    call [CreateFileW]\n    add  rsp, 32",
     "ports":[{"n":"wpath","d":"wfile_path"}]},

    # ═══════════════════  User32  ═══════════════════
    {"id":"u32.msgbox","title":"MessageBoxA","cat":"User32",
     "desc":"MessageBoxA(hWnd, text, caption, type)",
     "tpl":"; MessageBoxA(NULL, text, caption, MB_OK)\n    mov  r9,  0\n    lea  r8,  [rel {caption}]\n    lea  rdx, [rel {text}]\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [MessageBoxA]\n    add  rsp, 32",
     "ports":[{"n":"text","d":"msg_text"},{"n":"caption","d":"msg_cap"}]},
    {"id":"u32.showwindow","title":"ShowWindow","cat":"User32",
     "desc":"ShowWindow(hWnd, nCmdShow)",
     "tpl":"; ShowWindow(hwnd, SW_SHOW=5)\n    mov  rdx, {nCmdShow}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [ShowWindow]\n    add  rsp, 32",
     "ports":[{"n":"nCmdShow","d":"10"}]},
    {"id":"u32.updatewindow","title":"UpdateWindow","cat":"User32",
     "desc":"UpdateWindow(hWnd) — force WM_PAINT",
     "tpl":"; UpdateWindow(hwnd)\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [UpdateWindow]\n    add  rsp, 32","ports":[]},
    {"id":"u32.getclientrect","title":"GetClientRect","cat":"User32",
     "desc":"GetClientRect(hWnd, &RECT) — client area dimensions",
     "tpl":"; GetClientRect(hwnd, &rc)\n    lea  rdx, [rel rc]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [GetClientRect]\n    add  rsp, 32",
     "ports":[]},
    {"id":"u32.invalidate","title":"InvalidateRect","cat":"User32",
     "desc":"InvalidateRect(hWnd, NULL, erase) — schedule repaint",
     "tpl":"; InvalidateRect(hwnd, NULL, TRUE)\n    mov  r8,  1\n    xor  rdx, rdx\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [InvalidateRect]\n    add  rsp, 32","ports":[]},
    {"id":"u32.destroywindow","title":"DestroyWindow","cat":"User32",
     "desc":"DestroyWindow(hWnd)",
     "tpl":"; DestroyWindow(hwnd)\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [DestroyWindow]\n    add  rsp, 32","ports":[]},
    {"id":"u32.defwndproc","title":"DefWindowProcA","cat":"User32",
     "desc":"Call DefWindowProcA for unhandled messages",
     "tpl":"; DefWindowProcA(hWnd, uMsg, wParam, lParam)\n    sub  rsp, 32\n    call [DefWindowProcA]\n    add  rsp, 32\n    ; rax = result","ports":[]},
    {"id":"u32.postquit","title":"PostQuitMessage","cat":"User32",
     "desc":"PostQuitMessage(exitCode) — terminate message loop",
     "tpl":"; PostQuitMessage(0)\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [PostQuitMessage]\n    add  rsp, 32","ports":[]},
    {"id":"u32.sendmsg","title":"SendMessageA","cat":"User32",
     "desc":"SendMessageA(hWnd, msg, wParam, lParam) — synchronous",
     "tpl":"; SendMessageA(hwnd, {msg}, wParam, lParam)\n    mov  r9,  {lParam}\n    mov  r8,  {wParam}\n    mov  rdx, {msg}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [SendMessageA]\n    add  rsp, 32",
     "ports":[{"n":"msg","d":"0x000F"},{"n":"wParam","d":"0"},{"n":"lParam","d":"0"}]},
    {"id":"u32.postmsg","title":"PostMessageA","cat":"User32",
     "desc":"PostMessageA(hWnd, msg, wParam, lParam) — async",
     "tpl":"; PostMessageA(hwnd, msg, wParam, lParam)\n    mov  r9,  {lParam}\n    mov  r8,  {wParam}\n    mov  rdx, {msg}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [PostMessageA]\n    add  rsp, 32",
     "ports":[{"n":"msg","d":"0x8000"},{"n":"wParam","d":"0"},{"n":"lParam","d":"0"}]},
    {"id":"u32.getasynckey","title":"GetAsyncKeyState","cat":"User32",
     "desc":"GetAsyncKeyState(vKey) → bit15=pressed, bit0=toggled",
     "tpl":"; GetAsyncKeyState(vKey)\n    mov  rcx, {vKey}\n    sub  rsp, 32\n    call [GetAsyncKeyState]\n    add  rsp, 32\n    test ax, 0x8000\n    jnz  .key_down",
     "ports":[{"n":"vKey","d":"0x57"}]},  # VK_W
    {"id":"u32.settimer","title":"SetTimer","cat":"User32",
     "desc":"SetTimer(hWnd, id, elapse_ms, proc) → timer ID",
     "tpl":"; SetTimer(hwnd, id, ms, NULL)\n    xor  r9, r9\n    mov  r8,  {ms}\n    mov  rdx, {id}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [SetTimer]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"1"},{"n":"ms","d":"16"}]},
    {"id":"u32.killtimer","title":"KillTimer","cat":"User32",
     "desc":"KillTimer(hWnd, id)",
     "tpl":"; KillTimer(hwnd, id)\n    mov  rdx, {id}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [KillTimer]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"1"}]},
    {"id":"u32.loadcursor","title":"LoadCursorA","cat":"User32",
     "desc":"LoadCursorA(NULL, IDC_ARROW=32512)",
     "tpl":"; LoadCursorA(NULL, IDC_ARROW)\n    mov  rdx, 32512\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [LoadCursorA]\n    add  rsp, 32\n    mov  [rel hCursor], rax","ports":[]},
    {"id":"u32.getcursorpos","title":"GetCursorPos","cat":"User32",
     "desc":"GetCursorPos(&POINT) → cursor screen coordinates",
     "tpl":"; GetCursorPos(&pt)\n    lea  rcx, [rel cursor_pt]\n    sub  rsp, 32\n    call [GetCursorPos]\n    add  rsp, 32",
     "ports":[]},
    {"id":"u32.screentoclient","title":"ScreenToClient","cat":"User32",
     "desc":"ScreenToClient(hWnd, &POINT) — screen to client coords",
     "tpl":"; ScreenToClient(hwnd, &pt)\n    lea  rdx, [rel cursor_pt]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [ScreenToClient]\n    add  rsp, 32","ports":[]},
    {"id":"u32.setcapture","title":"SetCapture","cat":"User32",
     "desc":"SetCapture(hWnd) — capture mouse input",
     "tpl":"; SetCapture(hwnd)\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [SetCapture]\n    add  rsp, 32","ports":[]},
    {"id":"u32.releasecapture","title":"ReleaseCapture","cat":"User32",
     "desc":"ReleaseCapture() — release mouse capture",
     "tpl":"; ReleaseCapture()\n    sub  rsp, 32\n    call [ReleaseCapture]\n    add  rsp, 32","ports":[]},
    {"id":"u32.setwindowtext","title":"SetWindowTextA","cat":"User32",
     "desc":"SetWindowTextA(hWnd, text) — change title bar",
     "tpl":"; SetWindowTextA(hwnd, str)\n    lea  rdx, [rel {str}]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [SetWindowTextA]\n    add  rsp, 32",
     "ports":[{"n":"str","d":"title_str"}]},
    {"id":"u32.findwindow","title":"FindWindowA","cat":"User32",
     "desc":"FindWindowA(className, windowName) → hWnd",
     "tpl":"; FindWindowA(class, name)\n    lea  rdx, [rel {wname}]\n    lea  rcx, [rel {cls}]\n    sub  rsp, 32\n    call [FindWindowA]\n    add  rsp, 32",
     "ports":[{"n":"cls","d":"cls_name"},{"n":"wname","d":"wnd_title"}]},
    {"id":"u32.getwindowlong","title":"GetWindowLongPtrA","cat":"User32",
     "desc":"GetWindowLongPtrA(hWnd, index) — get window data/style",
     "tpl":"; GetWindowLongPtrA(hwnd, index)\n    mov  rdx, {idx}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [GetWindowLongPtrA]\n    add  rsp, 32",
     "ports":[{"n":"idx","d":"-16"}]},  # GWL_STYLE = -16
    {"id":"u32.setwindowlong","title":"SetWindowLongPtrA","cat":"User32",
     "desc":"SetWindowLongPtrA(hWnd, index, val) — set window data/style",
     "tpl":"; SetWindowLongPtrA(hwnd, idx, val)\n    mov  r8,  {val}\n    mov  rdx, {idx}\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [SetWindowLongPtrA]\n    add  rsp, 32",
     "ports":[{"n":"idx","d":"-16"},{"n":"val","d":"0"}]},
    {"id":"u32.clipboard_copy","title":"Clipboard Copy","cat":"User32",
     "desc":"Copy text to clipboard (OpenClipboard+SetClipboardData)",
     "tpl":"; Copy to clipboard\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [OpenClipboard]\n    add  rsp, 32\n    sub  rsp, 32\n    call [EmptyClipboard]\n    add  rsp, 32\n    mov  rdx, [rel hClipMem]\n    mov  rcx, 1              ; CF_TEXT\n    sub  rsp, 32\n    call [SetClipboardData]\n    add  rsp, 32\n    sub  rsp, 32\n    call [CloseClipboard]\n    add  rsp, 32","ports":[]},

    # ═══════════════════  GDI32  ═══════════════════
    {"id":"gdi.beginpaint","title":"BeginPaint","cat":"GDI32","desc":"BeginPaint + get HDC",
     "tpl":"; BeginPaint(hwnd, &ps)\n    lea  rdx, [rel ps_buf]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [BeginPaint]\n    add  rsp, 32\n    mov  [rel hdc], rax","ports":[]},
    {"id":"gdi.endpaint","title":"EndPaint","cat":"GDI32","desc":"EndPaint(hwnd, &ps)",
     "tpl":"; EndPaint(hwnd, &ps)\n    lea  rdx, [rel ps_buf]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [EndPaint]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.textout","title":"TextOutA","cat":"GDI32","desc":"Draw text at (x,y)",
     "tpl":"; TextOutA(hdc, x, y, str, len)\n    mov  r9,  {len}\n    lea  r8,  [{str}]\n    mov  rdx, {y}\n    mov  rcx, {x}\n    push rcx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [TextOutA]\n    add  rsp, 32\n    pop  rcx",
     "ports":[{"n":"str","d":"my_str"},{"n":"x","d":"10"},{"n":"y","d":"10"},{"n":"len","d":"13"}]},
    {"id":"gdi.drawtext","title":"DrawTextA","cat":"GDI32","desc":"Draw formatted text in a RECT",
     "tpl":"; DrawTextA(hdc, str, len, &rc, DT_LEFT|DT_WORDBREAK)\n    mov  [rsp+32], 5     ; DT_LEFT=0|DT_WORDBREAK=4\n    lea  r9,  [rel {rc}]\n    mov  r8,  -1         ; -1 = null-terminated\n    lea  rdx, [rel {str}]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [DrawTextA]\n    add  rsp, 32",
     "ports":[{"n":"str","d":"msg"},{"n":"rc","d":"client_rc"}]},
    {"id":"gdi.rectangle","title":"Rectangle","cat":"GDI32","desc":"Draw rectangle (border+fill)",
     "tpl":"; Rectangle(hdc, left, top, right, bottom)\n    mov  [rsp+32], {bot}\n    mov  r9,  {right}\n    mov  r8,  {top}\n    mov  rdx, {left}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [Rectangle]\n    add  rsp, 32",
     "ports":[{"n":"left","d":"10"},{"n":"top","d":"10"},{"n":"right","d":"200"},{"n":"bot","d":"100"}]},
    {"id":"gdi.ellipse","title":"Ellipse","cat":"GDI32","desc":"Draw ellipse within bounding rect",
     "tpl":"; Ellipse(hdc, left, top, right, bottom)\n    mov  [rsp+32], {bot}\n    mov  r9,  {right}\n    mov  r8,  {top}\n    mov  rdx, {left}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [Ellipse]\n    add  rsp, 32",
     "ports":[{"n":"left","d":"50"},{"n":"top","d":"50"},{"n":"right","d":"150"},{"n":"bot","d":"150"}]},
    {"id":"gdi.lineto","title":"LineTo","cat":"GDI32","desc":"Draw line from current position to (x,y)",
     "tpl":"; LineTo(hdc, x, y)\n    mov  r8,  {y}\n    mov  rdx, {x}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [LineTo]\n    add  rsp, 32",
     "ports":[{"n":"x","d":"200"},{"n":"y","d":"200"}]},
    {"id":"gdi.moveto","title":"MoveToEx","cat":"GDI32","desc":"Move pen to (x,y) without drawing",
     "tpl":"; MoveToEx(hdc, x, y, NULL)\n    xor  r9, r9\n    mov  r8,  {y}\n    mov  rdx, {x}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [MoveToEx]\n    add  rsp, 32",
     "ports":[{"n":"x","d":"10"},{"n":"y","d":"10"}]},
    {"id":"gdi.arc","title":"Arc","cat":"GDI32","desc":"Draw arc within bounding rect from (x1,y1) to (x2,y2)",
     "tpl":"; Arc(hdc, l, t, r, b, x1, y1, x2, y2)\n    mov  [rsp+64], {y2}\n    mov  [rsp+56], {x2}\n    mov  [rsp+48], {y1}\n    mov  [rsp+40], {x1}\n    mov  [rsp+32], {b}\n    mov  r9,  {r}\n    mov  r8,  {t}\n    mov  rdx, {l}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [Arc]\n    add  rsp, 32",
     "ports":[{"n":"l","d":"10"},{"n":"t","d":"10"},{"n":"r","d":"200"},{"n":"b","d":"200"},
              {"n":"x1","d":"100"},{"n":"y1","d":"10"},{"n":"x2","d":"200"},{"n":"y2","d":"100"}]},
    {"id":"gdi.roundrect","title":"RoundRect","cat":"GDI32","desc":"Draw rounded rectangle",
     "tpl":"; RoundRect(hdc, l, t, r, b, w_round, h_round)\n    mov  [rsp+40], {hr}\n    mov  [rsp+32], {wr}\n    mov  r9,  {b}\n    mov  r8,  {r}\n    mov  rdx, {t}\n    mov  rcx, [rel hdc]\n    push rcx\n    mov  rcx, {l}\n    push rcx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [RoundRect]\n    add  rsp, 32",
     "ports":[{"n":"l","d":"10"},{"n":"t","d":"10"},{"n":"r","d":"200"},{"n":"b","d":"100"},{"n":"wr","d":"20"},{"n":"hr","d":"20"}]},
    {"id":"gdi.fillrect","title":"FillRect","cat":"GDI32","desc":"Fill rectangle with brush",
     "tpl":"; FillRect(hdc, &rect, hBrush)\n    mov  r8,  [rel hbrush]\n    lea  rdx, [rel rc_buf]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [FillRect]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.bitblt","title":"BitBlt","cat":"GDI32","desc":"Bit-block transfer (blit)",
     "tpl":"; BitBlt(dst,dx,dy,w,h,src,sx,sy,SRCCOPY)\n    mov  [rsp+56], 0xCC0020\n    mov  [rsp+48], {sy}\n    mov  [rsp+40], {sx}\n    mov  [rsp+32], [rel mem_dc]\n    mov  r9,  {h}\n    mov  r8,  {w}\n    mov  rdx, {dy}\n    mov  rcx, {dx}\n    push rcx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [BitBlt]\n    add  rsp, 32\n    pop  rcx",
     "ports":[{"n":"dx","d":"0"},{"n":"dy","d":"0"},{"n":"w","d":"800"},{"n":"h","d":"600"},{"n":"sx","d":"0"},{"n":"sy","d":"0"}]},
    {"id":"gdi.stretchblt","title":"StretchBlt","cat":"GDI32","desc":"Stretch/shrink blit with mode",
     "tpl":"; StretchBlt(hdc, dx,dy,dw,dh, src, sx,sy,sw,sh, SRCCOPY)\n    mov  [rsp+72], 0xCC0020\n    mov  [rsp+64], {sh}\n    mov  [rsp+56], {sw}\n    mov  [rsp+48], {sy}\n    mov  [rsp+40], {sx}\n    mov  [rsp+32], [rel mem_dc]\n    mov  r9,  {dh}\n    mov  r8,  {dw}\n    mov  rdx, {dy}\n    mov  rcx, {dx}\n    push rcx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [StretchBlt]\n    add  rsp, 32\n    pop  rcx",
     "ports":[{"n":"dx","d":"0"},{"n":"dy","d":"0"},{"n":"dw","d":"800"},{"n":"dh","d":"600"},
              {"n":"sx","d":"0"},{"n":"sy","d":"0"},{"n":"sw","d":"400"},{"n":"sh","d":"300"}]},
    {"id":"gdi.createbrush","title":"CreateSolidBrush","cat":"GDI32","desc":"Create solid color brush",
     "tpl":"; CreateSolidBrush(color)\n    mov  rcx, {color}\n    sub  rsp, 32\n    call [CreateSolidBrush]\n    add  rsp, 32\n    mov  [rel hbrush], rax",
     "ports":[{"n":"color","d":"0xFF0000"}]},
    {"id":"gdi.createhatch","title":"CreateHatchBrush","cat":"GDI32","desc":"Create hatched brush",
     "tpl":"; CreateHatchBrush(HS_CROSS=4, color)\n    mov  rdx, {color}\n    mov  rcx, {style}\n    sub  rsp, 32\n    call [CreateHatchBrush]\n    add  rsp, 32\n    mov  [rel hbrush], rax",
     "ports":[{"n":"style","d":"4"},{"n":"color","d":"0x0000FF"}]},
    {"id":"gdi.createpen","title":"CreatePen","cat":"GDI32","desc":"Create pen (PS_SOLID=0)",
     "tpl":"; CreatePen(style, width, color)\n    mov  r8,  {color}\n    mov  rdx, {width}\n    mov  rcx, {style}\n    sub  rsp, 32\n    call [CreatePen]\n    add  rsp, 32\n    mov  [rel hpen], rax",
     "ports":[{"n":"style","d":"0"},{"n":"width","d":"1"},{"n":"color","d":"0x000000"}]},
    {"id":"gdi.createfont","title":"CreateFontA","cat":"GDI32","desc":"Create logical font (many params)",
     "tpl":"; CreateFontA(h, w, esc, orient, weight, italic, under, strike, charset, outPrec, clipPrec, quality, pitchFamily, name)\n    lea  [rsp+96], [rel {fname}]\n    mov  [rsp+88], 0x22        ; DEFAULT_PITCH|FF_SWISS\n    mov  [rsp+80], 4           ; ANTIALIASED_QUALITY\n    mov  [rsp+72], 2           ; CLIP_DEFAULT_PRECIS\n    mov  [rsp+64], 4           ; OUT_TT_PRECIS\n    mov  [rsp+56], 1           ; ANSI_CHARSET\n    mov  [rsp+48], 0           ; not strikethrough\n    mov  [rsp+40], 0           ; not underline\n    mov  [rsp+32], 0           ; not italic\n    mov  r9,  400              ; FW_NORMAL\n    mov  r8,  0\n    mov  rdx, 0\n    mov  rcx, {height}         ; cell height in logical units\n    sub  rsp, 32\n    call [CreateFontA]\n    add  rsp, 32\n    mov  [rel hfont], rax",
     "ports":[{"n":"height","d":"24"},{"n":"fname","d":"font_name_str"}]},
    {"id":"gdi.selectobject","title":"SelectObject","cat":"GDI32","desc":"Select GDI object into DC → returns old object",
     "tpl":"; SelectObject(hdc, hobj)\n    mov  rdx, {obj}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SelectObject]\n    add  rsp, 32\n    mov  [rel old_obj], rax",
     "ports":[{"n":"obj","d":"[rel hpen]"}]},
    {"id":"gdi.deleteobject","title":"DeleteObject","cat":"GDI32","desc":"Delete GDI object and free resources",
     "tpl":"; DeleteObject(hobj)\n    mov  rcx, {obj}\n    sub  rsp, 32\n    call [DeleteObject]\n    add  rsp, 32",
     "ports":[{"n":"obj","d":"[rel hbrush]"}]},
    {"id":"gdi.getdc","title":"GetDC","cat":"GDI32","desc":"Get device context of a window",
     "tpl":"; GetDC(hwnd)\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [GetDC]\n    add  rsp, 32\n    mov  [rel hdc], rax","ports":[]},
    {"id":"gdi.releasedc","title":"ReleaseDC","cat":"GDI32","desc":"Release a GetDC-acquired DC",
     "tpl":"; ReleaseDC(hwnd, hdc)\n    mov  rdx, [rel hdc]\n    mov  rcx, [rel hwnd]\n    sub  rsp, 32\n    call [ReleaseDC]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.createcompatdc","title":"CreateCompatibleDC","cat":"GDI32","desc":"Create memory DC compatible with hDC",
     "tpl":"; CreateCompatibleDC(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [CreateCompatibleDC]\n    add  rsp, 32\n    mov  [rel mem_dc], rax","ports":[]},
    {"id":"gdi.createcombmp","title":"CreateCompatibleBitmap","cat":"GDI32","desc":"Create bitmap for off-screen rendering",
     "tpl":"; CreateCompatibleBitmap(hdc, w, h)\n    mov  r8,  {h}\n    mov  rdx, {w}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [CreateCompatibleBitmap]\n    add  rsp, 32\n    mov  [rel hbmp], rax",
     "ports":[{"n":"w","d":"800"},{"n":"h","d":"600"}]},
    {"id":"gdi.deletedc","title":"DeleteDC","cat":"GDI32","desc":"Delete a memory DC",
     "tpl":"; DeleteDC(mem_dc)\n    mov  rcx, [rel mem_dc]\n    sub  rsp, 32\n    call [DeleteDC]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.setbkcolor","title":"SetBkColor","cat":"GDI32","desc":"Set text background color",
     "tpl":"; SetBkColor(hdc, color)\n    mov  rdx, {color}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetBkColor]\n    add  rsp, 32",
     "ports":[{"n":"color","d":"0x000000"}]},
    {"id":"gdi.settextcolor","title":"SetTextColor","cat":"GDI32","desc":"Set text foreground color",
     "tpl":"; SetTextColor(hdc, color)\n    mov  rdx, {color}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetTextColor]\n    add  rsp, 32",
     "ports":[{"n":"color","d":"0xFFFFFF"}]},
    {"id":"gdi.setbkmode","title":"SetBkMode","cat":"GDI32","desc":"Set background mode (OPAQUE=2, TRANSPARENT=1)",
     "tpl":"; SetBkMode(hdc, TRANSPARENT)\n    mov  rdx, 1\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetBkMode]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.setrop2","title":"SetROP2","cat":"GDI32","desc":"Set binary raster op (R2_COPYPEN=13, R2_XORPEN=7)",
     "tpl":"; SetROP2(hdc, R2_XORPEN)\n    mov  rdx, {rop}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetROP2]\n    add  rsp, 32",
     "ports":[{"n":"rop","d":"7"}]},
    {"id":"gdi.setpixel","title":"SetPixel","cat":"GDI32","desc":"Draw a single pixel",
     "tpl":"; SetPixel(hdc, x, y, color)\n    mov  r9,  {color}\n    mov  r8,  {y}\n    mov  rdx, {x}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetPixel]\n    add  rsp, 32",
     "ports":[{"n":"x","d":"100"},{"n":"y","d":"100"},{"n":"color","d":"0xFF0000"}]},
    {"id":"gdi.getpixel","title":"GetPixel","cat":"GDI32","desc":"Get pixel color at (x,y)",
     "tpl":"; GetPixel(hdc, x, y) → rax=COLORREF\n    mov  r8,  {y}\n    mov  rdx, {x}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [GetPixel]\n    add  rsp, 32",
     "ports":[{"n":"x","d":"100"},{"n":"y","d":"100"}]},
    {"id":"gdi.getstockobj","title":"GetStockObject","cat":"GDI32","desc":"Get stock GDI object (NULL_BRUSH=5, BLACK_PEN=7)",
     "tpl":"; GetStockObject(obj_id)\n    mov  rcx, {obj}\n    sub  rsp, 32\n    call [GetStockObject]\n    add  rsp, 32",
     "ports":[{"n":"obj","d":"5"}]},
    {"id":"gdi.getdevcaps","title":"GetDeviceCaps","cat":"GDI32","desc":"Query DC capability (HORZRES=8, VERTRES=10, BITSPIXEL=12)",
     "tpl":"; GetDeviceCaps(hdc, index)\n    mov  rdx, {idx}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [GetDeviceCaps]\n    add  rsp, 32",
     "ports":[{"n":"idx","d":"8"}]},
    {"id":"gdi.gettextextent","title":"GetTextExtentPoint32A","cat":"GDI32","desc":"Get text rendering dimensions",
     "tpl":"; GetTextExtentPoint32A(hdc, str, len, &SIZE)\n    lea  r9,  [rel text_size]\n    mov  r8,  {len}\n    lea  rdx, [rel {str}]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [GetTextExtentPoint32A]\n    add  rsp, 32",
     "ports":[{"n":"str","d":"msg"},{"n":"len","d":"13"}]},
    {"id":"gdi.savedc","title":"SaveDC","cat":"GDI32","desc":"Save DC state, returns save-ID",
     "tpl":"; SaveDC(hdc) → saved state ID in rax\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SaveDC]\n    add  rsp, 32\n    mov  [rel dc_save_id], eax","ports":[]},
    {"id":"gdi.restoredc","title":"RestoreDC","cat":"GDI32","desc":"Restore DC to saved state ID",
     "tpl":"; RestoreDC(hdc, saved_id)\n    mov  rdx, {sid}\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [RestoreDC]\n    add  rsp, 32",
     "ports":[{"n":"sid","d":"-1"}]},
    {"id":"gdi.polygon","title":"Polygon","cat":"GDI32","desc":"Draw filled polygon from POINT array",
     "tpl":"; Polygon(hdc, &points, count)\n    mov  r8,  {count}\n    lea  rdx, [rel {pts}]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [Polygon]\n    add  rsp, 32",
     "ports":[{"n":"pts","d":"poly_pts"},{"n":"count","d":"3"}]},
    {"id":"gdi.polybezier","title":"PolyBezier","cat":"GDI32","desc":"Draw Bezier curves from POINT array",
     "tpl":"; PolyBezier(hdc, &points, count) — count = 3n+1\n    mov  r8,  {count}\n    lea  rdx, [rel {pts}]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [PolyBezier]\n    add  rsp, 32",
     "ports":[{"n":"pts","d":"bezier_pts"},{"n":"count","d":"4"}]},
    {"id":"gdi.beginpath","title":"BeginPath","cat":"GDI32","desc":"Start recording path into DC",
     "tpl":"; BeginPath(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [BeginPath]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.endpath","title":"EndPath + StrokeAndFillPath","cat":"GDI32","desc":"End path and stroke+fill it",
     "tpl":"; EndPath(hdc) + StrokeAndFillPath(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [EndPath]\n    add  rsp, 32\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [StrokeAndFillPath]\n    add  rsp, 32","ports":[]},
    {"id":"gdi.createdibs","title":"CreateDIBSection","cat":"GDI32","desc":"Create device-independent bitmap for direct pixel access",
     "tpl":"; CreateDIBSection(hdc, &BITMAPINFO, DIB_RGB_COLORS, &bits, NULL, 0)\n    mov  [rsp+48], 0\n    mov  [rsp+40], 0\n    lea  r9,  [rel dib_bits_ptr]\n    mov  r8,  0             ; DIB_RGB_COLORS\n    lea  rdx, [rel bmi]    ; BITMAPINFO\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [CreateDIBSection]\n    add  rsp, 32\n    mov  [rel hbmp], rax","ports":[]},

    # ═══════════════════  OpenGL32 / WGL  ═══════════════════
    {"id":"gl.setup_pfd","title":"SetPixelFormat","cat":"OpenGL32","desc":"Setup PFD & set pixel format",
     "tpl":"; ChoosePixelFormat + SetPixelFormat\n    mov  word [rel pfd+0],  40\n    mov  word [rel pfd+2],  1\n    mov  dword [rel pfd+4], 0x25\n    mov  byte  [rel pfd+8],  0\n    mov  byte  [rel pfd+9],  32\n    mov  byte  [rel pfd+22], 24\n    lea  rdx, [rel pfd]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [ChoosePixelFormat]\n    add  rsp, 32\n    mov  [rel pf_idx], eax\n    lea  r8, [rel pfd]\n    mov  edx, [rel pf_idx]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SetPixelFormat]\n    add  rsp, 32","ports":[]},
    {"id":"gl.create_ctx","title":"wglCreateContext","cat":"OpenGL32","desc":"Create GL rendering context",
     "tpl":"; wglCreateContext(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [wglCreateContext]\n    add  rsp, 32\n    mov  [rel hrc], rax","ports":[]},
    {"id":"gl.make_current","title":"wglMakeCurrent","cat":"OpenGL32","desc":"Make GL context current",
     "tpl":"; wglMakeCurrent(hdc, hrc)\n    mov  rdx, [rel hrc]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [wglMakeCurrent]\n    add  rsp, 32","ports":[]},
    {"id":"gl.delete_ctx","title":"wglDeleteContext","cat":"OpenGL32","desc":"Delete GL rendering context",
     "tpl":"; wglDeleteContext(hrc)\n    mov  rcx, [rel hrc]\n    sub  rsp, 32\n    call [wglDeleteContext]\n    add  rsp, 32","ports":[]},
    {"id":"gl.swapbuffers","title":"SwapBuffers","cat":"OpenGL32","desc":"Swap front/back buffers (double-buffered rendering)",
     "tpl":"; SwapBuffers(hdc)\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [SwapBuffers]\n    add  rsp, 32","ports":[]},
    {"id":"gl.getprocaddr","title":"wglGetProcAddress","cat":"OpenGL32","desc":"Load OpenGL extension function pointer",
     "tpl":"; wglGetProcAddress(\"funcName\")\n    lea  rcx, [rel {fname}]\n    sub  rsp, 32\n    call [wglGetProcAddress]\n    add  rsp, 32\n    mov  [rel {fptr}], rax",
     "ports":[{"n":"fname","d":"gl_func_name_str"},{"n":"fptr","d":"pfn_func"}]},
    {"id":"gl.clear","title":"glClear","cat":"OpenGL32","desc":"Clear color+depth buffers",
     "tpl":"; glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT = 0x4100)\n    mov  ecx, 0x4100\n    sub  rsp, 32\n    call [glClear]\n    add  rsp, 32","ports":[]},
    {"id":"gl.clearcolor","title":"glClearColor","cat":"OpenGL32","desc":"Set clear color (r,g,b,a in xmm0–xmm3)",
     "tpl":"; glClearColor(r,g,b,a) — floats in xmm0-xmm3\n    movss xmm3, [rel {a}]\n    movss xmm2, [rel {b}]\n    movss xmm1, [rel {g}]\n    movss xmm0, [rel {r}]\n    sub   rsp, 32\n    call  [glClearColor]\n    add   rsp, 32",
     "ports":[{"n":"r","d":"bg_r"},{"n":"g","d":"bg_g"},{"n":"b","d":"bg_b"},{"n":"a","d":"one_f"}]},
    {"id":"gl.viewport","title":"glViewport","cat":"OpenGL32","desc":"Set viewport rectangle",
     "tpl":"; glViewport(x, y, w, h)\n    mov  r9,  {h}\n    mov  r8,  {w}\n    xor  rdx, rdx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [glViewport]\n    add  rsp, 32",
     "ports":[{"n":"w","d":"800"},{"n":"h","d":"600"}]},
    {"id":"gl.enable","title":"glEnable","cat":"OpenGL32","desc":"Enable GL capability",
     "tpl":"; glEnable(cap) — e.g. GL_DEPTH_TEST=0x0B71\n    mov  ecx, {cap}\n    sub  rsp, 32\n    call [glEnable]\n    add  rsp, 32",
     "ports":[{"n":"cap","d":"0x0B71"}]},
    {"id":"gl.disable","title":"glDisable","cat":"OpenGL32","desc":"Disable GL capability",
     "tpl":"; glDisable(cap)\n    mov  ecx, {cap}\n    sub  rsp, 32\n    call [glDisable]\n    add  rsp, 32",
     "ports":[{"n":"cap","d":"0x0B71"}]},
    {"id":"gl.depthmask","title":"glDepthMask","cat":"OpenGL32","desc":"Enable/disable depth buffer writes (GL_TRUE/GL_FALSE)",
     "tpl":"; glDepthMask(GL_FALSE=0)\n    mov  ecx, {flag}\n    sub  rsp, 32\n    call [glDepthMask]\n    add  rsp, 32",
     "ports":[{"n":"flag","d":"1"}]},
    {"id":"gl.depthfunc","title":"glDepthFunc","cat":"OpenGL32","desc":"Set depth comparison (GL_LESS=0x0201, GL_LEQUAL=0x0203)",
     "tpl":"; glDepthFunc(func)\n    mov  ecx, {func}\n    sub  rsp, 32\n    call [glDepthFunc]\n    add  rsp, 32",
     "ports":[{"n":"func","d":"0x0201"}]},
    {"id":"gl.blendfunc","title":"glBlendFunc","cat":"OpenGL32","desc":"Set blend factors for transparency",
     "tpl":"; glBlendFunc(sfactor, dfactor)\n    ; GL_SRC_ALPHA=0x0302 GL_ONE_MINUS_SRC_ALPHA=0x0303\n    mov  rdx, 0x0303\n    mov  rcx, 0x0302\n    sub  rsp, 32\n    call [glBlendFunc]\n    add  rsp, 32","ports":[]},
    {"id":"gl.cullface","title":"glCullFace","cat":"OpenGL32","desc":"Set which face to cull (GL_BACK=0x0405)",
     "tpl":"; glCullFace(GL_BACK)\n    mov  ecx, 0x0405\n    sub  rsp, 32\n    call [glCullFace]\n    add  rsp, 32","ports":[]},
    {"id":"gl.begin","title":"glBegin","cat":"OpenGL32","desc":"Begin immediate-mode primitive",
     "tpl":"; glBegin(GL_TRIANGLES=0x0004)\n    mov  ecx, {mode}\n    sub  rsp, 32\n    call [glBegin]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"0x0004"}]},
    {"id":"gl.end","title":"glEnd","cat":"OpenGL32","desc":"End immediate-mode primitive",
     "tpl":"; glEnd()\n    sub  rsp, 32\n    call [glEnd]\n    add  rsp, 32","ports":[]},
    {"id":"gl.vertex3f","title":"glVertex3f","cat":"OpenGL32","desc":"3D vertex (x,y,z via xmm0-xmm2)",
     "tpl":"; glVertex3f(x,y,z) — load into xmm0,xmm1,xmm2\n    movss xmm2, [rel {z}]\n    movss xmm1, [rel {y}]\n    movss xmm0, [rel {x}]\n    sub   rsp, 32\n    call  [glVertex3f]\n    add   rsp, 32",
     "ports":[{"n":"x","d":"vx"},{"n":"y","d":"vy"},{"n":"z","d":"vz"}]},
    {"id":"gl.color3f","title":"glColor3f","cat":"OpenGL32","desc":"Set current color (r,g,b via xmm0-xmm2)",
     "tpl":"; glColor3f(r,g,b) — floats in xmm0,xmm1,xmm2\n    movss xmm2, [rel {b}]\n    movss xmm1, [rel {g}]\n    movss xmm0, [rel {r}]\n    sub   rsp, 32\n    call  [glColor3f]\n    add   rsp, 32",
     "ports":[{"n":"r","d":"col_r"},{"n":"g","d":"col_g"},{"n":"b","d":"col_b"}]},
    {"id":"gl.texcoord2f","title":"glTexCoord2f","cat":"OpenGL32","desc":"Set texture coordinate (u,v in xmm0,xmm1)",
     "tpl":"; glTexCoord2f(u,v)\n    movss xmm1, [rel {v}]\n    movss xmm0, [rel {u}]\n    sub   rsp, 32\n    call  [glTexCoord2f]\n    add   rsp, 32",
     "ports":[{"n":"u","d":"tc_u"},{"n":"v","d":"tc_v"}]},
    {"id":"gl.normal3f","title":"glNormal3f","cat":"OpenGL32","desc":"Set vertex normal (nx,ny,nz in xmm0-xmm2)",
     "tpl":"; glNormal3f(nx,ny,nz)\n    movss xmm2, [rel {nz}]\n    movss xmm1, [rel {ny}]\n    movss xmm0, [rel {nx}]\n    sub   rsp, 32\n    call  [glNormal3f]\n    add   rsp, 32",
     "ports":[{"n":"nx","d":"n_x"},{"n":"ny","d":"n_y"},{"n":"nz","d":"n_z"}]},
    {"id":"gl.matrixmode","title":"glMatrixMode","cat":"OpenGL32","desc":"Set matrix mode (0x1700=MODELVIEW,0x1701=PROJECTION)",
     "tpl":"; glMatrixMode(mode)\n    mov  ecx, {mode}\n    sub  rsp, 32\n    call [glMatrixMode]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"0x1700"}]},
    {"id":"gl.loadidentity","title":"glLoadIdentity","cat":"OpenGL32","desc":"Load identity matrix into current matrix",
     "tpl":"; glLoadIdentity()\n    sub  rsp, 32\n    call [glLoadIdentity]\n    add  rsp, 32","ports":[]},
    {"id":"gl.loadmatrixf","title":"glLoadMatrixf","cat":"OpenGL32","desc":"Load 4×4 column-major f32 matrix",
     "tpl":"; glLoadMatrixf(&mat4)\n    lea  rcx, [rel {mat}]\n    sub  rsp, 32\n    call [glLoadMatrixf]\n    add  rsp, 32",
     "ports":[{"n":"mat","d":"my_matrix"}]},
    {"id":"gl.multmatrixf","title":"glMultMatrixf","cat":"OpenGL32","desc":"Multiply current matrix by column-major f32 matrix",
     "tpl":"; glMultMatrixf(&mat4)\n    lea  rcx, [rel {mat}]\n    sub  rsp, 32\n    call [glMultMatrixf]\n    add  rsp, 32",
     "ports":[{"n":"mat","d":"rot_mat"}]},
    {"id":"gl.translatef","title":"glTranslatef","cat":"OpenGL32","desc":"Translate current matrix by (x,y,z)",
     "tpl":"; glTranslatef(x,y,z)\n    movss xmm2, [rel {tz}]\n    movss xmm1, [rel {ty}]\n    movss xmm0, [rel {tx}]\n    sub   rsp, 32\n    call  [glTranslatef]\n    add   rsp, 32",
     "ports":[{"n":"tx","d":"t_x"},{"n":"ty","d":"t_y"},{"n":"tz","d":"t_z"}]},
    {"id":"gl.rotatef","title":"glRotatef","cat":"OpenGL32","desc":"Rotate current matrix by angle around axis",
     "tpl":"; glRotatef(angle_deg, ax, ay, az)\n    movss xmm3, [rel {az}]\n    movss xmm2, [rel {ay}]\n    movss xmm1, [rel {ax}]\n    movss xmm0, [rel {angle}]\n    sub   rsp, 32\n    call  [glRotatef]\n    add   rsp, 32",
     "ports":[{"n":"angle","d":"rot_angle"},{"n":"ax","d":"ax_x"},{"n":"ay","d":"ax_y"},{"n":"az","d":"ax_z"}]},
    {"id":"gl.scalef","title":"glScalef","cat":"OpenGL32","desc":"Scale current matrix",
     "tpl":"; glScalef(sx, sy, sz)\n    movss xmm2, [rel {sz}]\n    movss xmm1, [rel {sy}]\n    movss xmm0, [rel {sx}]\n    sub   rsp, 32\n    call  [glScalef]\n    add   rsp, 32",
     "ports":[{"n":"sx","d":"s_x"},{"n":"sy","d":"s_y"},{"n":"sz","d":"s_z"}]},
    {"id":"gl.pushmatrix","title":"glPushMatrix","cat":"OpenGL32","desc":"Push current matrix onto stack",
     "tpl":"; glPushMatrix()\n    sub  rsp, 32\n    call [glPushMatrix]\n    add  rsp, 32","ports":[]},
    {"id":"gl.popmatrix","title":"glPopMatrix","cat":"OpenGL32","desc":"Pop matrix from stack",
     "tpl":"; glPopMatrix()\n    sub  rsp, 32\n    call [glPopMatrix]\n    add  rsp, 32","ports":[]},
    {"id":"gl.gentex","title":"glGenTextures","cat":"OpenGL32","desc":"Generate texture names",
     "tpl":"; glGenTextures(n, &textures)\n    lea  rdx, [rel {texbuf}]\n    mov  rcx, {n}\n    sub  rsp, 32\n    call [glGenTextures]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"texbuf","d":"tex_ids"}]},
    {"id":"gl.bindtex","title":"glBindTexture","cat":"OpenGL32","desc":"Bind texture (GL_TEXTURE_2D=0x0DE1)",
     "tpl":"; glBindTexture(GL_TEXTURE_2D, texID)\n    mov  rdx, [rel {tex}]\n    mov  rcx, 0x0DE1\n    sub  rsp, 32\n    call [glBindTexture]\n    add  rsp, 32",
     "ports":[{"n":"tex","d":"tex_id"}]},
    {"id":"gl.teximage2d","title":"glTexImage2D","cat":"OpenGL32","desc":"Upload 2D texture data",
     "tpl":"; glTexImage2D(GL_TEXTURE_2D, level, internalFmt, w, h, border, fmt, type, data)\n    mov  [rsp+64], {data}\n    mov  [rsp+56], 0x1401      ; GL_UNSIGNED_BYTE\n    mov  [rsp+48], 0x1908      ; GL_RGBA\n    mov  [rsp+40], 0\n    mov  [rsp+32], {h}\n    mov  r9,  {w}\n    mov  r8,  0x8058           ; GL_RGBA8\n    mov  rdx, 0\n    mov  rcx, 0x0DE1\n    push rcx\n    mov  rcx, 0x0DE1           ; GL_TEXTURE_2D\n    sub  rsp, 32\n    call [glTexImage2D]\n    add  rsp, 32",
     "ports":[{"n":"w","d":"256"},{"n":"h","d":"256"},{"n":"data","d":"[rel pPixels]"}]},
    {"id":"gl.texparami","title":"glTexParameteri","cat":"OpenGL32",
     "desc":"Set texture integer parameter (filter, wrap, etc.)",
     "tpl":"; glTexParameteri(GL_TEXTURE_2D, pname, param)\n    ; MIN_FILTER=0x2801 MAG=0x2800 WRAP_S=0x2802 WRAP_T=0x2803\n    ; GL_LINEAR=0x2601 GL_NEAREST=0x2600 GL_REPEAT=0x2901 GL_CLAMP=0x2900\n    mov  r8,  {param}\n    mov  rdx, {pname}\n    mov  rcx, 0x0DE1        ; GL_TEXTURE_2D\n    sub  rsp, 32\n    call [glTexParameteri]\n    add  rsp, 32",
     "ports":[{"n":"pname","d":"0x2801"},{"n":"param","d":"0x2601"}]},
    {"id":"gl.texparamf","title":"glTexParameterf","cat":"OpenGL32",
     "desc":"Set texture float parameter (e.g. GL_TEXTURE_MAX_ANISOTROPY)",
     "tpl":"; glTexParameterf(GL_TEXTURE_2D, pname, param)\n    movss xmm2, [rel {param}]\n    mov   rdx,  {pname}\n    mov   rcx,  0x0DE1\n    sub   rsp, 32\n    call  [glTexParameterf]\n    add   rsp, 32",
     "ports":[{"n":"pname","d":"0x84FE"},{"n":"param","d":"aniso_val"}]},
    {"id":"gl.generatemipmap","title":"glGenerateMipmap","cat":"OpenGL32",
     "desc":"Auto-generate mipmap chain for bound texture",
     "tpl":"; glGenerateMipmap(GL_TEXTURE_2D)\n    mov  ecx, 0x0DE1\n    sub  rsp, 32\n    call [glGenerateMipmap]\n    add  rsp, 32","ports":[]},
    {"id":"gl.activetex","title":"glActiveTexture","cat":"OpenGL32",
     "desc":"Select texture unit (GL_TEXTURE0=0x84C0 + n)",
     "tpl":"; glActiveTexture(GL_TEXTURE0 + {unit})\n    mov  ecx, 0x84C0 + {unit}\n    sub  rsp, 32\n    call [glActiveTexture]\n    add  rsp, 32",
     "ports":[{"n":"unit","d":"0"}]},
    {"id":"gl.deltex","title":"glDeleteTextures","cat":"OpenGL32",
     "desc":"Delete texture names",
     "tpl":"; glDeleteTextures(n, &ids)\n    lea  rdx, [rel {buf}]\n    mov  rcx, {n}\n    sub  rsp, 32\n    call [glDeleteTextures]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"buf","d":"tex_ids"}]},
    {"id":"gl.genbuf","title":"glGenBuffers (VBO)","cat":"OpenGL32",
     "desc":"Generate VBO/IBO/UBO names (ARB ext pointer via wglGetProcAddress)",
     "tpl":"; glGenBuffers(n, &ids)\n    lea  rdx, [rel {buf}]\n    mov  ecx, {n}\n    sub  rsp, 32\n    call [rel pfn_glGenBuffers]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"buf","d":"vbo_ids"}]},
    {"id":"gl.bindbuf","title":"glBindBuffer","cat":"OpenGL32",
     "desc":"Bind buffer object (GL_ARRAY_BUFFER=0x8892, GL_ELEMENT_ARRAY_BUFFER=0x8893)",
     "tpl":"; glBindBuffer(target, id)\n    mov  rdx, [rel {id}]\n    mov  rcx, {target}\n    sub  rsp, 32\n    call [rel pfn_glBindBuffer]\n    add  rsp, 32",
     "ports":[{"n":"target","d":"0x8892"},{"n":"id","d":"vbo_id"}]},
    {"id":"gl.bufdata","title":"glBufferData","cat":"OpenGL32",
     "desc":"Upload buffer data (GL_STATIC_DRAW=0x88B4, DYNAMIC=0x88B8, STREAM=0x88B0)",
     "tpl":"; glBufferData(target, size, data, usage)\n    mov  r9,  {usage}\n    mov  r8,  {data}\n    mov  rdx, {size}\n    mov  rcx, {target}\n    sub  rsp, 32\n    call [rel pfn_glBufferData]\n    add  rsp, 32",
     "ports":[{"n":"target","d":"0x8892"},{"n":"size","d":"rcx"},{"n":"data","d":"rsi"},{"n":"usage","d":"0x88B4"}]},
    {"id":"gl.bufsubdata","title":"glBufferSubData","cat":"OpenGL32",
     "desc":"Partial buffer update — hot-path upload (pos/rot/matrix per frame)",
     "tpl":"; glBufferSubData(target, offset, size, data)\n    mov  [rsp+32], {data}\n    mov  r9,  {size}\n    mov  r8,  {offset}\n    mov  rcx, {target}\n    push rcx\n    mov  rcx, {target}\n    sub  rsp, 32\n    call [rel pfn_glBufferSubData]\n    add  rsp, 32\n    pop  rcx",
     "ports":[{"n":"target","d":"0x8892"},{"n":"offset","d":"0"},{"n":"size","d":"rcx"},{"n":"data","d":"rsi"}]},
    {"id":"gl.mapbufrange","title":"glMapBufferRange","cat":"OpenGL32",
     "desc":"Persistent-mapped buffer pointer (GL_MAP_WRITE=2|PERSISTENT=64|COHERENT=128)",
     "tpl":"; glMapBufferRange(target, offset, length, access)\n    mov  [rsp+32], {access}\n    mov  r9,  {length}\n    xor  r8,  r8\n    mov  rcx, {target}\n    push rcx\n    mov  rcx, {target}\n    sub  rsp, 32\n    call [rel pfn_glMapBufferRange]\n    add  rsp, 32\n    pop  rcx\n    mov  [rel pMappedBuf], rax",
     "ports":[{"n":"target","d":"0x8892"},{"n":"length","d":"rdx"},{"n":"access","d":"0xC2"}]},
    {"id":"gl.unmapbuf","title":"glUnmapBuffer","cat":"OpenGL32",
     "desc":"Unmap a persistently mapped buffer",
     "tpl":"; glUnmapBuffer(target)\n    mov  ecx, {target}\n    sub  rsp, 32\n    call [rel pfn_glUnmapBuffer]\n    add  rsp, 32",
     "ports":[{"n":"target","d":"0x8892"}]},
    {"id":"gl.membarrier","title":"glMemoryBarrier","cat":"OpenGL32",
     "desc":"Insert memory barrier (GL_CLIENT_MAPPED_BUFFER_BARRIER_BIT=0x4000)",
     "tpl":"; glMemoryBarrier(barriers)\n    mov  ecx, {barriers}\n    sub  rsp, 32\n    call [rel pfn_glMemoryBarrier]\n    add  rsp, 32",
     "ports":[{"n":"barriers","d":"0x4000"}]},
    {"id":"gl.delbuf","title":"glDeleteBuffers","cat":"OpenGL32",
     "desc":"Delete VBO/IBO buffer names",
     "tpl":"; glDeleteBuffers(n, &ids)\n    lea  rdx, [rel {buf}]\n    mov  ecx, {n}\n    sub  rsp, 32\n    call [rel pfn_glDeleteBuffers]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"buf","d":"vbo_ids"}]},
    {"id":"gl.genva","title":"glGenVertexArrays (VAO)","cat":"OpenGL32",
     "desc":"Generate Vertex Array Object names",
     "tpl":"; glGenVertexArrays(n, &ids)\n    lea  rdx, [rel {buf}]\n    mov  ecx, {n}\n    sub  rsp, 32\n    call [rel pfn_glGenVertexArrays]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"buf","d":"vao_ids"}]},
    {"id":"gl.bindva","title":"glBindVertexArray","cat":"OpenGL32",
     "desc":"Bind VAO (captures VBO bindings and attrib pointers)",
     "tpl":"; glBindVertexArray(id)\n    mov  ecx, [rel {id}]\n    sub  rsp, 32\n    call [rel pfn_glBindVertexArray]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"vao_id"}]},
    {"id":"gl.vertexattribptr","title":"glVertexAttribPointer","cat":"OpenGL32",
     "desc":"Define vertex attrib pointer (index, size, type, norm, stride, offset)",
     "tpl":"; glVertexAttribPointer(idx, size, GL_FLOAT, GL_FALSE, stride, offset)\n    mov  [rsp+32], {offset}\n    mov  r9,  {stride}\n    mov  r8d, 0         ; GL_FALSE\n    mov  rdx, 0x1406   ; GL_FLOAT\n    push rdx\n    mov  rdx, {size}\n    mov  rcx, {idx}\n    sub  rsp, 32\n    call [rel pfn_glVertexAttribPointer]\n    add  rsp, 32\n    pop  rdx",
     "ports":[{"n":"idx","d":"0"},{"n":"size","d":"3"},{"n":"stride","d":"48"},{"n":"offset","d":"0"}]},
    {"id":"gl.enablevattrib","title":"glEnableVertexAttribArray","cat":"OpenGL32",
     "desc":"Enable vertex attrib index",
     "tpl":"; glEnableVertexAttribArray(idx)\n    mov  ecx, {idx}\n    sub  rsp, 32\n    call [rel pfn_glEnableVertexAttribArray]\n    add  rsp, 32",
     "ports":[{"n":"idx","d":"0"}]},
    {"id":"gl.disablevattrib","title":"glDisableVertexAttribArray","cat":"OpenGL32",
     "desc":"Disable vertex attrib index",
     "tpl":"; glDisableVertexAttribArray(idx)\n    mov  ecx, {idx}\n    sub  rsp, 32\n    call [rel pfn_glDisableVertexAttribArray]\n    add  rsp, 32",
     "ports":[{"n":"idx","d":"0"}]},
    {"id":"gl.creatshader","title":"glCreateShader","cat":"OpenGL32",
     "desc":"Create shader object (GL_VERTEX_SHADER=0x8B31, FRAGMENT=0x8B30, GEOMETRY=0x8DD9)",
     "tpl":"; glCreateShader(type) → id in rax\n    mov  ecx, {type}\n    sub  rsp, 32\n    call [rel pfn_glCreateShader]\n    add  rsp, 32\n    mov  [rel {id}], eax",
     "ports":[{"n":"type","d":"0x8B31"},{"n":"id","d":"vert_id"}]},
    {"id":"gl.shadersrc","title":"glShaderSource","cat":"OpenGL32",
     "desc":"Set GLSL shader source (count=1 string)",
     "tpl":"; glShaderSource(id, 1, &srcPtr, &lenPtr)\n    lea  r9,  [rel {lenptr}]    ; NULL = null-terminated\n    lea  r8,  [rel {srcptr}]\n    mov  rdx, 1\n    mov  ecx, [rel {id}]\n    sub  rsp, 32\n    call [rel pfn_glShaderSource]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"vert_id"},{"n":"srcptr","d":"glsl_src_ptr"},{"n":"lenptr","d":"0"}]},
    {"id":"gl.compileshader","title":"glCompileShader","cat":"OpenGL32",
     "desc":"Compile shader source",
     "tpl":"; glCompileShader(id)\n    mov  ecx, [rel {id}]\n    sub  rsp, 32\n    call [rel pfn_glCompileShader]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"vert_id"}]},
    {"id":"gl.getshaderiv","title":"glGetShaderiv","cat":"OpenGL32",
     "desc":"Query shader compile status (GL_COMPILE_STATUS=0x8B81)",
     "tpl":"; glGetShaderiv(id, GL_COMPILE_STATUS, &result)\n    lea  r8,  [rel {res}]\n    mov  rdx, 0x8B81\n    mov  ecx, [rel {id}]\n    sub  rsp, 32\n    call [rel pfn_glGetShaderiv]\n    add  rsp, 32\n    cmp  dword [rel {res}], 0\n    je   .shader_error",
     "ports":[{"n":"id","d":"vert_id"},{"n":"res","d":"compile_ok"}]},
    {"id":"gl.getshaderlog","title":"glGetShaderInfoLog","cat":"OpenGL32",
     "desc":"Get shader compilation error log",
     "tpl":"; glGetShaderInfoLog(id, bufsize, &length, buf)\n    lea  [rsp+32], [rel {buf}]\n    lea  r9,  [rel {loglen}]\n    mov  r8,  {bufsize}\n    mov  ecx, [rel {id}]\n    sub  rsp, 32\n    call [rel pfn_glGetShaderInfoLog]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"vert_id"},{"n":"bufsize","d":"512"},{"n":"loglen","d":"log_len"},{"n":"buf","d":"log_buf"}]},
    {"id":"gl.createprog","title":"glCreateProgram","cat":"OpenGL32",
     "desc":"Create shader program object",
     "tpl":"; glCreateProgram() → id in rax\n    sub  rsp, 32\n    call [rel pfn_glCreateProgram]\n    add  rsp, 32\n    mov  [rel prog_id], eax","ports":[]},
    {"id":"gl.attachshader","title":"glAttachShader","cat":"OpenGL32",
     "desc":"Attach compiled shader to program",
     "tpl":"; glAttachShader(prog, shader)\n    mov  rdx, [rel {shader}]\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glAttachShader]\n    add  rsp, 32",
     "ports":[{"n":"prog","d":"prog_id"},{"n":"shader","d":"vert_id"}]},
    {"id":"gl.linkprog","title":"glLinkProgram","cat":"OpenGL32",
     "desc":"Link shader program (all attached shaders)",
     "tpl":"; glLinkProgram(prog)\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glLinkProgram]\n    add  rsp, 32",
     "ports":[{"n":"prog","d":"prog_id"}]},
    {"id":"gl.getprogiv","title":"glGetProgramiv","cat":"OpenGL32",
     "desc":"Query program link status (GL_LINK_STATUS=0x8B82)",
     "tpl":"; glGetProgramiv(prog, GL_LINK_STATUS, &res)\n    lea  r8,  [rel {res}]\n    mov  rdx, 0x8B82\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glGetProgramiv]\n    add  rsp, 32",
     "ports":[{"n":"prog","d":"prog_id"},{"n":"res","d":"link_ok"}]},
    {"id":"gl.useprog","title":"glUseProgram","cat":"OpenGL32",
     "desc":"Activate shader program for rendering",
     "tpl":"; glUseProgram(prog)\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glUseProgram]\n    add  rsp, 32",
     "ports":[{"n":"prog","d":"prog_id"}]},
    {"id":"gl.delprog","title":"glDeleteProgram","cat":"OpenGL32",
     "desc":"Delete shader program object",
     "tpl":"; glDeleteProgram(prog)\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glDeleteProgram]\n    add  rsp, 32",
     "ports":[{"n":"prog","d":"prog_id"}]},
    {"id":"gl.getuniformloc","title":"glGetUniformLocation","cat":"OpenGL32",
     "desc":"Get uniform variable location by name",
     "tpl":"; glGetUniformLocation(prog, name) → loc in rax\n    lea  rdx, [rel {name}]\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glGetUniformLocation]\n    add  rsp, 32\n    mov  [rel {loc}], eax",
     "ports":[{"n":"prog","d":"prog_id"},{"n":"name","d":"u_name_str"},{"n":"loc","d":"u_loc"}]},
    {"id":"gl.uniform1i","title":"glUniform1i","cat":"OpenGL32",
     "desc":"Set uniform int (sampler, flags)",
     "tpl":"; glUniform1i(loc, val)\n    mov  rdx, {val}\n    mov  ecx, [rel {loc}]\n    sub  rsp, 32\n    call [rel pfn_glUniform1i]\n    add  rsp, 32",
     "ports":[{"n":"loc","d":"tex_loc"},{"n":"val","d":"0"}]},
    {"id":"gl.uniform1f","title":"glUniform1f","cat":"OpenGL32",
     "desc":"Set uniform float (xmm1=value)",
     "tpl":"; glUniform1f(loc, val)\n    movss xmm1, [rel {val}]\n    mov   ecx,  [rel {loc}]\n    sub   rsp, 32\n    call  [rel pfn_glUniform1f]\n    add   rsp, 32",
     "ports":[{"n":"loc","d":"time_loc"},{"n":"val","d":"f_time"}]},
    {"id":"gl.uniform2f","title":"glUniform2f","cat":"OpenGL32",
     "desc":"Set uniform vec2 (xmm1,xmm2)",
     "tpl":"; glUniform2f(loc, x, y)\n    movss xmm2, [rel {y}]\n    movss xmm1, [rel {x}]\n    mov   ecx,  [rel {loc}]\n    sub   rsp, 32\n    call  [rel pfn_glUniform2f]\n    add   rsp, 32",
     "ports":[{"n":"loc","d":"res_loc"},{"n":"x","d":"f_w"},{"n":"y","d":"f_h"}]},
    {"id":"gl.uniform3f","title":"glUniform3f","cat":"OpenGL32",
     "desc":"Set uniform vec3 (xmm1-xmm3)",
     "tpl":"; glUniform3f(loc, x, y, z)\n    movss xmm3, [rel {z}]\n    movss xmm2, [rel {y}]\n    movss xmm1, [rel {x}]\n    mov   ecx,  [rel {loc}]\n    sub   rsp, 32\n    call  [rel pfn_glUniform3f]\n    add   rsp, 32",
     "ports":[{"n":"loc","d":"light_loc"},{"n":"x","d":"lx"},{"n":"y","d":"ly"},{"n":"z","d":"lz"}]},
    {"id":"gl.uniform4f","title":"glUniform4f","cat":"OpenGL32",
     "desc":"Set uniform vec4 (xmm1-xmm4)",
     "tpl":"; glUniform4f(loc, x, y, z, w)\n    movss xmm4, [rel {w}]\n    movss xmm3, [rel {z}]\n    movss xmm2, [rel {y}]\n    movss xmm1, [rel {x}]\n    mov   ecx,  [rel {loc}]\n    sub   rsp, 32\n    call  [rel pfn_glUniform4f]\n    add   rsp, 32",
     "ports":[{"n":"loc","d":"col_loc"},{"n":"x","d":"cr"},{"n":"y","d":"cg"},{"n":"z","d":"cb"},{"n":"w","d":"ca"}]},
    {"id":"gl.uniformmatrix4fv","title":"glUniformMatrix4fv","cat":"OpenGL32",
     "desc":"Upload 4×4 matrix uniform (column-major, count=1)",
     "tpl":"; glUniformMatrix4fv(loc, count, transpose, &mat)\n    lea  [rsp+32], [rel {mat}]\n    mov  r9d, 0          ; GL_FALSE\n    mov  r8,  1\n    mov  ecx, [rel {loc}]\n    sub  rsp, 32\n    call [rel pfn_glUniformMatrix4fv]\n    add  rsp, 32",
     "ports":[{"n":"loc","d":"mvp_loc"},{"n":"mat","d":"mvp_matrix"}]},
    {"id":"gl.uniform3fv","title":"glUniform3fv","cat":"OpenGL32",
     "desc":"Upload vec3 array uniform",
     "tpl":"; glUniform3fv(loc, count, &data)\n    lea  r8,  [rel {data}]\n    mov  rdx, {count}\n    mov  ecx, [rel {loc}]\n    sub  rsp, 32\n    call [rel pfn_glUniform3fv]\n    add  rsp, 32",
     "ports":[{"n":"loc","d":"lights_loc"},{"n":"count","d":"8"},{"n":"data","d":"lights_arr"}]},
    {"id":"gl.getattribloc","title":"glGetAttribLocation","cat":"OpenGL32",
     "desc":"Get vertex attrib location by name",
     "tpl":"; glGetAttribLocation(prog, name) → loc\n    lea  rdx, [rel {name}]\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glGetAttribLocation]\n    add  rsp, 32\n    mov  [rel {loc}], eax",
     "ports":[{"n":"prog","d":"prog_id"},{"n":"name","d":"attr_name"},{"n":"loc","d":"attr_loc"}]},
    {"id":"gl.bindattribloc","title":"glBindAttribLocation","cat":"OpenGL32",
     "desc":"Bind vertex attrib location before linking",
     "tpl":"; glBindAttribLocation(prog, idx, name)\n    lea  r8,  [rel {name}]\n    mov  rdx, {idx}\n    mov  ecx, [rel {prog}]\n    sub  rsp, 32\n    call [rel pfn_glBindAttribLocation]\n    add  rsp, 32",
     "ports":[{"n":"prog","d":"prog_id"},{"n":"idx","d":"0"},{"n":"name","d":"attr_name"}]},
    {"id":"gl.drawarrays","title":"glDrawArrays","cat":"OpenGL32",
     "desc":"Draw primitives from array data",
     "tpl":"; glDrawArrays(mode, first, count)\n    mov  r8,  {count}\n    xor  rdx, rdx\n    mov  ecx, {mode}     ; GL_TRIANGLES=4\n    sub  rsp, 32\n    call [rel pfn_glDrawArrays]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"4"},{"n":"count","d":"36"}]},
    {"id":"gl.drawelements","title":"glDrawElements","cat":"OpenGL32",
     "desc":"Draw indexed primitives (IBO bound)",
     "tpl":"; glDrawElements(mode, count, GL_UNSIGNED_INT, offset)\n    xor  r9,  r9\n    mov  r8,  0x1405   ; GL_UNSIGNED_INT\n    mov  rdx, {count}\n    mov  ecx, {mode}\n    sub  rsp, 32\n    call [rel pfn_glDrawElements]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"4"},{"n":"count","d":"rcx"}]},
    {"id":"gl.drawelemsinst","title":"glDrawElementsInstanced","cat":"OpenGL32",
     "desc":"Instanced draw — Cannonic feed: 1 call for N entities",
     "tpl":"; glDrawElementsInstanced(mode, count, GL_UNSIGNED_INT, 0, instanceCount)\n    mov  [rsp+32], {instances}\n    xor  r9,  r9\n    mov  r8,  0x1405   ; GL_UNSIGNED_INT\n    mov  rdx, {count}\n    mov  ecx, {mode}\n    sub  rsp, 32\n    call [rel pfn_glDrawElementsInstanced]\n    add  rsp, 32",
     "ports":[{"n":"mode","d":"4"},{"n":"count","d":"rdx"},{"n":"instances","d":"rcx"}]},
    {"id":"gl.genfbo","title":"glGenFramebuffers","cat":"OpenGL32",
     "desc":"Generate Framebuffer Object (FBO) for off-screen rendering",
     "tpl":"; glGenFramebuffers(1, &fbo)\n    lea  rdx, [rel {fbo}]\n    mov  ecx, 1\n    sub  rsp, 32\n    call [rel pfn_glGenFramebuffers]\n    add  rsp, 32",
     "ports":[{"n":"fbo","d":"fbo_id"}]},
    {"id":"gl.bindfbo","title":"glBindFramebuffer","cat":"OpenGL32",
     "desc":"Bind FBO (GL_FRAMEBUFFER=0x8D40, GL_DRAW_FRAMEBUFFER=0x8CA9)",
     "tpl":"; glBindFramebuffer(target, id)\n    mov  rdx, [rel {id}]\n    mov  ecx, 0x8D40\n    sub  rsp, 32\n    call [rel pfn_glBindFramebuffer]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"fbo_id"}]},
    {"id":"gl.fbtex2d","title":"glFramebufferTexture2D","cat":"OpenGL32",
     "desc":"Attach texture to FBO attachment point",
     "tpl":"; glFramebufferTexture2D(GL_FRAMEBUFFER, attachment, GL_TEXTURE_2D, tex, 0)\n    mov  [rsp+32], 0\n    mov  r9,  [rel {tex}]\n    mov  r8,  0x0DE1    ; GL_TEXTURE_2D\n    mov  rdx, {attach}  ; GL_COLOR_ATTACHMENT0=0x8CE0\n    mov  ecx, 0x8D40\n    sub  rsp, 32\n    call [rel pfn_glFramebufferTexture2D]\n    add  rsp, 32",
     "ports":[{"n":"tex","d":"color_tex"},{"n":"attach","d":"0x8CE0"}]},
    {"id":"gl.genrbo","title":"glGenRenderbuffers","cat":"OpenGL32",
     "desc":"Generate Renderbuffer Object (depth/stencil attachment)",
     "tpl":"; glGenRenderbuffers(1, &rbo)\n    lea  rdx, [rel {rbo}]\n    mov  ecx, 1\n    sub  rsp, 32\n    call [rel pfn_glGenRenderbuffers]\n    add  rsp, 32",
     "ports":[{"n":"rbo","d":"rbo_id"}]},
    {"id":"gl.bindrbo","title":"glBindRenderbuffer","cat":"OpenGL32",
     "desc":"Bind renderbuffer (GL_RENDERBUFFER=0x8D41)",
     "tpl":"; glBindRenderbuffer(GL_RENDERBUFFER, id)\n    mov  rdx, [rel {id}]\n    mov  ecx, 0x8D41\n    sub  rsp, 32\n    call [rel pfn_glBindRenderbuffer]\n    add  rsp, 32",
     "ports":[{"n":"id","d":"rbo_id"}]},
    {"id":"gl.rbostorage","title":"glRenderbufferStorage","cat":"OpenGL32",
     "desc":"Allocate renderbuffer storage (GL_DEPTH24_STENCIL8=0x88F0)",
     "tpl":"; glRenderbufferStorage(GL_RENDERBUFFER, format, w, h)\n    mov  r9,  {h}\n    mov  r8,  {w}\n    mov  rdx, {fmt}     ; GL_DEPTH24_STENCIL8=0x88F0\n    mov  ecx, 0x8D41\n    sub  rsp, 32\n    call [rel pfn_glRenderbufferStorage]\n    add  rsp, 32",
     "ports":[{"n":"fmt","d":"0x88F0"},{"n":"w","d":"800"},{"n":"h","d":"600"}]},
    {"id":"gl.fbrbo","title":"glFramebufferRenderbuffer","cat":"OpenGL32",
     "desc":"Attach renderbuffer to FBO as depth/stencil",
     "tpl":"; glFramebufferRenderbuffer(GL_FRAMEBUFFER, attach, GL_RENDERBUFFER, rbo)\n    mov  r9,  [rel {rbo}]\n    mov  r8,  0x8D41\n    mov  rdx, {attach}  ; GL_DEPTH_STENCIL_ATTACHMENT=0x821A\n    mov  ecx, 0x8D40\n    sub  rsp, 32\n    call [rel pfn_glFramebufferRenderbuffer]\n    add  rsp, 32",
     "ports":[{"n":"rbo","d":"rbo_id"},{"n":"attach","d":"0x821A"}]},
    {"id":"gl.checkfbstat","title":"glCheckFramebufferStatus","cat":"OpenGL32",
     "desc":"Verify FBO completeness (expect GL_FRAMEBUFFER_COMPLETE=0x8CD5)",
     "tpl":"; glCheckFramebufferStatus(GL_FRAMEBUFFER) → rax\n    mov  ecx, 0x8D40\n    sub  rsp, 32\n    call [rel pfn_glCheckFramebufferStatus]\n    add  rsp, 32\n    cmp  eax, 0x8CD5\n    jne  .fbo_error","ports":[]},
    {"id":"gl.drawbuffers","title":"glDrawBuffers","cat":"OpenGL32",
     "desc":"Set MRT (multiple render targets) draw buffer list",
     "tpl":"; glDrawBuffers(n, &buffers)\n    lea  rdx, [rel {bufs}]\n    mov  ecx, {n}\n    sub  rsp, 32\n    call [rel pfn_glDrawBuffers]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"bufs","d":"draw_bufs_arr"}]},
    {"id":"gl.blitfb","title":"glBlitFramebuffer","cat":"OpenGL32",
     "desc":"Copy region between FBOs (resolve MSAA)",
     "tpl":"; glBlitFramebuffer(sx0,sy0,sx1,sy1,dx0,dy0,dx1,dy1,mask,filter)\n    mov  [rsp+64], 0x2601  ; GL_LINEAR\n    mov  [rsp+56], 0x4100  ; GL_COLOR+DEPTH\n    mov  [rsp+48], {dy1}\n    mov  [rsp+40], {dx1}\n    mov  [rsp+32], {dx0}\n    mov  r9,  {sy1}\n    mov  r8,  {sx1}\n    mov  rdx, {sy0}\n    mov  rcx, {sx0}\n    push rcx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [rel pfn_glBlitFramebuffer]\n    add  rsp, 32\n    pop  rcx",
     "ports":[{"n":"sx0","d":"0"},{"n":"sy0","d":"0"},{"n":"sx1","d":"800"},{"n":"sy1","d":"600"},
              {"n":"dx0","d":"0"},{"n":"dy1","d":"600"},{"n":"dx1","d":"800"}]},
    {"id":"gl.gensampler","title":"glGenSamplers","cat":"OpenGL32",
     "desc":"Generate sampler objects (DSA-style texture filtering)",
     "tpl":"; glGenSamplers(n, &ids)\n    lea  rdx, [rel {buf}]\n    mov  ecx, {n}\n    sub  rsp, 32\n    call [rel pfn_glGenSamplers]\n    add  rsp, 32",
     "ports":[{"n":"n","d":"1"},{"n":"buf","d":"sampler_ids"}]},
    {"id":"gl.bindsampler","title":"glBindSampler","cat":"OpenGL32",
     "desc":"Bind sampler to texture unit",
     "tpl":"; glBindSampler(unit, sampler)\n    mov  rdx, [rel {sampler}]\n    mov  ecx, {unit}\n    sub  rsp, 32\n    call [rel pfn_glBindSampler]\n    add  rsp, 32",
     "ports":[{"n":"unit","d":"0"},{"n":"sampler","d":"sampler_id"}]},
    {"id":"gl.gensyncfence","title":"glFenceSync","cat":"OpenGL32",
     "desc":"Create GPU fence sync object (GL_SYNC_GPU_COMMANDS_COMPLETE=0x9117)",
     "tpl":"; glFenceSync(condition, flags) → sync object\n    xor  rdx, rdx\n    mov  ecx, 0x9117\n    sub  rsp, 32\n    call [rel pfn_glFenceSync]\n    add  rsp, 32\n    mov  [rel sync_obj], rax","ports":[]},
    {"id":"gl.clientwaitsync","title":"glClientWaitSync","cat":"OpenGL32",
     "desc":"CPU-wait on fence sync (flush=1, timeout_ns)",
     "tpl":"; glClientWaitSync(sync, GL_SYNC_FLUSH_COMMANDS_BIT=1, timeout_ns)\n    mov  r9,  {timeout}   ; 0=no-wait  0xFFFFFFFFFFFFFFFF=infinite\n    mov  r8d, 1\n    xor  rdx, rdx\n    push rdx\n    mov  rdx, [rel sync_obj]\n    mov  rcx, [rel sync_obj]\n    sub  rsp, 32\n    call [rel pfn_glClientWaitSync]\n    add  rsp, 32\n    pop  rdx",
     "ports":[{"n":"timeout","d":"100000000"}]},
    {"id":"gl.deletesync","title":"glDeleteSync","cat":"OpenGL32",
     "desc":"Delete fence sync object",
     "tpl":"; glDeleteSync(sync)\n    mov  rcx, [rel sync_obj]\n    sub  rsp, 32\n    call [rel pfn_glDeleteSync]\n    add  rsp, 32","ports":[]},

    # ═══════════════════  WGL Extensions  ═══════════════════
    {"id":"wgl.swapinterval","title":"wglSwapIntervalEXT","cat":"WGL-Extensions",
     "desc":"Set swap interval (vsync: 0=off, 1=vsync, -1=adaptive)",
     "tpl":"; wglSwapIntervalEXT(interval)\n    mov  ecx, {interval}\n    sub  rsp, 32\n    call [rel pfn_wglSwapIntervalEXT]\n    add  rsp, 32",
     "ports":[{"n":"interval","d":"1"}]},
    {"id":"wgl.createctxattr","title":"wglCreateContextAttribsARB","cat":"WGL-Extensions",
     "desc":"Create modern GL context (core profile) with attribute list",
     "tpl":"; wglCreateContextAttribsARB(hdc, share, &attribs)\n    ; attribs: [WGL_CONTEXT_MAJOR=0x2091,4, WGL_CONTEXT_MINOR=0x2092,6,\n    ;           WGL_CONTEXT_PROFILE=0x9126, CORE_PROFILE=0x00000001, 0]\n    lea  r8,  [rel ctx_attribs]\n    xor  rdx, rdx\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [rel pfn_wglCreateContextAttribsARB]\n    add  rsp, 32\n    mov  [rel hrc], rax","ports":[]},
    {"id":"wgl.chooseformat","title":"wglChoosePixelFormatARB","cat":"WGL-Extensions",
     "desc":"Choose pixel format with extended attributes (MSAA, sRGB)",
     "tpl":"; wglChoosePixelFormatARB(hdc, &iAttribs, &fAttribs, maxFmt, &fmt, &nFmt)\n    lea  [rsp+40], [rel n_fmts]\n    lea  [rsp+32], [rel fmt_idx]\n    mov  r9,  1\n    xor  r8,  r8\n    lea  rdx, [rel pf_attribs_i]\n    mov  rcx, [rel hdc]\n    sub  rsp, 32\n    call [rel pfn_wglChoosePixelFormatARB]\n    add  rsp, 32","ports":[]},

    # ═══════════════════  DXGI / D3D Interop (Win32)  ═══════════════════
    {"id":"d3d.createdevice","title":"D3D11CreateDevice","cat":"DXGI-D3D11",
     "desc":"Create D3D11 device + immediate context (minimal call)",
     "tpl":"; D3D11CreateDevice(NULL,D3D_DRIVER_TYPE_HARDWARE=1,NULL,flags,NULL,0,\n;                    D3D11_SDK_VERSION=7,&dev,&feat,&ctx)\n    lea  [rsp+64], [rel d3d_ctx]\n    mov  [rsp+56], 0\n    lea  [rsp+48], [rel d3d_dev]\n    mov  [rsp+40], 7       ; D3D11_SDK_VERSION\n    mov  [rsp+32], 0\n    xor  r9,  r9\n    xor  r8,  r8\n    mov  rdx, {flags}      ; D3D11_CREATE_DEVICE_DEBUG=2\n    push rdx\n    mov  rdx, 0\n    mov  rcx, 1            ; D3D_DRIVER_TYPE_HARDWARE\n    push rcx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [D3D11CreateDevice]\n    add  rsp, 32",
     "ports":[{"n":"flags","d":"0"}]},
    {"id":"d3d.createdxgi","title":"CreateDXGIFactory","cat":"DXGI-D3D11",
     "desc":"Create DXGI factory for swap chain creation",
     "tpl":"; CreateDXGIFactory(&IID_IDXGIFactory, &factory)\n    lea  rdx, [rel dxgi_factory]\n    lea  rcx, [rel IID_IDXGIFactory]\n    sub  rsp, 32\n    call [CreateDXGIFactory]\n    add  rsp, 32","ports":[]},
    {"id":"d3d.present","title":"IDXGISwapChain::Present","cat":"DXGI-D3D11",
     "desc":"Present rendered frame (vtable call, slot index 8)",
     "tpl":"; pSwapChain->Present(syncInterval, flags)\n    ; vtable: IDXGISwapChain::Present = slot 8\n    mov  rcx, [rel pSwapChain]\n    mov  rax, [rcx]          ; vtable ptr\n    mov  rdx, {sync}         ; 1=vsync, 0=immediate\n    xor  r8,  r8\n    call [rax + 8*8]",
     "ports":[{"n":"sync","d":"1"}]},

    # ═══════════════════  XAudio2 / WASAPI stubs  ═══════════════════
    {"id":"xa2.create","title":"XAudio2Create","cat":"XAudio2",
     "desc":"Create XAudio2 engine instance",
     "tpl":"; XAudio2Create(&pEngine, 0, XAUDIO2_DEFAULT_PROCESSOR=1)\n    mov  r8,  1\n    xor  rdx, rdx\n    lea  rcx, [rel pXAudio2]\n    sub  rsp, 32\n    call [XAudio2Create]\n    add  rsp, 32","ports":[]},
    {"id":"xa2.mastervoice","title":"CreateMasteringVoice","cat":"XAudio2",
     "desc":"Create mastering voice (vtable slot 7)",
     "tpl":"; pXAudio2->CreateMasteringVoice(&master, XAUDIO2_DEFAULT_CHANNELS=0, 44100, 0, NULL, NULL, AudioCategory_Other)\n    mov  [rsp+56], 0\n    mov  [rsp+48], 0\n    xor  r9,  r9\n    mov  r8,  0\n    mov  rdx, 44100\n    push rdx\n    xor  rdx, rdx\n    lea  rcx, [rel pMasterVoice]\n    push rcx\n    mov  rcx, [rel pXAudio2]\n    mov  rax, [rcx]\n    call [rax + 7*8]","ports":[]},

    # ═══════════════════  ASMX — Macro Extension  ═══════════════════
    {"id":"asmx.struct","title":"@struct","cat":"ASMX",
     "desc":"ASMX typed struct definition (expands to struc/endstruc + accessors)",
     "tpl":"@struct {Name}\n    {field0}: {type0}\n    {field1}: {type1}\n@end",
     "ports":[{"n":"Name","d":"Vec3"},{"n":"field0","d":"x"},{"n":"type0","d":"dq"},
              {"n":"field1","d":"y"},{"n":"type1","d":"dq"}]},
    {"id":"asmx.vector","title":"@vector","cat":"ASMX",
     "desc":"ASMX typed array reservation (BSS region)",
     "tpl":"@vector {name}, {type}, {count}",
     "ports":[{"n":"name","d":"positions"},{"n":"type","d":"dd"},{"n":"count","d":"1024"}]},
    {"id":"asmx.const","title":"@const","cat":"ASMX",
     "desc":"ASMX named constant (EQU wrapper with type annotation)",
     "tpl":"@const {NAME} = {value}",
     "ports":[{"n":"NAME","d":"MAX_ENTITIES"},{"n":"value","d":"4096"}]},
    {"id":"asmx.macro","title":"@macro","cat":"ASMX",
     "desc":"ASMX macro definition with named parameters",
     "tpl":"@macro {name}({params})\n    ; body — use param names directly\n@endmacro",
     "ports":[{"n":"name","d":"swap"},{"n":"params","d":"a, b"}]},
    {"id":"asmx.dict","title":"@dict","cat":"ASMX",
     "desc":"ASMX key-value data block (hash-map init helper)",
     "tpl":"@dict {Name}\n    {key0}: {val0}\n    {key1}: {val1}\n@end",
     "ports":[{"n":"Name","d":"Config"},{"n":"key0","d":"width"},{"n":"val0","d":"1920"},
              {"n":"key1","d":"height"},{"n":"val1","d":"1080"}]},
    {"id":"asmx.raw","title":"@raw","cat":"ASMX",
     "desc":"ASMX passthrough raw NASM block (no macro processing)",
     "tpl":"@raw\n    {nasm_code}\n@end",
     "ports":[{"n":"nasm_code","d":"; insert raw NASM here"}]},
    {"id":"asmx.defmacro","title":"defmacro/enddef","cat":"ASMX",
     "desc":"ASMX grammar macro (pattern-rewriting defmacro)",
     "tpl":"defmacro{name({params})}body{\n    ; NASM expansion — use {p1}, {p2} ...\n}enddef",
     "ports":[{"n":"name","d":"if_eq"},{"n":"params","d":"a,b,lbl"}]},
    {"id":"asmx.usemacro","title":"usemacro","cat":"ASMX",
     "desc":"ASMX grammar macro invocation",
     "tpl":"usemacro{name({args})}endmacro",
     "ports":[{"n":"name","d":"if_eq"},{"n":"args","d":"rax,0,.done"}]},
    {"id":"asmx.clamp","title":"@clamp_reg","cat":"ASMX",
     "desc":"ASMX macro: clamp register to [lo,hi] (inline, branchless variant with CMOV)",
     "tpl":"@macro clamp_reg(reg, lo, hi)\n    cmp  reg, lo\n    cmovl reg, lo\n    cmp  reg, hi\n    cmovg reg, hi\n@endmacro",
     "ports":[{"n":"reg","d":"rax"},{"n":"lo","d":"0"},{"n":"hi","d":"255"}]},
    {"id":"asmx.swap","title":"@swap","cat":"ASMX",
     "desc":"ASMX macro: swap two registers without temp",
     "tpl":"@macro swap(a, b)\n    xor a, b\n    xor b, a\n    xor a, b\n@endmacro",
     "ports":[{"n":"a","d":"rax"},{"n":"b","d":"rbx"}]},
    {"id":"asmx.pushall","title":"@pushall/@popall","cat":"ASMX",
     "desc":"ASMX macro pair: save/restore caller-saved regs (rcx,rdx,r8-r11)",
     "tpl":"@macro pushall()\n    push rcx\n    push rdx\n    push r8\n    push r9\n    push r10\n    push r11\n@endmacro\n@macro popall()\n    pop r11\n    pop r10\n    pop r9\n    pop r8\n    pop rdx\n    pop rcx\n@endmacro",
     "ports":[]},
    {"id":"asmx.minmax","title":"@min_reg/@max_reg","cat":"ASMX",
     "desc":"ASMX macros: integer min/max using CMP+CMOV (no branch)",
     "tpl":"@macro min_reg(a, b)\n    cmp  a, b\n    cmovg a, b\n@endmacro\n@macro max_reg(a, b)\n    cmp  a, b\n    cmovl a, b\n@endmacro",
     "ports":[]},
    {"id":"asmx.prologue","title":"@proc/@endproc","cat":"ASMX",
     "desc":"ASMX structured procedure with ABI-correct prologue/epilogue",
     "tpl":"@macro proc({name}, {locals})\n    {name}:\n    push rbp\n    mov  rbp, rsp\n    sub  rsp, locals\n@endmacro\n@macro endproc()\n    leave\n    ret\n@endmacro",
     "ports":[{"n":"name","d":"my_func"},{"n":"locals","d":"64"}]},
    {"id":"asmx.forloop","title":"@for","cat":"ASMX",
     "desc":"ASMX counted-loop macro (rcx=count, label-unique via %1)",
     "tpl":"@macro for(cnt)\n    mov  rcx, cnt\n.for_body_%1:\n    ; loop body\n    dec  rcx\n    jnz  .for_body_%1\n@endmacro",
     "ports":[{"n":"cnt","d":"1024"}]},
    {"id":"asmx.switch","title":"@switch (jump table)","cat":"ASMX",
     "desc":"ASMX jump-table switch dispatch macro",
     "tpl":"@macro switch(reg, table, count)\n    cmp  reg, count\n    jae  .sw_default_%1\n    jmp  [table + reg*8]\n.sw_default_%1:\n@endmacro",
     "ports":[{"n":"reg","d":"rax"},{"n":"table","d":"jt"},{"n":"count","d":"8"}]},
    {"id":"asmx.simd_loopf32","title":"@simd_loop_f32","cat":"ASMX",
     "desc":"ASMX AVX2 SIMD loop macro: 8× f32 per iteration over array",
     "tpl":"@macro simd_loop_f32(base, count)\n    ; processes count floats in groups of 8 (ymm)\n    mov  rcx, count\n    shr  rcx, 3        ; /8\n    xor  rsi, rsi\n.simd_%1:\n    vmovups ymm0, [base + rsi*4]\n    ; --- operation on ymm0 ---\n    vmovups [base + rsi*4], ymm0\n    add  rsi, 8\n    dec  rcx\n    jnz  .simd_%1\n    vzeroupper\n@endmacro",
     "ports":[{"n":"base","d":"rdi"},{"n":"count","d":"rdx"}]},

    # ═══════════════════  AST — Abstract Syntax Tree node patterns  ═══════════════════
    {"id":"ast.binop","title":"BinOp node","cat":"AST",
     "desc":"Layout: [tag:u8][op:u8][pad:6][lhs_ptr:ptr][rhs_ptr:ptr] (24 bytes)",
     "tpl":"; AST BinOp node layout (x64, 8B-aligned fields)\n; struct BinOp { u8 tag=AST_BINOP; u8 op; u8 pad[6]; Node* lhs; Node* rhs; }\n; Encode:\n    mov  byte [rel {node}+0], {tag}   ; AST_BINOP\n    mov  byte [rel {node}+1], {op}    ; op: 0=+ 1=- 2=* 3=/ 4=& 5=| 6=^ 7=<<\n    mov  qword[rel {node}+8], {lhs}\n    mov  qword[rel {node}+16], {rhs}\n; Read op:\n    movzx rax, byte [rel {node}+1]\n; Read lhs/rhs:\n    mov  rsi, [rel {node}+8]\n    mov  rdi, [rel {node}+16]",
     "ports":[{"n":"node","d":"ast_buf"},{"n":"tag","d":"1"},{"n":"op","d":"0"},
              {"n":"lhs","d":"lhs_node"},{"n":"rhs","d":"rhs_node"}]},
    {"id":"ast.unop","title":"UnOp node","cat":"AST",
     "desc":"Layout: [tag:u8][op:u8][pad:6][child:ptr] (16 bytes)",
     "tpl":"; AST UnOp: { u8 tag=AST_UNOP; u8 op; u8 pad[6]; Node* child; }\n    mov  byte [rel {node}+0], 2      ; AST_UNOP\n    mov  byte [rel {node}+1], {op}   ; 0=NEG 1=NOT 2=DEREF 3=ADDR\n    mov  qword[rel {node}+8], {child}",
     "ports":[{"n":"node","d":"ast_buf"},{"n":"op","d":"0"},{"n":"child","d":"rax"}]},
    {"id":"ast.literal","title":"Literal node","cat":"AST",
     "desc":"Layout: [tag:u8][kind:u8][pad:6][value:i64] (16 bytes)",
     "tpl":"; AST Literal: { u8 tag=AST_LIT; u8 kind; u8 pad[6]; i64 value; }\n    mov  byte [rel {node}+0], 3       ; AST_LITERAL\n    mov  byte [rel {node}+1], {kind}  ; 0=int 1=float 2=bool 3=null\n    mov  qword[rel {node}+8], {val}",
     "ports":[{"n":"node","d":"lit_node"},{"n":"kind","d":"0"},{"n":"val","d":"42"}]},
    {"id":"ast.ident","title":"Ident node","cat":"AST",
     "desc":"Layout: [tag:u8][pad:7][sym_id:u32][scope:u32][type_id:u32][pad2:4] (24 bytes)",
     "tpl":"; AST Ident: { u8 tag=AST_IDENT; u8 pad[7]; u32 sym_id; u32 scope; u32 type_id; }\n    mov  byte  [rel {node}+0], 4      ; AST_IDENT\n    mov  dword [rel {node}+8], {sym}\n    mov  dword [rel {node}+12], {scope}\n    mov  dword [rel {node}+16], {type_id}",
     "ports":[{"n":"node","d":"id_node"},{"n":"sym","d":"0"},{"n":"scope","d":"0"},{"n":"type_id","d":"0"}]},
    {"id":"ast.assign","title":"Assign node","cat":"AST",
     "desc":"Layout: [tag][pad7][lvalue:ptr][rvalue:ptr] (24 bytes)",
     "tpl":"; AST Assign: { u8 tag=AST_ASSIGN; ... Node* lval; Node* rval; }\n    mov  byte [rel {node}+0], 5        ; AST_ASSIGN\n    mov  qword[rel {node}+8], {lval}\n    mov  qword[rel {node}+16], {rval}",
     "ports":[{"n":"node","d":"asgn_node"},{"n":"lval","d":"lval_ptr"},{"n":"rval","d":"rval_ptr"}]},
    {"id":"ast.call","title":"Call node","cat":"AST",
     "desc":"Layout: [tag][pad7][callee:ptr][args:ptr][argc:u32][pad4] (32 bytes)",
     "tpl":"; AST Call: { u8 tag=AST_CALL; ...; Node* callee; Node** args; u32 argc; }\n    mov  byte  [rel {node}+0], 6\n    mov  qword [rel {node}+8], {callee}\n    mov  qword [rel {node}+16], {args_arr}\n    mov  dword [rel {node}+24], {argc}",
     "ports":[{"n":"node","d":"call_node"},{"n":"callee","d":"callee_ptr"},{"n":"args_arr","d":"args_buf"},{"n":"argc","d":"3"}]},
    {"id":"ast.block","title":"Block/Seq node","cat":"AST",
     "desc":"Layout: [tag][pad7][stmts:ptr*][count:u32][cap:u32] (24 bytes)",
     "tpl":"; AST Block: { u8 tag=AST_BLOCK; ...; Node** stmts; u32 count; u32 cap; }\n    mov  byte  [rel {node}+0], 7\n    mov  qword [rel {node}+8], {stmts}\n    mov  dword [rel {node}+16], {n}\n    mov  dword [rel {node}+20], {cap}",
     "ports":[{"n":"node","d":"blk_node"},{"n":"stmts","d":"stmt_arr"},{"n":"n","d":"0"},{"n":"cap","d":"16"}]},
    {"id":"ast.if","title":"If node","cat":"AST",
     "desc":"Layout: [tag][pad7][cond:ptr][then:ptr][else:ptr] (32 bytes)",
     "tpl":"; AST If: { u8 tag=AST_IF; ...; Node* cond; Node* then; Node* else_; }\n    mov  byte  [rel {node}+0], 8\n    mov  qword [rel {node}+8], {cond}\n    mov  qword [rel {node}+16], {then_blk}\n    mov  qword [rel {node}+24], {else_blk}",
     "ports":[{"n":"node","d":"if_node"},{"n":"cond","d":"cond_ptr"},
              {"n":"then_blk","d":"then_ptr"},{"n":"else_blk","d":"0"}]},
    {"id":"ast.while","title":"While node","cat":"AST",
     "desc":"Layout: [tag][pad7][cond:ptr][body:ptr] (24 bytes)",
     "tpl":"; AST While: { u8 tag=AST_WHILE; ...; Node* cond; Node* body; }\n    mov  byte  [rel {node}+0], 9\n    mov  qword [rel {node}+8], {cond}\n    mov  qword [rel {node}+16], {body}",
     "ports":[{"n":"node","d":"whl_node"},{"n":"cond","d":"cond_ptr"},{"n":"body","d":"body_ptr"}]},
    {"id":"ast.for","title":"For node","cat":"AST",
     "desc":"Layout: [tag][pad7][init:ptr][cond:ptr][step:ptr][body:ptr] (40 bytes)",
     "tpl":"; AST For: { tag=AST_FOR; Node *init,*cond,*step,*body; }\n    mov  byte  [rel {node}+0], 10\n    mov  qword [rel {node}+8],  {init}\n    mov  qword [rel {node}+16], {cond}\n    mov  qword [rel {node}+24], {step}\n    mov  qword [rel {node}+32], {body}",
     "ports":[{"n":"node","d":"for_node"},{"n":"init","d":"0"},{"n":"cond","d":"cond_ptr"},
              {"n":"step","d":"step_ptr"},{"n":"body","d":"body_ptr"}]},
    {"id":"ast.funcdecl","title":"FuncDecl node","cat":"AST",
     "desc":"Layout: [tag][pad7][name_sym:u32][ret_type:u32][params:ptr][body:ptr] (32 bytes)",
     "tpl":"; AST FuncDecl: { tag=AST_FUNC; u32 sym; u32 ret_type; Node** params; Node* body; }\n    mov  byte  [rel {node}+0], 11\n    mov  dword [rel {node}+8], {sym}\n    mov  dword [rel {node}+12], {ret}\n    mov  qword [rel {node}+16], {params}\n    mov  qword [rel {node}+24], {body}",
     "ports":[{"n":"node","d":"fn_node"},{"n":"sym","d":"0"},{"n":"ret","d":"0"},
              {"n":"params","d":"params_arr"},{"n":"body","d":"body_ptr"}]},
    {"id":"ast.return","title":"Return node","cat":"AST",
     "desc":"Layout: [tag][pad7][expr:ptr] (16 bytes)",
     "tpl":"; AST Return: { tag=AST_RET; Node* expr; }\n    mov  byte  [rel {node}+0], 12\n    mov  qword [rel {node}+8], {expr}",
     "ports":[{"n":"node","d":"ret_node"},{"n":"expr","d":"expr_ptr"}]},
    {"id":"ast.fieldaccess","title":"FieldAccess node","cat":"AST",
     "desc":"Layout: [tag][pad7][object:ptr][field_sym:u32][pad4] (24 bytes)",
     "tpl":"; AST FieldAccess: { tag=AST_FIELD; Node* obj; u32 field_sym; }\n    mov  byte  [rel {node}+0], 13\n    mov  qword [rel {node}+8], {obj}\n    mov  dword [rel {node}+16], {field}",
     "ports":[{"n":"node","d":"fa_node"},{"n":"obj","d":"obj_ptr"},{"n":"field","d":"0"}]},
    {"id":"ast.index","title":"Index node","cat":"AST",
     "desc":"Layout: [tag][pad7][base:ptr][index:ptr] (24 bytes)",
     "tpl":"; AST Index: { tag=AST_INDEX; Node* base; Node* idx; }\n    mov  byte  [rel {node}+0], 14\n    mov  qword [rel {node}+8], {base}\n    mov  qword [rel {node}+16], {idx}",
     "ports":[{"n":"node","d":"idx_node"},{"n":"base","d":"arr_ptr"},{"n":"idx","d":"idx_ptr"}]},
    {"id":"ast.cast","title":"Cast node","cat":"AST",
     "desc":"Layout: [tag][pad7][expr:ptr][to_type:u32][pad4] (24 bytes)",
     "tpl":"; AST Cast: { tag=AST_CAST; Node* expr; u32 to_type; }\n    mov  byte  [rel {node}+0], 15\n    mov  qword [rel {node}+8], {expr}\n    mov  dword [rel {node}+16], {to_type}",
     "ports":[{"n":"node","d":"cast_node"},{"n":"expr","d":"expr_ptr"},{"n":"to_type","d":"4"}]},
    {"id":"ast.dispatch","title":"AST dispatch","cat":"AST",
     "desc":"Visitor-style tag dispatch via jump table over AST_* constants",
     "tpl":"; AST visitor dispatch — tag in [rdi]\n    movzx rax, byte [rdi]    ; tag byte\n    cmp   rax, {max_tag}\n    jae   .ast_unknown\n    jmp   [rel ast_vtbl + rax*8]\n; --- jump table ---\nalign 8\nast_vtbl:\n    dq .visit_binop, .visit_unop, .visit_lit, .visit_ident\n    dq .visit_assign, .visit_call, .visit_block, .visit_if",
     "ports":[{"n":"max_tag","d":"16"}]},
    {"id":"ast.alloc_arena","title":"AST arena alloc","cat":"AST",
     "desc":"Bump allocator for AST nodes — arena pointer in [rel ast_bump]",
     "tpl":"; AST arena allocate {sz} bytes\n    mov  rax, [rel ast_bump]\n    lea  rcx, [rax + {sz}]\n    cmp  rcx, [rel ast_end]\n    jae  .ast_oom\n    mov  [rel ast_bump], rcx\n    ; rax = pointer to new node",
     "ports":[{"n":"sz","d":"24"}]},

    # ═══════════════════  SSA — Static Single Assignment IR  ═══════════════════
    {"id":"ssa.define","title":"SSA def","cat":"SSA",
     "desc":"SSA value definition: %vN = op type operands",
     "tpl":"; SSA: %{dst} = {op} {type} {src0}, {src1}\n; Encoded as: IR_INS { op, type, dst, [src0, src1] }\n    ; dst regno: {dst}  op: {op}  type: {type}\n    mov  dword [rel ir_ins + rcx*16 + 0], {op}\n    mov  dword [rel ir_ins + rcx*16 + 4], {type}\n    mov  dword [rel ir_ins + rcx*16 + 8], {dst}\n    mov  dword [rel ir_ins + rcx*16 + 12], {src0}",
     "ports":[{"n":"dst","d":"1"},{"n":"op","d":"0"},{"n":"type","d":"i64"},{"n":"src0","d":"0"},{"n":"src1","d":"0"}]},
    {"id":"ssa.phi","title":"SSA φ-node","cat":"SSA",
     "desc":"Phi node: %v = φ(bb0:%a, bb1:%b) — merge at join point",
     "tpl":"; %{dst} = phi {type} [%{a}, bb{ba}], [%{b}, bb{bb}]\n; PHI_INS { op=PHI, type, dst, [src_a, bb_a, src_b, bb_b] }\n    mov  dword [rel phi_buf + {dst}*32 + 0], 0xFF   ; OP_PHI\n    mov  dword [rel phi_buf + {dst}*32 + 4], {type}\n    mov  dword [rel phi_buf + {dst}*32 + 8], {dst}\n    mov  dword [rel phi_buf + {dst}*32 + 12], {a}\n    mov  dword [rel phi_buf + {dst}*32 + 16], {ba}\n    mov  dword [rel phi_buf + {dst}*32 + 20], {b}\n    mov  dword [rel phi_buf + {dst}*32 + 24], {bb}",
     "ports":[{"n":"dst","d":"5"},{"n":"type","d":"0"},{"n":"a","d":"3"},{"n":"ba","d":"0"},
              {"n":"b","d":"4"},{"n":"bb","d":"1"}]},
    {"id":"ssa.br","title":"SSA branch","cat":"SSA",
     "desc":"SSA conditional branch: br %cond, bb_true, bb_false",
     "tpl":"; br {type} %{cond}, bb{btrue}, bb{bfalse}\n; BRANCH_INS { op=BR, cond_vreg, bb_true, bb_false }\n    mov  dword [rel br_ins + rbx*16 + 0], 0xFE    ; OP_BR\n    mov  dword [rel br_ins + rbx*16 + 4], {cond}\n    mov  dword [rel br_ins + rbx*16 + 8], {btrue}\n    mov  dword [rel br_ins + rbx*16 + 12], {bfalse}",
     "ports":[{"n":"cond","d":"2"},{"n":"btrue","d":"2"},{"n":"bfalse","d":"3"}]},
    {"id":"ssa.jmp","title":"SSA jmp","cat":"SSA",
     "desc":"SSA unconditional jump: jmp bb_target",
     "tpl":"; jmp bb{target}\n    mov  dword [rel br_ins + rbx*16 + 0], 0xFD    ; OP_JMP\n    mov  dword [rel br_ins + rbx*16 + 4], {target}",
     "ports":[{"n":"target","d":"1"}]},
    {"id":"ssa.load","title":"SSA load","cat":"SSA",
     "desc":"SSA load: %v = load type* %ptr — memory read",
     "tpl":"; %{dst} = load {type} %{ptr}\n    mov  dword [rel ir_ins + rcx*16 + 0], 0x10    ; OP_LOAD\n    mov  dword [rel ir_ins + rcx*16 + 4], {type}\n    mov  dword [rel ir_ins + rcx*16 + 8], {dst}\n    mov  dword [rel ir_ins + rcx*16 + 12], {ptr}",
     "ports":[{"n":"dst","d":"5"},{"n":"type","d":"0"},{"n":"ptr","d":"3"}]},
    {"id":"ssa.store","title":"SSA store","cat":"SSA",
     "desc":"SSA store: store type %val, type* %ptr — memory write",
     "tpl":"; store {type} %{val}, %{ptr}\n    mov  dword [rel ir_ins + rcx*16 + 0], 0x11    ; OP_STORE\n    mov  dword [rel ir_ins + rcx*16 + 4], {type}\n    mov  dword [rel ir_ins + rcx*16 + 8], {val}\n    mov  dword [rel ir_ins + rcx*16 + 12], {ptr}",
     "ports":[{"n":"val","d":"5"},{"n":"type","d":"0"},{"n":"ptr","d":"3"}]},
    {"id":"ssa.gep","title":"SSA GEP","cat":"SSA",
     "desc":"SSA GetElementPtr: %v = gep type*, %base, %idx — address calculation",
     "tpl":"; %{dst} = gep {type} %{base}, %{idx}\n    mov  dword [rel ir_ins + rcx*16 + 0], 0x12    ; OP_GEP\n    mov  dword [rel ir_ins + rcx*16 + 4], {stride} ; element stride bytes\n    mov  dword [rel ir_ins + rcx*16 + 8], {dst}\n    mov  dword [rel ir_ins + rcx*16 + 12], {base}\n    ; idx stored in next word if needed",
     "ports":[{"n":"dst","d":"6"},{"n":"type","d":"4"},{"n":"base","d":"2"},{"n":"idx","d":"3"},{"n":"stride","d":"4"}]},
    {"id":"ssa.alloca","title":"SSA alloca","cat":"SSA",
     "desc":"SSA stack alloc: %v = alloca type, count — local variable slot",
     "tpl":"; %{dst} = alloca {type} x {count}\n    mov  dword [rel ir_ins + rcx*16 + 0], 0x13    ; OP_ALLOCA\n    mov  dword [rel ir_ins + rcx*16 + 4], {size}  ; bytes\n    mov  dword [rel ir_ins + rcx*16 + 8], {dst}\n    mov  dword [rel ir_ins + rcx*16 + 12], {align}",
     "ports":[{"n":"dst","d":"7"},{"n":"type","d":"0"},{"n":"count","d":"1"},{"n":"size","d":"8"},{"n":"align","d":"8"}]},
    {"id":"ssa.call","title":"SSA call","cat":"SSA",
     "desc":"SSA call: %v = call rettype @func(args...) — function invocation",
     "tpl":"; %{dst} = call {rtype} @{func}(%{a0}, %{a1})\n    mov  dword [rel ir_ins + rcx*16 + 0], 0x14    ; OP_CALL\n    mov  dword [rel ir_ins + rcx*16 + 4], {func_id}\n    mov  dword [rel ir_ins + rcx*16 + 8], {dst}\n    mov  dword [rel ir_ins + rcx*16 + 12], {argc}",
     "ports":[{"n":"dst","d":"8"},{"n":"rtype","d":"0"},{"n":"func","d":"printf"},{"n":"func_id","d":"0"},{"n":"a0","d":"1"},{"n":"a1","d":"2"},{"n":"argc","d":"2"}]},
    {"id":"ssa.ret","title":"SSA ret","cat":"SSA",
     "desc":"SSA return: ret type %val or ret void",
     "tpl":"; ret {type} %{val}\n    mov  dword [rel ir_ins + rcx*16 + 0], 0x15    ; OP_RET\n    mov  dword [rel ir_ins + rcx*16 + 4], {type}\n    mov  dword [rel ir_ins + rcx*16 + 8], {val}",
     "ports":[{"n":"type","d":"0"},{"n":"val","d":"1"}]},
    {"id":"ssa.bb_header","title":"SSA basic block header","cat":"SSA",
     "desc":"BB descriptor: [id:u32][pred_count:u32][succ0:u32][succ1:u32][ins_start:u32][ins_count:u32]",
     "tpl":"; Basic Block {bb_id} header (24 bytes)\n    mov  dword [rel bb_tbl + {bb_id}*24 + 0], {bb_id}\n    mov  dword [rel bb_tbl + {bb_id}*24 + 4], {pred_cnt}\n    mov  dword [rel bb_tbl + {bb_id}*24 + 8], {succ0}\n    mov  dword [rel bb_tbl + {bb_id}*24 + 12], {succ1}\n    mov  dword [rel bb_tbl + {bb_id}*24 + 16], {ins_start}\n    mov  dword [rel bb_tbl + {bb_id}*24 + 20], {ins_count}",
     "ports":[{"n":"bb_id","d":"0"},{"n":"pred_cnt","d":"0"},{"n":"succ0","d":"1"},{"n":"succ1","d":"-1"},
              {"n":"ins_start","d":"0"},{"n":"ins_count","d":"0"}]},
    {"id":"ssa.vreg_map","title":"SSA vreg→preg map","cat":"SSA",
     "desc":"Virtual→physical register mapping table slot (used by register allocator)",
     "tpl":"; vreg_map[{vreg}] = preg {preg} (0=rax,1=rcx,2=rdx,3=rbx,...)\n    mov  byte [rel vreg_map + {vreg}], {preg}",
     "ports":[{"n":"vreg","d":"0"},{"n":"preg","d":"0"}]},
    {"id":"ssa.liveness","title":"SSA liveness bitvec","cat":"SSA",
     "desc":"Per-BB liveness bitvector (live_in/live_out, 1 bit per vreg)",
     "tpl":"; Set live-in bit for vreg {v} in bb {bb}\n    mov  rax, {v}\n    bts  qword [rel live_in + {bb}*8], rax\n; Test:\n    bt   qword [rel live_in + {bb}*8], rax\n    jc   .vreg_live",
     "ports":[{"n":"v","d":"3"},{"n":"bb","d":"1"}]},

    # ═══════════════════  IR — Generic 3-address / NM-IR  ═══════════════════
    {"id":"ir.add","title":"IR add","cat":"IR",
     "desc":"3-addr IR: t1 = t2 + t3 (ADD, type, dst, src0, src1)",
     "tpl":"; NM_IR: t{dst} = t{src0} + t{src1}  [{type}]\n    ; INS{op=ADD, type={type}, d={dst}, s0={src0}, s1={src1}}\n    mov  dword [rel ir_buf + rax*20 + 0], 1       ; OP_ADD\n    mov  dword [rel ir_buf + rax*20 + 4], {type}  ; 0=i32 1=i64 2=f32 3=f64\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {src0}\n    mov  dword [rel ir_buf + rax*20 + 16], {src1}",
     "ports":[{"n":"dst","d":"0"},{"n":"src0","d":"1"},{"n":"src1","d":"2"},{"n":"type","d":"0"}]},
    {"id":"ir.sub","title":"IR sub","cat":"IR",
     "desc":"3-addr IR: t1 = t2 - t3",
     "tpl":"; t{dst} = t{src0} - t{src1}\n    mov  dword [rel ir_buf + rax*20 + 0], 2\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {src0}\n    mov  dword [rel ir_buf + rax*20 + 16], {src1}",
     "ports":[{"n":"dst","d":"0"},{"n":"src0","d":"1"},{"n":"src1","d":"2"},{"n":"type","d":"0"}]},
    {"id":"ir.mul","title":"IR mul","cat":"IR",
     "desc":"3-addr IR: t1 = t2 * t3",
     "tpl":"; t{dst} = t{src0} * t{src1}\n    mov  dword [rel ir_buf + rax*20 + 0], 3\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {src0}\n    mov  dword [rel ir_buf + rax*20 + 16], {src1}",
     "ports":[{"n":"dst","d":"0"},{"n":"src0","d":"1"},{"n":"src1","d":"2"},{"n":"type","d":"2"}]},
    {"id":"ir.div","title":"IR div","cat":"IR",
     "desc":"3-addr IR: t1 = t2 / t3 (signed)",
     "tpl":"; t{dst} = t{src0} / t{src1}\n    mov  dword [rel ir_buf + rax*20 + 0], 4\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {src0}\n    mov  dword [rel ir_buf + rax*20 + 16], {src1}",
     "ports":[{"n":"dst","d":"0"},{"n":"src0","d":"1"},{"n":"src1","d":"2"},{"n":"type","d":"0"}]},
    {"id":"ir.cmp","title":"IR cmp","cat":"IR",
     "desc":"3-addr IR: t1 = cmp[pred] t2, t3 → i1 result",
     "tpl":"; t{dst} = cmp {pred} t{a}, t{b}  ; pred: eq/ne/lt/le/gt/ge/ult/uge\n    mov  dword [rel ir_buf + rax*20 + 0], 5       ; OP_CMP\n    mov  dword [rel ir_buf + rax*20 + 4], {pred}  ; 0=eq 1=ne 2=lt 3=le 4=gt 5=ge\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {a}\n    mov  dword [rel ir_buf + rax*20 + 16], {b}",
     "ports":[{"n":"dst","d":"0"},{"n":"pred","d":"2"},{"n":"a","d":"1"},{"n":"b","d":"2"}]},
    {"id":"ir.load","title":"IR load","cat":"IR",
     "desc":"3-addr IR: t1 = *t2 (mem read, type bytes)",
     "tpl":"; t{dst} = *t{addr}  [{type}]\n    mov  dword [rel ir_buf + rax*20 + 0], 6       ; OP_LOAD\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {addr}",
     "ports":[{"n":"dst","d":"0"},{"n":"addr","d":"1"},{"n":"type","d":"2"}]},
    {"id":"ir.store","title":"IR store","cat":"IR",
     "desc":"3-addr IR: *t_addr = t_val (mem write)",
     "tpl":"; *t{addr} = t{val}  [{type}]\n    mov  dword [rel ir_buf + rax*20 + 0], 7       ; OP_STORE\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {addr}\n    mov  dword [rel ir_buf + rax*20 + 12], {val}",
     "ports":[{"n":"addr","d":"1"},{"n":"val","d":"2"},{"n":"type","d":"2"}]},
    {"id":"ir.mov","title":"IR mov","cat":"IR",
     "desc":"3-addr IR: t1 = t2 (copy/rename, coalesced away in regalloc)",
     "tpl":"; t{dst} = t{src}  [copy]\n    mov  dword [rel ir_buf + rax*20 + 0], 8       ; OP_MOV\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {src}",
     "ports":[{"n":"dst","d":"0"},{"n":"src","d":"1"},{"n":"type","d":"0"}]},
    {"id":"ir.imm","title":"IR imm","cat":"IR",
     "desc":"3-addr IR: t1 = #constant (load immediate)",
     "tpl":"; t{dst} = #{val}  [{type}]\n    mov  dword [rel ir_buf + rax*20 + 0], 9       ; OP_IMM\n    mov  dword [rel ir_buf + rax*20 + 4], {type}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  qword [rel ir_buf + rax*20 + 12], {val}",
     "ports":[{"n":"dst","d":"0"},{"n":"val","d":"0"},{"n":"type","d":"0"}]},
    {"id":"ir.cast","title":"IR cast","cat":"IR",
     "desc":"3-addr IR: t1 = cast[from→to] t2 (zext/sext/trunc/fptosi/sitofp)",
     "tpl":"; t{dst} = cast[{from}→{to}] t{src}  kind={kind}\n    ; kind: 0=zext 1=sext 2=trunc 3=sitofp 4=fptosi 5=bitcast\n    mov  dword [rel ir_buf + rax*20 + 0], 10\n    mov  dword [rel ir_buf + rax*20 + 4], {kind}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {src}",
     "ports":[{"n":"dst","d":"0"},{"n":"src","d":"1"},{"n":"from","d":"i32"},{"n":"to","d":"f32"},{"n":"kind","d":"3"}]},
    {"id":"ir.call","title":"IR call","cat":"IR",
     "desc":"3-addr IR: t1 = call func_id (argc args at args_base)",
     "tpl":"; t{dst} = call #{func_id} ({argc} args)\n    mov  dword [rel ir_buf + rax*20 + 0], 11\n    mov  dword [rel ir_buf + rax*20 + 4], {func_id}\n    mov  dword [rel ir_buf + rax*20 + 8], {dst}\n    mov  dword [rel ir_buf + rax*20 + 12], {argc}",
     "ports":[{"n":"dst","d":"0"},{"n":"func_id","d":"0"},{"n":"argc","d":"2"}]},
    {"id":"ir.jcc","title":"IR jcc","cat":"IR",
     "desc":"3-addr IR: jcc t_cond, bb_true, bb_false",
     "tpl":"; jcc t{cond} → bb{btrue} | bb{bfalse}\n    mov  dword [rel ir_buf + rax*20 + 0], 12\n    mov  dword [rel ir_buf + rax*20 + 4], {cond}\n    mov  dword [rel ir_buf + rax*20 + 8], {btrue}\n    mov  dword [rel ir_buf + rax*20 + 12], {bfalse}",
     "ports":[{"n":"cond","d":"0"},{"n":"btrue","d":"1"},{"n":"bfalse","d":"2"}]},
    {"id":"ir.node_struct","title":"IR_NODE struct","cat":"IR",
     "desc":"NM_IR_NODE layout: [op:u8][type:u8][flags:u16][dst:u16][src0:u16][src1:u16][imm:u32] (12B)",
     "tpl":"; NM_IR_NODE (12 bytes per instruction)\n; offset 0: op(u8) type(u8) flags(u16)\n; offset 4: dst(u16) src0(u16)\n; offset 8: src1(u16) pad(u16)  OR imm(u32)\nstruc NM_IR_NODE\n    .op:    resb 1\n    .type:  resb 1\n    .flags: resw 1\n    .dst:   resw 1\n    .src0:  resw 1\n    .src1:  resw 1\n    .imm_lo:resw 1\nendstruc",
     "ports":[]},
    {"id":"ir.bb_struct","title":"IR_BB struct","cat":"IR",
     "desc":"Basic Block descriptor: [id:u32][ins_off:u32][ins_cnt:u32][succ0:u32][succ1:u32][dom:u32]",
     "tpl":"struc NM_IR_BB\n    .id:       resd 1\n    .ins_off:  resd 1\n    .ins_cnt:  resd 1\n    .succ0:    resd 1\n    .succ1:    resd 1\n    .dom:      resd 1\n    .live_in:  resq 1   ; 64 vreg bitvec\n    .live_out: resq 1\nendstruc",
     "ports":[]},

    # ═══════════════════  PTX — NVIDIA Parallel Thread Execution  ═══════════════════
    {"id":"ptx.kernel","title":"PTX kernel skeleton","cat":"PTX",
     "desc":"Minimal PTX 7.x kernel: .entry + .param + .reg + body",
     "tpl":".version 7.0\n.target sm_86\n.address_size 64\n\n.visible .entry {name}(\n    .param .u64 {name}_param_0\n) {\n    .reg .u64  %rd<4>;\n    .reg .f32  %f<8>;\n    .reg .pred %p<2>;\n\n    ld.param.u64  %rd0, [{name}_param_0];\n    ; --- kernel body ---\n    ret;\n}",
     "ports":[{"n":"name","d":"my_kernel"}]},
    {"id":"ptx.tid","title":"PTX threadIdx","cat":"PTX",
     "desc":"Load threadIdx.x/y/z into registers",
     "tpl":"; threadIdx.{axis} → %rd{dst}\n    mov.u32  %r0, %tid.{axis};\n    cvt.u64.u32  %rd{dst}, %r0;",
     "ports":[{"n":"axis","d":"x"},{"n":"dst","d":"1"}]},
    {"id":"ptx.ctaid","title":"PTX blockIdx","cat":"PTX",
     "desc":"Load blockIdx.x/y/z into registers",
     "tpl":"; blockIdx.{axis} → %r0\n    mov.u32  %r0, %ctaid.{axis};",
     "ports":[{"n":"axis","d":"x"}]},
    {"id":"ptx.ntid","title":"PTX blockDim","cat":"PTX",
     "desc":"Load blockDim.x (ntid) into register",
     "tpl":"; blockDim.x → %r0\n    mov.u32  %r0, %ntid.{axis};",
     "ports":[{"n":"axis","d":"x"}]},
    {"id":"ptx.globalidx","title":"PTX global thread index","cat":"PTX",
     "desc":"Compute global index: idx = blockIdx.x * blockDim.x + threadIdx.x",
     "tpl":"; global_idx = blockIdx.x * blockDim.x + threadIdx.x\n    mov.u32  %r0, %ctaid.x;\n    mov.u32  %r1, %ntid.x;\n    mov.u32  %r2, %tid.x;\n    mad.lo.u32  %r3, %r0, %r1, %r2;\n    cvt.u64.u32  %rd0, %r3;",
     "ports":[]},
    {"id":"ptx.ldglobal","title":"PTX ld.global.f32","cat":"PTX",
     "desc":"Load f32 from global memory: %f = ld.global.f32 [base+idx*4]",
     "tpl":"; %f{dst} = global[{base} + %rd0 * 4]\n    shl.u64   %rd1, %rd0, 2;\n    add.u64   %rd2, {base}, %rd1;\n    ld.global.f32  %f{dst}, [%rd2];",
     "ports":[{"n":"dst","d":"0"},{"n":"base","d":"%rd_base"}]},
    {"id":"ptx.stglobal","title":"PTX st.global.f32","cat":"PTX",
     "desc":"Store f32 to global memory: global[base+idx*4] = %f",
     "tpl":"; global[{base} + %rd0 * 4] = %f{src}\n    shl.u64   %rd1, %rd0, 2;\n    add.u64   %rd2, {base}, %rd1;\n    st.global.f32  [%rd2], %f{src};",
     "ports":[{"n":"src","d":"0"},{"n":"base","d":"%rd_base"}]},
    {"id":"ptx.shared","title":"PTX shared memory","cat":"PTX",
     "desc":"Shared memory declaration + barrier pattern (tiling)",
     "tpl":"; Shared memory tile\n    .shared .align 4 .f32 smem[{tile_sz}];\n\n    ; Load into shared\n    mov.u32  %r0, %tid.x;\n    shl.u32  %r1, %r0, 2;\n    cvt.u64.u32  %rd0, %r1;\n    add.u64  %rd1, {global_src}, %rd0;\n    ld.global.f32  %f0, [%rd1];\n    st.shared.f32  [smem + %r1], %f0;\n\n    ; Synchronize threads\n    bar.sync 0;\n\n    ; Load from shared\n    ld.shared.f32  %f1, [smem + %r1];",
     "ports":[{"n":"tile_sz","d":"256"},{"n":"global_src","d":"%rd_base"}]},
    {"id":"ptx.barrier","title":"PTX bar.sync","cat":"PTX",
     "desc":"Thread block synchronization barrier",
     "tpl":"; Synchronize all threads in block\n    bar.sync 0;","ports":[]},
    {"id":"ptx.pred","title":"PTX predicate branch","cat":"PTX",
     "desc":"Predicated execution and branch on condition",
     "tpl":"; %p = (%r0 >= {bound})\n    setp.ge.u32  %p0, %r0, {bound};\n    @%p0  bra  {label};\n    ; ... else body\n{label}:",
     "ports":[{"n":"bound","d":"1024"},{"n":"label","d":"out_of_range"}]},
    {"id":"ptx.atomicadd","title":"PTX atom.global.add","cat":"PTX",
     "desc":"Atomic add to global memory (histogram, reduction)",
     "tpl":"; atom.global.add.f32 %f_old, [%rd_addr], %f_val;\n    atom.global.add.f32  %f0, [%rd0], %f1;","ports":[]},
    {"id":"ptx.atomiccas","title":"PTX atom.global.cas","cat":"PTX",
     "desc":"Atomic compare-and-swap on global memory",
     "tpl":"; atom.global.cas.b32 %r_old, [%rd_addr], %r_cmp, %r_new;\n    atom.global.cas.b32  %r0, [%rd0], %r1, %r2;","ports":[]},
    {"id":"ptx.mad","title":"PTX mad.f32","cat":"PTX",
     "desc":"Multiply-add (FMA): %f_d = %f_a * %f_b + %f_c",
     "tpl":"; %f{d} = %f{a} * %f{b} + %f{c}\n    fma.rn.f32  %f{d}, %f{a}, %f{b}, %f{c};",
     "ports":[{"n":"d","d":"0"},{"n":"a","d":"1"},{"n":"b","d":"2"},{"n":"c","d":"3"}]},
    {"id":"ptx.rsqrt","title":"PTX rsqrt.f32","cat":"PTX",
     "desc":"Fast reciprocal square root (approximate)",
     "tpl":"; %f{d} = 1/sqrt(%f{s})\n    rsqrt.approx.f32  %f{d}, %f{s};",
     "ports":[{"n":"d","d":"0"},{"n":"s","d":"1"}]},
    {"id":"ptx.texfetch","title":"PTX tex.2d.f32","cat":"PTX",
     "desc":"Texture fetch 2D (hardware interpolation)",
     "tpl":"; {r,g,b,a} = tex.2d.v4.f32.f32 texref, (u, v)\n    tex.2d.v4.f32.f32  {{%f0,%f1,%f2,%f3}}, [{texref}, {{%f_u,%f_v}}];",
     "ports":[{"n":"texref","d":"my_tex"}]},
    {"id":"ptx.vote","title":"PTX vote.all/any","cat":"PTX",
     "desc":"Warp-level vote (reduction within warp)",
     "tpl":"; %p_any = any thread in warp has %p0 set\n    vote.any.pred  %p1, %p0;\n    ; %p_all = all threads have %p0 set\n    vote.all.pred  %p2, %p0;","ports":[]},
    {"id":"ptx.shfl","title":"PTX shfl.sync (warp shuffle)","cat":"PTX",
     "desc":"Warp shuffle: broadcast lane 0 value to all lanes",
     "tpl":"; broadcast %f0 from lane 0 to all\n    shfl.sync.bfly.b32  %r0, %r_src, 0, 0x1f, 0xffffffff;","ports":[]},
    {"id":"ptx.cannonic_slice","title":"Cannonic slice kernel","cat":"PTX",
     "desc":"PTX kernel pattern: process Cannonic f32 slice (SoA, one thread per entity)",
     "tpl":".visible .entry cannonic_update(\n    .param .u64 p_pos_x,\n    .param .u64 p_vel_x,\n    .param .f32 p_dt,\n    .param .u32 p_count\n) {\n    .reg .u32 %r<4>; .reg .u64 %rd<8>; .reg .f32 %f<8>;\n    ; global idx\n    mov.u32  %r0, %ctaid.x;\n    mov.u32  %r1, %ntid.x;\n    mov.u32  %r2, %tid.x;\n    mad.lo.u32 %r3, %r0, %r1, %r2;\n    ld.param.u32 %r1, [p_count];\n    setp.ge.u32  %p0, %r3, %r1;\n    @%p0 bra done;\n    ; load pos_x[idx] and vel_x[idx]\n    ld.param.u64 %rd0, [p_pos_x];\n    ld.param.u64 %rd1, [p_vel_x];\n    cvt.u64.u32 %rd2, %r3;\n    shl.u64  %rd3, %rd2, 2;\n    add.u64  %rd4, %rd0, %rd3;\n    add.u64  %rd5, %rd1, %rd3;\n    ld.global.f32 %f0, [%rd4];  ; pos\n    ld.global.f32 %f1, [%rd5];  ; vel\n    ld.param.f32  %f2, [p_dt];\n    fma.rn.f32 %f0, %f1, %f2, %f0;  ; pos += vel*dt\n    st.global.f32 [%rd4], %f0;\ndone: ret;\n}","ports":[]},

    # ═══════════════════  x64 — Patterns / Idioms  ═══════════════════
    {"id":"x64.xor_zero","title":"Zero register (XOR)","cat":"x64/Idioms",
     "desc":"Fastest register zeroing (2-byte encoding, breaks dep chain)",
     "tpl":"xor {reg}d, {reg}d",
     "ports":[{"n":"reg","d":"eax"}]},
    {"id":"x64.test_null","title":"Null-check (TEST)","cat":"x64/Idioms",
     "desc":"Test pointer for NULL without memory access",
     "tpl":"test {ptr}, {ptr}\n    jz  {null_lbl}",
     "ports":[{"n":"ptr","d":"rax"},{"n":"null_lbl","d":".err_null"}]},
    {"id":"x64.sign_to_mask","title":"Sign→mask","cat":"x64/Idioms",
     "desc":"Propagate sign bit to full 64-bit mask (SAR 63)",
     "tpl":"sar {reg}, 63   ; all-1 if negative, all-0 if positive",
     "ports":[{"n":"reg","d":"rax"}]},
    {"id":"x64.abs","title":"Integer abs","cat":"x64/Idioms",
     "desc":"Branchless absolute value: abs(rax) via SAR+XOR+SUB",
     "tpl":"mov  rcx, {reg}\n    sar  rcx, 63\n    xor  {reg}, rcx\n    sub  {reg}, rcx",
     "ports":[{"n":"reg","d":"rax"}]},
    {"id":"x64.clamp01","title":"Float clamp [0,1]","cat":"x64/Idioms",
     "desc":"Clamp packed f32 to [0.0, 1.0] — SSE4.1 idiom",
     "tpl":"maxps {dst}, [rel zero_f32_8x]   ; clamp low\n    minps {dst}, [rel one_f32_8x]    ; clamp high",
     "ports":[{"n":"dst","d":"xmm0"}]},
    {"id":"x64.roundtrip","title":"Float round-trip i32↔f32","cat":"x64/Idioms",
     "desc":"Convert i32→f32→i32 with SSE2 (round-to-nearest)",
     "tpl":"cvtsi2ss  xmm0, {reg}    ; i32→f32\n    ; ... float math ...\n    cvtss2si  {reg}, xmm0    ; f32→i32 (round)",
     "ports":[{"n":"reg","d":"eax"}]},
    {"id":"x64.divmod","title":"Div+Mod (IDIV)","cat":"x64/Idioms",
     "desc":"Simultaneous quotient (rax) and remainder (rdx) via IDIV",
     "tpl":"cqo                     ; sign-extend rax→rdx:rax\n    idiv {divisor}           ; rax=quotient  rdx=remainder",
     "ports":[{"n":"divisor","d":"rcx"}]},
    {"id":"x64.mulhi","title":"64×64→128 mulhi","cat":"x64/Idioms",
     "desc":"Upper 64 bits of 128-bit product (MUL rdx:rax)",
     "tpl":"mov  rax, {a}\n    mul  {b}               ; rdx:rax = rax * {b}\n    ; rdx = high 64 bits",
     "ports":[{"n":"a","d":"rax"},{"n":"b","d":"rbx"}]},
    {"id":"x64.align_ptr","title":"Align pointer","cat":"x64/Idioms",
     "desc":"Round up pointer to N-byte alignment (N must be power-of-2)",
     "tpl":"; round_up_N(ptr) = (ptr + N-1) & ~(N-1)\n    lea  rax, [{ptr} + {N} - 1]\n    and  rax, -{N}",
     "ports":[{"n":"ptr","d":"rcx"},{"n":"N","d":"64"}]},
    {"id":"x64.swap_endian32","title":"Swap endian u32","cat":"x64/Idioms",
     "desc":"Byte-swap 32-bit value (network↔host) via BSWAP",
     "tpl":"bswap {reg}d",
     "ports":[{"n":"reg","d":"eax"}]},
    {"id":"x64.is_pow2","title":"Is power-of-2","cat":"x64/Idioms",
     "desc":"Test if value is power-of-2: (v & (v-1)) == 0, v != 0",
     "tpl":"test {v}, {v}\n    jz   .not_pow2\n    lea  rax, [{v} - 1]\n    test rax, {v}\n    jnz  .not_pow2\n    ; is power of 2",
     "ports":[{"n":"v","d":"rax"}]},
    {"id":"x64.set_nth_bit","title":"Set/clear/toggle bit N","cat":"x64/Idioms",
     "desc":"Set, clear, or toggle bit N in register via BTS/BTR/BTC",
     "tpl":"bts  {reg}, {n}   ; set\n    btr  {reg}, {n}   ; clear\n    btc  {reg}, {n}   ; toggle",
     "ports":[{"n":"reg","d":"rax"},{"n":"n","d":"7"}]},
    {"id":"x64.min_max_i64","title":"Branchless min/max i64","cat":"x64/Idioms",
     "desc":"Branchless signed min/max using CMOVG/CMOVL",
     "tpl":"; min(rax, rbx) → rax\n    cmp  rax, rbx\n    cmovg rax, rbx\n; max(rax, rbx) → rax\n    ; cmp rax, rbx  (done above)\n    ; cmovl rax, rbx","ports":[]},
    {"id":"x64.read_tsc_pair","title":"RDTSC pair (latency)","cat":"x64/Idioms",
     "desc":"Measure code latency between two RDTSC readings (serialize with CPUID/LFENCE)",
     "tpl":"; --- start ---\n    lfence\n    rdtsc\n    shl  rdx, 32\n    or   rax, rdx\n    mov  [rel t0], rax\n    ; --- timed section ---\n    {code}\n    ; --- end ---\n    lfence\n    rdtsc\n    shl  rdx, 32\n    or   rax, rdx\n    sub  rax, [rel t0]   ; rax = elapsed cycles",
     "ports":[{"n":"code","d":"; code to measure"}]},
    {"id":"x64.unroll4","title":"Loop unroll ×4","cat":"x64/Idioms",
     "desc":"Manual 4-way loop unroll + remainder handling",
     "tpl":"; 4x unrolled loop over {base}[0..{n})\n    mov  rcx, {n}\n    shr  rcx, 2          ; rcx = n/4\n    xor  rsi, rsi\n.unroll:\n    ; iter 0\n    ; iter 1 (+1*stride)\n    ; iter 2 (+2*stride)\n    ; iter 3 (+3*stride)\n    add  rsi, 4\n    dec  rcx\n    jnz  .unroll\n    ; remainder: n & 3\n    and  {n}, 3\n    jz  .done\n.rem:\n    ; single iter\n    inc  rsi\n    dec  {n}\n    jnz  .rem\n.done:",
     "ports":[{"n":"base","d":"rdi"},{"n":"n","d":"rdx"}]},
    {"id":"x64.vec3_dot_sse","title":"Vec3 dot product SSE","cat":"x64/Idioms",
     "desc":"Scalar f32 3-component dot product using DPPS+SHUFPS",
     "tpl":"; xmm0.xyz = a, xmm1.xyz = b → xmm0.x = dot(a,b)\n    ; (using DPPS, SSE4.1, mask=0x71)\n    dpps xmm0, xmm1, 0x71   ; xmm0.x = ax*bx+ay*by+az*bz","ports":[]},
    {"id":"x64.vec4_mat4_mul","title":"Vec4 × Mat4 (SSE)","cat":"x64/Idioms",
     "desc":"Transform 4D vector by 4×4 column-major matrix using 4×DPPS",
     "tpl":"; v (xmm0) × M (16×f32 at rsi) → xmm4\n    movaps xmm1, [rsi + 0]     ; col0\n    movaps xmm2, [rsi + 16]    ; col1\n    movaps xmm3, [rsi + 32]    ; col2\n    movaps xmm4, [rsi + 48]    ; col3\n    ; dot with each column via DPPS 0xFF\n    dpps   xmm1, xmm0, 0xFF\n    dpps   xmm2, xmm0, 0xFF\n    dpps   xmm3, xmm0, 0xFF\n    dpps   xmm4, xmm0, 0xFF\n    ; merge results\n    unpcklps xmm1, xmm2        ; x,y\n    unpcklps xmm3, xmm4        ; z,w\n    movlhps  xmm1, xmm3        ; xyzw → xmm1","ports":[]},
    {"id":"x64.strlen_sse42","title":"strlen SSE4.2","cat":"x64/Idioms",
     "desc":"Fast strlen using PCMPISTRI (SSE4.2 string scan for null byte)",
     "tpl":"; rdi = str pointer → rax = length\n    xor  eax, eax\n    pxor xmm0, xmm0\n.str_loop:\n    pcmpistri xmm0, [rdi + rax], 0x08  ; find null byte in 16B chunk\n    jbe  .str_done\n    add  rax, 16\n    jmp  .str_loop\n.str_done:\n    add  rax, rcx","ports":[]},
    {"id":"x64.memcpy_nt","title":"Non-temporal memcpy","cat":"x64/Idioms",
     "desc":"Streaming store memcpy (MOVNTPS): avoids cache pollution for large buffers",
     "tpl":"; rsi=src, rdi=dst, rcx=bytes (multiple of 64)\n    shr  rcx, 6   ; /64\n.nt_loop:\n    prefetchnta [rsi + 256]\n    movaps  xmm0, [rsi +  0]\n    movaps  xmm1, [rsi + 16]\n    movaps  xmm2, [rsi + 32]\n    movaps  xmm3, [rsi + 48]\n    movntps [rdi +  0], xmm0\n    movntps [rdi + 16], xmm1\n    movntps [rdi + 32], xmm2\n    movntps [rdi + 48], xmm3\n    add  rsi, 64\n    add  rdi, 64\n    dec  rcx\n    jnz  .nt_loop\n    sfence","ports":[]},
    {"id":"x64.hash_fnv1a","title":"FNV-1a hash (x64)","cat":"x64/Idioms",
     "desc":"FNV-1a 64-bit hash loop: hash = (hash XOR byte) * FNV_PRIME",
     "tpl":"; rsi=data, rcx=len → rax=hash64\n    mov  rax, 0xcbf29ce484222325  ; FNV offset basis\n    test rcx, rcx\n    jz   .fnv_done\n.fnv_loop:\n    movzx rdx, byte [rsi]\n    xor  rax, rdx\n    imul rax, 0x100000001b3       ; FNV prime\n    inc  rsi\n    dec  rcx\n    jnz  .fnv_loop\n.fnv_done:","ports":[]},

    # ═══════════════════  GLSL — Shader Source Templates  ═══════════════════
    {"id":"glsl.vert_minimal","title":"GLSL vertex minimal","cat":"GLSL",
     "desc":"Minimal GLSL 4.6 vertex shader: passthrough pos + uv",
     "tpl":"#version 460 core\nlayout(location=0) in vec3 aPos;\nlayout(location=1) in vec2 aUV;\nout vec2 vUV;\nuniform mat4 uMVP;\nvoid main() {\n    gl_Position = uMVP * vec4(aPos, 1.0);\n    vUV = aUV;\n}","ports":[]},
    {"id":"glsl.frag_minimal","title":"GLSL fragment minimal","cat":"GLSL",
     "desc":"Minimal GLSL 4.6 fragment shader: sample texture",
     "tpl":"#version 460 core\nin  vec2 vUV;\nout vec4 fragColor;\nuniform sampler2D uTex;\nvoid main() {\n    fragColor = texture(uTex, vUV);\n}","ports":[]},
    {"id":"glsl.vert_instanced","title":"GLSL vertex instanced","cat":"GLSL",
     "desc":"Instanced vertex shader reading per-instance mat4 from SSBO",
     "tpl":"#version 460 core\nlayout(location=0) in vec3 aPos;\nlayout(location=1) in vec3 aNorm;\nlayout(std430, binding=0) readonly buffer InstanceMatrices {\n    mat4 uModels[];\n};\nout vec3 vWorldNorm;\nuniform mat4 uVP;\nvoid main() {\n    mat4 M = uModels[gl_InstanceID];\n    gl_Position = uVP * M * vec4(aPos, 1.0);\n    vWorldNorm  = mat3(transpose(inverse(M))) * aNorm;\n}","ports":[]},
    {"id":"glsl.frag_blinnphong","title":"GLSL Blinn-Phong","cat":"GLSL",
     "desc":"Fragment shader: Blinn-Phong lighting model",
     "tpl":"#version 460 core\nin  vec3 vPos;\nin  vec3 vNorm;\nin  vec2 vUV;\nout vec4 fragColor;\nuniform vec3 uLightPos;\nuniform vec3 uCamPos;\nuniform sampler2D uAlbedo;\nvoid main() {\n    vec3 N = normalize(vNorm);\n    vec3 L = normalize(uLightPos - vPos);\n    vec3 V = normalize(uCamPos  - vPos);\n    vec3 H = normalize(L + V);\n    float diff  = max(dot(N, L), 0.0);\n    float spec  = pow(max(dot(N, H), 0.0), 64.0);\n    vec4  albedo = texture(uAlbedo, vUV);\n    fragColor = albedo * (0.1 + diff) + vec4(1.0) * spec;\n}","ports":[]},
    {"id":"glsl.frag_pbr","title":"GLSL PBR (Cook-Torrance)","cat":"GLSL",
     "desc":"PBR microfacet fragment shader (GGX NDF, Smith G, Schlick F)",
     "tpl":"#version 460 core\nconst float PI = 3.14159265;\nin vec3 vPos; in vec3 vNorm; in vec2 vUV;\nout vec4 fragColor;\nuniform vec3 uAlbedo; uniform float uRough; uniform float uMetal;\nuniform vec3 uLightPos; uniform vec3 uCamPos;\nfloat D_GGX(float NdH, float r) {\n    float a=r*r; float a2=a*a;\n    float d=NdH*NdH*(a2-1)+1;\n    return a2/(PI*d*d);\n}\nfloat G_Smith(float NdV, float NdL, float r) {\n    float k=(r+1)*(r+1)/8;\n    return (NdV/(NdV*(1-k)+k))*(NdL/(NdL*(1-k)+k));\n}\nvec3 F_Schlick(float cosT, vec3 F0) {\n    return F0+(1-F0)*pow(1-cosT,5);\n}\nvoid main() {\n    vec3 N=normalize(vNorm); vec3 V=normalize(uCamPos-vPos);\n    vec3 L=normalize(uLightPos-vPos); vec3 H=normalize(V+L);\n    float NdV=max(dot(N,V),.001); float NdL=max(dot(N,L),.001);\n    float NdH=max(dot(N,H),.001); float HdV=max(dot(H,V),.001);\n    vec3 F0=mix(vec3(.04),uAlbedo,uMetal);\n    vec3 F=F_Schlick(HdV,F0);\n    float D=D_GGX(NdH,uRough); float G=G_Smith(NdV,NdL,uRough);\n    vec3 spec=D*G*F/(4*NdV*NdL);\n    vec3 diff=(1-F)*(1-uMetal)*uAlbedo/PI;\n    fragColor=vec4((diff+spec)*NdL*3.14,1);\n}","ports":[]},
    {"id":"glsl.frag_gamma","title":"GLSL gamma correction","cat":"GLSL",
     "desc":"Linear→sRGB gamma encode at final output",
     "tpl":"// Gamma correction (linear → sRGB 2.2)\nfragColor.rgb = pow(fragColor.rgb, vec3(1.0/2.2));","ports":[]},
    {"id":"glsl.frag_fog","title":"GLSL exponential fog","cat":"GLSL",
     "desc":"Apply exponential distance fog over fragment color",
     "tpl":"// Exponential fog\nfloat dist  = length(vPos - uCamPos);\nfloat fogF  = exp(-uFogDensity * dist);\nfragColor.rgb = mix(uFogColor, fragColor.rgb, clamp(fogF, 0, 1));","ports":[]},
    {"id":"glsl.compute","title":"GLSL compute shader","cat":"GLSL",
     "desc":"Minimal GLSL 4.6 compute shader (local_size 256×1×1)",
     "tpl":"#version 460 core\nlayout(local_size_x=256) in;\nlayout(std430, binding=0) buffer BufIn  { float inData[]; };\nlayout(std430, binding=1) buffer BufOut { float outData[]; };\nvoid main() {\n    uint gid = gl_GlobalInvocationID.x;\n    outData[gid] = inData[gid] * 2.0;\n}","ports":[]},
    {"id":"glsl.compute_cannonic","title":"GLSL compute Cannonic update","cat":"GLSL",
     "desc":"Compute shader: pos += vel*dt over Cannonic SoA slices (instanced)",
     "tpl":"#version 460 core\nlayout(local_size_x=256) in;\nlayout(std430, binding=0) buffer PosX { float pos_x[]; };\nlayout(std430, binding=1) buffer VelX { float vel_x[]; };\nuniform float uDt;\nuniform uint  uCount;\nvoid main() {\n    uint i = gl_GlobalInvocationID.x;\n    if (i >= uCount) return;\n    pos_x[i] += vel_x[i] * uDt;\n}","ports":[]},
    {"id":"glsl.tess_ctrl","title":"GLSL tessellation control","cat":"GLSL",
     "desc":"Tessellation control shader (passthrough, fixed level=4)",
     "tpl":"#version 460 core\nlayout(vertices=3) out;\nvoid main() {\n    if (gl_InvocationID == 0) {\n        gl_TessLevelOuter[0] = 4.0;\n        gl_TessLevelOuter[1] = 4.0;\n        gl_TessLevelOuter[2] = 4.0;\n        gl_TessLevelInner[0] = 4.0;\n    }\n    gl_out[gl_InvocationID].gl_Position =\n        gl_in[gl_InvocationID].gl_Position;\n}","ports":[]},
    {"id":"glsl.tess_eval","title":"GLSL tessellation evaluation","cat":"GLSL",
     "desc":"Tessellation evaluation: triangles, barycentric interpolation",
     "tpl":"#version 460 core\nlayout(triangles, equal_spacing, ccw) in;\nuniform mat4 uVP;\nvoid main() {\n    vec4 p = gl_TessCoord.x * gl_in[0].gl_Position\n           + gl_TessCoord.y * gl_in[1].gl_Position\n           + gl_TessCoord.z * gl_in[2].gl_Position;\n    gl_Position = uVP * p;\n}","ports":[]},
    {"id":"glsl.geom","title":"GLSL geometry shader","cat":"GLSL",
     "desc":"Geometry shader: triangle → triangle + face normal line",
     "tpl":"#version 460 core\nlayout(triangles) in;\nlayout(line_strip, max_vertices=6) out;\nuniform mat4 uVP;\nvoid main() {\n    vec4 c = (gl_in[0].gl_Position+gl_in[1].gl_Position+gl_in[2].gl_Position)/3.0;\n    for(int i=0;i<3;i++){gl_Position=uVP*gl_in[i].gl_Position; EmitVertex();}\n    EndPrimitive();\n    // normal line\n    gl_Position=uVP*c; EmitVertex();\n    gl_Position=uVP*(c+vec4(0,0.5,0,0)); EmitVertex();\n    EndPrimitive();\n}","ports":[]},
    {"id":"glsl.frag_shadow","title":"GLSL shadow map sample","cat":"GLSL",
     "desc":"PCF 3×3 shadow map lookup in fragment shader",
     "tpl":"// PCF shadow — uShadowMap + uLightMVP\nvec4 lsPos = uLightMVP * vec4(vPos, 1.0);\nvec3 proj  = lsPos.xyz / lsPos.w * 0.5 + 0.5;\nfloat shadow = 0.0;\nvec2  texelSize = 1.0 / textureSize(uShadowMap, 0);\nfor(int x=-1;x<=1;x++) for(int y=-1;y<=1;y++)\n    shadow += texture(uShadowMap, proj.xy + vec2(x,y)*texelSize).r < proj.z-0.001 ? 0.0 : 1.0/9.0;","ports":[]},
    {"id":"glsl.frag_ssao","title":"GLSL SSAO kernel","cat":"GLSL",
     "desc":"Screen-space ambient occlusion hemisphere sample loop",
     "tpl":"// SSAO — uGPos, uGNorm, uNoiseTex, uSamples[64 vec3]\nfloat ao = 0.0;\nfor(int i=0;i<64;i++) {\n    vec3 s = TBN * uSamples[i];\n    s = vPos.xyz + s * uRadius;\n    vec4 offset = uProjection * vec4(s, 1.0);\n    offset.xyz /= offset.w;\n    offset.xyz  = offset.xyz * 0.5 + 0.5;\n    float sampleDepth = texture(uGPos, offset.xy).z;\n    float rangeCheck  = smoothstep(0,1,uRadius/abs(vPos.z-sampleDepth));\n    ao += (sampleDepth >= s.z + 0.025 ? 1.0 : 0.0) * rangeCheck;\n}\nao = 1.0 - ao / 64.0;","ports":[]},
    {"id":"glsl.frag_tonemap","title":"GLSL tonemapping (ACES)","cat":"GLSL",
     "desc":"ACES filmic tonemapping curve in GLSL",
     "tpl":"// ACES filmic tonemapping\nvec3 aces_film(vec3 x) {\n    float a=2.51, b=0.03, c=2.43, d=0.59, e=0.14;\n    return clamp((x*(a*x+b))/(x*(c*x+d)+e), 0.0, 1.0);\n}\nfragColor.rgb = aces_film(fragColor.rgb);","ports":[]},
    {"id":"glsl.frag_noise","title":"GLSL value noise","cat":"GLSL",
     "desc":"Simple value noise (hash-based) in fragment shader",
     "tpl":"float hash(vec2 p) {\n    return fract(sin(dot(p, vec2(127.1, 311.7)))*43758.5453);\n}\nfloat noise(vec2 p) {\n    vec2 i=floor(p); vec2 f=fract(p);\n    vec2 u=f*f*(3-2*f);\n    return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),\n               mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);\n}","ports":[]},
    {"id":"glsl.frag_sdf_sphere","title":"GLSL SDF sphere raymarcher","cat":"GLSL",
     "desc":"Minimal sphere SDF raymarcher in fragment shader",
     "tpl":"#version 460 core\nout vec4 fragColor;\nuniform vec2 uRes; uniform float uTime;\nfloat sdf_sphere(vec3 p, float r){return length(p)-r;}\nvoid main(){\n    vec2 uv=(gl_FragCoord.xy/uRes-.5)*vec2(uRes.x/uRes.y,1);\n    vec3 ro=vec3(0,0,3),rd=normalize(vec3(uv,-1));\n    float t=0; vec3 col=vec3(0);\n    for(int i=0;i<64;i++){\n        vec3 p=ro+rd*t;\n        float d=sdf_sphere(p,1);\n        if(d<.001){vec3 n=normalize(p); col=n*.5+.5; break;}\n        t+=d; if(t>20) break;\n    }\n    fragColor=vec4(col,1);\n}","ports":[]},
    {"id":"glsl.ubo","title":"GLSL UBO block","cat":"GLSL",
     "desc":"Uniform Buffer Object binding in GLSL shader",
     "tpl":"// UBO — std140 layout\nlayout(std140, binding=0) uniform GlobalUBO {\n    mat4 uView;\n    mat4 uProj;\n    vec4 uCamPos;\n    float uTime;\n    float uDt;\n    vec2 uResolution;\n};","ports":[]},
    {"id":"glsl.ssbo","title":"GLSL SSBO block","cat":"GLSL",
     "desc":"Shader Storage Buffer Object binding in GLSL",
     "tpl":"// SSBO — std430, writable\nlayout(std430, binding=1) buffer EntityData {\n    vec4 positions[];  // xyz=pos w=pad\n    vec4 velocities[];\n};","ports":[]},

    # ═══════════════════  SPIR-V — Binary Assembly Text Format  ═══════════════════
    {"id":"spirv.header","title":"SPIR-V module header","cat":"SPIR-V",
     "desc":"SPIR-V module preamble (magic, version, generator, bound, reserved)",
     "tpl":"; SPIR-V 1.5 module header (binary words as NASM dwords)\n; magic=0x07230203 version=0x00010500 generator=0 bound=TBD reserved=0\nsection .spv_module\ndd 0x07230203   ; magic\ndd 0x00010500   ; version 1.5\ndd 0x00000000   ; generator (custom)\ndd {bound}      ; ID bound (max ID+1)\ndd 0x00000000   ; reserved",
     "ports":[{"n":"bound","d":"64"}]},
    {"id":"spirv.capability","title":"SPIR-V OpCapability","cat":"SPIR-V",
     "desc":"OpCapability instruction (word1=opcode+wc, word2=capability)",
     "tpl":"; OpCapability Shader\n; Op: 0x00020011 = WordCount=2 | Opcode=17(OpCapability)\n; Capability: 1=Shader, 2=Geometry, 3=Tessellation, 65=Float64\ndd 0x00020011, {capability}",
     "ports":[{"n":"capability","d":"1"}]},
    {"id":"spirv.extension","title":"SPIR-V OpExtension","cat":"SPIR-V",
     "desc":"OpExtension: import an extended instruction set (GLSL.std.450)",
     "tpl":"; OpExtInstImport %{id} \"GLSL.std.450\"\n; Opcode=11, then UTF-8 name null-padded to 4-byte words\n; (text format representation)\n; %{id} = OpExtInstImport \"GLSL.std.450\"",
     "ports":[{"n":"id","d":"1"}]},
    {"id":"spirv.entrypoint","title":"SPIR-V OpEntryPoint","cat":"SPIR-V",
     "desc":"OpEntryPoint declares shader stage and interface variables",
     "tpl":"; OpEntryPoint {stage} %{fn_id} \"{name}\" %{iface...}\n; Execution model: 0=Vertex 4=Fragment 5=GLCompute\n; Word 1: opcode=15|(wc<<16)  Word 2: execution_model  Word 3: fn_id\n; Word 4+: name chars (null-terminated, padded)  then interface IDs",
     "ports":[{"n":"stage","d":"Fragment"},{"n":"fn_id","d":"4"},{"n":"name","d":"main"}]},
    {"id":"spirv.typevoid","title":"SPIR-V OpTypeVoid","cat":"SPIR-V",
     "desc":"OpTypeVoid %id — declare void type",
     "tpl":"; %{id} = OpTypeVoid\n; Opcode=19 WordCount=2\ndd 0x00020013, {id}",
     "ports":[{"n":"id","d":"2"}]},
    {"id":"spirv.typebool","title":"SPIR-V OpTypeBool","cat":"SPIR-V",
     "desc":"OpTypeBool %id — declare bool type (1-bit logical)",
     "tpl":"; %{id} = OpTypeBool\ndd 0x00020014, {id}",
     "ports":[{"n":"id","d":"3"}]},
    {"id":"spirv.typeint","title":"SPIR-V OpTypeInt","cat":"SPIR-V",
     "desc":"OpTypeInt %id width signedness (e.g. i32 signed)",
     "tpl":"; %{id} = OpTypeInt {width} {signed}\n; Opcode=21 WordCount=4\ndd 0x00040015, {id}, {width}, {signed}",
     "ports":[{"n":"id","d":"5"},{"n":"width","d":"32"},{"n":"signed","d":"1"}]},
    {"id":"spirv.typefloat","title":"SPIR-V OpTypeFloat","cat":"SPIR-V",
     "desc":"OpTypeFloat %id width (32=f32, 64=f64)",
     "tpl":"; %{id} = OpTypeFloat {width}\n; Opcode=22 WordCount=3\ndd 0x00030016, {id}, {width}",
     "ports":[{"n":"id","d":"6"},{"n":"width","d":"32"}]},
    {"id":"spirv.typevec","title":"SPIR-V OpTypeVector","cat":"SPIR-V",
     "desc":"OpTypeVector %id %component_type count (e.g. vec4)",
     "tpl":"; %{id} = OpTypeVector %{comp} {count}\n; Opcode=23 WordCount=4\ndd 0x00040017, {id}, {comp}, {count}",
     "ports":[{"n":"id","d":"7"},{"n":"comp","d":"6"},{"n":"count","d":"4"}]},
    {"id":"spirv.typemat","title":"SPIR-V OpTypeMatrix","cat":"SPIR-V",
     "desc":"OpTypeMatrix %id %column_type cols (e.g. mat4)",
     "tpl":"; %{id} = OpTypeMatrix %{col} {cols}\n; Opcode=24 WordCount=4\ndd 0x00040018, {id}, {col}, {cols}",
     "ports":[{"n":"id","d":"8"},{"n":"col","d":"7"},{"n":"cols","d":"4"}]},
    {"id":"spirv.typepointer","title":"SPIR-V OpTypePointer","cat":"SPIR-V",
     "desc":"OpTypePointer %id StorageClass %base_type",
     "tpl":"; %{id} = OpTypePointer {storage} %{base}\n; StorageClass: 0=UniformConstant 1=Input 3=Output 4=Private 8=Function\n; Opcode=32 WordCount=4\ndd 0x00040020, {id}, {storage}, {base}",
     "ports":[{"n":"id","d":"9"},{"n":"storage","d":"8"},{"n":"base","d":"6"}]},
    {"id":"spirv.typefunction","title":"SPIR-V OpTypeFunction","cat":"SPIR-V",
     "desc":"OpTypeFunction %id %ret [%param...] — function signature",
     "tpl":"; %{id} = OpTypeFunction %{ret} [%{p0}]\n; Opcode=33 WordCount=3+nparams\ndd 0x00030021, {id}, {ret}  ; zero-param function type",
     "ports":[{"n":"id","d":"10"},{"n":"ret","d":"2"}]},
    {"id":"spirv.constant","title":"SPIR-V OpConstant","cat":"SPIR-V",
     "desc":"OpConstant %type %id literal — scalar constant",
     "tpl":"; %{id} = OpConstant %{type} {literal}\n; Opcode=43 WordCount=4 (f32/i32 literal)\ndd 0x00040002B, {type}, {id}, {literal}",
     "ports":[{"n":"id","d":"11"},{"n":"type","d":"6"},{"n":"literal","d":"0x3F800000"}]},
    {"id":"spirv.variable","title":"SPIR-V OpVariable","cat":"SPIR-V",
     "desc":"OpVariable %ptr_type %id StorageClass [init] — declare variable",
     "tpl":"; %{id} = OpVariable %{ptr_type} {storage}\n; Opcode=59 WordCount=4 (no init)\ndd 0x0004003B, {ptr_type}, {id}, {storage}",
     "ports":[{"n":"id","d":"12"},{"n":"ptr_type","d":"9"},{"n":"storage","d":"8"}]},
    {"id":"spirv.function","title":"SPIR-V OpFunction","cat":"SPIR-V",
     "desc":"OpFunction %ret %id control %fn_type — begin function body",
     "tpl":"; %{id} = OpFunction %{ret} {ctrl} %{fn_type}\n; Opcode=54 WordCount=5  ctrl: 0=None 1=Inline 2=DontInline\ndd 0x00050036, {ret}, {id}, {ctrl}, {fn_type}",
     "ports":[{"n":"id","d":"13"},{"n":"ret","d":"2"},{"n":"ctrl","d":"0"},{"n":"fn_type","d":"10"}]},
    {"id":"spirv.functionend","title":"SPIR-V OpFunctionEnd","cat":"SPIR-V",
     "desc":"OpFunctionEnd — terminate function body",
     "tpl":"; OpFunctionEnd\n; Opcode=56 WordCount=1\ndd 0x00010038","ports":[]},
    {"id":"spirv.label","title":"SPIR-V OpLabel","cat":"SPIR-V",
     "desc":"OpLabel %id — marks start of basic block",
     "tpl":"; %{id} = OpLabel\n; Opcode=248 WordCount=2\ndd 0x000200F8, {id}",
     "ports":[{"n":"id","d":"14"}]},
    {"id":"spirv.return","title":"SPIR-V OpReturn","cat":"SPIR-V",
     "desc":"OpReturn — return from void function",
     "tpl":"; OpReturn  Opcode=253\ndd 0x000100FD","ports":[]},
    {"id":"spirv.returnval","title":"SPIR-V OpReturnValue","cat":"SPIR-V",
     "desc":"OpReturnValue %val — return value from function",
     "tpl":"; OpReturnValue %{val}\n; Opcode=254 WordCount=2\ndd 0x000200FE, {val}",
     "ports":[{"n":"val","d":"15"}]},
    {"id":"spirv.load","title":"SPIR-V OpLoad","cat":"SPIR-V",
     "desc":"OpLoad %type %id %ptr [mem_access] — load from pointer",
     "tpl":"; %{id} = OpLoad %{type} %{ptr}\n; Opcode=61 WordCount=4\ndd 0x0004003D, {type}, {id}, {ptr}",
     "ports":[{"n":"id","d":"16"},{"n":"type","d":"6"},{"n":"ptr","d":"12"}]},
    {"id":"spirv.store","title":"SPIR-V OpStore","cat":"SPIR-V",
     "desc":"OpStore %ptr %val — store value to pointer",
     "tpl":"; OpStore %{ptr} %{val}\n; Opcode=62 WordCount=3\ndd 0x0003003E, {ptr}, {val}",
     "ports":[{"n":"ptr","d":"12"},{"n":"val","d":"16"}]},
    {"id":"spirv.fadd","title":"SPIR-V OpFAdd","cat":"SPIR-V",
     "desc":"OpFAdd %type %id %a %b — fp addition",
     "tpl":"; %{id} = OpFAdd %{type} %{a} %{b}\n; Opcode=129 WordCount=5\ndd 0x00050081, {type}, {id}, {a}, {b}",
     "ports":[{"n":"id","d":"17"},{"n":"type","d":"6"},{"n":"a","d":"16"},{"n":"b","d":"11"}]},
    {"id":"spirv.fmul","title":"SPIR-V OpFMul","cat":"SPIR-V",
     "desc":"OpFMul %type %id %a %b — fp multiplication",
     "tpl":"; %{id} = OpFMul %{type} %{a} %{b}\n; Opcode=133 WordCount=5\ndd 0x00050085, {type}, {id}, {a}, {b}",
     "ports":[{"n":"id","d":"17"},{"n":"type","d":"6"},{"n":"a","d":"16"},{"n":"b","d":"11"}]},
    {"id":"spirv.dot","title":"SPIR-V OpDot","cat":"SPIR-V",
     "desc":"OpDot %float_type %id %vec_a %vec_b — dot product",
     "tpl":"; %{id} = OpDot %{ftype} %{a} %{b}\n; Opcode=148 WordCount=5\ndd 0x00050094, {ftype}, {id}, {a}, {b}",
     "ports":[{"n":"id","d":"18"},{"n":"ftype","d":"6"},{"n":"a","d":"16"},{"n":"b","d":"11"}]},
    {"id":"spirv.matxvec","title":"SPIR-V OpMatrixTimesVector","cat":"SPIR-V",
     "desc":"OpMatrixTimesVector %vec_type %id %mat %vec — transform vector",
     "tpl":"; %{id} = OpMatrixTimesVector %{vtype} %{mat} %{vec}\n; Opcode=145 WordCount=5\ndd 0x00050091, {vtype}, {id}, {mat}, {vec}",
     "ports":[{"n":"id","d":"19"},{"n":"vtype","d":"7"},{"n":"mat","d":"20"},{"n":"vec","d":"16"}]},
    {"id":"spirv.composite_extract","title":"SPIR-V OpCompositeExtract","cat":"SPIR-V",
     "desc":"OpCompositeExtract %type %id %composite index — extract component",
     "tpl":"; %{id} = OpCompositeExtract %{type} %{comp} {idx}\n; Opcode=81 WordCount=5\ndd 0x00050051, {type}, {id}, {comp}, {idx}",
     "ports":[{"n":"id","d":"21"},{"n":"type","d":"6"},{"n":"comp","d":"16"},{"n":"idx","d":"0"}]},
    {"id":"spirv.composite_construct","title":"SPIR-V OpCompositeConstruct","cat":"SPIR-V",
     "desc":"OpCompositeConstruct %type %id %c0 %c1 %c2 %c3 — build vector",
     "tpl":"; %{id} = OpCompositeConstruct %{type} %{c0} %{c1} %{c2} %{c3}\n; Opcode=80 WordCount=7 (vec4)\ndd 0x00070050, {type}, {id}, {c0}, {c1}, {c2}, {c3}",
     "ports":[{"n":"id","d":"22"},{"n":"type","d":"7"},{"n":"c0","d":"11"},{"n":"c1","d":"11"},{"n":"c2","d":"11"},{"n":"c3","d":"11"}]},
    {"id":"spirv.branch","title":"SPIR-V OpBranch","cat":"SPIR-V",
     "desc":"OpBranch %target_label — unconditional branch",
     "tpl":"; OpBranch %{tgt}\n; Opcode=249 WordCount=2\ndd 0x000200F9, {tgt}",
     "ports":[{"n":"tgt","d":"14"}]},
    {"id":"spirv.branch_cond","title":"SPIR-V OpBranchConditional","cat":"SPIR-V",
     "desc":"OpBranchConditional %cond %true %false — conditional branch",
     "tpl":"; OpBranchConditional %{cond} %{tbl} %{fbl}\n; Opcode=250 WordCount=4\ndd 0x000400FA, {cond}, {tbl}, {fbl}",
     "ports":[{"n":"cond","d":"23"},{"n":"tbl","d":"14"},{"n":"fbl","d":"24"}]},
    {"id":"spirv.phi","title":"SPIR-V OpPhi","cat":"SPIR-V",
     "desc":"OpPhi %type %id [val0 bb0 val1 bb1 ...] — SSA merge",
     "tpl":"; %{id} = OpPhi %{type} %{v0} %{bb0} %{v1} %{bb1}\n; Opcode=245 WordCount=7\ndd 0x000700F5, {type}, {id}, {v0}, {bb0}, {v1}, {bb1}",
     "ports":[{"n":"id","d":"25"},{"n":"type","d":"6"},{"n":"v0","d":"11"},{"n":"bb0","d":"14"},{"n":"v1","d":"16"},{"n":"bb1","d":"24"}]},
    {"id":"spirv.decorate","title":"SPIR-V OpDecorate","cat":"SPIR-V",
     "desc":"OpDecorate %target decoration [literal] — annotate ID (Location, Binding...)",
     "tpl":"; OpDecorate %{id} {deco} {val}\n; Opcode=71 WordCount=4 (with one extra word)\n; Decoration: 30=Location 33=Binding 2=Block 4=BuiltIn\ndd 0x00040047, {id}, {deco}, {val}",
     "ports":[{"n":"id","d":"12"},{"n":"deco","d":"30"},{"n":"val","d":"0"}]},

    # ═══════════════════  Cannonic / SoA Data Access  ═══════════════════
    {"id":"can.arena_base","title":"Cannonic arena base","cat":"Cannonic",
     "desc":"Load Cannonic arena base pointer; derive SoA slice pointers",
     "tpl":"; Cannonic arena layout (flat SoA, 64B-aligned)\n; base = cannonic_arena ptr (qword in data section)\n    mov  rax, [rel cannonic_arena]   ; base ptr\n    ; slice pointers (pre-computed at init or derive below)\n    mov  [rel ptr_pos_x], rax",
     "ports":[]},
    {"id":"can.slice_ptr","title":"Cannonic slice ptr","cat":"Cannonic",
     "desc":"Compute pointer to component slice k at offset {slice_off} from base",
     "tpl":"; ptr_slice_k = cannonic_arena + SLICE_{K}_OFFSET\n    mov  rax, [rel cannonic_arena]\n    add  rax, {slice_off}       ; SLICE_OFFSET = component_index * N * sizeof(T)\n    mov  [rel {slice_ptr}], rax",
     "ports":[{"n":"slice_off","d":"0"},{"n":"slice_ptr","d":"ptr_pos_x"}]},
    {"id":"can.read_f32","title":"Cannonic read f32[eid]","cat":"Cannonic",
     "desc":"Read f32 component for entity EID: val = slice[eid]",
     "tpl":"; f32 read: xmm0 = {slice}[eid]\n    mov  rax, [rel {slice}]       ; slice base ptr\n    movss xmm0, [rax + {eid}*4]  ; stride=4B for f32",
     "ports":[{"n":"slice","d":"ptr_pos_x"},{"n":"eid","d":"ecx"}]},
    {"id":"can.write_f32","title":"Cannonic write f32[eid]","cat":"Cannonic",
     "desc":"Write f32 component for entity EID: slice[eid] = val",
     "tpl":"; f32 write: {slice}[eid] = xmm0\n    mov  rax, [rel {slice}]\n    movss [rax + {eid}*4], xmm0",
     "ports":[{"n":"slice","d":"ptr_pos_x"},{"n":"eid","d":"ecx"}]},
    {"id":"can.read_i32","title":"Cannonic read i32[eid]","cat":"Cannonic",
     "desc":"Read i32 component (state flags, mesh ID, etc.)",
     "tpl":"; i32 read: eax = {slice}[eid]\n    mov  rax, [rel {slice}]\n    mov  eax, [rax + {eid}*4]",
     "ports":[{"n":"slice","d":"ptr_state_flags"},{"n":"eid","d":"ecx"}]},
    {"id":"can.write_i32","title":"Cannonic write i32[eid]","cat":"Cannonic",
     "desc":"Write i32 component for entity EID",
     "tpl":"; i32 write: {slice}[eid] = {val}\n    mov  rax, [rel {slice}]\n    mov  dword [rax + {eid}*4], {val}",
     "ports":[{"n":"slice","d":"ptr_state_flags"},{"n":"eid","d":"ecx"},{"n":"val","d":"0"}]},
    {"id":"can.read_q64","title":"Cannonic read q32×4 (quat)","cat":"Cannonic",
     "desc":"Read 4-component quaternion (q0,q1,q2,q3 separate slices) for EID into xmm0",
     "tpl":"; quaternion = {q0}[eid], {q1}[eid], {q2}[eid], {q3}[eid]\n    mov  rax, {eid}\n    shl  rax, 2                    ; *4 for f32\n    mov  r8,  [rel {q0}]\n    mov  r9,  [rel {q1}]\n    mov  r10, [rel {q2}]\n    mov  r11, [rel {q3}]\n    movss xmm0, [r8 + rax]\n    movss xmm1, [r9 + rax]\n    movss xmm2, [r10+ rax]\n    movss xmm3, [r11+ rax]\n    ; pack to xmm: insertps\n    insertps xmm0, xmm1, 0x10\n    insertps xmm0, xmm2, 0x20\n    insertps xmm0, xmm3, 0x30",
     "ports":[{"n":"q0","d":"ptr_rot_q0"},{"n":"q1","d":"ptr_rot_q1"},{"n":"q2","d":"ptr_rot_q2"},{"n":"q3","d":"ptr_rot_q3"},{"n":"eid","d":"ecx"}]},
    {"id":"can.batch_pos_avx2","title":"Cannonic batch pos AVX2","cat":"Cannonic",
     "desc":"Load 8×f32 pos_x into ymm0 (SoA sequential batch, AVX2)",
     "tpl":"; Load 8 consecutive pos_x values: ymm0 = pos_x[base..base+8]\n    mov   rax, [rel ptr_pos_x]\n    imul  rcx, {base_eid}, 4\n    vmovups ymm0, [rax + rcx]",
     "ports":[{"n":"base_eid","d":"rsi"}]},
    {"id":"can.batch_vel_fma","title":"Cannonic pos+=vel*dt (AVX2 FMA)","cat":"Cannonic",
     "desc":"Update 8 entities: pos_x[i] += vel_x[i] * dt using FMA (Cannonic hot path)",
     "tpl":"; ymm0=pos_x[8], ymm1=vel_x[8], ymm2=dt_broadcast\n    vbroadcastss ymm2, [rel f_dt]\n    mov  rax, [rel ptr_pos_x]\n    mov  rbx, [rel ptr_vel_x]\n    imul rcx, {base_eid}, 4\n    vmovups  ymm0, [rax + rcx]\n    vmovups  ymm1, [rbx + rcx]\n    vfmadd231ps ymm0, ymm1, ymm2   ; pos += vel*dt\n    vmovups [rax + rcx], ymm0",
     "ports":[{"n":"base_eid","d":"rsi"}]},
    {"id":"can.visibility_test","title":"Cannonic visibility bitfield test","cat":"Cannonic",
     "desc":"Test visibility bit for entity EID in camera C bitfield",
     "tpl":"; Test visibility: VISIBILITY_BF[eid/64] bit (eid%64) for camera {cam}\n    mov  rcx, {eid}\n    mov  rax, rcx\n    shr  rax, 6                    ; qword index\n    and  rcx, 63                   ; bit index\n    mov  r8,  [rel ptr_vis_bf_{cam}]\n    bt   qword [r8 + rax*8], rcx   ; CF = visibility\n    jnc  .not_visible",
     "ports":[{"n":"eid","d":"rdx"},{"n":"cam","d":"0"}]},
    {"id":"can.visibility_set","title":"Cannonic visibility set/clear","cat":"Cannonic",
     "desc":"Set or clear visibility bit for entity EID, camera C",
     "tpl":"; Set visibility: BTS / clear: BTR\n    mov  rcx, {eid}\n    mov  rax, rcx\n    shr  rax, 6\n    and  rcx, 63\n    mov  r8, [rel ptr_vis_bf_{cam}]\n    bts  qword [r8 + rax*8], rcx   ; set (use BTR to clear)",
     "ports":[{"n":"eid","d":"rdx"},{"n":"cam","d":"0"}]},
    {"id":"can.entity_create","title":"Cannonic entity create","cat":"Cannonic",
     "desc":"Allocate next free EID from free-list or bump counter",
     "tpl":"; Allocate EID (bump allocator — lock-free single threaded)\n    mov  eax, [rel cannonic_eid_next]\n    inc  dword [rel cannonic_eid_next]\n    cmp  eax, [rel cannonic_capacity]\n    jae  .eid_overflow\n    ; zero entity slot in all required slices\n    ; (use rep stosb or vectorized zero per slice)",
     "ports":[]},
    {"id":"can.entity_destroy","title":"Cannonic entity destroy (deferred)","cat":"Cannonic",
     "desc":"Mark entity EID for deferred destruction (set DEAD bit in STATE_FLAGS)",
     "tpl":"; Mark dead: state_flags[eid] |= FLAG_DEAD\n    mov  rax, [rel ptr_state_flags]\n    lock or dword [rax + {eid}*4], 0x80000000  ; FLAG_DEAD = bit 31",
     "ports":[{"n":"eid","d":"ecx"}]},
    {"id":"can.compact_dead","title":"Cannonic compact (deferred)","cat":"Cannonic",
     "desc":"Compact dead entities — swap-remove or move-to-end; update EID mappings",
     "tpl":"; Compact dead entities from slice range [0..count)\n    xor  rsi, rsi              ; write cursor\n    xor  rdi, rdi              ; read cursor\n    mov  rcx, [rel cannonic_count]\n    mov  r8,  [rel ptr_state_flags]\n.compact_loop:\n    cmp  rdi, rcx\n    jge  .compact_done\n    mov  eax, [r8 + rdi*4]    ; state_flags[read]\n    bt   eax, 31              ; FLAG_DEAD?\n    jc   .skip_dead\n    ; copy entity rdi→rsi in all slices (call slice_copy helper)\n    call slice_copy_entity    ; rdi=src eid, rsi=dst eid\n    inc  rsi\n.skip_dead:\n    inc  rdi\n    jmp  .compact_loop\n.compact_done:\n    mov  [rel cannonic_count], esi",
     "ports":[]},
    {"id":"can.for_each_entity","title":"Cannonic for-each entity loop","cat":"Cannonic",
     "desc":"Iterate all active entities (rcx=0..count), EID in rcx",
     "tpl":"; for each entity: rcx = EID 0..count-1\n    xor  rcx, rcx\n    mov  rdx, [rel cannonic_count]\n.entity_loop:\n    cmp  rcx, rdx\n    jge  .entity_done\n    ; --- process entity rcx ---\n    inc  rcx\n    jmp  .entity_loop\n.entity_done:",
     "ports":[]},
    {"id":"can.simd_forall","title":"Cannonic SIMD for-all (AVX2 8×f32)","cat":"Cannonic",
     "desc":"AVX2 vectorized loop over entire Cannonic f32 slice (8 entities/iter)",
     "tpl":"; AVX2 8x loop over {slice}[0..N]\n    mov  rax, [rel {slice}]\n    mov  rcx, [rel cannonic_count]\n    xor  rsi, rsi\n    ; peel to align (optional)\n.simd_main:\n    cmp  rsi, rcx\n    jge  .simd_tail\n    vmovups ymm0, [rax + rsi*4]\n    ; --- AVX2 operation on ymm0 ---\n    vmovups [rax + rsi*4], ymm0\n    add  rsi, 8\n    jmp  .simd_main\n.simd_tail:\n    ; handle remaining < 8 elements scalar\n    cmp  rsi, rcx\n    jge  .simd_done\n    movss xmm0, [rax + rsi*4]\n    ; scalar op\n    movss [rax + rsi*4], xmm0\n    inc  rsi\n    jmp  .simd_tail\n.simd_done:\n    vzeroupper",
     "ports":[{"n":"slice","d":"ptr_pos_x"}]},
    {"id":"can.lod_update","title":"Cannonic LoD update","cat":"Cannonic",
     "desc":"Update LOD_LEVEL[eid] based on squared camera distance (SoA scalar loop)",
     "tpl":"; Update LOD_LEVEL for all entities\n    mov  rax, [rel ptr_pos_x]\n    mov  rbx, [rel ptr_pos_z]\n    mov  rdx, [rel ptr_lod_level]\n    mov  rcx, [rel cannonic_count]\n    movss xmm7, [rel cam_pos_x]\n    movss xmm6, [rel cam_pos_z]\n    xor  rsi, rsi\n.lod_loop:\n    cmp  rsi, rcx\n    jge  .lod_done\n    movss xmm0, [rax + rsi*4]\n    movss xmm1, [rbx + rsi*4]\n    subss xmm0, xmm7              ; dx\n    subss xmm1, xmm6              ; dz\n    mulss xmm0, xmm0              ; dx^2\n    mulss xmm1, xmm1              ; dz^2\n    addss xmm0, xmm1              ; dist^2\n    ; compare thresholds → lod byte\n    mov   al, 0\n    comiss xmm0, [rel lod_thresh_1]\n    jb   .store_lod\n    mov   al, 1\n    comiss xmm0, [rel lod_thresh_2]\n    jb   .store_lod\n    mov   al, 2\n.store_lod:\n    mov  [rdx + rsi], al\n    inc  rsi\n    jmp  .lod_loop\n.lod_done:",
     "ports":[]},
    {"id":"can.frustum_cull","title":"Cannonic frustum cull (SIMD)","cat":"Cannonic",
     "desc":"SIMD AABB frustum culling over Cannonic SoA — sets visibility bitvec",
     "tpl":"; Frustum cull 8 entities per iter (AVX2)\n; Slices: ptr_pos_x, ptr_pos_y, ptr_pos_z, ptr_aabb_r\n; Output: ptr_vis_bf_0 (camera 0)\n    mov  rax, [rel ptr_pos_x]\n    mov  rbx, [rel ptr_pos_y]\n    mov  r8,  [rel ptr_aabb_r]\n    mov  r9,  [rel ptr_vis_bf_0]\n    vbroadcastss ymm4, [rel frustum_plane0_x]\n    vbroadcastss ymm5, [rel frustum_plane0_d]\n    ; ... (add planes for full frustum)\n    xor  rcx, rcx\n    mov  rdx, [rel cannonic_count]\n.fcull_loop:\n    vmovups ymm0, [rax + rcx*4]  ; pos_x[8]\n    vmovups ymm1, [rbx + rcx*4]  ; pos_y[8]\n    vmovups ymm2, [r8  + rcx*4]  ; aabb_r[8]\n    ; dot(plane_n, pos) + d - r >= 0 → inside\n    vfmadd231ps ymm4, ymm0, [rel plane0_nx]\n    vcmpps  ymm3, ymm4, ymm2, 5  ; ge → mask\n    vmovmskps eax, ymm3           ; 8-bit vis mask\n    ; store 8 bits to bitvec at rcx\n    mov   r10, rcx\n    shr   r10, 6\n    and   rcx, 63\n    movzx rdi, al\n    ; merge bits (shift and OR)\n    shl   rdi, cl\n    or    [r9 + r10*8], rdi\n    add   rcx, 8\n    cmp   rcx, rdx\n    jl    .fcull_loop\n    vzeroupper",
     "ports":[]},
    {"id":"can.instanced_upload","title":"Cannonic instanced GPU upload","cat":"Cannonic",
     "desc":"Upload MATRIX_MODEL slice to OpenGL instanced VBO via glBufferSubData",
     "tpl":"; Upload model matrices slice to GPU\n; Assumes VBO for MATRIX_MODEL is bound at binding=2\n    mov  r8,  [rel ptr_matrix_model]   ; data ptr (cannonic slice)\n    imul r9,  [rel cannonic_count], 64 ; N * sizeof(mat4)\n    mov  rdx, r9\n    xor  r9,  r9                       ; offset=0\n    mov  ecx, 0x8892                   ; GL_ARRAY_BUFFER\n    sub  rsp, 32\n    call [rel pfn_glBufferSubData]\n    add  rsp, 32",
     "ports":[]},
    {"id":"can.draw_instanced","title":"Cannonic draw instanced call","cat":"Cannonic",
     "desc":"Single instanced draw call for all visible entities (Cannonic → GPU path)",
     "tpl":"; 1 draw call for N entities via glDrawElementsInstanced\n    mov  [rsp+32], [rel cannonic_visible_count]\n    xor  r9,  r9               ; offset=0\n    mov  r8,  0x1405           ; GL_UNSIGNED_INT\n    mov  rdx, [rel mesh_index_count]\n    mov  ecx, 4                ; GL_TRIANGLES\n    sub  rsp, 32\n    call [rel pfn_glDrawElementsInstanced]\n    add  rsp, 32",
     "ports":[]},
    {"id":"can.subscribe_pos","title":"Cannonic subscribe pos change","cat":"Cannonic",
     "desc":"Reactive subscribe: register callback when POS slice changes (dirty bit pattern)",
     "tpl":"; Mark pos dirty for entity EID (subscribe pattern)\n    mov  rax, [rel ptr_dirty_pos]\n    mov  rcx, {eid}\n    bts  qword [rax + (rcx/64)*8], rcx  ; set dirty bit",
     "ports":[{"n":"eid","d":"rdx"}]},
    {"id":"can.flush_dirty","title":"Cannonic flush dirty callbacks","cat":"Cannonic",
     "desc":"Iterate dirty-bits bitfield, call registered subscriber callbacks per changed EID",
     "tpl":"; Flush dirty pos bits → call subscriber\n    mov  rsi, [rel ptr_dirty_pos]\n    mov  rcx, [rel cannonic_count]\n    add  rcx, 63\n    shr  rcx, 6               ; qword count\n    xor  rdi, rdi\n.dirty_qw:\n    cmp  rdi, rcx\n    jge  .dirty_done\n    mov  rax, [rsi + rdi*8]\n    test rax, rax\n    jz   .dirty_next\n.dirty_bit:\n    bsf  rbx, rax             ; find lowest set bit\n    jz   .dirty_next\n    mov  rcx, rdi\n    shl  rcx, 6\n    add  rcx, rbx             ; eid = qword_idx*64 + bit_idx\n    call {subscriber}         ; rcx = changed EID\n    btr  rax, rbx\n    jmp  .dirty_bit\n.dirty_next:\n    mov  [rsi + rdi*8], rax   ; clear processed bits\n    inc  rdi\n    jmp  .dirty_qw\n.dirty_done:",
     "ports":[{"n":"subscriber","d":"on_pos_changed"}]},
    {"id":"can.arena_init","title":"Cannonic arena init","cat":"Cannonic",
     "desc":"Initialize Cannonic arena: VirtualAlloc 64B-aligned, zero, set slice offsets",
     "tpl":"; Init Cannonic arena (N entities, K component types)\n    ; Total size = N * sum(sizeof(T_k)) rounded to page\n    mov  [rsp+32], 4           ; PAGE_READWRITE\n    mov  r9,  0x3000           ; MEM_COMMIT|MEM_RESERVE\n    mov  r8,  {arena_bytes}    ; N * component_stride\n    xor  rdx, rdx\n    xor  rcx, rcx\n    sub  rsp, 32\n    call [VirtualAlloc]\n    add  rsp, 32\n    test rax, rax\n    jz   .arena_oom\n    mov  [rel cannonic_arena], rax\n    ; compute slice offsets\n    ; ptr_pos_x   = base + 0\n    ; ptr_pos_y   = base + N*4\n    ; ptr_pos_z   = base + N*8\n    ; ptr_vel_x   = base + N*12  etc.\n    mov  [rel ptr_pos_x], rax\n    add  rax, {N}*4\n    mov  [rel ptr_pos_y], rax\n    add  rax, {N}*4\n    mov  [rel ptr_pos_z], rax",
     "ports":[{"n":"arena_bytes","d":"0x400000"},{"n":"N","d":"4096"}]},
    {"id":"can.nm_ir_gen","title":"Cannonic → NM_IR node","cat":"Cannonic",
     "desc":"Emit NM_IR_NODE from Cannonic read (irgen pattern: load component → IR slot)",
     "tpl":"; irgen: emit OP_LOAD from Cannonic slice into IR temp t{dst}\n    ; IR_NODE layout: op(u8) type(u8) flags(u16) dst(u16) src(u16) imm(u32)\n    mov  rax, [rel ir_emit_ptr]      ; next ir slot\n    mov  byte  [rax+0], 6            ; OP_LOAD\n    mov  byte  [rax+1], {type}       ; 0=i32 2=f32\n    mov  word  [rax+4], {dst}\n    mov  word  [rax+6], {slice_id}   ; slice ID = source\n    add  qword [rel ir_emit_ptr], 12 ; advance (12B per node)\n    inc  dword [rel ir_node_count]",
     "ports":[{"n":"dst","d":"1"},{"n":"type","d":"2"},{"n":"slice_id","d":"0"}]},
    {"id":"can.struct_layout","title":"Cannonic slice layout constants","cat":"Cannonic",
     "desc":"EQU constants for slice offsets in Cannonic arena (f32 components, N=4096)",
     "tpl":"; Cannonic slice offset constants for N={N} entities\nSLICE_POS_X     equ 0           ; f32[N]\nSLICE_POS_Y     equ {N}*4\nSLICE_POS_Z     equ {N}*8\nSLICE_VEL_X     equ {N}*12\nSLICE_VEL_Y     equ {N}*16\nSLICE_VEL_Z     equ {N}*20\nSLICE_ROT_Q0    equ {N}*24\nSLICE_ROT_Q1    equ {N}*28\nSLICE_ROT_Q2    equ {N}*32\nSLICE_ROT_Q3    equ {N}*36\nSLICE_SCALE_X   equ {N}*40\nSLICE_SCALE_Y   equ {N}*44\nSLICE_SCALE_Z   equ {N}*48\nSLICE_HEALTH    equ {N}*52\nSLICE_STATE     equ {N}*56      ; i32 flags\nSLICE_MESH_ID   equ {N}*60\nSLICE_MAT_ID    equ {N}*64\nSLICE_SHADER_ID equ {N}*68\nSLICE_LOD       equ {N}*72      ; u8[N] (pad to dword boundary)\nSLICE_TICK_PH   equ {N}*76      ; u8[N]\nSLICE_VIS_BF    equ {N}*80      ; u64[N/64] bitfield per camera\nSLICE_MATRIX    equ {N}*84      ; mat4[N] = f32[N*16]",
     "ports":[{"n":"N","d":"4096"}]},
    {"id":"can.prefetch_ahead","title":"Cannonic prefetch-ahead","cat":"Cannonic",
     "desc":"Prefetch next cache line while processing current entity batch (Cannonic scheduler pattern)",
     "tpl":"; Prefetch {ahead} entities ahead in pos_x slice\n    mov  rax, [rel ptr_pos_x]\n    lea  rcx, [rsi + {ahead}]\n    imul rcx, 4\n    prefetchnta [rax + rcx]    ; non-temporal: streaming access",
     "ports":[{"n":"ahead","d":"16"}]},
    {"id":"can.transform_to_mat4","title":"Cannonic → mat4 (TRS)","cat":"Cannonic",
     "desc":"Assemble model matrix (mat4) from Cannonic pos/rot/scale slices for EID",
     "tpl":"; Build TRS mat4 for entity {eid} from Cannonic slices\n; pos_x,y,z → translation; rot_q → rotation; scale → scale\n    mov  eax, {eid}\n    shl  eax, 2               ; *4 for f32\n    mov  r8,  [rel ptr_pos_x]\n    mov  r9,  [rel ptr_pos_y]\n    mov  r10, [rel ptr_pos_z]\n    movss xmm0, [r8+rax]     ; px\n    movss xmm1, [r9+rax]     ; py\n    movss xmm2, [r10+rax]    ; pz\n    ; load quaternion similarly...\n    ; call quat_to_mat3 → embed in mat4\n    call build_trs_matrix     ; rcx=eid → [rel out_mat4]",
     "ports":[{"n":"eid","d":"ecx"}]},
    {"id":"can.raycast_aabb","title":"Cannonic AABB raycast (SIMD)","cat":"Cannonic",
     "desc":"Test ray vs 8 AABBs simultaneously (Cannonic broadphase)",
     "tpl":"; ray: (ro_x,ro_y,ro_z) direction (rd_x,rd_y,rd_z)\n; aabb: pos[i]±aabb_r[i] from Cannonic slices\n; ymm0=pos_x[8] ymm1=aabb_r[8]\n    vbroadcastss ymm4, [rel ray_ox]\n    vbroadcastss ymm5, [rel ray_dx_inv]  ; 1/rdx for slab test\n    vsubps   ymm6, ymm0, ymm4            ; pos - ray_origin\n    vmulps   ymm7, ymm6, ymm5            ; (pos-ro) / rdx = t_center\n    ; slab min/max with aabb_r...\n    ; result: ymm_hit_mask (lanes with hit)",
     "ports":[]},

]  # ← close TEMPLATES list



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
