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

int_frombytes = int.from_bytes

base_size = sizeof(c_ssize_t)
key_blacklist = {'__weakref__', '__module__'}
hooks = set()

def generate_slotmap(slotmap={}):
    if slotmap:
        return slotmap
    static_size = type.__sizeof__(type) // base_size

    def mem(addr, size):
        return (c_char*size).from_address(addr)

    class scratch:
        __slots__ = ()
    size = type(scratch).__sizeof__(scratch)
    start = id(scratch)
    end = start + size
    cls_mem = mem(start, size)
    intermediate = []
    for i in range(0, size, base_size):
        val = int_frombytes(cls_mem.raw[i:i + base_size], sys.byteorder)
        if start < val < end:
            intermediate.append((i//base_size, val))
    last_addr = None
    offsets, sizes = [0], [static_size]
    for offset, addr in sorted(intermediate, key=lambda i:i[1]):
        if last_addr is not None:
            sizes.append((addr - last_addr)//base_size)
        offsets.append(offset)
        last_addr = addr
    sizes.append((end - last_addr)//base_size)

    structs = tuple(zip(sizes, offsets))

    seen = set()
    wrappers = set()

    for subcls in object.__subclasses__():
        for name, method in vars(subcls).items():
            if not name.startswith('__') or name in seen:
                continue
            seen.add(name)
            oldmem = cls_mem.raw
            try:
                setattr(scratch, name, None)
            except (TypeError, AttributeError) as e:
                continue
            if oldmem[base_size:] != cls_mem.raw[base_size:]:
                for i in range(0, len(oldmem), base_size):
                    ovalue = int_frombytes(oldmem[i:i + base_size], sys.byteorder)
                    nvalue = int_frombytes(cls_mem.raw[i:i + base_size], sys.byteorder)
                    if ovalue != nvalue and i != 0:
                        wrappers.add((
                            i,
                            name
                        ))
                delattr(scratch, name)

    for offset, name in wrappers:
        last = 0
        for size, location in structs:
            end = last + size * base_size
            if last <= offset < end:
                locs = slotmap.get(name, ())
                item = (
                    size,
                    location * base_size,
                    size - (end - offset) // base_size
                )

                if item not in locs:
                    locs += (item,)

                slotmap[name] = locs
            last = end
    slotmap["__doc__"] = ((8, 0, 22),) # This
    slotmap["__dict__"] = ((8, 0, 33),) # is
    slotmap["__name__"] = ((8, 0, 3),) # The Ultimate Showdown
    slotmap["__basicsize__"] = ((8, 0, 4),) # of
    slotmap["__itemsize__"] = ((8, 0, 5),) # Ultimate Destiny
    return slotmap

methods_cache = {}
attr_cache = {}

def orig(self, *args, **kwargs):
    '''
    Inspects the callers frame to deduce the original implmentation of a hooked function
    The original implmentation is then called with all passed arguments
    Not intended to be used outside hooked functions
    '''
    f = sys._getframe(1) # get callers frame
    name = args[0]
    cls = type(self)
    for key in dir(cls):
        value = getattr(cls, key, None)
        if getattr(value, '__code__', None) == f.f_code:
            for mcls in cls.mro():
                orig_m = methods_cache.get(f'{id(mcls)}.{key}', None)
                if orig_m:
                    return orig_m(self, *args, **kwargs)
        elif hasattr(value, '__code__') is False and key == name:
            for scls in cls.mro():
                orig_a = attr_cache.get(f'{id(scls)}.{key}', None)
                if orig_a:
                    return orig_a
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

modifyables = {'__name__', '__doc__'}
class CP:
    __slots__ = 'value'
    def __init__(self, x):
        self.value = x

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
        else:
            orig_a = getattr(cls, name, None)
            if orig_a and hook_id not in attr_cache and is_base:
                attr_cache[hook_id] = orig_a
        if name == '__class_getitem__': #special case (is already bound method, need to rebind)
            mtype = type(attr)
            attr = mtype(attr.__func__, cls)
        attributes[name] = attr
        if is_base:
            hooks.add(hook_id)
        if name == '__doc__' and attr:
            c_char_p.from_address(id(cls) + 176).value = c_void_p.from_address(id(pcls) + 176).value
        elif name == '__dict__':
            py_object.from_address(id(cls) + 264).value = pcls.__dict__['__dict__']
        elif name == '__name__':
            c_char_p.from_address(id(cls) + 24).value = pcls.__dict__['__name__'].encode()
        elif name == '__basicsize__':
            c_ssize_t.from_address(id(cls) + 32).value = pcls.__dict__['__basicsize__']
        elif name == '__itemsize__':
            c_ssize_t.from_address(id(cls) + 40).value = pcls.__dict__['__itemsize__']
        else:
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
            else:
                orig_a = attr_cache.pop(f'{id(mcls)}.{name}', None)
                if orig_a:
                    if name == '__dict__':
                        cls_dict.clear()
                        cls_dict.update(orig_a)
                    elif name == '__doc__':
                        class A:
                           __doc__ = orig_a
                        c_char_p.from_address(id(cls) + 176).value = c_void_p.from_address(id(A) + 176).value
                    elif name == '__name__':
                        c_char_p.from_address(id(cls) + 24).value = orig_a.encode()
                    elif name == '__basicsize__':
                        c_char_p.from_address(id(cls) + 32).value = orig_a
                    elif name == '__itemsize__':
                        c_char_p.from_address(id(cls) + 40).value = orig_a
                    cls_dict[name] = orig_a
                    
        hooks.remove(hook_id)
        pythonapi.PyType_Modified(py_object(cls))
    else:
        raise RuntimeError(f'{cls.__name__}.{name} not hooked')

'''
Here's a comment:
Since the orig_a and orig_m do the same thing to methods and
non-methods (respectively), shouldn't they be combined?
'''
