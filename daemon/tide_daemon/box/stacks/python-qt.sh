#!/bin/bash
# Szablon stosu python-qt (PT-192, #29): Python 3 z Ubuntu, PyQt6 z QML (QtQuick) z apt, Qt w trybie offscreen
# (QT_QPA_PLATFORM=offscreen; `xvfb-run` z obrazu bazowego dla testów, które potrzebują serwera X). Czcionki są
# w obrazie bazowym (/usr/local/share/fonts/tide) — szablon ich nie pobiera.
#
# Wersje: pakiety z archiwum Ubuntu 24.04 (jak obraz bazowy). PySide6 z PyPI nie jest instalowane — projekt,
# który go potrzebuje, dopisuje przypiętą wersję w `setup` profilu (pypi.org jest w domenach szablonu).
set -euo pipefail

# shellcheck source=lib.sh
source "${BASH_SOURCE[0]%/*}/lib.sh"

PYTHON_QT_PACKAGES=(python3 python3-venv python3-pip python3-pyqt6 python3-pyqt6.qtquick
    qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-window
    qml6-module-qtquick-templates qml6-module-qtqml-workerscript libgl1 libegl1 xauth)

check_qt() {
    QT_QPA_PLATFORM=offscreen python3 -c '
from PyQt6.QtCore import QT_VERSION_STR
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine
app = QGuiApplication([])
print("Qt", QT_VERSION_STR, "offscreen:", app.platformName())
'
    echo "czcionki: $(fc-list | wc -l)"
}

main() {
    stack_main
    apt_install "${PYTHON_QT_PACKAGES[@]}"
    write_stack_env "" python-qt QT_QPA_PLATFORM=offscreen
    check_qt
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
