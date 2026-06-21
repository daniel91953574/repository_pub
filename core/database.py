"""
Database of Assembly instructions, registers, ASMX macros, and Templates.
"""

REGISTERS = {
    "64-bit": ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"],
    "32-bit": ["eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "r8d", "r9d", "r10d", "r11d", "r12d", "r13d", "r14d", "r15d"],
    "16-bit": ["ax", "bx", "cx", "dx", "si", "di", "bp", "sp", "r8w", "r9w", "r10w", "r11w", "r12w", "r13w", "r14w", "r15w"],
    "8-bit": ["al", "ah", "bl", "bh", "cl", "ch", "dl", "dh", "sil", "dil", "bpl", "spl", "r8b", "r9b", "r10b", "r11b", "r12b", "r13b", "r14b", "r15b"],
    "SIMD": ["xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7", "ymm0", "zmm0"],
}

INSTRUCTIONS = {
    "mov": "Move data", "lea": "Load effective address", "push": "Push onto stack", "pop": "Pop from stack",
    "add": "Add", "sub": "Subtract", "imul": "Signed multiply", "mul": "Unsigned multiply",
    "idiv": "Signed divide", "div": "Unsigned divide", "inc": "Increment", "dec": "Decrement",
    "and": "Bitwise AND", "or": "Bitwise OR", "xor": "Bitwise XOR", "not": "Bitwise NOT",
    "test": "Bitwise AND (sets flags)", "cmp": "Compare (subtract, sets flags)", "jmp": "Unconditional jump",
    "call": "Call procedure", "ret": "Return from procedure", "je": "Jump if equal", "jne": "Jump if not equal",
    "jg": "Jump if greater", "jge": "Jump if greater or equal", "jl": "Jump if less", "jle": "Jump if less or equal",
    "syscall": "Fast system call", "int": "Software interrupt", "nop": "No operation", "hlt": "Halt processor"
}

DIRECTIVES = [
    "bits", "use16", "use32", "use64", "default", "section", "segment", "global", "extern", 
    "align", "db", "dw", "dd", "dq", "resb", "resw", "resd", "resq", "equ", "times"
]

ASMX_MACROS = {
    "@struct": "Define struct: @struct Name: f:type; @end",
    "@macro": "Define macro: @macro name(params) ... @endmacro",
    "@const": "Define constant: @const NAME = value",
    "@vector": "Define array: @vector name, type, count",
}

TEMPLATES = {
    "Windows x64 Console EXE": """\
; Windows x64 Executable - Console Application
bits 64
default rel

section .text
global main
extern ExitProcess

main:
    ; Prologue: Align stack to 16-bytes and allocate 32-bytes shadow space
    push    rbp
    mov     rbp, rsp
    sub     rsp, 32

    ; --- Your code here ---
    
    ; Epilogue & Exit
    xor     ecx, ecx        ; exit code 0
    call    ExitProcess
    
section .data
    ; Initialized data

section .bss
    ; Uninitialized data
""",
    "Windows x64 GDI32 App": """\
; Windows x64 Executable - GDI32 Window
bits 64
default rel

section .text
global main
extern ExitProcess
extern GetModuleHandleA
extern RegisterClassExA
extern CreateWindowExA
extern ShowWindow
extern UpdateWindow
extern GetMessageA
extern TranslateMessage
extern DispatchMessageA
extern DefWindowProcA

main:
    push    rbp
    mov     rbp, rsp
    sub     rsp, 48         ; Reserve shadow space

    ; 1. GetModuleHandleA
    xor     ecx, ecx
    call    GetModuleHandleA
    mov     [rel hInst], rax

    ; 2. RegisterClassExA
    ; Note: Pass a pointer in rcx to the WNDCLASSEX struct!
    ; (Fill the WNDCLASSEX struct in memory first)
    ; lea rcx, [rel wc]
    ; call RegisterClassExA

    ; Exit
    xor     ecx, ecx
    call    ExitProcess

section .bss
    hInst resq 1
"""
}

def get_all_completions():
    completions = []
    for cat, regs in REGISTERS.items():
        for reg in regs:
            completions.append({"word": reg, "kind": "Register", "detail": cat})
    for inst, desc in INSTRUCTIONS.items():
        completions.append({"word": inst, "kind": "Instruction", "detail": desc})
    for direct in DIRECTIVES:
        completions.append({"word": direct, "kind": "Directive", "detail": "NASM Directive"})
        completions.append({"word": "%" + direct, "kind": "Directive", "detail": "NASM Preprocessor"})
    for macro, desc in ASMX_MACROS.items():
        completions.append({"word": macro, "kind": "ASMX", "detail": desc})
    return completions
