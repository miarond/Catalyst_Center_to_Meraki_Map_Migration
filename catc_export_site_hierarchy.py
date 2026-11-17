"""
# BSD 3-Clause License

Copyright (c) 2026, Cisco Systems, Inc. and/or its affiliates

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

__author__ = "Aron Donaldson <ardonald@cisco.com>"
__contributor__ = ""
__copyright__ = "Copyright (c) 2026 Cisco and/or its affiliates."
__license__ = "BSD 3-Clause License (https://opensource.org/license/bsd-3-clause)"

import base64
import os
import sys
import tarfile
import json
import time
import csv
import itertools
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from getpass import getpass
from argparse import ArgumentParser
import logging
from concurrent.futures import ProcessPoolExecutor, CancelledError, TimeoutError, BrokenExecutor, InvalidStateError
from concurrent.futures.process import BrokenProcessPool

from PIL import Image
from catalystcentersdk import CatalystCenterAPI
import requests

# Disable certificate warnings
urllib3.disable_warnings(InsecureRequestWarning)

def config_logging(args):
    """
    Configures logging handler for the script.
    NOTSET: 0
    DEBUG: 10
    INFO: 20
    WARNING: 30
    ERROR: 40
    CRITICAL: 50

    The output format is configured as such:

    Date    Timezone+-  Time + 3   Log   Script   Func.     Code    Log message
            GMT offset  Millisec.  Lvl   name     name    line num     text
    ---------- -------- ------------- ---- --------------------------  -----------
    YYYY-MM-DD CDT-0500 HH:MM:SS.msec INFO (module, function, lineNo): Log Message

    param args: Argparse namespace object with "debug" variable.

    Example output: 2025-06-25 CDT-0500 16:12:52.154 INFO (myscript, myfunction, 123): testing
    Format: YYYY-MM-DD [timezone][+,-][GMT offset] HH:mm:ss.msec [logging level] (module, function, lineNo): [message]
    """
    logger = logging.getLogger(__name__)
    if args.debug:
        logger.setLevel(10)
    else:
        logger.setLevel(20)
    logging.basicConfig(format='%(asctime)s.%(msecs)03d %(levelname)s (%(module)s, %(funcName)s, %(lineno)d): %(message)s', datefmt='%Y-%m-%d %Z%z %H:%M:%S')
    return logger


def read_csv(args):
    try:
        with open(args.input_file, "r") as f:
            reader = csv.DictReader(f)
            input_data = []
            for row in reader:
                input_data.append(row)
            f.close()
        logger.debug(f"Input data: {input_data}")
        return input_data
    except Exception as e:
        logger.critical(f"Error encountered parsing input file into JSON: {e}")
        return None


def get_all_buildings(args):
    buildings = api.site_design.get_sites(type="building")
    building_ids = []
    for building in buildings["response"]:
        try:
            building_ids.append({"id": building["id"], "name": building["name"]})
        except Exception as e:
            logger.critical(f"Failed to get ID for building: {building}")
    return building_ids


def generate_input_file(api):
    columns = [
        "type",
        "name",
        "id",
        "parent",
        "nameHierarchy",
        "latitude",
        "longitude",
        "address",
        "country",
        "floorNumber"
    ]
    buildings = api.site_design.get_sites(type="building")
    floors = api.site_design.get_sites(type="floor")
    rows = []
    for item in buildings["response"]:
        logger.debug(f"Building: {item}")
        try:
            row = {
                "type": item["type"],
                "name": item["name"],
                "id": item["id"],
                "parent": item["parent"],
                "nameHierarchy": item["nameHierarchy"],
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "address": item["address"],
                "country": item["country"],
                "floorNumber": 0
            }
            rows.append(row)
        except Exception as e:
            logger.critical(f"Error encountered creating building row: {e}")
    for item in floors["response"]:
        logger.debug(f"Floor: {item}")
        try:
            row = {
                "type": item["type"],
                "name": item["name"],
                "id": item["id"],
                "parent": item["parent"],
                "nameHierarchy": item["nameHierarchy"],
                "latitude": 0,
                "longitude": 0,
                "address": "",
                "country": "",
                "floorNumber": item["floorNumber"]
            }
            rows.append(row)
        except Exception as e:
            logger.critical(f"Error encountered creating floor row: {e}")
    with open("input_file_example.csv", "w") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        f.close()
    logger.info("Input file example created: input_file_example.csv")


def download_file(target, session_details):
    # catalystcentersdk function for export_map_archive is broken - need to do this manually
    logger = session_details["logging"]
    export_url = f"{session_details['base_url']}/dna/intent/api/v1/maps/export/{target['id']}"
    headers = {
        "Content-Type": "text/plain",
        "x-auth-token": session_details["access_token"]
    }
    result = requests.post(export_url, headers=headers, data=f"{target["name"]}.tar.gz", verify=False)
    logger.debug(f"Map export request: {result}")
    api = CatalystCenterAPI(username=session_details["username"], password=session_details["password"], base_url=session_details["base_url"], 
                            version=session_details["version"], verify=False, debug=session_details["debug"])
    while True:
        status = api.task.get_task_by_id(result.json()["response"]["taskId"])
        logger.debug(f"Export task status: {status}")
        if status["response"].get("endTime"):
            # File ID is contained in "data" as: '/file/ad154b5c-18a5-479a-a3dc-9873bb802525'
            fileId = status["response"]["data"].split("/")[-1]
            result = api.file.download_a_file_by_file_id(fileId, dirpath=f"{os.getcwd()}/site_map_exports/", save_file=True)
            logger.debug(f"File save result: {result}")
            logger.info(f"Map archive saved: {os.getcwd()}/site_map_exports/{target["name"]}.tar.gz")
            break
        else:
            logger.info("Waiting 5 seconds...")
            time.sleep(5)


def export_maps(args, api):
    export_targets = []
    if args.input_file:
        input_data = read_csv(args)
        if input_data:
            for row in input_data:
                logger.debug(f"Export target: {row}")
                if row["type"].lower() == "building":
                    export_targets.append({"id": row["id"], "name": row["name"]})
        else:
            export_targets = get_all_buildings(args)
    else:
        export_targets = get_all_buildings(args)
    os.makedirs(f'site_map_exports', exist_ok=True)

    # Use multiprocessing to download exports in parallel.
    session_details = {
        "base_url": api.base_url,
        "access_token": api.access_token,
        "username": args.username,
        "password": args.password,
        "version": args.version,
        "debug": args.debug,
        "logging": logger
    }
    with ProcessPoolExecutor() as executor:
        results = []
        try:
            for variable in executor.map(download_file, export_targets, itertools.repeat(session_details)):
                results.append(variable)
        except (CancelledError, TimeoutError, BrokenExecutor, InvalidStateError, BrokenProcessPool) as e:
            logger.error(f"Error encountered with process: {e}")


def main(args):
    global logger
    logger = config_logging(args)
    if args.password:
        pass
    else:
        args.password = getpass("Enter the password to use for authenticating to Catalyst Center: ", stream=None)

    global api
    api = CatalystCenterAPI(username=args.username, password=args.password, base_url=f"https://{args.server}:443", version=args.version, verify=False, debug=args.debug)
    if args.gen_input_file:
        generate_input_file(api)
    else:
        export_maps(args, api)


if __name__ == '__main__':
    parser = ArgumentParser(description="Script for exporting floor plans and AP placement from Catalyst Center and importing into Meraki Dashboard.")
    parser.add_argument("-s", "--server", help="IP or FQDN of Catalyst Center server.", required=True)
    parser.add_argument("--version", help="Software version installed on Catalyst Center server.", default="2.3.7.9", choices=[
        "2.3.7.9",
        "3.1.3.0",
        "3.1.6.0",
        "3.2.3.0"
    ])
    parser.add_argument("-u", "--username", help="Username to connect to Catalyst Center server (must be a Super Admin).", required=True)
    parser.add_argument("-p", "--password", help="Password for user account to connect to Catalyst Center server.", required=False)
    parser.add_argument("-i", "--input_file", help="CSV input file listing the buildings and floors you want to export; " \
        "if no input file is provided, all buildings and floors will be exported.", required=False)
    parser.add_argument("--debug", help="Enable DEBUG level logging output.", default=False, action="store_true")
    parser.add_argument("--gen_input_file", help="ONLY generate an input file template, based on current site hierarchy in Catalyst Center.", default=False, action="store_true")
    args = parser.parse_args()
    main(args)
