"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import configparser
import logging
from typing import Any

from gfutilities._common import LOGGER_NAME

_CONFIG = {}

# Flags that only code sets: the running machine's own state, never an
# option. A configuration file that names one is not read for it.
CODE_SET_KEYS = frozenset((
    'EMULATOR.ACTIVE',        # the running machine is the emulator
    'SETTINGS.SET',           # the first settings report went out
    'HOMING.HUNT_ACK_ONLY',   # a GRBL homing session's hunts
    'CAPTURE.HEADER_PATH',    # the setup wizard's one-shot header capture
))

logger = logging.getLogger(LOGGER_NAME)


def parse(cfg_file: str) -> None:
    """
    Parses configuration file and returns dict object with values as: <SECTION>.<PARAM>: value.
    Note: SECTION and PARAM are converted to uppercase. "true" and "false",
    in any case, become booleans. A key in CODE_SET_KEYS is skipped.
    :param cfg_file: path to configuration file
    :type cfg_file: str
    """
    config = configparser.ConfigParser()
    config.read(cfg_file)
    for section in config.sections():
        for key in config.options(section):
            name = '%s.%s' % (str.upper(section), str.upper(key))
            if name in CODE_SET_KEYS:
                logger.warning('%s: [%s] %s is set by the program, not the configuration; '
                               'ignored' % (cfg_file, section, key))
                continue
            # %(name)s refers to another key of the section; a value
            # that merely contains a % (a password) is taken as it is.
            try:
                raw = config.get(section, key)
            except configparser.InterpolationError:
                raw = config.get(section, key, raw=True)
            if raw.lower() == 'true':
                value = True
            elif raw.lower() == 'false':
                value = False
            else:
                value = raw
            _CONFIG[name] = value


def log_level(level_str: str) -> int:
    """
    Returns logging log level object based on provided string.
    :param level_str: Desired logging level
    :type level_str: str
    :return: Logging level object
    :rtype: int
    """
    levels = {
        'CRITICAL': logging.CRITICAL, 'ERROR': logging.ERROR, 'WARNING': logging.WARNING, 'INFO': logging.INFO,
        'DEBUG': logging.DEBUG
    }
    if level_str in levels:
        return levels[level_str]
    else:
        return logging.INFO


def get_cfg(parameter: str) -> Any:
    """
    Returns value of configuration parameter
    :param parameter: Parameter to return
    :type parameter: str
    :return: The value of the parameter
    :rtype: Any
    """
    return _CONFIG.get(parameter)


def set_cfg(parameter: str, value: Any, keep_value: bool = False):
    """
    Returns value of configuration parameter
    :param parameter: Parameter to set
    :type parameter: str
    :param value: Value of parameter
    :type value: Any
    :param keep_value: Keep existing value if set
    :type keep_value: bool
    """
    if keep_value and get_cfg(parameter) is not None:
        return
    _CONFIG[parameter] = value


__all__ = ['get_cfg', 'parse', 'set_cfg', 'log_level']
