"""
(C) Copyright 2020
Scott Wiederhold, s.e.wiederhold@gmail.com
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import configparser
import logging
from typing import Any

_CONFIG = {}


def parse(cfg_file: str) -> None:
    """
    Parses configuration file and returns dict object with values as: <SECTION>.<PARAM>: value.
    Note: SECTION and PARAM are converted to uppercase.
    :param cfg_file: path to configuration file
    :type cfg_file: str
    """
    config = configparser.ConfigParser()
    config.read(cfg_file)
    for section in config.sections():
        for key in config.options(section):
            # %(name)s refers to another key of the section; a value
            # that merely contains a % (a password) is taken as it is.
            try:
                raw = config.get(section, key)
            except configparser.InterpolationError:
                raw = config.get(section, key, raw=True)
            if raw == 'True':
                value = True
            elif raw == 'False':
                value = False
            else:
                value = raw
            _CONFIG['%s.%s' % (str.upper(section), str.upper(key))] = value


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
