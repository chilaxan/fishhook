from ctypes import c_char, pythonapi, py_object
import sys, dis

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

NULL = object()
NOT_FOUND = object()
def build_orig():
    class Cache:
        __slots__ = ['key', 'value']
        def __init__(self, key, value):
            self.key = key
            self.value = value

    def add_cache(func, **kwargs):
        code = func.__code__
        func_copy = type(func)(
            code.replace(co_consts=code.co_consts+tuple(Cache(key, value) for key, value in kwargs.items())),
            func.__globals__,
            name=func.__name__,
            closure=func.__closure__
        )
        func_copy.__defaults__ = func.__defaults__
        func_copy.__kwdefaults__ = func.__kwdefaults__
        func_copy.__qualname__ = func.__qualname__
        return func_copy

    getframe = sys._getframe
    frame_items = vars(type(sys._getframe()))
    get_code = frame_items['f_code'].__get__
    get_back = frame_items['f_back'].__get__
    get_locals = frame_items['f_locals'].__get__
    code_items = vars(type((lambda:0).__code__))
    get_consts = code_items['co_consts'].__get__
    get_varnames = code_items['co_varnames'].__get__
    get_argcount = code_items['co_argcount'].__get__
    get_kwonlyargcount = code_items['co_kwonlyargcount'].__get__
    get_flags = code_items['co_flags'].__get__
    tuple_getitem = tuple.__getitem__
    tuple_iter = tuple.__iter__
    tuple_len = tuple.__len__
    int_add = int.__add__
    int_and = int.__and__
    int_bool = int.__bool__
    dict_get = dict.get
    flags = {v: k for k, v in dis.COMPILER_FLAG_NAMES.items()}.get
    def get_cache(code, key):
        consts = get_consts(code)
        for cache in tuple_iter(tuple_getitem(consts, slice(None, None, -1))):
            if isinstance(cache, Cache):
                if cache.key == key:
                    return cache.value
            else:
                break # caches are injected at end of consts array
        return NOT_FOUND

    def get_cache_trace(key, frame):
        while frame is not None:
            code = get_code(frame)
            if (val := get_cache(code, key)) is not NOT_FOUND:
                if val is NULL:
                    raise RuntimeError('original implementation not found')
                return val
            frame = get_back(frame)
        raise RuntimeError('orig used incorrectly')

    def get_self(frame):
        co = get_code(frame)
        locals = get_locals(frame)
        names = get_varnames(co)
        nargs = get_argcount(co)
        nkwargs = get_kwonlyargcount(co)
        args = tuple_getitem(names, slice(None, nargs, None))
        nargs = int_add(nargs, nkwargs)
        varargs = None
        if int_bool(int_and(get_flags(co), flags('VARARGS'))):
            varargs = tuple_getitem(names, nargs)
        argvals = tuple(dict_get(locals, arg, NULL) for arg in tuple_iter(args))+dict_get(locals, varargs, ())
        if int_bool(tuple_len(argvals)) and (self := tuple_getitem(argvals, 0)) is not NULL:
            return self
        raise RuntimeError('unable to bind self')

    class Orig:
        '''
        Inspects the callers frame to deduce the original implementation of a hooked function
        The original implementation is then called with all passed arguments
        Not intended to be used outside hooked functions
        '''
        def __call__(self, *args, **kwargs):
            return get_cache_trace('orig', getframe(1))(*args, **kwargs)

        def __getattr__(self, attr):
            frame = getframe(1)
            orig_attr = get_cache_trace('orig', frame)
            attr_name = get_cache_trace('attr_name', frame)
            if attr_name != attr:
                raise AttributeError('attribute not currently bound to \'orig\'')
            return orig_attr.__get__(get_self(frame))

        def __setattr__(self, attr, value):
            frame = getframe(1)
            orig_attr = get_cache_trace('orig', frame)
            attr_name = get_cache_trace('attr_name', frame)
            if attr_name != attr:
                raise AttributeError('attribute not currently bound to \'orig\'')
            return orig_attr.__set__(get_self(frame), value)

        def __delattr__(self, attr):
            frame = getframe(1)
            orig_attr = get_cache_trace('orig', frame)
            attr_name = get_cache_trace('attr_name', frame)
            if attr_name != attr:
                raise AttributeError('attribute not currently bound to \'orig\'')
            return orig_attr.__delete__(get_self(frame))

    return Orig(), add_cache, get_cache

