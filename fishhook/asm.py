from ctypes import util
from ctypes import *
import atexit

'''
Allows for hooking internal C funtions and redirecting them to python code
Originally used for grabbing a reference to the CPython interened strings table
'''

base_size = sizeof(c_void_p)
libc = cdll.LoadLibrary(util.find_library('c'))

PAGE_SIZE = libc.getpagesize()
MEM_READ = 1
MEM_WRITE = 2
MEM_EXEC = 4
ENDIAN = 'little' if memoryview(b'\1\0').cast('h')[0]==1 else 'big'

libc.mprotect.argtypes = (c_void_p, c_size_t, c_int)
libc.mprotect.restype = c_int

def mprotect(addr, size, flags):
    addr_align = addr & ~(PAGE_SIZE - 1)
    mem_end = (addr + size) & ~(PAGE_SIZE - 1)
    if (addr + size) > mem_end:
        mem_end += PAGE_SIZE
    memlen = mem_end - addr_align
    libc.mprotect(addr_align, memlen, flags)

def addr(cfunc):
    ptr = c_void_p.from_address(addressof(cfunc))
    return ptr.value

def hook(cfunc, restype=c_int, argtypes=()):
    cfunctype = PYFUNCTYPE(restype, *argtypes)
    cfunc.restype, cfunc.argtypes = restype, argtypes
    o_ptr = addr(cfunc)
    mprotect(o_ptr, 5, MEM_READ | MEM_WRITE | MEM_EXEC)
    mem = (c_ubyte*5).from_address(o_ptr)
    default = mem[:]
    def wrapper(func):
        @cfunctype
        def injected(*args, **kwargs):
            try:
                mem[:] = default
                return func(*args, **kwargs)
            finally:
                mem[:] = jmp
        n_ptr = addr(injected)
        offset = n_ptr - o_ptr - 5
        jmp = b'\xe9' + (offset & ((1 << 32) - 1)).to_bytes(4, ENDIAN)
        mem[:] = jmp
        @atexit.register
        def unhook():
            mem[:] = default
        injected.unhook = unhook
        return injected
    return wrapper
