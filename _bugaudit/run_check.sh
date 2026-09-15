#!/Users/cyrax8590gmail.com/Personal/Projects/untitled folder 2/.venv/bin/python
import sys
print(sys.version)
try:
    import continuum
    print("continuum imported from:", continuum.__file__)
except Exception as e:
    print("IMPORT FAILED:", type(e).__name__, e)
