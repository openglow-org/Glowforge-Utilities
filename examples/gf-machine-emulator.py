#!/usr/bin/python
"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging

from gfutilities.configuration import *
from gfutilities import GFUIService, Emulator

parse('gf-machine-emulator.cfg')

logging.basicConfig(format='(%(levelname)s) %(module)s:%(funcName)s %(message)s')
logger = logging.getLogger("openglow")
logger.setLevel(log_level(get_cfg('LOGGING.CONSOLE_LEVEL')))

if get_cfg('LOGGING.FILE'):
    fh = logging.FileHandler(get_cfg('LOGGING.FILE'))
    fh.setLevel(log_level(get_cfg('LOGGING.LEVEL')))
    fh.setFormatter(logging.Formatter('%(asctime)s (%(levelname)s) %(module)s:%(funcName)s %(message)s'))
    logger.addHandler(fh)

service = GFUIService(Emulator())
service.connect()
service.run()
