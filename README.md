# Stitch

This module allows for swapping out the slot pointers contained in static
classes with the **generic** slot pointers used by python for heap classes.
This allows for assigning arbitrary python functions to static class dunders
using *stitch* and *stitch_cls* and for applying new functionality to previously
unused dunders. A stitched static dunder can be restored to original
functionality using the *unstitch* function
