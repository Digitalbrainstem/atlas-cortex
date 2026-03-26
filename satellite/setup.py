from setuptools import setup, find_packages
setup(
    name="atlas-satellite",
    version="0.0.2",
    packages=find_packages(),
    install_requires=[
        "aiohttp",
        "websockets",
        "numpy",
    ],
)
