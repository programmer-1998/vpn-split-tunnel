project(
    'vpn-split-tunnel',
    version: '0.1.0',
    license: 'GPL-3.0-or-later',
    default_options: [
        'warning_level=2',
        'werror=false',
    ],
    meson_version: '>= 1.2.0'
)

# Dependencies
# These are checks, not link inputs: nothing here is compiled against GTK.
# They were `pkg.get_variable(...)` / `pkg.dependency(...)` before, and the
# pkgconfig module has neither method at the Meson version this file asks for,
# so setup failed on the first line of it and the install path never ran.
gtk_dep = dependency('gtk4', version: '>=4.10')
adw_dep = dependency('libadwaita-1', version: '>=1.4')
dependency('gio-2.0', version: '>=2.76')
dependency('glib-2.0', version: '>=2.76')
dependency('gobject-2.0', version: '>=2.76')

# Python module installation
python = import('python').find_installation()

# Install Python package
# `pythondir` is not a Meson option; the python module knows where its own
# packages go for the chosen prefix. install_subdir() copies the named
# directory *into* install_dir, so install_dir is its parent -- appending the
# package name here nested it as .../vpn_split_tunnel/vpn_split_tunnel and
# `python -m vpn_split_tunnel` found no __main__.
python_install_dir = python.get_install_dir()
install_subdir(
    'src/vpn_split_tunnel',
    install_dir: python_install_dir,
    exclude_directories: ['__pycache__'],
)

# Compile the .po catalogues into .mo with the bundled compiler. The gettext
# tools (msgfmt) are not guaranteed to exist on the build machine, and the
# compiler is a plain Python script, so the build depends on nothing extra.
python_tools = files('tools/msgfmt.py')
fa_catalogue = custom_target(
    'vpn-split-tunnel.mo',
    input: 'resources/locale/fa/LC_MESSAGES/vpn-split-tunnel.po',
    output: 'vpn-split-tunnel.mo',
    command: [python.full_path(), python_tools, '@INPUT@', '@OUTPUT@'],
    install: true,
    install_dir: join_paths(get_option('datadir'), 'locale', 'fa', 'LC_MESSAGES'),
)

# Stylesheet: the app loads it by filesystem path at startup; a missing file
# is not an error, so install it wherever the app's data files live.
install_data(
    'resources/css/style.css',
    install_dir: join_paths(get_option('datadir'), 'vpn-split-tunnel'),
)

# Install desktop file
desktop = configure_file(
    input: 'resources/vpn-split-tunnel.desktop.in',
    output: 'vpn-split-tunnel.desktop',
    configuration: {
        'APP_ID': 'com.github.sina.vpn-split-tunnel',
        'VERSION': meson.project_version(),
    },
    install: true,
    install_dir: join_paths(get_option('datadir'), 'applications'),
)

# Install icon
icon = install_data(
    'resources/icons/vpn-split-tunnel.svg',
    install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor', 'scalable', 'apps'),
    rename: 'com.github.sina.vpn-split-tunnel.svg',
)

# Install AppStream metadata
appstream = install_data(
    'resources/metainfo/com.github.sina.vpn-split-tunnel.metainfo.xml',
    install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

# Install polkit policy
polkit_dir = join_paths(get_option('datadir'), 'polkit-1', 'actions')
install_data('resources/com.github.sina.vpn-split-tunnel.policy', install_dir: polkit_dir)

# Install bash completion
bash_completion_dir = join_paths(get_option('datadir'), 'bash-completion', 'completions')
install_data('scripts/vpn-split-tunnel.bash-completion', install_dir: bash_completion_dir, rename: 'vpn-split-tunnel')

# Main executable wrapper
exe_script = configure_file(
    input: 'scripts/vpn-split-tunnel.in',
    output: 'vpn-split-tunnel',
    configuration: {
        'PYTHON': python.full_path(),
        'MODULE': 'vpn_split_tunnel.__main__',
        'PYTHONPATH': python_install_dir,
    },
    install: true,
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
)

# Polkit helper script
helper_script = configure_file(
    input: 'scripts/vpn-split-tunnel-helper.in',
    output: 'vpn-split-tunnel-helper',
    configuration: {
        'PYTHON': python.full_path(),
        'MODULE': 'vpn_split_tunnel.cli.helper',
        'PYTHONPATH': python_install_dir,
    },
    install: true,
    install_dir: get_option('libexecdir'),
    install_mode: 'rwxr-xr-x',
)

# Test
test('import_test', python, args: ['-c', 'import vpn_split_tunnel; print("OK")'])
test('cli_help', exe_script, args: ['--help'])

# Summary
summary(
    {
        'GTK4': gtk_dep.found(),
        'libadwaita': adw_dep.found(),
        'Python': python.version(),
        'Install prefix': get_option('prefix'),
    },
    section: 'Configuration',
)
