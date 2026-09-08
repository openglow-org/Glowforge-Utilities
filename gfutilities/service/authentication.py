"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging
from requests import Session

from gfutilities._common import *
from gfutilities.configuration import get_cfg, set_cfg
from gfutilities.service.websocket import request, update_header

logger = logging.getLogger(LOGGER_NAME)


def authenticate_machine(s: Session) -> bool:
    """
    Authenticates machine to service.
    POSTS serial number prefixed with an S and machine password to <server_url>/machines/sign_in.
    On success, server responds with JSON encoded 'ws_token' for WSS auth, and 'auth_token' that is
    added as a header to all session web API requests.
    Auth token header is 'Authorization': 'Bearer <auth_token>'
    :param s: Emulator session object.
    :type s: Session
    :return: Authentication result.
    :rtype: bool
    """
    logger.info('START')

    r = request(s, get_cfg('SERVICE.SERVER_URL') + '/machines/sign_in', 'POST',
                _retry_auth=False,
                data={'serial': 'S' + str(get_cfg('MACHINE.SERIAL')), 'password': get_cfg('MACHINE.PASSWORD')})
    if r:
        rj = r.json()
        # Never log the response body: it carries the bearer auth_token and
        # the single-use ws_token verbatim.
        logger.debug('sign-in response received (keys: %s)' % sorted(rj.keys()))
        set_cfg('SESSION.WS_TOKEN', rj['ws_token'])
        set_cfg('SESSION.AUTH_TOKEN', rj['auth_token'])
        update_header(s, 'Authorization', "Bearer %s" % get_cfg('SESSION.AUTH_TOKEN'))
        logger.info('SUCCESS')
        return True
    else:
        logger.error('FAILED')
        return False
