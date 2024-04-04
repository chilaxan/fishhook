import setuptools

setuptools.setup(
    ext_modules=[
        setuptools.Extension("fishhook._asm", sources=["fishhook/_asmmodule.c"])
    ],
)
