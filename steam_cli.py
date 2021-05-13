#!/usr/bin/env python3

"""
Usage:
  steam-cli login           [options]
  steam-cli install         [options] (--id=<id>|--name=<name>)
  steam-cli execute         [options] (--id=<id>|--name=<name>)
  steam-cli show            [options] (--id=<id>|--name=<name>)
  steam-cli list            [options] [--installed] [--disk-usage]
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
"""

import os
import re
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
  _pct = 0
  _cmd = None

  def message(fmt, pct, txt):
    nonlocal _txt
    nonlocal _pct
    if txt:
      _txt = txt
    if pct is None:
      _pct = 100
    elif pct >= 0:
      _pct = min(pct, 99)
    else:
      _pct += -pct
    bar = '#' * int(_pct / 2) + ' ' * int(50 - (_pct + 1) / 2)
    return fmt.format(pct=int(_pct), txt=_txt, bar=bar)

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
      print(f'Downloading {source} to {target}')
      open(target, 'wb').write(await r.read())
    else:
      print(f'Missing {source}')
      open(target + '~', 'wb').write(bytes())


async def execute(cmd, *args, **kwargs):
  print(f'Executing "{cmd}"')
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
    self._cats = None
    self.progress = progress

  def load_cache(self):
    if self._pkgs and self._pkgids and \
       self._apps and self._appids:
      return

    pkgs_cache = os.path.join(CACHE_DIR, 'pkgs.json')
    if os.path.exists(pkgs_cache):
      with open(pkgs_cache, 'r') as f:
        self._pkgs = dict(sorted([(int(k), v) for k,v in json.load(f).items()]))
        self._pkgids = list(self._pkgs.keys())

    apps_cache = os.path.join(CACHE_DIR, 'apps.json')
    if os.path.exists(apps_cache):
      with open(apps_cache, 'r') as f:
        self._apps = dict(sorted([(int(k), v) for k,v in json.load(f).items()]))
        self._appids = list(self._apps.keys())

  def save_cache(self):
    pkgs_cache = os.path.join(CACHE_DIR, 'pkgs.json')
    if self._pkgs:
      with open(pkgs_cache, 'w') as f:
        json.dump(self._pkgs, f)

    apps_cache = os.path.join(CACHE_DIR, 'apps.json')
    if self._apps:
      with open(apps_cache, 'w') as f:
        json.dump(self._apps, f)

  def update_cache(self):
    shutil.rmtree(CACHE_DIR)
    os.makedirs(CACHE_DIR)
    self._pkgs = None
    self._pkgids = None
    self._apps = None
    self._appids = None
    self.pkgs
    self.apps

  def close_progress(self):
    self.progress(None)

  def expect(self, patterns, callbacks):
      compiled = self.steam.compile_pattern_list([*patterns,
                                                  '\x1b\[1m\r\nSteam>\x1b\[0m'])
      i = self.steam.expect_list(compiled)
      while i < len(patterns):
        callbacks[i](*self.steam.match.groups())
        i = self.steam.expect_list(compiled)

  def steam_file(self, path):
      file = os.path.join(STEAM_DIR, 'steam', path)
      if os.path.exists(file):
        return file

      file = os.path.join(STEAM_DIR, 'root', path)
      if os.path.exists(file):
        return file

      file = os.path.join(STEAM_DIR, 'debian-installation', path)
      if os.path.exists(file):
        return file

      os.path.join(STEAM_DIR, path)

  @property
  def steam(self):
    if not self._steam:
      self._steam = pexpect.spawn('steamcmd +@ShutdownOnFailedCommand 0',
                                  echo=False)
      self.expect([r'\[([^\]]+)\] (Checking for available update|Downloading [Uu]pdate|Download complete)[^\n]+\r\n'],
                  [lambda pct, txt: self.progress(int('0' + trydecode(pct).strip(' %-')), 'Updating')])

    return self._steam

  def quit(self):
    if self._steam:
      self._steam.sendline('quit');

  def on_login(self):
    self.logged_on = True

  def on_error(self, error):
    print(error)
    raise Exception()

  def login(self):
    while not self.logged_on:
      cfg = self.steam_file('config/config.vdf')
      if os.path.exists(cfg):
        with open(cfg) as f:
          username = list(vdf.parse(f)['InstallConfigStore']['Software']['Valve']['Steam']['Accounts'].keys())[0]
      else:
        username = input('Username: ')

      self.steam.sendline(f'login {username}')
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

  def on_pkgid(self, i):
    self._pkgids += [i]

  @property
  def pkgids(self):
    self.load_cache()
    if not self._pkgids:
      self._pkgids = []
      self.login()

      self.steam.sendline('licenses_print')
      self.expect([r'License packageID (\d+):\r\n'],
                  [lambda i: self.on_pkgid(int(i))])

      self._pkgids = sorted(list(set(self._pkgids)))
      self.save_cache()

    return self._pkgids

  def on_pkg(self, i, s):
    with open(os.path.join(CACHE_DIR, f'pkg-{i}.vdf'), 'wb') as f:
      f.write(s)
    self._pkgs[i] = vdf.loads(trydecode(s))
    self.progress(100 * len(self._pkgs) / len(self._pkgids))

  @property
  def pkgs(self):
    self.load_cache()
    if not self._pkgs:
      self._pkgs = {}
      self.login()
      self.progress(0, 'Loading pkgs')

      with tempfile.NamedTemporaryFile(mode='w+') as s:
        for i in self.pkgids:
          cache = os.path.join(CACHE_DIR, f'pkg-{i}.vdf')
          if os.path.exists(cache):
            self.on_pkg(i, open(cache, 'rb').read())
            continue
          print(f'package_info_print {i}', file=s)
        s.flush()

        self.steam.sendline(f'runscript "{s.name}"')
        self.expect([r'"(\d+)"\r\n{\r\n((?:[^\n]*\r\n)*?)}\r\n'],
                    [lambda i, s: self.on_pkg(int(i), s)])

      self._pkgs = dict(sorted(self._pkgs.items()))
      self.save_cache()

    return self._pkgs

  @property
  def appids(self):
    self.load_cache()
    if not self._appids:
      self._appids = []

      for p in self.pkgs.values():
        self._appids += list([int(i) for i in p['appids'].values()])

      self._appids = list(set(self._appids))
      self.save_cache()

    return self._appids

  def on_app(self, i, s):
    with open(os.path.join(CACHE_DIR, f'app-{i}.vdf'), 'wb') as f:
      f.write(s)
    self._apps[i] = vdf.loads(trydecode(s))
    self.progress(100 * len(self._apps) / len(self._appids))

  @property
  def apps(self):
    self.load_cache()
    if not self._apps:
      self._apps = {}
      self.login()
      self.progress(0, 'Loading apps')

      with tempfile.NamedTemporaryFile(mode='w+') as s:
        for i in self.appids:
          cache = os.path.join(CACHE_DIR, f'app-{i}.vdf')
          if os.path.exists(cache):
            self.on_app(i, open(cache, 'rb').read())
            continue
          print(f'app_info_print {i}', file=s)
        s.flush()

        self.steam.sendline(f'runscript "{s.name}"')
        self.expect([r'"(\d+)"\r\n{\r\n((?:[^\n]*\r\n)*?)}\r\n'],
                    [lambda i, s: self.on_app(int(i), s)])

      self._apps = dict(sorted(self._apps.items()))
      self.save_cache()

    return self._apps

  def apps_by_type(self, t):
    for k,v in self.apps.items():
      if not 'common' in v:
        continue
      if 'driverversion' in v['common']:
        continue
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

  @property
  def cats(self):
    if not self._cats:
      import plyvel

      if os.path.exists(os.path.join(CACHE_DIR, 'leveldb')):
        shutil.rmtree(os.path.join(CACHE_DIR, 'leveldb'))
      shutil.copytree(self.steam_file('config/htmlcache/Local Storage/leveldb'),
                      os.path.join(CACHE_DIR, 'leveldb'))
      db = plyvel.DB(os.path.join(CACHE_DIR, 'leveldb'))

      keys = []
      for k,v in db:
        if not b'\x00\x01' in k:
          continue

        site, key = k.split(b'\x00\x01')
        if site != b'_https://steamloopback.host':
          continue

        if b'-cloud-storage-namespace-' in key and not b'.modified' in key:
          assert(v[0] == 1)
          keys.append(k)

      self._cats = dict((i,[]) for i in self.appids)
      for k in keys:
        v = []

        for kk,vv in json.loads(db.get(k)[1:]):
          if 'is_deleted' in vv and vv['is_deleted']:
            print(f'deleted: {vv}')
            pass
          elif not 'value' in vv or not 'key' in vv:
            print(f'unknown: {vv}')
            pass
          elif vv['key'] == 'collection-bootstrap-complete':
            pass
          else:
            vvv = json.loads(vv['value'])
            if not isinstance(vvv, dict) or not 'added' in vvv:
              continue

            for i in vvv['added']:
              if i in self._cats:
                self._cats[i] += [vvv['name'].title()]
              else:
                print('{}: unknown app:{}'.format(vvv['name'], i))

    return self._cats

  async def _download_covers(self, s, i, k, v, sem, pct):
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

    async with sem:
      if 'clienticon' in v['common']:
        if os.path.exists(ico) and not os.path.exists(png):
          _, n, _ = await execute("identify -quiet -format '%p %h %w %z %k\\n' "
                                  "'{}' | sort -n -r -k2 -k3 -k4 -k5 | head -n1 "
                                  "| awk '{{print $1}}'".format(ico),
                                  stdout=subprocess.PIPE)

          await execute('convert {} \\( -clone {} \\) -delete 0--2 {}'
                        .format(ico, n.strip(), png))

    self.progress(-pct)

  async def download_covers(self):
    import aiohttp

    self.progress(0, 'Downloading')

    connector = aiohttp.TCPConnector(limit=16)
    sem = asyncio.Semaphore(16)
    pct = 100. / len(self.games)
    async with aiohttp.ClientSession(connector=connector) as s:
      await asyncio.gather(*(self._download_covers(s, i, k, v, sem, pct)
                             for i,(k,v) in enumerate(self.games.items())))

  def id(self, **kwargs):
    if kwargs.get('id', None) and kwargs['id'] in self.apps:
      return int(kwargs['id'])

    elif kwargs.get('name', None):
      for a in self.apps.values():
        if not 'common' in a: continue
        if not 'gameid' in a['common']: continue
        if not 'name' in a['common']: continue
        if kwargs['name'] == a['common']['name']:
          return int(a['common']['gameid'])

    raise GameNotFoundError

  def list(self, **kwargs):
    for a in sorted(self.games.values(), key=lambda g: g['common']['name']):
      kwargs['id'] = int(a['common']['gameid'])
      install_dir = self.install_dir(**kwargs)
      if not kwargs['installed'] or os.path.exists(install_dir):
        if kwargs['disk_usage'] and os.path.exists(install_dir):
          print(subprocess.check_output(['du','-sh', install_dir]).split()[0].decode('utf-8'), end='\t')
        else:
          print(' ', end='\t')
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

      self.steam.sendline(f'runscript "{s.name}"')
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

  def categories(self):
    for k,v in sorted(self.games.items(), key=lambda p: p[1]['common']['name']):
      print(v['common']['name'])
      for c in self.cats[k]:
        print(' ', c)


def main():
  args = docopt.docopt(__doc__, version='steam-cli v1.0.0')
  args = dict(((k if '--' not in k else k.strip('-').replace('-', '_'),v)
               for k,v in args.items()))

  STEAM_DIR = os.path.expanduser(args['steam_dir'])
  GAMES_DIR = os.path.expanduser(args['games_dir'])

  with progress(args['gui']) as p:
    client = SteamClient(progress=p, **args)

    if args['login']:
      client.login()
    elif args['list']:
      client.list(**args)
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
    elif args['categories']:
      client.categories()
    else:
      print(args)

    client.quit()


if __name__ == '__main__':
  main()
