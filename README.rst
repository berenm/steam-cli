steam-cli: better command-line interface for Steam
================================================================================

Python script that allows you to interact with steam directly from the command
line, in a better way than using steamcmd.

DEPENDENCIES
--------------------------------------------------------------------------------

steam-cli relies on the following tools, that should be available on your
distribution:

- ``python``
- ``secret-tool`` (from libsecret-tools, for credential storage)
- ``wine`` (optional)

INSTALLATION
--------------------------------------------------------------------------------

Install steam-cli with pip:

``pip install git+https://github.com/berenm/steam-cli@python``

USAGE
--------------------------------------------------------------------------------

::

  Usage: steam-cli <command> (--id=<app-id>|--name=<app-name>)
                             [--platform=<platform>]
                             [--bitness=<bitness>]
                             [--language=<language>]
                             [--games-dir=<directory>]
                             [--steam-dir=<directory>]
  Commands:
    login               Login to steam and store credentials in keyring
    install             Download and install game
    execute             Execute installed game
    list                List all available games
    update-cache        Update cached game list

  Mandatory arguments:
    -i, --id            AppID of the game
    -n, --name          Name of the game

  Other arguments:
    -p, --platform      Platform to install
    -b, --bitness       Bitness of the platform
    -l, --language      Language of the game to install
    -g, --games-dir     Directory where to find installed games
    -s, --steam-dir     Directory where to find steam [default: ~/.steam]

    --debug             Run in debug mode (mostly set -x)
    --gui <gui>         Choose the GUI to use for progress indication from the
                        list of supported GUIs: none, text, curses, system

``steam-cli`` should be able to start any game, but sadly many games check whether they
were started from Steam, and if not, launch Steam and restart from there.

This is also the case for windows games, running inside ``wine``, but there ``steam``
executable will probably not be found and these games won't start at all.

For a list of DRM-free games, see http://steam.wikia.com/wiki/List_of_DRM-free_games

LICENSE
-------------------------------------------------------------------------------

 This is free and unencumbered software released into the public domain.

 See accompanying file UNLICENSE or copy at http://unlicense.org/UNLICENSE
