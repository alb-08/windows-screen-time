"""
Create a Start Menu shortcut with a registered AppUserModelID so Windows
will display Game Time Limiter toasts in the Action Center.

Run once (no admin needed):
    python setup_shortcut.py

This is only required if toasts aren't appearing. Modern Windows requires
unpackaged apps to have a Start Menu shortcut tagged with a matching AUMID
for the WinRT toast API to deliver notifications.
"""
import os
import sys
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell

APP_ID = "GameTimeLimiter"


def main() -> None:
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable)
        print("Warning: pythonw.exe not found; using python.exe.")

    main_script = Path(__file__).parent.resolve() / "main.py"

    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    start_menu.mkdir(parents=True, exist_ok=True)
    lnk_path = start_menu / "Game Time Limiter.lnk"

    shortcut = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink,
        None,
        pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IShellLink,
    )
    shortcut.SetPath(str(pythonw))
    shortcut.SetArguments(f'"{main_script}"')
    shortcut.SetWorkingDirectory(str(main_script.parent))
    shortcut.SetDescription("Game Time Limiter")

    ps = shortcut.QueryInterface(propsys.IID_IPropertyStore)
    ps.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    ps.Commit()

    persist = shortcut.QueryInterface(pythoncom.IID_IPersistFile)
    persist.Save(str(lnk_path), 0)

    print(f"Shortcut created: {lnk_path}")
    print(f"AppUserModelID: {APP_ID}")
    print()
    print("Test it:")
    print('  python -c "from windows_toasts import WindowsToaster, Toast; '
          't=WindowsToaster(\'GameTimeLimiter\'); x=Toast(); '
          "x.text_fields=['Test','It works']; t.show_toast(x)\"")


if __name__ == "__main__":
    main()
