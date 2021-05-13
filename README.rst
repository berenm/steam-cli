steam-cli: better command-line interface for Steam
================================================================================

Python script that allows you to interact with steam directly from the command
line, in a better way than using steamcmd.

INSTALLATION
--------------------------------------------------------------------------------

Install steam-cli with pip:

``pip install git+https://github.com/berenm/steam-cli@python``

USAGE
--------------------------------------------------------------------------------

::

  Usage:
    steam-cli login           [options]
    steam-cli install         [options] (--id=<id>|--name=<name>)
    steam-cli execute         [options] (--id=<id>|--name=<name>)
    steam-cli show            [options] (--id=<id>|--name=<name>)
    steam-cli list            [options]
    steam-cli download-covers [options]
    steam-cli update-cache    [options]
    steam-cli categories      [options]

  Commands:
    login [auth-token]  Login to steam (without auth-token to trigger the email)
    install             Download and install game
    execute             Execute installed game
    show                Show game details
    list                List all available games
    download-covers     Download game cover images
    update-cache        Update cached game list
    categories          List all game categories

    -i, --id=<id>       Appid of the game
    -n, --name=<name>   Name of the game

    --installed         Only list installed games
    --disk-usage        Print disk usage for each game

  Options:
    -p, --platform=<p>     Platform to install
    -b, --bitness=<b>      Bitness of the platform
    -l, --language=<l>     Language of the game to install
    -g, --games-dir=<g>    Directory where to find installed games [default: ~/]
    -s, --steam-dir=<s>    Directory where to find steam [default: ~/.steam]
    -i, --install-dir=<g>  Directory where to install game

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
