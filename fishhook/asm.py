from ctypes import *
import platform
import weakref

if platform.system() == 'Windows':
    raise RuntimeError('fishhook.asm does not currently work on Windows')

from ._asm import writeExecutableMemory
from .fishhook import getmem
from .jit import TRAMPOLINE

'''
Allows for hooking internal C funtions and redirecting them to python code
Originally used for grabbing a reference to the CPython interened strings table
'''

def addr(cfunc):
    ptr = c_void_p.from_address(addressof(cfunc))
    return ptr.value

def make_storage(**registers):
    class _RegisterStorage(Structure):
        pass
    _RegisterStorage._fields_ = [
        (register, typ) for register, typ in registers.items()
    ]
    return _RegisterStorage()

def hook(cfunc, restype=c_int, argtypes=(), registers=None):
    cfunctype = PYFUNCTYPE(restype, *argtypes)
    cfunc.restype, cfunc.argtypes = restype, argtypes
    o_ptr = addr(cfunc)
    def wrapper(func):
        if registers:
            storage = make_storage(**registers)
            regs = list(registers)
        else:
            storage = None
            regs = ()
        @cfunctype
        def injected(*args, **kwargs):
            try:
                writeExecutableMemory(mem, default)
                if storage:
                    kwargs['registers'] = storage
                return func(*args, **kwargs)
            finally:
                writeExecutableMemory(mem, trampoline)
        n_ptr = addr(injected)
        trampoline = TRAMPOLINE(n_ptr, storage, regs)
        mem = getmem(o_ptr, len(trampoline), 'c')
        default = mem.tobytes()
        writeExecutableMemory(mem, trampoline)
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
