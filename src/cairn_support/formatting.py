from typing import Literal

import rich


def log(message: str, level: Literal["info", "warning", "error"]) -> None:
    """Print to the console."""
    colors = {
        "info": "blue",
        "warning": "yellow",
        "error": "red",
    }
    level_id = level[0].upper()

    rich.print(f"[bold {colors[level]}][{level_id}][/] {message}")