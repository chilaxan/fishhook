from ctypes import *
import sys
import platform

try:
    import capstone as CS
except ModuleNotFoundError as err:
    if 'distutils' in err.name:
        # capstone depends on distutils and pkg_resources, but in most configs does not need them
        # 3.12+ has deprecated these modules
        # we can stub them out and re-attempt the import
        class stub:
            def __getattr__(self, attr):
                return self
            def __call__(self, *args):
                return ''

        sys.modules['distutils'] = stub()
        sys.modules['distutils.sysconfig'] = stub()
        sys.modules['pkg_resources'] = stub()
        import capstone as CS

try:
    import keystone as KS
except ModuleNotFoundError as err:
    if 'distutils' in err.name:
        # keystone depends on distutils and pkg_resources, but in most configs does not need them
        # 3.12+ has deprecated these modules
        # we can stub them out and re-attempt the import
        class stub:
            def __getattr__(self, attr):
                return self
            def __call__(self, *args):
                return ''

        sys.modules['distutils'] = stub()
        sys.modules['distutils.sysconfig'] = stub()
        sys.modules['pkg_resources'] = stub()
        import keystone as KS

ENDIAN = 'LITTLE' if memoryview(b'\1\0').cast('h')[0]==1 else 'BIG'
BIT_SIZE = sys.maxsize.bit_length() + 1
ARCH = platform.machine().upper()
if ARCH == 'AMD64' or 'X86' in ARCH:
    ARCH = 'X86'

if ARCH == 'AARCH64':
    ARCH = 'ARM64'

assert ARCH in ['ARM64', 'X86'], f'Unsupported/Untested Architecture: {ARCH}'

def maketools():
    cs_arch = getattr(CS, f'CS_ARCH_{ARCH}')
    cs_mode = getattr(CS, f'CS_MODE_{ENDIAN}_ENDIAN')
    ks_arch = getattr(KS, f'KS_ARCH_{ARCH}')
    ks_mode = getattr(KS, f'KS_MODE_{ENDIAN}_ENDIAN')
    if ARCH == 'X86':
        cs_mode += getattr(CS, f'CS_MODE_{BIT_SIZE}')
        ks_mode += getattr(KS, f'KS_MODE_{BIT_SIZE}')

    return CS.Cs(cs_arch, cs_mode), KS.Ks(ks_arch, ks_mode)

DECOMPILER, COMPILER = maketools()

fragements = {
    'ARM64': {
        'push': 'str {0}, [sp, #-16]!;',
        'pop': 'ldr {0}, [sp], #16;',
        'load_const': 'ldr {0}, =0x{1:x};',
        'call': 'blr {0};',
        'ret': 'ret;',

        'store_mem': 'str {0}, [{1}, #0x{2:x}];',
        'read_mem': 'ldr {0}, [{1}, #0x{2:x}];'
    },
    'X86': {
        'push': 'push {0};',
        'pop': 'pop {0};',
        'load_const': 'mov {0}, 0x{1:x};',
        'call': 'call {0};',
        'ret': 'ret;',

        'store_mem': 'mov {3} ptr [{1} + 0x{2:x}], {0};',
        'read_mem': 'mov {0}, {3} ptr [{1} + 0x{2:x}];'
    }
}

def size_to_typ(n):
    if ARCH == 'X86':
        if n == 1:
            return 'BYTE'
        elif n == 2:
            return 'WORD'
        elif n == 4:
            return 'DWORD'
        elif n == 8:
            return 'QWORD'
        else:
            raise RuntimeError(f'x86 register memory size {n} is unsupported')

def inst(n, *args):
    return fragements[ARCH][n].format(*args)

TMP_REG = 'lr' if ARCH == 'ARM64' else 'r15'

def TRAMPOLINE(address, storage=None, registers=()):
    if storage:
        header = footer = inst('load_const', TMP_REG, addressof(storage))
        for register in registers:
            field = getattr(type(storage), register)
            offset = field.offset
            typ = size_to_typ(field.size)
            header += inst('store_mem', register, TMP_REG, offset, typ)
            footer += inst('read_mem', register, TMP_REG, offset, typ)
    else:
        header = footer = ''

    payload = '\n'.join([
        inst('push', TMP_REG),
        header,
        inst('load_const', TMP_REG, address),
        inst('call', TMP_REG),
        footer,
        inst('pop', TMP_REG),
        inst('ret')
    ])

    payload, _ = COMPILER.asm(payload)
    if payload is None:
        raise RuntimeError('unable to build payload')
    return bytes(payload)