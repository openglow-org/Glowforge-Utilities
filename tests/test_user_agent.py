"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from gfutilities.configuration import get_cfg, set_cfg
from gfutilities.service import websocket


def _cfg(agent=None, fw='2.6.0-2228'):
    set_cfg('SERVICE.USER_AGENT', agent)
    set_cfg('FACTORY_FIRMWARE.FW_VERSION', fw)
    set_cfg('SESSION.USER_AGENT', None)


def test_default_user_agent_names_openglow_and_the_factory_version():
    _cfg()
    assert websocket.user_agent() == 'OpenGlow/2.6.0-2228'


def test_empty_user_agent_falls_to_the_default():
    _cfg(agent='')
    assert websocket.user_agent() == 'OpenGlow/2.6.0-2228'


def test_configured_user_agent_wins():
    _cfg(agent='ForgeFIRM/0.0.1')
    assert websocket.user_agent() == 'ForgeFIRM/0.0.1'


def test_session_carries_the_user_agent():
    _cfg(agent='ForgeFIRM/0.0.1')
    s = websocket.get_session()
    assert s.headers['user-agent'] == 'ForgeFIRM/0.0.1'
    # The WebSocket client reads the resolved value from the session config.
    assert get_cfg('SESSION.USER_AGENT') == 'ForgeFIRM/0.0.1'
