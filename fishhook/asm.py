from ctypes import *
import platform

if platform.system() == 'Windows':
    raise RuntimeError('fishhook.asm does not currently work on Windows')

import sys
import weakref
import capstone as CS
import keystone as KS
from ._asm import writeExecutableMemory
from .fishhook import getmem

'''
Allows for hooking internal C funtions and redirecting them to python code
Originally used for grabbing a reference to the CPython interened strings table
'''

ENDIAN = 'LITTLE' if memoryview(b'\1\0').cast('h')[0]==1 else 'BIG'
BIT_SIZE = sys.maxsize.bit_length() + 1
ARCH = platform.machine().upper()
if ARCH == 'AMD64' or 'X86' in ARCH:
    ARCH = 'X86'

if ARCH == 'AARCH64':
    ARCH = 'ARM64'

assert ARCH in ['ARM64', 'X86'], f'Unsupported/Untested Architecture: {ARCH}'

arm64_trampoline = '''
    # we can use lr as our jump register to reduce asm size
    str lr, [sp, #-16]!;        # save lr onto stack, have to use 16 bytes because of arm stack alignment
    ldr lr, =0x{address:x};     # load 64bit address into lr
    blr lr;                     # branch and link to [lr], overwriting [lr] to point next instruction at the same time
    ldr lr, [sp], #16;          # restore lr from stack
    ret;
'''

x86_trampoline = '''
    # we use a callee saved register to ensure we arent corrupting anything downstream
    push r15;                   # save r15 register on stack
    mov r15, 0x{address:x};     # move 64bit address into r15
    call r15;                   # call r15
    pop r15;                    # restore r15
    ret;                        # return to caller
'''

trampolines = {
    'ARM64': arm64_trampoline,
    'X86': x86_trampoline
}

def maketools():
    cs_arch = getattr(CS, f'CS_ARCH_{ARCH}')
    cs_mode = getattr(CS, f'CS_MODE_{ENDIAN}_ENDIAN')
    ks_arch = getattr(KS, f'KS_ARCH_{ARCH}')
    ks_mode = getattr(KS, f'KS_MODE_{ENDIAN}_ENDIAN')
    if ARCH == 'X86':
        cs_mode += getattr(CS, f'CS_MODE_{BIT_SIZE}')
        ks_mode += getattr(KS, f'KS_MODE_{BIT_SIZE}')

    return CS.Cs(cs_arch, cs_mode), KS.Ks(ks_arch, ks_mode), trampolines[ARCH]

DECOMPILER, COMPILER, TRAMPOLINE_ASM = maketools()

def addr(cfunc):
    ptr = c_void_p.from_address(addressof(cfunc))
    return ptr.value

def hook(cfunc, restype=c_int, argtypes=()):
    cfunctype = PYFUNCTYPE(restype, *argtypes)
    cfunc.restype, cfunc.argtypes = restype, argtypes
    o_ptr = addr(cfunc)
    def wrapper(func):
        @cfunctype
        def injected(*args, **kwargs):
            try:
                writeExecutableMemory(mem, default)
                return func(*args, **kwargs)
            finally:
                writeExecutableMemory(mem, jmp)
        n_ptr = addr(injected)
        jmp_b, _ = COMPILER.asm(TRAMPOLINE_ASM.format(address=n_ptr))
        jmp = bytes(jmp_b)
        mem = getmem(o_ptr, len(jmp), 'c')
        default = mem.tobytes()
        writeExecutableMemory(mem, jmp)
        def unhook():
            writeExecutableMemory(mem, default)
        # reset memory back to default if hook is deallocated
        weakref.finalize(injected, unhook)
        injected.unhook = unhook
        return injected
    return wrapper

def get_interned_strings_dict():
    @hook(pythonapi.PyDict_SetDefault, restype=py_object, argtypes=[py_object, py_object, py_object])
    def setdefault(self, key, value):
        if key == 'MAGICVAL':
            return self
        return pythonapi.PyDict_SetDefault(self, key, value)

    pythonapi.PyUnicode_InternFromString.restype = py_object
    interned = pythonapi.PyUnicode_InternFromString(b'MAGICVAL')
    return interned
