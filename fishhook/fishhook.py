from ctypes import c_char, pythonapi, py_object
import sys, dis, types

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
    if type(o).__flags__ & (1 << 31):
        return o
    else:
        raise RuntimeError('Invalid class or object')

def build_unlock_lock():
    flag_offset = find_offset(getmem(int), int.__flags__)

    def unlock(cls):
        cls_mem = allocate_structs(assert_cls(cls))
        flags = cls.__flags__
        try:
            return flags, cls_mem[tp_dict_offset] == 0
        finally:
            if sys.version_info[0:2] <= (3, 9):
                cls_mem[flag_offset] |= Py_TPFLAGS_HEAPTYPE
            elif sys.version_info[0:2] >= (3, 10):
                cls_mem[flag_offset] &= ~Py_TPFLAGS_IMMUTABLE

    def lock(cls, flags=None, should_have_null_tp_dict=False):
        cls_mem = getmem(assert_cls(cls))
        if should_have_null_tp_dict:
            materialized_dict_addr = cls_mem[tp_dict_offset]
            if materialized_dict_addr:
                cls_mem[tp_dict_offset] = 0
                getmem(materialized_dict_addr, 8)[0] -= 1 # clear materialized dict
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

class Template:pass
template_mem = getmem(Template)
tp_base_offset = find_offset(template_mem, id(Template.__base__))
tp_basicsize_offset = find_offset(template_mem, Template.__basicsize__)
tp_flags_offset = find_offset(template_mem, Template.__flags__)
tp_dict_offset = find_offset(template_mem, id(getdict(Template)))
tp_bases_offset = find_offset(template_mem, id(Template.__bases__))

def patch_object():
    '''
    adds fake class to inheritance chain so that object can be modified
    also patches type.__base__ to never return fake class
    in theory is safe, if not, possible alternative would be injecting a class
    into all lookups by modifying type.__bases__?
    '''

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
        flags, should_have_null_tp_dict = unlock(cls)
        if should_have_null_tp_dict: # added for 3.12+ to ensure consistent state
            getdict(cls)[attr] = value
        setattr(cls, attr, value)
    finally:
        lock(cls, flags, should_have_null_tp_dict)
        pythonapi.PyType_Modified(py_object(cls))

def force_delattr(cls, attr):
    flags = None
    try:
        flags, should_have_null_tp_dict = unlock(cls)
        if should_have_null_tp_dict: # added for 3.12+ to ensure consistent state
            del getdict(cls)[attr]
        try:
            delattr(cls, attr)
        except AttributeError:
            # for some attributes that are not cached
            # delattr does not have a consistent state
            # luckily, seems like we can ignore it
            pass
    finally:
        lock(cls, flags, should_have_null_tp_dict)
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
    locals_get = type(get_locals(getframe())).get # this enables compat with >= 3.13 FrameLocalsProxy
    flags = {v: k for k, v in dis.COMPILER_FLAG_NAMES.items()}.get
    new_slice = slice.__call__
    get_class = vars(object)['__class__'].__get__
    str_equals = str.__eq__
    def get_cache(code, key):
        consts = get_consts(code)
        for cache in tuple_iter(tuple_getitem(consts, new_slice(None, None, -1))):
            if cache is not None and get_class(cache) is Cache:
                if str_equals(cache.key, key):
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
        args = tuple_getitem(names, new_slice(None, nargs, None))
        nargs = int_add(nargs, nkwargs)
        varargs = None
        if int_bool(int_and(get_flags(co), flags('VARARGS'))):
            varargs = tuple_getitem(names, nargs)
        argvals = (*(locals_get(locals, arg, NULL) for arg in tuple_iter(args)), *locals_get(locals, varargs, ()))
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

def reduce_classes(*cls):
    for c in cls:
        if hasattr(types, 'UnionType') and isinstance(c, types.UnionType):
            yield from reduce_classes(*c.__args__)
        elif hasattr(types, 'GenericAlias') and isinstance(c, types.GenericAlias):
            yield from reduce_classes(c.__origin__)
        else:
            yield c

def hook(_cls, *more_classes,  name=None, func=None):
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
        for cls in reduce_classes(_cls, *more_classes):
            if isinstance(func, (classmethod, staticmethod)):
                code = func.__func__.__code__
            else:
                code = func.__code__
            name = name or code.co_name
            orig_val = vars(cls).get(name, NULL)
            if isinstance(func, classmethod):
                new_func = classmethod(add_cache(func.__func__, orig=orig_val))
            elif isinstance(func, staticmethod):
                new_func = staticmethod(add_cache(func.__func__, orig=orig_val))
            else:
                new_func = add_cache(func, orig=orig_val)
            force_setattr(cls, name, new_func)
        return func
    if func:
        return wrapper(func)
    return wrapper

def unhook(cls, name):
    '''
    Removes new implementation on static dunder
    Restores the original implementation of a static dunder if it exists
    '''
    current = vars(cls).get(name)
    if isinstance(current, (classmethod, staticmethod)):
        current = current.__func__
    if isinstance(current, property):
        for func in [current.fget, current.fset, current.fdel]:
            if hasattr(func, '__code__'):
                current = func
                break
    if not hasattr(current, '__code__'):
        raise RuntimeError(f'{cls.__name__}.{name} not hooked')
    orig_val = get_cache(current.__code__, 'orig')
    if orig_val is NOT_FOUND or name not in vars(cls):
        raise RuntimeError(f'{cls.__name__}.{name} not hooked')
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

class classproperty(property):
    def __get__(self, owner_self, owner_cls):
        return self.fget(owner_cls)

def hook_var(cls, name, value):
    '''
    Allows for easy hooking of static class variables
    '''
    def prop(_):
        return value
    prop = add_cache(prop, orig=vars(cls).get(name, NULL))
    force_setattr(cls, name, classproperty(prop))

hook.var = hook_var

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
            elif isinstance(value, (type(lambda:0), classmethod, staticmethod)):
                hook(cls, name=attr, func=value)
            else:
                hook_var(cls, attr, value)
        return ncls
    if ncls:
        return wrapper(ncls)
    return wrapper

hook.cls = hook_cls
