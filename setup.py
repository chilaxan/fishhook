import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="fishhook",
    version="0.3.1",
    author="chilaxan",
    author_email="chilaxan@gmail.com",
    description="Allows for runtime hooking of static class functions",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/chilaxan/fishhook",
    packages=['fishhook'],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.8',
    ext_modules=[setuptools.Extension('fishhook._asm', sources=['fishhook/_asmmodule.c'])],
    install_requires=['capstone', 'keystone-engine']
)
