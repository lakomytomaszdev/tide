# Ścieżki względem $HOME, które składają się na wygląd Plasmy.

# Pliki konfiguracji — przy przywracaniu nadpisywane, a te, których w kopii nie było, usuwane.
CONFIG_FILES=(
    .config/kdeglobals
    .config/plasmarc
    .config/plasmashellrc
    .config/plasma-org.kde.plasma.desktop-appletsrc
    .config/kwinrc
    .config/kwinrulesrc
    .config/kglobalshortcutsrc
    .config/breezerc
    .config/klassy
    .config/kcminputrc
    .config/ksplashrc
    .config/kscreenlockerrc
    .config/konsolerc
    .config/plasma-localerc
    .config/plasmanotifyrc
    .config/kdedefaults
    .config/gtk-3.0
    .config/gtk-4.0
    .config/xsettingsd
    .config/Kvantum
)

# Katalogi przywracane 1:1 — wszystko, co dodano po kopii, znika.
MIRRORED_DIRS=(
    .local/share/plasma
    .local/share/color-schemes
    .local/share/konsole
    .local/share/aurorae
    .local/share/kwin
)

# Duże katalogi tylko uzupełniane — nic z nich nie kasujemy, motywy i tapety mogą zostać.
ADD_ONLY_DIRS=(
    .local/share/icons
    .icons
    .local/share/plasma_icons
    .local/share/wallpapers
)
