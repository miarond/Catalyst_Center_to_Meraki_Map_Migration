#!/usr/bin/env python3
"""
Copyright (c) 2026 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

__author__ = "Trevor Maco <tmaco@cisco.com>"
__contributor__ = "Aron Donaldson <ardonald@cisco.com>"
__copyright__ = "Copyright (c) 2026 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import secrets
import string
import sys
import time
from typing import ClassVar, Optional

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3 import Retry
from urllib3.exceptions import InsecureRequestWarning

from config.config import c
from logger.logrr import lm

# Suppress only the single InsecureRequestWarning from urllib3 needed for unverified HTTPS requests.
urllib3.disable_warnings(InsecureRequestWarning)


def generate_random_filename_suffix(length: int = 10) -> str:
    """
    Generate a random string of the specified length to be used as a filename suffix
    :param length: Length of the random string
    :return: Random string of the specified length
    """
    # Define the allowed characters in a filename (lowercase and uppercase letters and digits)
    allowed_chars = string.ascii_letters + string.digits

    # Generate a random string of the specified length
    random_string = ''.join(secrets.choice(allowed_chars) for _ in range(length))
    return random_string


class CAT_CENTER_API(object):
    _instance: ClassVar[Optional['CAT_CENTER_API']] = None

    CAT_CENTER_AUTH_URL: ClassVar[str] = f"{c.CAT_CENTER_BASE_URL}/dna/system/api/v1/auth/token"
    CAT_CENTER_INTENT_URL: ClassVar[str] = f"{c.CAT_CENTER_BASE_URL}/dna/intent/api"

    def __init__(self):
        """
        Initialize the CAT_CENTER class: Obtain Token
        """
        self.auth = HTTPBasicAuth(c.CAT_CENTER_USERNAME, c.CAT_CENTER_PASSWORD)

        # Setup Session (handle 429 with custom backoff)
        session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        session.mount(c.CAT_CENTER_BASE_URL, HTTPAdapter(max_retries=retries))
        self.session = session

        # Obtain Token For future calls
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self.post_wrapper(self.CAT_CENTER_AUTH_URL, headers=headers)
        if token:
            self.x_auth_token = token
        else:
            raise SystemExit

    @classmethod
    def get_instance(cls):
        """
        Get Singleton instance of Cat Center Class
        :return: Singleton instance of Cat Center Class
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_wrapper(self, url: str, headers: dict, params: dict = None) -> dict | None:
        """
        Wrapper for Get Request (includes support for 429, paging, error handling)
        :param url: URL to make GET request to
        :param headers: Headers to include in the request
        :param params: Parameters to include in the request
        :return: Response data (or None on failure)
        """
        # Build Get Request Components
        components = {"headers": headers}
        if params:
            components['params'] = params

        response = self.session.get(url=url, verify=False, **components)

        if response.ok:
            response_data = response.json()
            return response_data['response']
        else:
            # Print failure message on error
            lm.lnp("Request FAILED: " + str(
                response.status_code) + '\n' + f'Response Headers: {response.headers}' + '\n' + f'Response Params: {params}' + '\n' + f'Response Content: {response.text}',
                   level="error")
            return None

    def post_wrapper(self, url: str, headers: dict, params: dict = None, body: dict | str = None) -> dict | str | None:
        """
        Wrapper for Post Request (includes support for 429, paging, error handling)
        :param url: URL to make POST request to
        :param headers: Headers to include in the request
        :param params: Parameters to include in the request
        :param body: Body to include in the request
        :return: Response data (or None on failure)
        """
        # Build POST Request Components
        components = {"headers": headers}
        if body:
            components['data'] = body
        if params:
            components['params'] = params

        # If special post request for authentication, include self.auth
        if url == self.CAT_CENTER_AUTH_URL:
            response = self.session.post(url=url, auth=self.auth, verify=False, **components)
        else:
            response = self.session.post(url=url, verify=False, **components)

        if response.ok:
            response_data = response.json()
            if url == self.CAT_CENTER_AUTH_URL:
                return response_data['Token']
            else:
                return response_data['response']
        else:
            # Print failure message on error
            lm.lnp("Request FAILED: " + str(
                response.status_code) + '\n' + f'Response Headers: {response.headers}' + '\n' + f'Response Params: {params}' + '\n' + f'Response Content: {response.text}',
                   level="error")
            return None

    def site_floor_hierachy_to_id_mapping(self) -> dict | None:
        """
        Create and return mappings of Catalyst Center Floors to their ID's and Lat, Long coordinates. Only grab floors with valid Lat, Long
        :return: Dictionary of Floor Name to ID and Lat, Long coordinates
        """
        # Get All Catalyst Center Floors
        sites_v2_url = f"{self.CAT_CENTER_INTENT_URL}/v2/site"
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Auth-Token": self.x_auth_token}
        params = {"type": "floor"}

        floors = self.get_wrapper(sites_v2_url, headers=headers, params=params)

        # Populate mapping dictionary of floors to ID (only floors with a valid latitude and longitude address)
        if floors:
            floor_to_id = {}
            for floor in floors:
                # Find and process Location Latitude and Longitude (guaranteed to be there, Cat Center doesn't allow not
                # providing an address to the building)
                address = None
                for info in floor['additionalInfo']:
                    if info['nameSpace'] == "Location":
                        # Found address!
                        address = info['attributes']

                # Obtain Latitude and Longitude from parent site (building) - assuming we found an address
                if address:
                    buildings = self.get_wrapper(sites_v2_url, headers=headers,
                                                 params={'id': address['addressInheritedFrom']})
                    if buildings:
                        building = buildings[0]
                        for info in building['additionalInfo']:
                            if info['nameSpace'] == "Location" and (
                                    'latitude' in info['attributes'] and 'longitude' in info['attributes']):
                                # Finally add this as a valid floor to select for the migration
                                floor_to_id[floor['groupNameHierarchy']] = {
                                    "id": floor['id'],
                                    "parentId": floor['parentId'],
                                    "name": floor['name'],
                                    "floorNumber": self.get_floor_details(floor["id"]),
                                    "lat": info['attributes']['latitude'],
                                    "long": info['attributes']['longitude']
                                }
            return floor_to_id
        else:
            return None


    def get_floor_details(self, floor_id: str) -> int | None:
        url = f"{self.CAT_CENTER_INTENT_URL}/v2/floors/{floor_id}"
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Auth-Token": self.x_auth_token}
        floor = self.get_wrapper(url, headers=headers)
        if floor:
            try:
                floorNumber = floor["floorNumber"]
            except:
                floorNumber = None
            return floorNumber
        else:
            return None


    def export_buildings(self) -> dict | None:
        """
        Create and return mappings of Catalyst Center Buildings
        :return: Dictionary of Building Name to ID and Lat, Long coordinates
        """
        # Get All Catalyst Center Floors
        sites_v2_url = f"{self.CAT_CENTER_INTENT_URL}/v2/site"
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Auth-Token": self.x_auth_token}
        params = {"type": "building"}

        buildings = self.get_wrapper(sites_v2_url, headers=headers, params=params)

        # Populate mapping dictionary of floors to ID (only floors with a valid latitude and longitude address)
        if buildings:
            building_to_id = {}
            for building in buildings:
                # Find and process Location Latitude and Longitude (guaranteed to be there, Cat Center doesn't allow not
                # providing an address to the building)
                # Obtain Latitude and Longitude from parent site (building) - assuming we found an address
                building_to_id[building["name"]] = {
                    "id": building["id"],
                    "name": building["name"],
                    "address": building["additionalInfo"][0]["attributes"]["address"],
                    "lat": building["additionalInfo"][0]["attributes"]["latitude"],
                    "long": building["additionalInfo"][0]["attributes"]["longitude"],
                    "floors": []
                }
                # if building["address"] != "":
                #     building_to_id[building["name"]]["address"] = building["address"]
                # else:
                #     address = None
            return building_to_id
        else:
            return None

    def export_map_archive(self, site_hierarchy_uuid: str) -> dict | None:
        """
        Export Map Archive for a given Floor (contains XML with AP data, plus raw image uploaded of floor plan)
        Schedules "task" within Catalyst Center
        :param site_hierarchy_uuid: Site UUID of the Floor to export (ex: City 1/Building 1/Floor 1)
        :return:
        """
        # Trigger Map Archive Export for Floor, return task
        export_map_archive_url = f"{self.CAT_CENTER_INTENT_URL}/v1/maps/export/{site_hierarchy_uuid}"
        headers = {"Content-Type": "text/plain;charset=utf-8", "X-Auth-Token": self.x_auth_token}

        # Generate File Name
        suffix = generate_random_filename_suffix()
        body = f"ExportMapArchiveRequest-{suffix}".encode('utf-8')

        response = self.post_wrapper(export_map_archive_url, headers=headers, body=body)
        return response

    def get_task_result(self, task_id) -> str | dict | None:
        """
        Get the result of a task by task_id, for long-running export map archive requests
        :param task_id: Catalyst Center Task ID
        :return: Task (showing completion or Error fi failed)
        """
        task_url = f"{self.CAT_CENTER_INTENT_URL}/v1/task/{task_id}"
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Auth-Token": self.x_auth_token}

        # Iterate until the task completes (good or bad)
        while True:
            task = self.get_wrapper(task_url, headers=headers)
            if task:
                if task['progress'] == 'finished':
                    data = task['data']

                    if not task['isError']:
                        lm.lnp(f"Successfully completed task ({task_id}): {data} (Data)")
                        return data
                    else:
                        lm.lnp(f"Task Failed: {task_id}. Failure Reason: {task['failureReason']}", level="error")
                        return None
                else:
                    # Task is not yet finished, wait for a bit before checking again
                    time.sleep(5)
            else:
                return None

    def download_file_by_fileid(self, file_path: str) -> str | None:
        """
        Download a file from Catalyst Center by file path, write to exports folder (tar.gz file)
        :param file_path: Catalyst Center File Path
        :return: Name of file after it's been successfully downloaded (or Error)
        """
        # Construct the full URL for the file download endpoint
        file_url = f"{self.CAT_CENTER_INTENT_URL}/v1{file_path}"
        headers = {"X-Auth-Token": self.x_auth_token}

        # Make a GET request to download the file (not using get_wrapper, special)
        with self.session.get(file_url, headers=headers, stream=True, verify=False) as response:
            if response.status_code == 200:
                # Set Filename (must be the same as the created export apparently...)
                filename = response.headers['fileName']

                # Construct the full file path where the file will be saved
                full_file_path = c.ARCHIVE_PATH / filename

                # Write the content of the response to a local file
                with open(full_file_path, 'wb') as f:
                    f.write(response.raw.read())

                lm.lnp(f"File downloaded successfully: {filename}")
                return filename
            else:
                return None


try:
    cat_center_api = CAT_CENTER_API.get_instance()  # Singleton instance of CAT_CENTER_API
    lm.lnp(f"Successfully logged into Catalyst Center Appliance: {c.CAT_CENTER_BASE_URL}")
except SystemExit as e:
    # Failed to authenticate to Catalyst Center, exiting...
    lm.lnp(f"Unable to log into Catalyst Center Appliance: {c.CAT_CENTER_BASE_URL}", level="error")
    sys.exit(-1)
