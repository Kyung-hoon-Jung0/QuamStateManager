"""Allow ``python -m quam_state_manager`` to launch the desktop application.

Guarded (docs/102): anything that WALKS the package — ``pkgutil``, sphinx
autodoc, PyInstaller analysis, some pytest collection configs — imports
``__main__`` too, and an unguarded call here launched the whole desktop app
from a mere import.
"""

from quam_state_manager.main import main

if __name__ == "__main__":
    main()
