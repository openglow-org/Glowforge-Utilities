"""
(C) Copyright 2020
Scott Wiederhold, s.e.wiederhold@gmail.com
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging
from queue import Queue
import time
from typing import Any

from gfutilities._common import *
from gfutilities.configuration import get_cfg, set_cfg
from gfutilities.service.websocket import send_wss_event

logger = logging.getLogger(LOGGER_NAME)

MACHINE_SETTINGS = {
    # Settings that are communicated between the service and the device.
    # Mostly sent to the service from the machine. Default values are
    # included for those that are not otherwise set (see further down).
    #
    # "ITEM": (<data type>, <report to service>, <min value>, <max value>, <default value>, <idle callable>,
    # <run callable>, <cool_down callable>)
    "AAdc": MachineSetting(int, True, 0, 1023, 204),  # AirAssist
    "AAfc": MachineSetting(int, True, None, None, 536870912),  # AirAssist
    "AAid": MachineSetting(int, True, 0, 1023, 204),  # AirAssist
    "AAil": MachineSetting(int, True, None, None, 1000),  # AirAssist
    "AAin": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAix": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAli": MachineSetting(int, True, None, None, 1000),  # AirAssist
    "AArd": MachineSetting(int, True, 0, 1023, 0),  # AirAssist
    "AArl": MachineSetting(int, True, None, None, 1000),  # AirAssist
    "AArn": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AArx": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAsd": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAsl": MachineSetting(int, True, None, None, 1000),  # AirAssist
    "AAsn": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAsx": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAtv": MachineSetting(int, True, None, None, 3457),  # AirAssist
    "AAwd": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAwl": MachineSetting(int, True, None, None, 1000),  # AirAssist
    "AAwn": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AAwx": MachineSetting(int, True, None, None, 0),  # AirAssist
    "AScm": MachineSetting(int, True, None, None, 0),  #
    "AShw": MachineSetting(str, True, None, None, "00000000000000000000000000000000000000000000000000000000000"),  #
    "BAai": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAar": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAdb": MachineSetting(int, True, None, None, 5),  # Board Accel
    "BAdl": MachineSetting(int, True, None, None, 5),  # Board Accel
    "BAfa": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAfo": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAil": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAli": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BApc": MachineSetting(int, True, None, None, 500),  # Board Accel
    "BApi": MachineSetting(int, True, None, None, 500),  # Board Accel
    "BApr": MachineSetting(int, True, None, None, 200),  # Board Accel
    "BArl": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAsi": MachineSetting(int, True, None, None, 2),  # Board Accel
    "BAsr": MachineSetting(int, True, None, None, 2),  # Board Accel
    "BAxi": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAxr": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAyi": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAyr": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAzi": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BAzr": MachineSetting(int, True, None, None, 0),  # Board Accel
    "BDbs": MachineSetting(int, True, None, None, 0),  # Beam Detect: (unconfirmed)
    "BDet": MachineSetting(int, True, None, None, 96),  # e t Beam Detect
    "BDie": MachineSetting(int, True, None, None, 0),  # Beam Detect
    "BDlk": MachineSetting(int, True, None, None, 1966),  # Beam Detect: lambda k
    "BDlt": MachineSetting(int, True, None, None, 6553),  # Beam Detect: lambda t
    "BDpe": MachineSetting(int, True, None, None, 0),  # Beam Detect
    "BDps": MachineSetting(int, True, None, None, 0),  # Beam Detect: (unconfirmed)
    "BDtb": MachineSetting(int, True, None, None, 0),  # Beam Detect: (unconfirmed)
    "BDtr": MachineSetting(int, True, None, None, 32),  # Beam Detect: theta r
    "BDtt": MachineSetting(int, True, None, None, 40),  # Beam Detect: theta t
    "BDtv": MachineSetting(int, True, None, None, 0),  # Beam Detect: (unconfirmed)
    "BLev": MachineSetting(int, True, None, None, 0),  #
    "BLva": MachineSetting(int, True, None, None, 0),  #
    "BLvb": MachineSetting(int, True, None, None, 0),  #
    "BLvc": MachineSetting(int, True, None, None, 0),  #
    "BTcx": MachineSetting(int, True, None, None, 2147483647),  # Board Temp
    "BTfc": MachineSetting(int, True, None, None, 0),  # Board Temp
    "BTfo": MachineSetting(int, True, None, None, 0),  # Board Temp
    "BTin": MachineSetting(int, True, None, None, -2147483648),  # Board Temp
    "BTix": MachineSetting(int, True, None, None, 2147483647),  # Board Temp
    "BTli": MachineSetting(int, True, None, None, 1000),  # Board Temp
    "BTpd": MachineSetting(int, True, None, None, 2500),  # Board Temp
    "BTrn": MachineSetting(int, True, None, None, -2147483648),  # Board Temp
    "BTrx": MachineSetting(int, True, None, None, 2147483647),  # Board Temp
    "BTvl": MachineSetting(int, True, None, None, 752),  # Board Temp
    "BTwn": MachineSetting(int, True, None, None, -2147483648),  # Board Temp
    "BTwx": MachineSetting(int, True, None, None, 2147483647),  # Board Temp
    "BTxb": MachineSetting(int, True, None, None, 0),  # Board Temp
    "CAid": MachineSetting(int, True, None, None, 0),  #
    "CAwb": MachineSetting(int, True, None, None, 0),  #
    "CCbp": MachineSetting(int, True, None, None, 0),  #
    "CCbt": MachineSetting(int, True, None, None, 0),  #
    "CCfl": MachineSetting(int, True, None, None, 0),  #
    "CCif": MachineSetting(int, True, None, None, 0),  # CNC/cut control: (unconfirmed)
    "CCml": MachineSetting(int, True, None, None, 0),  #
    "CCpb": MachineSetting(int, True, None, None, 200000),  #
    "CCro": MachineSetting(int, True, None, None, 5000),  #
    "CCrp": MachineSetting(int, True, None, None, 10000),  # CNC/cut control: (unconfirmed)
    "CCsh": MachineSetting(int, True, None, None, 0),  # CNC/cut control: (unconfirmed)
    "CCst": MachineSetting(int, True, None, None, 2),  #
    "CCup": MachineSetting(int, True, None, None, 0),  #
    "CCwp": MachineSetting(int, True, None, None, 0),  # CNC/cut control: (unconfirmed)
    "CCxp": MachineSetting(int, True, None, None, 0),  #
    "CCyp": MachineSetting(int, True, None, None, 0),  #
    "CCzp": MachineSetting(int, True, None, None, 0),  #
    "CDcb": MachineSetting(int, True, None, None, 0),  #
    "CDcc": MachineSetting(int, True, None, None, 1),  #
    "CDsb": MachineSetting(int, True, None, None, 0),  #
    "CDtb": MachineSetting(int, True, None, None, 0),  #
    "CFdn": MachineSetting(int, True, None, None, 0),  #
    "CFdt": MachineSetting(int, True, None, None, 0),  #
    "CFdx": MachineSetting(int, True, None, None, 0),  #
    "CFen": MachineSetting(int, True, None, None, 0),  #
    "CFer": MachineSetting(int, True, None, None, 0),  #
    "CFex": MachineSetting(int, True, None, None, 0),  #
    "CFhp": MachineSetting(int, True, None, None, 0),  #
    "CFhs": MachineSetting(int, True, None, None, 0),  #
    "CFhw": MachineSetting(int, True, None, None, 0),  #
    "CFhx": MachineSetting(int, True, None, None, 0),  #
    "CFki": MachineSetting(int, True, None, None, 0),  #
    "CFkp": MachineSetting(int, True, None, None, 0),  #
    "CFli": MachineSetting(int, True, None, None, 0),  #
    "CFpd": MachineSetting(int, True, None, None, 0),  #
    "CFpp": MachineSetting(int, True, None, None, 0),  #
    "CFps": MachineSetting(int, True, None, None, 1),  #
    "CFrh": MachineSetting(int, True, None, None, 0),  #
    "CFsp": MachineSetting(int, True, None, None, 0),  #
    "CMdt": MachineSetting(int, True, None, None, 18364),  #
    "CMet": MachineSetting(int, True, None, None, 18134),  #
    "CMin": MachineSetting(int, True, None, None, 4008),  #
    "CMix": MachineSetting(int, True, None, None, 50010),  #
    "CMli": MachineSetting(int, True, None, None, 100),  #
    "CMpd": MachineSetting(int, True, None, None, 1000),  #
    "CMrn": MachineSetting(int, True, None, None, 1017),  #
    "CMrx": MachineSetting(int, True, None, None, 31040),  #
    "CMts": MachineSetting(int, True, None, None, 0),  #
    "CMup": MachineSetting(int, True, None, None, 100),  #
    "CMut": MachineSetting(int, True, None, None, 18134),  #
    "CMwn": MachineSetting(int, True, None, None, 1017),  #
    "CMwx": MachineSetting(int, True, None, None, 31040),  #
    "CTin": MachineSetting(int, True, None, None, 391),  #
    "CTix": MachineSetting(int, True, None, None, 935),  #
    "CTrn": MachineSetting(int, True, None, None, 591),  #
    "CTrx": MachineSetting(int, True, None, None, 971),  #
    "CTwn": MachineSetting(int, True, None, None, 591),  #
    "CTwx": MachineSetting(int, True, None, None, 971),  #
    "EFdc": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFfc": MachineSetting(int, True, None, None, 536870912),  # Exhaust Fan
    "EFid": MachineSetting(int, True, 0, 65535, 0),  # Exhaust Fan
    "EFil": MachineSetting(int, True, None, None, 1000),  # Exhaust Fan
    "EFin": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFix": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFli": MachineSetting(int, True, None, None, 1000),  # Exhaust Fan
    "EFrd": MachineSetting(int, True, 0, 65535, 0),  # Exhaust Fan
    "EFrl": MachineSetting(int, True, None, None, 1000),  # Exhaust Fan
    "EFrn": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFrx": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFsd": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFsl": MachineSetting(int, True, None, None, 1000),  # Exhaust Fan
    "EFsn": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFsx": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFtv": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFwd": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFwl": MachineSetting(int, True, None, None, 1000),  # Exhaust Fan
    "EFwn": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "EFwx": MachineSetting(int, True, None, None, 0),  # Exhaust Fan
    "FAdc": MachineSetting(int, True, None, None, 0),  #
    "FAfc": MachineSetting(int, True, None, None, 0),  #
    "FAid": MachineSetting(int, True, None, None, 0),  #
    "FAil": MachineSetting(int, True, None, None, 0),  #
    "FAin": MachineSetting(int, True, None, None, 0),  #
    "FAix": MachineSetting(int, True, None, None, 0),  #
    "FAli": MachineSetting(int, True, None, None, 0),  #
    "FArd": MachineSetting(int, True, None, None, 0),  #
    "FArl": MachineSetting(int, True, None, None, 0),  #
    "FArn": MachineSetting(int, True, None, None, 0),  #
    "FArx": MachineSetting(int, True, None, None, 0),  #
    "FAtv": MachineSetting(int, True, None, None, 0),  #
    "FAwd": MachineSetting(int, True, None, None, 0),  #
    "FAwl": MachineSetting(int, True, None, None, 0),  #
    "FAwn": MachineSetting(int, True, None, None, 0),  #
    "FAwx": MachineSetting(int, True, None, None, 0),  #
    "FEdc": MachineSetting(int, True, None, None, 0),  #
    "FEfc": MachineSetting(int, True, None, None, 0),  #
    "FEid": MachineSetting(int, True, None, None, 0),  #
    "FEil": MachineSetting(int, True, None, None, 0),  #
    "FEin": MachineSetting(int, True, None, None, 0),  #
    "FEix": MachineSetting(int, True, None, None, 0),  #
    "FEli": MachineSetting(int, True, None, None, 0),  #
    "FErd": MachineSetting(int, True, None, None, 0),  #
    "FErl": MachineSetting(int, True, None, None, 0),  #
    "FErn": MachineSetting(int, True, None, None, 0),  #
    "FErx": MachineSetting(int, True, None, None, 0),  #
    "FEta": MachineSetting(int, True, None, None, 0),  #
    "FEtb": MachineSetting(int, True, None, None, 0),  #
    "FEtc": MachineSetting(int, True, None, None, 0),  #
    "FEtd": MachineSetting(int, True, None, None, 0),  #
    "FEwd": MachineSetting(int, True, None, None, 0),  #
    "FEwl": MachineSetting(int, True, None, None, 0),  #
    "FEwn": MachineSetting(int, True, None, None, 0),  #
    "FEwx": MachineSetting(int, True, None, None, 0),  #
    "FIdc": MachineSetting(int, True, None, None, 0),  #
    "FIfc": MachineSetting(int, True, None, None, 0),  #
    "FIid": MachineSetting(int, True, None, None, 0),  #
    "FIil": MachineSetting(int, True, None, None, 0),  #
    "FIin": MachineSetting(int, True, None, None, 0),  #
    "FIix": MachineSetting(int, True, None, None, 0),  #
    "FIli": MachineSetting(int, True, None, None, 0),  #
    "FIrd": MachineSetting(int, True, None, None, 0),  #
    "FIrl": MachineSetting(int, True, None, None, 0),  #
    "FIrn": MachineSetting(int, True, None, None, 0),  #
    "FIrx": MachineSetting(int, True, None, None, 0),  #
    "FIta": MachineSetting(int, True, None, None, 0),  #
    "FItb": MachineSetting(int, True, None, None, 0),  #
    "FItc": MachineSetting(int, True, None, None, 0),  #
    "FItd": MachineSetting(int, True, None, None, 0),  #
    "FIwd": MachineSetting(int, True, None, None, 0),  #
    "FIwl": MachineSetting(int, True, None, None, 0),  #
    "FIwn": MachineSetting(int, True, None, None, 0),  #
    "FIwx": MachineSetting(int, True, None, None, 0),  #
    "FSli": MachineSetting(int, True, None, None, 0),  #
    "FSls": MachineSetting(int, True, None, None, 0),  #
    "FSmd": MachineSetting(int, True, None, None, 0),  #
    "FSms": MachineSetting(int, True, None, None, 0),  #
    "FSpv": MachineSetting(int, True, None, None, 0),  #
    "FStv": MachineSetting(int, True, None, None, 0),  #
    "FTcx": MachineSetting(int, True, None, None, 2147483647),  #
    "FTin": MachineSetting(int, True, None, None, -2147483648),  #
    "FTix": MachineSetting(int, True, None, None, 2147483647),  #
    "FTli": MachineSetting(int, True, None, None, 0),  #
    "FTpd": MachineSetting(int, True, None, None, 2500),  #
    "FTrn": MachineSetting(int, True, None, None, -2147483648),  #
    "FTrx": MachineSetting(int, True, None, None, 2147483647),  #
    "FTvl": MachineSetting(int, True, None, None, 0),  #
    "FTwn": MachineSetting(int, True, None, None, -2147483648),  #
    "FTwx": MachineSetting(int, True, None, None, 2147483647),  #
    "FTxb": MachineSetting(int, True, None, None, 0),  #
    "FVer": MachineSetting(int, True, None, None, 0),  #
    "HAai": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAar": MachineSetting(int, True, None, None, 0),  # Head Accel: run threshold
    "HAdb": MachineSetting(int, True, None, None, 5),  # Head Accel
    "HAdl": MachineSetting(int, True, None, None, 5),  # Head Accel
    "HAdr": MachineSetting(int, True, None, None, 5),  # Head Accel
    "HAfa": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAfo": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAil": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAli": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HApc": MachineSetting(int, True, None, None, 500),  # Head Accel
    "HApi": MachineSetting(int, True, None, None, 500),  # Head Accel
    "HApr": MachineSetting(int, True, None, None, 200),  # Head Accel
    "HArl": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAsi": MachineSetting(int, True, None, None, 2),  # Head Accel
    "HAsr": MachineSetting(int, True, None, None, 2),  # Head Accel
    "HAxi": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAxr": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAyi": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAyr": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAzi": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HAzr": MachineSetting(int, True, None, None, 0),  # Head Accel
    "HCae": MachineSetting(int, True, None, None, 0),  # Head Cam: Auto Exposure
    "HCag": MachineSetting(int, True, None, None, 0),  # Head Cam: Auto Gain
    "HCaw": MachineSetting(int, True, None, None, 2),  # Head Cam: AWB
    "HCbb": MachineSetting(int, True, None, None, 1400),  # Head Cam: Blue Balance
    "HCex": MachineSetting(int, True, None, None, 3000),  # Head Cam: Exposure
    "HCga": MachineSetting(int, True, None, None, 30),  # Head Cam: Gain
    "HChf": MachineSetting(int, True, 0, 1, 1),  # Head Cam: Horizontal Flip
    "HCil": MachineSetting(int, False, None, None, None),  # Head Cam Illumination
    "HCpr": MachineSetting(int, True, None, None, 0),  # Head Cam: (unconfirmed)
    "HCrb": MachineSetting(int, True, None, None, 1100),  # Head Cam: Red Balance
    "HCsm": MachineSetting(int, True, None, None, 0),  # Head Cam: (unconfirmed)
    "HCvf": MachineSetting(int, True, 0, 1, 0),  # Head Cam: Vertical Flip
    "HEfv": MachineSetting(int, True, None, None, 199234110),  # Head: Firmware Version Head
    "HEhl": MachineSetting(int, True, None, None, 0),  # Head
    "HEid": MachineSetting(int, True, None, None, 1100),  # Head
    "HEsn": MachineSetting(int, True, None, None, 123456789),  # Serial Number Head
    "HEua": MachineSetting(int, True, None, None, 841174597),  # Head
    "HEub": MachineSetting(int, True, None, None, 5472275),  # Head
    "HEuc": MachineSetting(int, True, None, None, 19),  # Head
    "HEul": MachineSetting(int, True, None, None, 0),  # Head
    "HEwl": MachineSetting(int, True, None, None, 0),  # Head
    "HIix": MachineSetting(int, True, None, None, 1023),  #
    "HIpd": MachineSetting(int, True, None, None, 100),  #
    "HIrx": MachineSetting(int, True, None, None, 1023),  #
    "HTcx": MachineSetting(int, True, None, None, 2147483647),  # Head Temp
    "HTfc": MachineSetting(int, True, None, None, 0),  # Head Temp
    "HTfo": MachineSetting(int, True, None, None, 0),  # Head Temp
    "HTin": MachineSetting(int, True, None, None, -2147483648),  # Head Temp
    "HTix": MachineSetting(int, True, None, None, 2147483647),  # Head Temp
    "HTli": MachineSetting(int, True, None, None, 1000),  # Head Temp
    "HTpd": MachineSetting(int, True, None, None, 2500),  # Head Temp
    "HTrn": MachineSetting(int, True, None, None, -2147483648),  # Head Temp
    "HTrx": MachineSetting(int, True, None, None, 2147483647),  # Head Temp
    "HTvl": MachineSetting(int, True, None, None, 768),  # Head Temp
    "HTwn": MachineSetting(int, True, None, None, -2147483648),  # Head Temp
    "HTwx": MachineSetting(int, True, None, None, 2147483647),  # Head Temp
    "HTxb": MachineSetting(int, True, None, None, 0),  # Head Temp
    "HVcr": MachineSetting(int, True, None, None, 0),  #
    "HVia": MachineSetting(int, True, None, None, 0),  # HV Laser Supply: (unconfirmed)
    "HVli": MachineSetting(int, True, None, None, 0),  #
    "HVlo": MachineSetting(int, True, None, None, 0),  # HV Laser Supply: (unconfirmed)
    "HVls": MachineSetting(int, True, None, None, 0),  # HV Laser Supply: (unconfirmed)
    "HVpg": MachineSetting(int, True, None, None, 0),  # HV Laser Supply: power good
    "HVps": MachineSetting(int, True, None, None, 0),  # HV Laser Supply: power good (sampled)
    "HVvo": MachineSetting(int, True, None, None, 0),  #
    "IAid": MachineSetting(int, True, None, None, 0),  #
    "IFdc": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFdm": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFfc": MachineSetting(int, True, None, None, 536870912),  # Intake Fan
    "IFid": MachineSetting(int, True, 0, 65535, 0),  # Intake Fan: Idle PWM
    "IFil": MachineSetting(int, True, None, None, 1000),  # Intake Fan
    "IFin": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFix": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFli": MachineSetting(int, True, None, None, 1000),  # Intake Fan
    "IFrd": MachineSetting(int, True, 0, 65535, 0),  # Intake Fan: Run PWM
    "IFrl": MachineSetting(int, True, None, None, 1000),  # Intake Fan
    "IFrn": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFrx": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFsd": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFsl": MachineSetting(int, True, None, None, 1000),  # Intake Fan
    "IFsn": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFsx": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFta": MachineSetting(int, True, None, None, 40882666),  # Intake Fan
    "IFtb": MachineSetting(int, True, None, None, 40424667),  # Intake Fan
    "IFwd": MachineSetting(int, True, 0, 65535, 0),  # Intake Fan
    "IFwl": MachineSetting(int, True, None, None, 1000),  # Intake Fan
    "IFwn": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IFwx": MachineSetting(int, True, None, None, 0),  # Intake Fan
    "IMct": MachineSetting(int, True, None, None, 3500),  #
    "IMff": MachineSetting(int, True, None, None, 0),  # Image capture: (unconfirmed)
    "IMfs": MachineSetting(int, True, None, None, 2),  #
    "IMjq": MachineSetting(int, True, None, None, 40),  #
    "IMmd": MachineSetting(int, True, None, None, 509649408),  #
    "IMrh": MachineSetting(int, True, None, None, 0),  # Image capture: (unconfirmed)
    "IMrs": MachineSetting(int, True, None, None, 0),  # Image capture: (unconfirmed)
    "IMrw": MachineSetting(int, True, None, None, 0),  # Image capture: (unconfirmed)
    "IRli": MachineSetting(int, True, None, None, 0),  #
    "IRpd": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor: poll period (ms)
    "IRva": MachineSetting(int, True, None, None, 32),  #
    "IRvb": MachineSetting(int, True, None, None, 32),  #
    "IRvc": MachineSetting(int, True, None, None, 35),  #
    "IRvd": MachineSetting(int, True, None, None, 36),  #
    "IRwb": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor W: baseline
    "IRwc": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor W: calibration center
    "IRwv": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor W: live value
    "IRwx": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor W: trip threshold
    "IRxb": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor X: baseline
    "IRxc": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor X: calibration center
    "IRxv": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor X: live value
    "IRxx": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor X: trip threshold
    "IRyb": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Y: baseline
    "IRyc": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Y: calibration center
    "IRyv": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Y: live value
    "IRyx": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Y: trip threshold
    "IRzb": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Z: baseline
    "IRzc": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Z: calibration center
    "IRzv": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Z: live value
    "IRzx": MachineSetting(int, True, None, None, 0),  # Lid IR flame sensor Z: trip threshold
    "ITcx": MachineSetting(int, True, None, None, 2147483647),  # Interconnect Temp
    "ITfc": MachineSetting(int, True, None, None, 0),  # Interconnect Temp
    "ITfo": MachineSetting(int, True, None, None, 0),  # Interconnect Temp
    "ITin": MachineSetting(int, True, None, None, -2147483648),  # Interconnect Temp
    "ITix": MachineSetting(int, True, None, None, 2147483647),  # Interconnect Temp
    "ITli": MachineSetting(int, True, None, None, 1000),  # Interconnect Temp
    "ITpd": MachineSetting(int, True, None, None, 5000),  # Interconnect Temp
    "ITrn": MachineSetting(int, True, None, None, -2147483648),  # Interconnect Temp
    "ITrx": MachineSetting(int, True, None, None, 2147483647),  # Interconnect Temp
    "ITvl": MachineSetting(int, True, None, None, 7904),  # Interconnect Temp
    "ITwn": MachineSetting(int, True, None, None, -2147483648),  # Interconnect Temp
    "ITwx": MachineSetting(int, True, None, None, 2147483647),  # Interconnect Temp
    "ITxb": MachineSetting(int, True, None, None, 0),  # Interconnect Temp
    "LAai": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAar": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAdb": MachineSetting(int, True, None, None, 5),  # Lid Accel
    "LAdl": MachineSetting(int, True, None, None, 5),  # Lid Accel
    "LAfa": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAfo": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAil": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAli": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LApc": MachineSetting(int, True, None, None, 500),  # Lid Accel
    "LApi": MachineSetting(int, True, None, None, 500),  # Lid Accel
    "LApr": MachineSetting(int, True, None, None, 200),  # Lid Accel
    "LArl": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAsi": MachineSetting(int, True, None, None, 2),  # Lid Accel
    "LAsr": MachineSetting(int, True, None, None, 2),  # Lid Accel
    "LAxi": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAxr": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAyi": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAyr": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAzi": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LAzr": MachineSetting(int, True, None, None, 0),  # Lid Accel
    "LCae": MachineSetting(int, True, None, None, 0),  # Lid Cam: Auto Exposure
    "LCag": MachineSetting(int, True, None, None, 0),  # Lid Cam: Auto Gain
    "LCaw": MachineSetting(int, True, None, None, 2),  # Lid Cam: AWB
    "LCbb": MachineSetting(int, True, None, None, 1400),  # Lid Cam: Blue Balance
    "LCex": MachineSetting(int, True, None, None, 3000),  # Lid Cam: Exposure
    "LCfl": MachineSetting(int, True, None, None, 0),  # Lid Cam: Flash On
    "LCfm": MachineSetting(int, True, None, None, 2),  # Lid Cam: Flash Mode
    "LCga": MachineSetting(int, True, None, None, 30),  # Lid Cam: Gain
    "LChf": MachineSetting(int, True, 0, 1, 1),  # Lid Cam: Horizontal Flip
    "LCpr": MachineSetting(int, True, None, None, 0),  # Lid Cam: (unconfirmed)
    "LCrb": MachineSetting(int, True, None, None, 1100),  # Lid Cam: Red Balance
    "LCsm": MachineSetting(int, True, None, None, 0),  # Lid Cam: (unconfirmed)
    "LCvf": MachineSetting(int, True, 0, 1, 0),  # Lid Cam: Vertical Flip
    "LLev": MachineSetting(int, True, None, None, 0),  # Lid LED
    "LLsp": MachineSetting(int, True, None, None, 28),  # Speed Lid LED
    "LLvl": MachineSetting(int, True, None, None, 132),  # Brightness Lid LED
    "LTcx": MachineSetting(int, True, None, None, 2147483647),  # Lid Temp
    "LTfc": MachineSetting(int, True, None, None, 0),  # Lid Temp
    "LTfo": MachineSetting(int, True, None, None, 0),  # Lid Temp
    "LTin": MachineSetting(int, True, None, None, -2147483648),  # Lid Temp
    "LTix": MachineSetting(int, True, None, None, 2147483647),  # Lid Temp
    "LTli": MachineSetting(int, True, None, None, 1000),  # Lid Temp
    "LTpd": MachineSetting(int, True, None, None, 5000),  # Lid Temp
    "LTrn": MachineSetting(int, True, None, None, -2147483648),  # Lid Temp
    "LTrx": MachineSetting(int, True, None, None, 2147483647),  # Lid Temp
    "LTvl": MachineSetting(int, True, None, None, 0),  # Lid Temp
    "LTwn": MachineSetting(int, True, None, None, -2147483648),  # Lid Temp
    "LTwx": MachineSetting(int, True, None, None, 2147483647),  # Lid Temp
    "LTxb": MachineSetting(int, True, None, None, 0),  # Lid Temp
    "MCbc": MachineSetting(int, True, None, None, 1),  # BaseMachine Control: Boot attempts
    "MCbf": MachineSetting(int, True, None, None, 1),  # BaseMachine Control: Boot Flags
    "MCbm": MachineSetting(int, True, None, None, 0),  # BaseMachine Control: (unconfirmed)
    "MCbr": MachineSetting(int, True, None, None, 320),  # BaseMachine Control: Manufacturing Info
    "MCbu": MachineSetting(int, True, None, None, 20576),  # BaseMachine Control: Boot Fuses
    "MCcl": MachineSetting(int, True, None, None, 1000),  # BaseMachine Control
    "MCcn": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCcp": MachineSetting(int, True, None, None, 2500),  # BaseMachine Control
    "MCct": MachineSetting(int, True, None, None, 49156),  # BaseMachine Control
    "MCcx": MachineSetting(int, True, None, None, 65000),  # BaseMachine Control
    "MCdb": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCds": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCdt": MachineSetting(int, True, None, None, None),  # BaseMachine Control: BaseMachine Time
    "MCdv": MachineSetting(str, True, None, None, '370ee0b4dad80d10d088b6b65fd10a9144eff5a5'),  # BaseMachine Control: Software Version
    "MCip": MachineSetting(str, True, None, None, '127.0.0.1'),  # BaseMachine Control: IP Address
    "MCla": MachineSetting(float, True, None, None, 0.13574219),  # BaseMachine Control
    "MClb": MachineSetting(float, True, None, None, 0.032226562),  # BaseMachine Control
    "MClc": MachineSetting(float, True, None, None, 0.010742188),  # BaseMachine Control
    "MCld": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCma": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCmi": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCov": MachineSetting(str, True, None, None, '2.6.0-2228'),  # BaseMachine Control: Firmware Version
    "MCpt": MachineSetting(int, True, None, None, 4),  # BaseMachine Control
    "MCrc": MachineSetting(int, True, None, None, 2),  # BaseMachine Control
    "MCsn": MachineSetting(int, True, None, None, None),  # BaseMachine Control: Serial Number
    "MCsv": MachineSetting(str, True, None, None, "2.6.0"),  # BaseMachine Control: Settings Version
    "MCsw": MachineSetting(int, True, None, None, 27),  # BaseMachine Control
    "MCtc": MachineSetting(int, True, None, None, 0),  # BaseMachine Control
    "MCut": MachineSetting(float, True, None, None, 0.26666668),  # BaseMachine Control
    "MMav": MachineSetting(int, True, None, None, 0),  # MM (unknown subsystem)
    "MMca": MachineSetting(int, True, None, None, 0),  # MM (unknown subsystem)
    "MMfr": MachineSetting(int, True, None, None, 0),  # MM (unknown subsystem)
    "MMsh": MachineSetting(int, True, None, None, 0),  # MM (unknown subsystem)
    "MMto": MachineSetting(int, True, None, None, 0),  # MM (unknown subsystem)
    "MMus": MachineSetting(int, True, None, None, 0),  # MM (unknown subsystem)
    "NIcc": MachineSetting(int, True, None, None, 1),  # Network Interface
    "NIcf": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIdc": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIdf": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIft": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIim": MachineSetting(int, True, None, None, 1500),  # MTU Network Interface
    "NIma": MachineSetting(str, True, None, None, "de:ad:be:ef:00:00"),  # MAC Network Interface
    "NIrb": MachineSetting(float, True, None, None, 0.017756462),  # Network Interface
    "NIrd": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIre": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIro": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIrp": MachineSetting(int, True, None, None, 50),  # Network Interface
    "NIrr": MachineSetting(float, True, None, None, 19.5),  # Network Interface
    "NIsa": MachineSetting(int, True, None, None, -57),  # Network Interface
    "NIss": MachineSetting(int, True, None, None, -42),  # Network Interface
    "NItb": MachineSetting(float, True, None, None, 0.0094909668),  # Network Interface
    "NItd": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIte": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NItf": MachineSetting(int, True, None, None, 0),  # Network Interface: (unconfirmed)
    "NItp": MachineSetting(int, True, None, None, 60),  # Network Interface
    "NItr": MachineSetting(float, True, None, None, 72.199997),  # Network Interface
    "NIts": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIuc": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NIuf": MachineSetting(int, True, None, None, 0),  # Network Interface
    "NRab": MachineSetting(int, True, None, None, 131072),  #
    "NRac": MachineSetting(int, True, None, None, 7),  #
    "NRae": MachineSetting(int, True, None, None, 2),  #
    "NRaf": MachineSetting(int, True, None, None, 1000),  #
    "NRaj": MachineSetting(int, True, None, None, 1000),  #
    "NRib": MachineSetting(int, True, None, None, 131072),  #
    "NRic": MachineSetting(int, True, None, None, 3),  #
    "NRie": MachineSetting(int, True, None, None, 2),  #
    "NRif": MachineSetting(int, True, None, None, 1000),  #
    "NRij": MachineSetting(int, True, None, None, 500),  #
    "NRpb": MachineSetting(int, True, None, None, 131072),  #
    "NRpc": MachineSetting(int, True, None, None, 3),  #
    "NRpe": MachineSetting(int, True, None, None, 2),  #
    "NRpf": MachineSetting(int, True, None, None, 1000),  #
    "NRpj": MachineSetting(int, True, None, None, 500),  #
    "NRwb": MachineSetting(int, True, None, None, 131072),  #
    "NRwc": MachineSetting(int, True, None, None, 120),  #
    "NRwe": MachineSetting(int, True, None, None, 2),  #
    "NRwf": MachineSetting(int, True, None, None, 500),  #
    "NRwj": MachineSetting(int, True, None, None, 250),  #
    "NTac": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTad": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTau": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTax": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTay": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTfc": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTfd": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTfu": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTfx": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "NTfy": MachineSetting(int, True, None, None, 0),  # NT (unknown subsystem)
    "PAcc": MachineSetting(int, True, None, None, 853),  # Purge Air
    "PAcm": MachineSetting(int, True, None, None, 0),  # Purge Air
    "PAli": MachineSetting(int, True, None, None, 1000),  # Purge Air
    "PAon": MachineSetting(int, True, None, None, 1),  # On Purge Air
    "PAos": MachineSetting(int, True, None, None, 0),  # Purge Air
    "PApd": MachineSetting(int, True, None, None, 1000),  # Purge Air
    "PCid": MachineSetting(int, True, None, None, 19795),  # ID PIC
    "PDct": MachineSetting(int, True, None, None, 0),  # Pulse data: pass/segment count
    "PDfm": MachineSetting(int, True, None, None, 0),  # Pulse data: format/mode
    "PRcp": MachineSetting(float, True, None, None, 2.875),  #
    "PRfc": MachineSetting(int, True, None, None, 0),  #
    "PRlc": MachineSetting(int, True, None, None, 0),  #
    "PRls": MachineSetting(int, True, None, None, 0),  #
    "PRnt": MachineSetting(int, True, None, None, 12),  #
    "PRrs": MachineSetting(int, True, None, None, 11206656),  #
    "PRsc": MachineSetting(int, True, None, None, 1),  #
    "PRvs": MachineSetting(int, True, None, None, 141811712),  #
    "PTli": MachineSetting(int, True, None, None, 1000),  # Power Temp
    "PTmn": MachineSetting(int, True, None, None, 0),  # Power Temp
    "PTmx": MachineSetting(int, True, None, None, 4294967295),  # Power Temp
    "PTpd": MachineSetting(int, True, None, None, 5000),  # Power Temp
    "PTvl": MachineSetting(int, True, None, None, 628),  # Power Temp
    "RAid": MachineSetting(int, True, None, None, 0),  #
    "SAid": MachineSetting(int, True, None, None, 0),  # Request ID the settings report is responding to
    "SLto": MachineSetting(int, True, None, None, 300000),  #
    "SNpl": MachineSetting(int, True, None, None, 0),  # SN (unknown subsystem)
    "STfr": MachineSetting(int, False, 1000, 200000, 10000),  # Step frequency
    "TCon": MachineSetting(int, True, None, None, 0),  #
    "TCth": MachineSetting(int, True, None, None, 2147483647),  #
    "TCtt": MachineSetting(int, True, None, None, 0),  #
    "TRuc": MachineSetting(int, True, None, None, 0),  # TR (unknown subsystem)
    "UAid": MachineSetting(int, True, None, None, 0),  #
    "ULst": MachineSetting(int, True, None, None, 0),  #
    "WPon": MachineSetting(int, True, 0, 1, 1),  # On Water Pump
    "WSst": MachineSetting(int, True, None, None, 0),  #
    "WTaf": MachineSetting(int, True, None, None, 3221225472),  # Water Temp
    "WTbf": MachineSetting(int, True, None, None, 3221225472),  # Water Temp
    "WTua": MachineSetting(int, True, None, None, 755),  # Water Temp
    "WTub": MachineSetting(int, True, None, None, 754),  # Water Temp
    "WTva": MachineSetting(int, True, None, None, 754),  # Water Temp
    "WTvb": MachineSetting(int, True, None, None, 751),  # Water Temp
    "XScc": MachineSetting(int, True, None, None, 33),  # Current X Step
    "XSdm": MachineSetting(int, True, 0, 2, 1),  # Decay Mode X Step
    "XShc": MachineSetting(int, True, 0, 1023, 33),  # Current X Step
    "XSmm": MachineSetting(int, True, 1, 32, 1),  # Microstepping Mode X Step
    "XSrc": MachineSetting(int, True, 0, 1023, 0),  # Current X Step
    "YScc": MachineSetting(int, True, None, None, 5),  # Current Y Step
    "YSdm": MachineSetting(int, True, 0, 2, 1),  # Decay Mode Y Step
    "YShc": MachineSetting(int, True, 0, 1023, 5),  # Current Y Step
    "YSmm": MachineSetting(int, True, 1, 32, 1),  # Microstepping Mode Y Step
    "YSrc": MachineSetting(int, True, 0, 1023, 0),  # Current Y Step
    "ZHin": MachineSetting(int, True, None, None, 180),  # Homing Step Interval MachineSetting(ms) Z Step
    "ZScc": MachineSetting(int, True, None, None, 1),  # Current Z Step
    "ZSen": MachineSetting(int, True, 0, 1, 0),  # Enable Z Step
    "ZShc": MachineSetting(int, True, None, None, 1),  # Current Z Step
    "ZShs": MachineSetting(int, True, None, None, 0),  # Z Step: home speed
    "ZSmd": MachineSetting(int, True, 0, 1, 1),  # Microstepping Mode Z Step
    "ZSrc": MachineSetting(int, True, None, None, 0),  # Current Z Step
    "ZStp": MachineSetting(int, True, None, None, 0),  # Z Step: (unconfirmed)
}

_CONFIG_PROVIDERS = {
    # These configuration item values are pulled from the configuration file.
    "MCdv": "FACTORY_FIRMWARE.APP_VERSION",
    "MCov": "FACTORY_FIRMWARE.FW_VERSION",
    "MCsn": "MACHINE.SERIAL",
    "HEfv": "MACHINE.HEAD_FIRMWARE",
    'HEid': "MACHINE.HEAD_ID",
    "HEsn": "MACHINE.HEAD_SERIAL",
}

_REGISTERED_PROVIDERS = {
    # These configuration item values are the result of running the specified function
    "MCdt": time.time,
}


def get_machine_setting(parameter: str) -> Any:
    """
    Return value for machine setting
    :param parameter: Setting
    :type parameter: str
    :return: Value or None if doesn't exist
    """
    setting = MACHINE_SETTINGS.get(parameter)
    if setting is None:
        return None
    return setting.default


def send_report(q: Queue, msg: dict) -> None:
    """
    Send device settings report in the required format.
    :param q: {WSS message queue
    :type q: Queue
    :param msg: WSS Message
    :type msg: dict
    :return:
    """
    settings = ''
    logger.info("START")
    for item, setting in MACHINE_SETTINGS.items():
        value = '' if setting.default is None else str(setting.default)
        if setting.in_report:
            if item == 'SAid':
                value = msg['id']
            if item in _CONFIG_PROVIDERS:
                config_value = get_cfg(_CONFIG_PROVIDERS.get(item))
                if config_value is not None:
                    value = str(config_value)
            if item in _REGISTERED_PROVIDERS:
                value = str(_REGISTERED_PROVIDERS[item]())
            if setting.type is int:
                value = int(float(value))
            elif setting.type is str:
                value = '"{}"'.format(value)
            else:
                value = setting.type(value)
            settings += '"{}":{},'.format(item, value)

    # The service appears to bypass the homing process if we send it an empty settings value.
    # It is assumed that this prevents the machine from re-homing every time it loses and regains
    # the connection to the Glowforge service (a seemingly frequent occurrence).
    # BYPASS_HOMING is honored only when the running machine IS the emulator
    # (EMULATOR.ACTIVE is set from code, not config): a real machine's config
    # carrying the key must not skip service homing.
    if not get_cfg('SETTINGS.SET') and not (get_cfg('EMULATOR.ACTIVE')
                                            and get_cfg('EMULATOR.BYPASS_HOMING')):
        send_wss_event(q, msg['id'], 'settings:completed',
                       data={'key': 'settings', 'value': '{"values":{%s}}' % settings[:-1]})
        set_cfg('SETTINGS.SET', True)
    else:
        send_wss_event(q, msg['id'], 'settings:completed', data={'key': 'settings', 'value': '{}'})
    logger.info("COMPLETE")


def update_settings(settings: dict) -> None:
    for key, val in settings.items():
        MACHINE_SETTINGS[key] = MACHINE_SETTINGS[key]._replace(**val)
