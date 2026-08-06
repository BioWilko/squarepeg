from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("squarepeg")
except PackageNotFoundError:  # running from a source checkout that was never pip-installed
    __version__ = "0.0.0+unknown"
