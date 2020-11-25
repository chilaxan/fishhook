# Fishhook

This module allows for swapping out the slot pointers contained in static
classes with the **generic** slot pointers used by python for heap classes.
This allows for assigning arbitrary python functions to static class dunders
using *hook* and *hook_cls* and for applying new functionality to previously
unused dunders. A hooked static dunder can be restored to original
functionality using the *unhook* function

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
@hook_cls(int)
class int_hook:
  attr = ...

  def __add__(self, other):
    ...
```

#### Links

[Github](https://github.com/chilaxan/fishhook)

[PyPi](https://pypi.org/project/fishhook/)
