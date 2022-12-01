import unittest
from . import hook, unhook, orig, force_delattr

class TestFishhook(unittest.TestCase):
    def test_hook_dunder(self):
        SENTINEL = object()
        @hook(int)
        def __matmul__(self, other):
            return SENTINEL

        self.assertIs(1 @ 2, SENTINEL)
        unhook(int, '__matmul__')
        with self.assertRaises(AttributeError):
            int.__matmul__

    def test_hook_unhook(self):
        orig_val = int.__add__
        @hook(int)
        def __add__(self, other):
            return orig(self, other)

        self.assertNotEqual(int.__add__, orig_val)
        unhook(int, '__add__')
        self.assertEqual(int.__add__, orig_val)

    def test_orig(self):
        HOOK_RAN = False
        @hook(int)
        def __add__(self, other):
            nonlocal HOOK_RAN
            HOOK_RAN = True
            return orig(self, other)

        a = 1 # prevents add from being optimized away
        self.assertEqual(a + 2, 3)
        self.assertTrue(HOOK_RAN)
        unhook(int, '__add__')

    def test_nested_hooks(self):
        HOOK_1_RAN = None
        HOOK_2_RAN = None

        @hook(int, name='__add__')
        def hook1(self, other):
            nonlocal HOOK_1_RAN
            HOOK_1_RAN = True
            return orig(self, other)

        @hook(int, name='__add__')
        def hook2(self, other):
            nonlocal HOOK_2_RAN
            HOOK_2_RAN = True
            return orig(self, other)

        HOOK_1_RAN = HOOK_2_RAN = False

        a = 1
        self.assertEqual(a + 2, 3)
        self.assertTrue(HOOK_1_RAN)
        self.assertTrue(HOOK_2_RAN)
        self.assertEqual(int.__add__.__name__, 'hook2')
        unhook(int, '__add__')
        self.assertEqual(int.__add__.__name__, 'hook1')
        unhook(int, '__add__')

    def test_nested_orig(self):
        def call(f, *args, **kwargs):
            return f(*args, **kwargs)

        @hook(int)
        def __add__(self, other):
            return call(orig, self, other)

        a = 1
        self.assertEqual(a + 2, 3)
        unhook(int, '__add__')

    def test_property_hook(self):
        orig_imag = int.imag
        SENTINEL = object()
        @hook.property(int)
        def imag(self):
            return SENTINEL

        self.assertIs(1 .imag, SENTINEL)
        self.assertEqual(orig_imag.__set__, int.imag.fset)
        unhook(int, 'imag')
        self.assertEqual(int.imag, orig_imag)

    def test_property_orig(self):
        orig_1_numerator = (1).numerator
        HOOK_RAN = False

        @hook.property(int)
        def numerator(self):
            nonlocal HOOK_RAN
            HOOK_RAN = True
            return orig.numerator

        HOOK_RAN = False
        self.assertEqual(orig_1_numerator, (1).numerator)
        self.assertTrue(HOOK_RAN)
        unhook(int, 'numerator')

    def test_hook_class(self):
        SENTINEL = object()
        @hook.cls(int)
        class int_hooks:
            attr = SENTINEL

            @property
            def imag(self):
                return (SENTINEL, SENTINEL)

            def __matmul__(self, other):
                return SENTINEL

        self.assertIs(int.attr, SENTINEL)
        self.assertEqual(1 .imag, (SENTINEL, SENTINEL))
        self.assertIs(1 @ 1, SENTINEL)

        unhook(int, 'imag')
        self.assertNotEqual(1 .imag, (SENTINEL, SENTINEL))
        force_delattr(int, 'attr')
        with self.assertRaises(AttributeError):
            int.attr
        unhook(int, '__matmul__')
        with self.assertRaises(TypeError):
            1 @ 1


if __name__ == '__main__':
    unittest.main()
