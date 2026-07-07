# data — packaged as a real (if trivial) Python package purely so setuptools'
# package_data mechanism can ship demo.nmea inside the sdist/wheel next to the
# installed modules. demo_device.py locates it at runtime via
# os.path.dirname(__file__) / "data" / "demo.nmea", which resolves correctly
# once this package is installed alongside the flat top-level modules.
