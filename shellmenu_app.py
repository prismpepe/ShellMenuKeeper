"""exe 版の入口。`python -m shellmenu` と同じ画面を開く。

shellmenu/__main__.py は相対 import を使うため、PyInstaller の入口にできない。
"""

from shellmenu.gui import main

if __name__ == "__main__":
    main()
