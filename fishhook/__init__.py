'''
This module allows for swapping out the slot pointers contained in static
classes with the `generic` slot pointers used by python for heap classes.
This allows for assigning arbitrary python functions to static class dunders
using `hook` and `hook_cls` and for applying new functionality to previously
unused dunders. A hooked static dunder can be restored to original
functionality using the `unhook` function
'''

__all__ = ['orig', 'hook_cls', 'hook', 'unhook']

from ctypes import *
import sys

base_size = sizeof(c_ssize_t)
key_blacklist = vars(type('',(),{})).keys()
hooks = set()

def generate_slotmap(slotmap={}):
    if slotmap:
        return slotmap
    static_size = type.__sizeof__(type) // base_size

    def mem(addr, size):
        return (c_char*size).from_address(addr)

    class _scratch:pass
    _size = type(_scratch).__sizeof__(_scratch)
    _start = id(_scratch)
    _end = _start + _size
    _cls_mem = mem(_start, _size)
    _intermediate = []
    for i in range(0, _size, base_size):
        _val = int.from_bytes(_cls_mem.raw[i:i + base_size], sys.byteorder)
        if _start < _val < _end:
            _intermediate.append((i//base_size, _val))
    _last_addr = None
    _offsets, _sizes = [0], [static_size]
    for _offset, _addr in sorted(_intermediate, key=lambda i:i[1]):
        if _last_addr is not None:
            _sizes.append((_addr - _last_addr)//base_size)
        _offsets.append(_offset)
        _last_addr = _addr
    _sizes.append((_end - _last_addr)//base_size)

    _structs = tuple(zip(_sizes, _offsets))

    _seen = set()
    _wrappers = set()

    for _subcls in object.__subclasses__():
        for _name, _method in vars(_subcls).items():
            if not _name.startswith('__') or not callable(_method) or _name in _seen:
                continue
            _seen.add(_name)
            _oldmem = _cls_mem.raw
            try:
                setattr(_scratch, _name, None)
            except (TypeError, AttributeError) as e:
                continue
            if _oldmem[base_size:] != _cls_mem.raw[base_size:]:
                for i in range(0, len(_oldmem), base_size):
                    ovalue = int.from_bytes(_oldmem[i:i + base_size], sys.byteorder)
                    nvalue = int.from_bytes(_cls_mem.raw[i:i + base_size], sys.byteorder)
                    if ovalue != nvalue and i != 0:
                        _wrappers.add((
                            i,
                            _name
                        ))
                delattr(_scratch, _name)

    for _offset, _name in _wrappers:
        _last = 0
        for _size, _location in _structs:
            _end = _last + _size * base_size
            if _last <= _offset < _end:
                _locs = slotmap.get(_name, ())
                _item = (
                    _size,
                    _location * base_size,
                    _size - (_end - _offset) // base_size
                )

                if _item not in _locs:
                    _locs += (_item,)

                slotmap[_name] = _locs
            _last = _end
    return slotmap

methods_cache = {}

def orig(self, *args, **kwargs):
    '''
    Inspects the callers frame to deduce the original implmentation of a hooked function
    The original implmentation is then called with all passed arguments
    Not intended to be used outside hooked functions
    '''
    f = sys._getframe(1) # get callers frame
    cls = type(self)
    for key in dir(cls):
        value = getattr(cls, key, None)
        if getattr(value, '__code__', None) == f.f_code:
            for mcls in cls.mro():
                orig_m = methods_cache.get(f'{id(mcls)}.{key}', None)
                if orig_m:
                    return orig_m(self, *args, **kwargs)
    raise RuntimeError('no original method found')

def getdict(cls):
    '''
    Obtains a writeable dictionary of a classes namespace
    Note that any modifications to this dictionary should be followed by a
    call to PyType_Modified(cls)
    '''
    cls_dict = cls.__dict__ # hold reference due to `cls.__dict__` being a getter
    if isinstance(cls_dict, dict):
        return cls_dict
    return py_object.from_address(id(cls_dict) + 2 * base_size).value

def getptrs(cls, slotdata):
    '''
    Yields pointers to all slots on `cls` that are referenced by `slotdata`
    Will instantialize any non-existant structs
    '''
    for size, base_addr, secondary_addr in slotdata:
        base_ptr = c_void_p.from_address(id(cls) + base_addr)
        struct_addr = base_ptr.value if base_addr else id(cls)
        if struct_addr:
            func_ptr = c_void_p.from_address(struct_addr + secondary_addr * base_size)
        else:
            new_struct = (c_void_p * size)()
            struct_addr = base_ptr.value = cast(new_struct, c_void_p).value
            func_ptr = c_void_p.from_address(struct_addr + secondary_addr * base_size)
        yield func_ptr

def update_subcls(cls, pcls):
    '''
    Used to update a subclasses slot pointers to those of the base class
    '''
    attributes = {}
    for name in vars(pcls).keys() - key_blacklist:
        if getattr(cls, name) is getattr(pcls, name):
            attributes[name] = getattr(pcls, name)
    if attributes:
        hook(cls, is_base=False)(body=attributes)


def hook_cls_from_cls(cls, pcls, is_base=True):
    '''
    hooks all dunders in `cls` to use the implmentations specified in `pcls`
    '''
    attribute_names = vars(pcls).keys() - key_blacklist
    attributes = {}
    for name in attribute_names:
        hook_id = f'{id(cls)}.{name}'
        attr = getattr(pcls, name)
        if callable(attr):
            orig_m = getattr(cls, name, None)
            if orig_m and hook_id not in methods_cache and is_base:
                methods_cache[hook_id] = orig_m
        if name == '__class_getitem__': #special case (is already bound method, need to rebind)
            mtype = type(attr)
            attr = mtype(attr.__func__, cls)
        attributes[name] = attr
        if is_base:
            hooks.add(hook_id)
        slotdata = generate_slotmap().get(name)
        if slotdata:
            ocls_ptrs = getptrs(cls, slotdata)
            pcls_ptrs = getptrs(pcls, slotdata)
            for optr, pptr in zip(ocls_ptrs, pcls_ptrs):
                optr.value = pptr.value
    if is_base:
        getdict(cls).update(attributes)
    pythonapi.PyType_Modified(py_object(cls))
    for subcls in type(cls).__subclasses__(cls):
        update_subcls(subcls, pcls)

def hook_cls(cls, **kwargs):
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
    def pwrapper(pcls):
        hook_cls_from_cls(cls, pcls, **kwargs)
    return pwrapper

class P:pass

def hook(cls, name=None, **kwargs):
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
    def pwrapper(attr=None, body=None):
        body = body or {}
        if attr is not None:
            nonlocal name
            if name is None:
                name = attr.__name__
            body[name] = attr
        if body:
            hook_cls_from_cls(cls, type(f'<{id(cls)}>', (P,), body), **kwargs)
    return pwrapper

def unhook(cls, name):
    '''
    Removes new implmentation on static dunder
    Restores the original implmentation of a static dunder if it exists
    Will also delete non-dunders
    '''
    hook_id = f'{id(cls)}.{name}'
    if hook_id in hooks:
        cls_dict = getdict(cls)
        del cls_dict[name]
        inherited_dict = {}
        for mcls in cls.mro()[::-1]:
            if mcls != cls:
                inherited_dict.update(vars(mcls))
        for mcls in cls.mro():
            orig_m = methods_cache.pop(f'{id(mcls)}.{name}', None)
            if orig_m:
                if orig_m not in inherited_dict.values():
                    cls_dict[name] = orig_m
                break
        hooks.remove(hook_id)
        pythonapi.PyType_Modified(py_object(cls))
    else:
        raise RuntimeError(f'{cls.__name__}.{name} not hooked')
