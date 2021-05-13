from setuptools import setup, find_packages

requires = [r.replace(' = "*"','').strip() for r in open('Pipfile').readlines()
            if '[' not in r and len(r.strip()) > 0]

setup(name='steam-cli', url='https://github.com/berenm/steam-cli',
	license='UNLICENSE',
  install_requires=requires,
  py_modules=['steam_cli'],
  entry_points={'console_scripts': ['steam-cli=steam_cli:main']},
)
