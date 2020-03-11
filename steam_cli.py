#!/usr/bin/env python3

"""
Usage:
  steam-cli login           [options]
  steam-cli install         [options] (--id=<id>|--name=<name>)
  steam-cli execute         [options] (--id=<id>|--name=<name>)
  steam-cli show            [options] (--id=<id>|--name=<name>)
  steam-cli list            [options]
  steam-cli download-covers [options]
  steam-cli update-cache    [options]

Commands:
  login [auth-token]  Login to steam and store credentials in keyring
                       (try login first without auth-token to trigger the email)
  install             Download and install game
  execute             Execute installed game
  show                Show game details
  list                List all available games
  download-covers     List all available games
  update-cache        Update cached game list

  -i, --id=<id>       Appid of the game
  -n, --name=<name>   Name of the game

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
"""

import os
import sys
import json
import getpass
import shutil
import subprocess
import contextlib
import datetime
import asyncio
import tempfile

import docopt
import dateutil.parser
import requests
import pexpect
import xdg
import vdf

STEAM_DIR = os.path.expandvars("$HOME/.steam")
CACHE_DIR = os.path.join(xdg.XDG_CACHE_HOME, "steam-cli")

if not os.path.exists(CACHE_DIR):
  os.makedirs(CACHE_DIR)

@contextlib.contextmanager
def progress(ui):
  _txt = '...'
  _cmd = None

  def message(fmt, pct, txt):
    nonlocal _txt
    _txt = txt if txt else _txt
    pct = 100 if pct is None else min(int(pct), 99)
    bar = '#' * int(pct / 2) + ' ' * int(50 - (pct + 1) / 2)
    return fmt.format(pct=pct, txt=_txt, bar=bar)

  try:
    if ui == 'text':
      yield (lambda pct, txt=None:
               print(message('\u001b[2K[{bar}] {txt}...', pct, txt),
                     end='\r'))

    elif ui == 'curses':
      def cmd():
        nonlocal _cmd
        if not _cmd:
          _cmd = subprocess.Popen(['whiptail', '--gauge', '', '6', '80', '0'],
                                 bufsize=0, stdin=subprocess.PIPE, text=True)
        return _cmd

      yield (lambda pct, txt=None:
               print(message('XXX\n{pct}\n{txt}...\nXXX', pct, txt),
                     file=cmd().stdin))

    elif ui == 'system':
      def cmd():
        nonlocal _cmd
        if not _cmd:
          _cmd = subprocess.Popen(['zenity', '--width', '320', '--progress',
                                   '--text', '', '--auto-kill', '--auto-close',
                                   '--no-cancel', '--time-remaining'],
                                   bufsize=0, stdin=subprocess.PIPE, text=True)
        return _cmd

      yield (lambda pct, txt=None:
               print(message('# {txt}...\n{pct}', pct, txt), file=cmd().stdin))

    else:
      yield (lambda pct, txt=None:
               print(message('{txt}... {pct}%', pct, txt), file=sys.stderr))

  finally:
    pass

  if _cmd:
    _cmd.stdin.close()
    _cmd.wait()


def trydecode(bytes, encodings=['utf-8', 'iso-8859-1']):
  for e in encodings[:-1]:
    try:
      return bytes.decode(e)
    except:
      continue
  return bytes.decode(encodings[-1])


async def download(session, source, target):
  if os.path.exists(target) or os.path.exists(target + '~'):
    return

  if not os.path.exists(os.path.dirname(target)):
    os.makedirs(os.path.dirname(target))

  async with session.get(source, allow_redirects=True) as r:
    if 200 <= r.status < 300:
      print('Downloading {} to {}'.format(source, target))
      open(target, 'wb').write(await r.read())
    else:
      print('Missing {}'.format(source))
      open(target + '~', 'wb').write(bytes())


async def execute(cmd, *args, **kwargs):
  print('Executing "{}"'.format(cmd))
  proc = await asyncio.create_subprocess_shell(cmd, *args, **kwargs)
  stdout, stderr = await proc.communicate()
  return (proc.returncode,
          stdout.decode('utf-8') if stdout else None,
          stderr.decode('utf-8') if stderr else None)


def titlecase(txt):
  return txt[0].upper() + txt[1:]


