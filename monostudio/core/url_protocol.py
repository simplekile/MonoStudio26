"""Register monostudio:// URL protocol on Windows (per-user, no admin)."""



from __future__ import annotations



import logging

import sys

from pathlib import Path



_log = logging.getLogger("monostudio.url_protocol")



_PROTOCOL_KEY = r"Software\Classes\monostudio"





def _protocol_launch_command() -> str:

    from monostudio.core.app_launch import app_deep_link_protocol_command



    return app_deep_link_protocol_command("%1")





def _default_icon_value() -> str:

    if getattr(sys, "frozen", False):

        exe = Path(sys.executable).resolve()

        return f'"{exe}",0'

    ico = Path(__file__).resolve().parents[2] / "monostudio_data" / "icons" / "app.ico"

    if ico.is_file():

        return f'"{ico.resolve()}",0'

    return ""





def register_monostudio_url_protocol() -> None:

    """Ensure HKCU monostudio:// opens this app install (idempotent)."""

    if sys.platform != "win32":

        return

    try:

        import winreg

    except ImportError:

        return



    command = _protocol_launch_command()

    icon = _default_icon_value()

    try:

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PROTOCOL_KEY) as root:

            winreg.SetValueEx(root, "", 0, winreg.REG_SZ, "URL:MONOS Protocol")

            winreg.SetValueEx(root, "URL Protocol", 0, winreg.REG_SZ, "")



        if icon:

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PROTOCOL_KEY + r"\DefaultIcon") as ic:

                winreg.SetValueEx(ic, "", 0, winreg.REG_SZ, icon)



        with winreg.CreateKey(

            winreg.HKEY_CURRENT_USER,

            _PROTOCOL_KEY + r"\shell\open\command",

        ) as cmd:

            winreg.SetValueEx(cmd, "", 0, winreg.REG_SZ, command)

        _log.debug("Registered monostudio:// protocol (no-console launch)")

    except OSError as ex:

        _log.debug("URL protocol registration skipped: %s", ex)