orig, add_cache, get_cache = build_orig()
del build_orig

def hook(cls, name=None, func=None):
    '''
    Decorator, allows for the decoration of functions to hook a specified dunder on a static class
    ex:

    @hook(int)
    def __add__(self, other):
        ...

    would set the implementation of `int.__add__` to the `__add__` specified above
    '''
    def wrapper(func):
        nonlocal name
        code = func.__code__
        name = name or code.co_name
        orig_val = vars(cls).get(name, NULL)
        force_setattr(cls, name, add_cache(func, orig=orig_val))
        return func
    if func:
        return wrapper(func)
    return wrapper

def unhook(cls, name):
    '''
    Removes new implementation on static dunder
    Restores the original implementation of a static dunder if it exists
    Will also delete non-dunders
    '''
    current = getattr(cls, name)
    if isinstance(current, property):
        for func in [current.fget, current.fset, current.fdel]:
            if hasattr(func, '__code__'):
                current = func
                break
    if not hasattr(current, '__code__'):
        raise RuntimeError('not hooked')
    orig_val = get_cache(current.__code__, 'orig')
    if orig_val is NOT_FOUND:
        raise RuntimeError('not hooked')
    if orig_val is not NULL:
        force_setattr(cls, name, orig_val)
    else:
        force_delattr(cls, name)

class hook_property:
    '''
    Descriptor, allows for hooking a specified descriptor on a class
    ex:

    @hook.property(int)
    def imag(self):
        ...

    @imag.setter
    def imag_setter(self, value):
        ...

    would set the implementation of `int.imag.__get__` to the `imag` specified above
    and set `int.imag.__set__` to `imag_setter`
    '''
    def __init__(self, cls, name=None, fget=None, fset=None, fdel=None):
        self.cls = cls
        self.prop = property()
        self.name = name
        self.__orig = NULL
        if fget:
            self.getter(fget)
        if fset:
            self.setter(fset)
        if fdel:
            self.deleter(fdel)

    def __prep(self, func):
        prop = self.prop
        names = [self.name] + [func.__name__] + [p.__name__ for p in [prop.fget, prop.fset, prop.fdel] if p]
        for name in names:
            if name is not None:
                self.name = name
                break
        orig = vars(self.cls).get(self.name, NULL)
        if self.__orig is NULL and orig is not prop and orig is not NULL:
            self.__orig = orig
        return add_cache(func, orig=self.__orig, attr_name=self.name)

    def __set_prop(self, prop):
        if self.name is None:
            raise RuntimeError('Invalid Hook')
        if self.__orig is not NULL:
            if prop.fget is None:
                prop = prop.getter(self.__orig.__get__)
            if prop.fset is None:
                prop = prop.setter(self.__orig.__set__)
            if prop.fdel is None:
                prop = prop.deleter(self.__orig.__delete__)
        force_setattr(self.cls, self.name, prop)
        self.prop = prop

    def __call__(self, func):
        return self.getter(func)

    def getter(self, func):
        self.fget = self.__prep(func)
        self.__set_prop(self.prop.getter(self.fget))
        return self

    def setter(self, func):
        self.fset = self.__prep(func)
        self.__set_prop(self.prop.setter(self.fset))
        return self

    def deleter(self, func):
        self.fdel = self.__prep(func)
        self.__set_prop(self.prop.deleter(self.fdel))
        return self

hook.property = hook_property

def hook_cls(cls, ncls=None):
    '''
    Decorator, allows for the decoration of classes to hook static classes
    ex:

    @hook.cls(int)
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
            elif isinstance(value, property):
                setattr(ncls, attr, hook_property(cls, name=attr, fget=value.fget, fset=value.fset, fdel=value.fdel))
            elif isinstance(value, type(lambda:0)):
                hook(cls, name=attr, func=value)
            else:
                force_setattr(cls, attr, value)
        return ncls
    if ncls:
        return wrapper(ncls)
    return wrapper

hook.cls = hook_cls
