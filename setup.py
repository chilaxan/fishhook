import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="stitch-chilaxan",
    version="0.0.1",
    author="chilaxan",
    author_email="chilaxan@gmail.com",
    description="Allows for runtime patches of static classes",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/chilaxan/stitch",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.8',
)
