'''
This module allows for swapping out the slot pointers contained in static
classes with the `generic` slot pointers used by python for heap classes.
This allows for assigning arbitrary python functions to static class dunders
using `stitch` and `stitch_cls` and for applying new functionality to previously
unused dunders. A stitched static dunder can be restored to original
functionality using the `unstitch` function
'''

__all__ = ['orig', 'stitch_cls', 'stitch', 'unstitch']

from ctypes import *
import sys

base_size = sizeof(c_ssize_t)
wrapper_type = type(int.__add__)
key_blacklist = vars(type('',(),{})).keys()
stitches = set()

_structs = (
    (type.__sizeof__(type) // base_size, 0),
    (3, 10), # num of fields in tp_as_async
    (36, 12), # num of fields in tp_as_number
    (3, 14), # num of fields in tp_as_mapping
    (10, 13) # num of fields in tp_as_sequence
)

slotmap = {}
_wrappers = set()

for _subcls in object.__subclasses__():
    for _key, _val in vars(_subcls).items():
        if isinstance(_val, wrapper_type):
            _wrapperbase = c_void_p.from_address(id(_val) + (5 * base_size))
            _offset = c_int.from_address(_wrapperbase.value + base_size)
            _wrappers.add((
                _offset.value,
                _val.__name__
            ))

for _offset, _name in _wrappers:
    _last = 0
    for _size, _location in _structs:
        if _last <= _offset < (_last := _last + _size * base_size):
            _locs = slotmap.get(_name, [])
            _item = (
                _size,
                _location * base_size,
                _size - (_last - _offset) // base_size
            )

            if _item not in _locs:
                _locs.append(_item)

            slotmap[_name] = _locs

methods_cache = {}

def orig(self, *args, **kwargs):
    '''
    Inspects the callers frame to deduce the original implmentation of a stitched function
    The original implmentation is then called with all passed arguments
    Not intended to be used outside stitched functions
    '''
    f = sys._getframe(1) # get callers frame
    cls = type(self)
    for key in dir(cls):
        value = getattr(cls, key)
        if getattr(value, '__code__', None) == f.f_code:
            for mcls in cls.mro():
                orig_m = methods_cache.get(f'{id(mcls)}.{key}', None)
                if orig_m:
                    return orig_m(self, *args, **kwargs)
    raise RuntimeError('no original method found')

def getdict(cls):
    '''
    Obtains a writeable dictionary of a classes namespace
    Note that any modifications to this dictionary will need to be followed by a
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
        stitch(cls, is_base=False)(body=attributes)


def stitch_cls_from_cls(cls, pcls, is_base=True):
    '''
    stitches all dunders in `cls` to use the implmentations specified in `pcls`
    '''
    attribute_names = vars(pcls).keys() - key_blacklist
    attributes = {}
    for name in attribute_names:
        stitch_id = f'{id(cls)}.{name}'
        attr = getattr(pcls, name)
        if callable(attr):
            orig_m = getattr(cls, name, None)
            if orig_m and stitch_id not in methods_cache and is_base:
                methods_cache[stitch_id] = orig_m
        attributes[name] = attr
        if is_base:
            stitches.add(stitch_id)
        if name in slotmap:
            slotdata = slotmap[name]
            ocls_ptrs = getptrs(cls, slotdata)
            pcls_ptrs = getptrs(pcls, slotdata)
            for optr, pptr in zip(ocls_ptrs, pcls_ptrs):
                optr.value = pptr.value
    if is_base:
        getdict(cls).update(attributes)
    pythonapi.PyType_Modified(py_object(cls))
    for subcls in type(cls).__subclasses__(cls):
        update_subcls(subcls, pcls)

def stitch_cls(cls, **kwargs):
    '''
    Decorator, allows for the decoration of classes to stitch static classes
    ex:

    @stitch_cls(int)
    class int_stitch:
        attr = ...

        def __add__(self, other):
            ...

    would apply all of the attributes specified in `int_stitch` to `int`
    '''
    def pwrapper(pcls):
        stitch_cls_from_cls(cls, pcls, **kwargs)
    return pwrapper

class P:pass

def stitch(cls, name=None, **kwargs):
    '''
    Decorator, allows for the decoration of functions to stitch a specified dunder on a static class
    ex:

    @stitch(int)
    def __add__(self, other):
        ...

    would set the implmentation of `int.__add__` to the `__add__` specified above
    Note that this function can also be used for non-function attributes,
    however it is recommended to use `stitch_cls` for batch stitches
    '''
    def pwrapper(attr=None, body=None):
        body = body or {}
        if attr is not None:
            nonlocal name
            if name is None:
                name = attr.__name__
            body[name] = attr
        if body:
            stitch_cls_from_cls(cls, type(f'<{id(cls)}>', (P,), body), **kwargs)
    return pwrapper

def unstitch(cls, name):
    '''
    Removes new implmentation on static dunder
    Restores the original implmentation of a static dunder if it exists
    Will also delete non-dunders
    '''
    stitch_id = f'{id(cls)}.{name}'
    if stitch_id in stitches:
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
        stitches.remove(stitch_id)
        pythonapi.PyType_Modified(py_object(cls))
    else:
        raise RuntimeError(f'{cls.__name__}.{name} not stitched')
