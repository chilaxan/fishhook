# Fishhook

This module allows for swapping out the slot pointers contained in static
classes with the **generic** slot pointers used by python for heap classes.
This allows for assigning arbitrary python functions to static class dunders
using *hook* and *hook_cls* and for applying new functionality to previously
unused dunders. A hooked static dunder can be restored to original
functionality using the *unhook* function

it is possible to hook descriptors using *hook.property*, and an example can be seen below

# Calling original methods
`orig(self, *args, **kwargs)` is a special function that looks up the original implementation of a hooked dunder in the methods cache. It will only work properly when used inside a hooked method where an original implementation existed

### hooking single methods
```py
@hook(int)
def __add__(self, other):
  ...
  return orig(self, other)
```

### hooking multiple methods
```py
@hook.cls(int)
class int_hook:
  attr = ...

  def __add__(self, other):
    ...
```

### hooking descriptors
```py
@hook.property(int)
def imag(self):
  ...
  return orig.imag
```

# fishhook.asm

This submodule allows for more in-depth C level hooks.
For obvious reasons, this is vastly unstable, mostly provided as an experiment.
Originally created as a way to grab a reference to the Interned strings dictionary.

```py
from fishhook import asm
from ctypes import py_object, pythonapi

@asm.hook(pythonapi.PyDict_SetDefault, restype=py_object, argtypes=[py_object, py_object, py_object])
def setdefault(self, key, value):
    if key == 'MAGICVAL':
        return self
    return pythonapi.PyDict_SetDefault(self, key, value)

pythonapi.PyUnicode_InternFromString.restype = py_object
interned = pythonapi.PyUnicode_InternFromString(b'MAGICVAL')
```

#### Links

[Github](https://github.com/chilaxan/fishhook)

[PyPi](https://pypi.org/project/fishhook/)
