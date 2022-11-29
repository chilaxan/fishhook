'''
This module allows for swapping out the slot pointers contained in static
classes with the `generic` slot pointers used by python for heap classes.
This allows for assigning arbitrary python functions to static class dunders
using `hook` and `hook_cls` and for applying new functionality to previously
unused dunders. A hooked static dunder can be restored to original
functionality using the `unhook` function
'''

__all__ = ['orig', 'hook_cls', 'hook', 'unhook']

from ctypes import c_char, pythonapi, py_object
import sys

BYTES_HEADER = bytes.__basicsize__ - 1

Py_TPFLAGS_IMMUTABLE = 1 << 8
Py_TPFLAGS_HEAPTYPE = 1 << 9
Py_TPFLAGS_READY = 1 << 12

def sizeof(obj):
    return type(obj).__sizeof__(obj)

TYPE_BASICSIZE = sizeof(type)

def getmem(obj_or_addr, size=None, fmt='P'):
    if size is None:
        if isinstance(obj_or_addr, type):
            # type(cls).__sizeof__(cls) calls a function with a heaptype member
            # if cls is currently unlocked and member happens to overlap with other data
            # crash occurs
            # we avoid this by hardcoding TYPE_BASICSIZE
            size = TYPE_BASICSIZE
        else:
            size = sizeof(obj_or_addr)
        addr = id(obj_or_addr)
    else:
        addr = obj_or_addr
    return memoryview((c_char*size).from_address(addr)).cast('c').cast(fmt)

def alloc(size, _storage=[]):
    _storage.append(bytes(size))
    return id(_storage[-1]) + BYTES_HEADER

def get_structs(htc=type('',(),{'__slots__':()})):
    htc_mem = getmem(htc)
    last = None
    for ptr, idx in sorted([(ptr, idx) for idx, ptr in enumerate(htc_mem)
            if id(htc) < ptr < id(htc) + sizeof(htc)]):
        if last:
            offset, lp = last
            yield offset, ptr - lp
        last = idx, ptr

def allocate_structs(cls):
    cls_mem = getmem(cls)
    for subcls in type(cls).__subclasses__(cls):
        allocate_structs(subcls)
    for offset, size in get_structs():
        cls_mem[offset] = cls_mem[offset] or alloc(size)
    return cls_mem

def find_offset(mem, val):
    return [*mem].index(val)

def assert_cls(o):
    if isinstance(o, type):
        return o
    else:
        raise RuntimeError('Invalid class or object')

def build_unlock_lock():
    flag_offset = find_offset(getmem(int), int.__flags__)

    def unlock(cls):
        cls_mem = allocate_structs(assert_cls(cls))
        flags = cls.__flags__
        try:
            return flags
        finally:
            if sys.version_info[0:2] <= (3, 9):
                cls_mem[flag_offset] |= Py_TPFLAGS_HEAPTYPE
            elif sys.version_info[0:2] >= (3, 10):
                cls_mem[flag_offset] &= ~Py_TPFLAGS_IMMUTABLE

    def lock(cls, flags=None):
        cls_mem = getmem(assert_cls(cls))
        if flags is None:
            if sys.version_info[0:2] <= (3, 9):
                cls_mem[flag_offset] &= ~Py_TPFLAGS_HEAPTYPE
            elif sys.version_info[0:2] >= (3, 10):
                cls_mem[flag_offset] |= Py_TPFLAGS_IMMUTABLE
        else:
            cls_mem[flag_offset] = flags

    return unlock, lock

unlock, lock = build_unlock_lock()

def getdict(cls, E=type('',(),{'__eq__':lambda s,o:o})()):
    '''
    Obtains a writeable dictionary of a classes namespace
    Note that any modifications to this dictionary should be followed by a
    call to PyType_Modified(cls)
    '''
    return cls.__dict__ == E

def newref(obj):
    getmem(obj)[0] += 1
    return obj

def patch_object():
    '''
    adds fake class to inheritance chain so that object can be modified
    also patches type.__base__ to never return fake class
    in theory is safe, if not, possible alternative would be injecting a class
    into all lookups by modifying type.__bases__?
    '''

    int_mem = getmem(int)
    tp_base_offset = find_offset(int_mem, id(int.__base__))
    tp_basicsize_offset = find_offset(int_mem, int.__basicsize__)
    tp_flags_offset = find_offset(int_mem, int.__flags__)
    tp_dict_offset = find_offset(int_mem, id(getdict(int)))
    tp_bases_offset = find_offset(int_mem, id(int.__bases__))
    fake_addr = alloc(sizeof(object))
    fake_mem = getmem(fake_addr, sizeof(object))
    fake_mem[0] = 1
    fake_mem[1] = id(newref(type))
    fake_mem[3] = alloc(0)
    fake_mem[tp_flags_offset] = Py_TPFLAGS_READY | Py_TPFLAGS_IMMUTABLE
    fake_mem[tp_dict_offset] = id(newref({}))
    fake_mem[tp_bases_offset] = id(newref(()))
    fake_mem[tp_basicsize_offset] = object.__basicsize__
    getmem(object)[tp_base_offset] = fake_addr

    # custom __base__ to protect fake super class
    # also restores original `__base__` functionality
    @property
    def __base__(self, object=object, orig=vars(type)['__base__'].__get__):
        if self is object:
            return None
        return orig(self)

    getdict(type)['__base__'] = __base__
    # call PyType_Modified to reload cache
    pythonapi.PyType_Modified(py_object(type))

