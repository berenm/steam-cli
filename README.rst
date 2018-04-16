steam-cli: better command-line interface for Steam
================================================================================

Bash script that allows you to interact with steam directly from the command
line, in a better way than using steamcmd.

DEPENDENCIES
--------------------------------------------------------------------------------

steam-cli relies on the following tools, that should be available on your
distribution:

- ``bash``
- ``steamcmd``
- ``jq``
- ``wine`` (optional)

INSTALLATION
--------------------------------------------------------------------------------

Simply download steam-cli somewhere in your path.

You can always get the latest version of the script from https://git.io/steam-cli

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
    install             Download and install game
    execute             Execute installed game
    list                List all available games

  Mandatory options:
    -i, --id            AppID of the game
    -n, --name          Name of the game

  Other options:
    -p, --platform      Platform to install
    -b, --bitness       Bitness of the platform
    -l, --language      Language of the game to install
    -g, --games-dir     Directory where to find installed games
    -s, --steam-dir     Directory where to find steam [default: ~/.steam]

LICENSE
-------------------------------------------------------------------------------

 This is free and unencumbered software released into the public domain.

 See accompanying file UNLICENSE or copy at http://unlicense.org/UNLICENSE