class GameNotFoundError(Exception):
  pass


class SteamClient:
  def __init__(self, progress=None, **kwargs):
    self.logged_on = False
    self._steam = None
    self._pkgids = None
    self._pkgs = None
    self._appids = None
    self._apps = None
    self.progress = progress

  def close_progress(self):
    self.progress(None)

  def expect(self, patterns, callbacks):
      i = self.steam.expect([*patterns, r'Steam>'])
      while i < len(patterns):
        callbacks[i](*self.steam.match.groups())
        i = self.steam.expect([*patterns, r'Steam>'])

  @property
  def steam(self):
    if not self._steam:
      self._steam = pexpect.spawn('steamcmd +@ShutdownOnFailedCommand 0',
                                  echo=False)
      self.expect([r'\[([^\]]+)\] (Checking for available update|Downloading [Uu]pdate|Download complete)[^\n]+\r\n'],
                  [lambda pct, txt: self.progress(int('0' + trydecode(pct).strip(' %-')), 'Updating')])

    return self._steam

  def on_login(self):
    self.logged_on = True

  def on_error(self, error):
    raise error

  def on_pkgid(self, i):
    self._pkgids += [i]

  def on_pkg(self, i, s):
    with open(os.path.join(CACHE_DIR, 'pkg-{}.vdf'.format(i)), 'wb') as f:
      f.write(s)
    self._pkgs[i] = vdf.loads(trydecode(s))
    self.progress(100 * len(self._pkgs) / len(self._pkgids))

  def on_app(self, i, s):
    with open(os.path.join(CACHE_DIR, 'app-{}.vdf'.format(i)), 'wb') as f:
      f.write(s)
    self._apps[i] = vdf.loads(trydecode(s))
    self.progress(100 * len(self._apps) / len(self._appids))

  def login(self):
    while not self.logged_on:
      cfg = os.path.join(STEAM_DIR, 'config/config.vdf')
      if not os.path.exists(cfg):
        cfg = os.path.join(STEAM_DIR, 'steam/config/config.vdf')
      if os.path.exists(cfg):
        with open(cfg) as f:
          username = list(vdf.parse(f)['InstallConfigStore']['Software']['Valve']['Steam']['Accounts'].keys())[0]
      else:
        username = input('Username: ')

      self.steam.sendline('login {}'.format(username))
      self.progress(0, 'Login')
      self.expect([r'password:', r"Steam Guard code:", r'Two-factor code:',
                   r'Logged in OK\r\n',
                   r'FAILED login with result code ([^\n]+)\r\n',
                   r"Logging in user '.*' to Steam Public \.\.\.\r\n",
                   r"Waiting for user info\.\.\.OK\r\n"],
                  [lambda: self.steam.sendline(getpass.getpass('Password: ')),
                   lambda: self.steam.sendline(input('Email code: ')),
                   lambda: self.steam.sendline(input('Two-factor code: ')),
                   lambda: self.on_login(), lambda e: self.on_error(e),
                   lambda: self.progress(50), lambda: self.progress(90)])

  @property
  def pkgids(self):
    if not self._pkgids:
      self._pkgids = []
      self.login()

      self.steam.sendline('licenses_print')
      self.expect([r'License packageID (\d+):\r\n'],
                  [lambda i: self.on_pkgid(int(i))])

      self._pkgids = list(set(self._pkgids))

    return self._pkgids

  @property
  def pkgs(self):
    if not self._pkgs:
      pkgs_cache = os.path.join(CACHE_DIR, 'pkgs.json')

      if os.path.exists(pkgs_cache):
        with open(pkgs_cache, 'r') as f:
          self._pkgs = json.load(f)
      else:
        self._pkgs = {}
        self.login()
        self.progress(0, 'Loading pkgs')

        with tempfile.NamedTemporaryFile(mode='w+') as s:
          for i in self.pkgids:
            cache = os.path.join(CACHE_DIR, 'pkg-{}.vdf'.format(i))
            if os.path.exists(cache):
              self.on_pkg(i, open(cache, 'rb').read())
              continue
            print('package_info_print {}'.format(i), file=s)
          s.flush()

          self.steam.sendline('runscript "{}"'.format(s.name))
          self.expect([r'"(\d+)"\r\n{(.*)\r\n}\r\n'],
                      [lambda i, s: self.on_pkg(int(i), s)])

        with open(pkgs_cache, 'w') as f:
          json.dump(self._pkgs, f)

    return self._pkgs

  @property
  def appids(self):
    if not self._appids:
      self._appids = []
      self.login()

      for p in self.pkgs.values():
        self._appids += list(p['appids'].values())

      self._appids = list(set(self._appids))

    return self._appids

  @property
  def apps(self):
    if not self._apps:
      apps_cache = os.path.join(CACHE_DIR, 'apps.json')

      if os.path.exists(apps_cache):
        with open(apps_cache, 'r') as f:
          self._apps = json.load(f)
      else:
        self._apps = {}
        self.login()
        self.progress(0, 'Loading apps')

        with tempfile.NamedTemporaryFile(mode='w+') as s:
          for i in self.appids:
            cache = os.path.join(CACHE_DIR, 'app-{}.vdf'.format(i))
            if os.path.exists(cache):
              self.on_app(i, open(cache, 'rb').read())
              continue
            print('app_info_print {}'.format(i), file=s)
          s.flush()

          self.steam.sendline('runscript "{}"'.format(s.name))
          self.expect([r'AppID :.*\r\n"(\d+)"\r\n{(.*)\r\n}\r\n'],
                      [lambda i, s: self.on_app(int(i), s)])

        self._apps = dict([(k,v) for k,v in self._apps.items()
                           if 'common' in v])
        with open(apps_cache, 'w') as f:
          json.dump(self._apps, f)

      self._apps = dict(sorted(self._apps.items(),
                               key=lambda i: i[1]['common']['name'].lower()))
    return self._apps

  def apps_by_type(self, t):
    for k,v in self.apps.items():
      if v['common']['type'].lower() == t:
        yield k,v

  @property
  def tools(self):
    return dict(self.apps_by_type('tool'))
  @property
  def configs(self):
    return dict(self.apps_by_type('config'))
  @property
  def dlcs(self):
    return dict(self.apps_by_type('dlc'))
  @property
  def applications(self):
    return dict(self.apps_by_type('application'))
  @property
  def games(self):
    return dict(self.apps_by_type('game'))
  @property
  def demos(self):
    return dict(self.apps_by_type('demo'))

  async def _download_covers(self, s, i, k, v):
    SOURCE = 'https://steamcdn-a.akamaihd.net/steam{}/apps/{}/{}.{}'
    TARGET = os.path.join(CACHE_DIR, '{}/{}.{}')

    await download(s, SOURCE.format('', k, 'library_600x900_2x', 'jpg'),
                   TARGET.format('covers/600x900', k, 'jpg'))

    await download(s, SOURCE.format('', k, 'library_600x900', 'jpg'),
                   TARGET.format('covers/300x450', k, 'jpg'))

    await download(s, SOURCE.format('', k, 'header', 'jpg'),
                   TARGET.format('headers', k, 'jpg'))

    await download(s, SOURCE.format('', k, 'logo', 'png'),
                   TARGET.format('logos/640x360', k, 'png'))

    if 'logo' in v['common']:
      n = v['common']['logo']
      await download(s, SOURCE.format('community/public/images', k, n, 'jpg'),
                     TARGET.format('logos/184x69', k, 'jpg'))

    if 'logo_small' in v['common']:
      n = v['common']['logo_small']
      await download(s, SOURCE.format('community/public/images', k, n, 'jpg'),
                     TARGET.format('logos/120x45', k, 'jpg'))

    if 'clienticon' in v['common']:
      ico = TARGET.format('icons', k, 'ico')
      png = TARGET.format('icons', k, 'png')
      n = v['common']['clienticon']
      await download(s, SOURCE.format('community/public/images', k, n, 'ico'),
                     ico)

    if 'clienticon' in v['common']:
      if os.path.exists(ico) and not os.path.exists(png):
        _, n, _ = await execute("identify -quiet -format '%p %h %w %z %k\\n' "
                                "'{}' | sort -n -r -k2 -k3 -k4 -k5 | head -n1 "
                                "| awk '{{print $1}}'".format(ico),
                                stdout=subprocess.PIPE)

        await execute('convert {} \\( -clone {} \\) -delete 0--2 {}'
                      .format(ico, n.strip(), png))

    self._pct += 100. / len(self.games)
    self.progress(self._pct)

  async def download_covers(self):
    import aiohttp

    self._pct = 0
    self.progress(0, 'Downloading')

    connector = aiohttp.TCPConnector(limit=16)
    async with aiohttp.ClientSession(connector=connector) as s:
      await asyncio.gather(*(self._download_covers(s, i, k, v)
                             for i,(k,v) in enumerate(self.games.items())))

  def update_cache(self):
    shutil.rmtree(CACHE_DIR)
    os.makedirs(CACHE_DIR)
    self.pkgs
    self.apps

  def id(self, **kwargs):
    if kwargs.get('id', None) and kwargs['id'] in self.apps:
      return kwargs['id']

    elif kwargs.get('name', None):
      for a in self.apps.values():
        if kwargs['name'] == a['common']['name']:
          return a['common']['gameid']

    raise GameNotFoundError

  def list(self):
    for a in self.games.values():
      print(a['common']['name'])

  def show(self, **kwargs):
    kwargs['id'] = self.id(**kwargs)
    print(json.dumps(self.apps[kwargs['id']], indent=2))

  def install_dir(self, **kwargs):
    if kwargs.get('install_dir', None):
      d = kwargs['install_dir']
    else:
      d = self.apps[kwargs['id']]['config']['installdir']

    d = os.path.join(os.path.expanduser(kwargs['games_dir']), d)
    d = os.path.expandvars(d)
    return d

  def install(self, **kwargs):
    self.login()

    kwargs['id'] = self.id(**kwargs)
    kwargs['install_dir'] = self.install_dir(**kwargs)

    with tempfile.NamedTemporaryFile(mode='w+') as s:
      script = 'force_install_dir \"{install_dir}\"\n'
      if kwargs.get('platform', None):
        script += '@sSteamCmdForcePlatformType "{platform}"\n'
      if kwargs.get('bitness', None):
        script += '@sSteamCmdForcePlatformBitness "{bitness}"\n'
      if kwargs.get('language', None):
        script += 'app_update "{id}" -validate -language "{language}"\n'
      else:
        script += 'app_update "{id}" -validate\n'

      print(script.format(**kwargs), file=s)
      s.flush()

      self.steam.sendline('runscript "{}"'.format(s.name))
      self.expect([r'Update state .* (reconfiguring|downloading|validating), progress: ([\d]+).*\r\n',
                   r"Success! App '{id}' fully installed\.\r\n".format(**kwargs)],
                  [lambda txt, pct: self.progress(int(pct), titlecase(trydecode(txt))),
                   lambda: self.progress(100)])

  def command(self, **kwargs):
    kwargs['id'] = self.id(**kwargs)
    kwargs['install_dir'] = self.install_dir(**kwargs)
    if not os.path.exists(kwargs['install_dir']):
      self.install(**kwargs)

    app = self.apps[kwargs['id']]
    print(app['config']['launch'])
    for v in app['config']['launch'].values():
      exe = os.path.join(kwargs['install_dir'], v['executable'])
      if os.path.exists(exe):
        return exe, v

  def execute(self, **kwargs):
    cmd, config = self.command(**kwargs)
    self.close_progress()

    print(cmd, config)
    if 'linux' in config['config']['oslist']:
      subprocess.run([cmd])
    elif 'windows' in config['config']['oslist']:
      subprocess.run(['wine', cmd])


def main():
  args = docopt.docopt(__doc__, version='steam-cli v1.0.0')
  args = dict(((k if '--' not in k else k.strip('-').replace('-', '_'),v)
               for k,v in args.items()))

  with progress(args['gui']) as p:
    client = SteamClient(progress=p, **args)

    if args['login']:
      client.login()
    elif args['list']:
      client.list()
    elif args['show']:
      client.show(**args)
    elif args['install']:
      client.install(**args)
    elif args['execute']:
      client.execute(**args)
    elif args['download-covers']:
      asyncio.run(client.download_covers())
    elif args['update-cache']:
      client.update_cache()
    else:
      print(args)


if __name__ == '__main__':
  main()