# needed to allow for `unlock(object)` to be stable
patch_object()

NULL = object()
class Dunder:
    __slots__ = ['value']
    def __init__(self, value):
        self.value = value

def build_orig():
    _orig_cache = {}
    getframe = sys._getframe
    get_type = vars(object)['__class__'].__get__
    get_code = vars(type(getframe()))['f_code'].__get__
    get_consts = vars(type(getframe().f_code))['co_consts'].__get__
    get_bases = vars(type)['__bases__'].__get__
    tuple_add = tuple.__add__
    tuple_iter = tuple.__iter__
    tuple_len = tuple.__len__
    tuple_getitem = tuple.__getitem__
    dict_contains = dict.__contains__
    dict_getitem = dict.__getitem__
    dict_setitem = dict.__setitem__
    dict_pop = dict.pop
    dict_get = dict.get
    def orig(self, *args, **kwargs):
        '''
        Inspects the callers frame to deduce the original implmentation of a hooked function
        The original implmentation is then called with all passed arguments
        Not intended to be used outside hooked functions
        '''
        frame = getframe(1)
        s_c = get_type(self)
        key = get_code(frame)
        consts = get_consts(key)
        if tuple_len(consts) and get_type(dunder := tuple_getitem(consts, -1)) is Dunder:
            name = dunder.value
        else:
            raise RuntimeError('orig called outside of hook')
        for c in tuple_iter(tuple_add((s_c,), get_bases(s_c))):
            if dict_contains(_orig_cache, (c, name, key)):
                if (func := dict_get(_orig_cache, (c, name, key))) is not NULL:
                    return func(self, *args, **kwargs)
                else:
                    break
        raise RuntimeError('original implementation not found')

    def add_to_cache(cls, name, key, func):
        dict_setitem(_orig_cache, (cls, name, key), func)

    def remove_from_cache(cls, name, key):
        dict_pop(_orig_cache, (cls, name, key), None)

    def get_from_cache(cls, name, key):
        return dict_get(_orig_cache, (cls, name, key))

    return orig, add_to_cache, get_from_cache, remove_from_cache

orig, add_to_cache, get_from_cache, remove_from_cache = build_orig()

def force_setattr(cls, attr, value):
    flags = None
    try:
        flags = unlock(cls)
        setattr(cls, attr, value)
    finally:
        lock(cls, flags)
        pythonapi.PyType_Modified(py_object(cls))

def force_delattr(cls, attr):
    flags = None
    try:
        flags = unlock(cls)
        delattr(cls, attr)
    finally:
        lock(cls, flags)
        pythonapi.PyType_Modified(py_object(cls))

def hook(cls, name=None, func=None):
    '''
    Decorator, allows for the decoration of functions to hook a specified dunder on a static class
    ex:

    @hook(int)
    def __add__(self, other):
        ...

    would set the implmentation of `int.__add__` to the `__add__` specified above
    Note that this function can also be used for non-function attributes,
    however it is recommended to use `hook_cls` for batch hooks
    '''
    def wrapper(func):
        nonlocal name
        code = func.__code__
        name = name or code.co_name
        orig = vars(cls).get(name, NULL)
        func_copy = type(func)(
            code.replace(co_consts=code.co_consts+(Dunder(name),)),
            func.__globals__,
            name=func.__name__,
            closure=func.__closure__
        )
        add_to_cache(cls, name, func_copy.__code__, orig)
        func_copy.__defaults__ = func.__defaults__
        func_copy.__kwdefaults__ = func.__kwdefaults__
        func_copy.__qualname__ = func.__qualname__
        force_setattr(cls, name, func_copy)
        return func
    if func:
        return wrapper(func)
    return wrapper

def unhook(cls, name):
    '''
    Removes new implmentation on static dunder
    Restores the original implmentation of a static dunder if it exists
    Will also delete non-dunders
    '''
    current = getattr(cls, name)
    orig_val = get_from_cache(cls, name, current.__code__)
    if orig_val is not NULL:
        force_setattr(cls, name, orig_val)
    else:
        force_delattr(cls, name)
    remove_from_cache(cls, name, current.__code__)

def hook_cls(cls, ncls=None):
    '''
    Decorator, allows for the decoration of classes to hook static classes
    ex:

    @hook_cls(int)
    class int_hook:
        attr = ...

        def __add__(self, other):
            ...

    would apply all of the attributes specified in `int_hook` to `int`
    '''
    def wrapper(ncls):
        key_blacklist = vars(type('',(),{})).keys()
        for (attr, value) in sorted(vars(ncls).items(), key=lambda v:callable(v[1]), reverse=True):
            if attr in key_blacklist:
                continue
            if callable(value):
                try:
                    hook(cls, name=attr, func=value)
                    continue
                except RuntimeError:
                    pass
            force_setattr(cls, attr, value)
    if ncls:
        return wrapper(ncls)
    return wrapper
