"""
These functions are taken from the bugg, since the products have similar goals https://github.com/bugg-resources/buggd/blob/main/src/buggd/apps/buggd/utils.py
However, not all the functions from the bugg were necessary in this product

"""

import subprocess
import os
import logging
import shutil
import filecmp
import json
import RPi.GPIO as GPIO
from datetime import datetime
import time
import requests

# Create a logger for this module and set its level
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def call_cmd_line(args, use_shell=True, print_output=False, run_in_bg=False):

    """
    Use command line calls - wrapper around subprocess.Popen
    """
    p = subprocess.Popen(args, stdout=subprocess.PIPE, shell=use_shell, encoding='utf8')
    if run_in_bg: return

    res = ''
    while True:
        output = p.stdout.readline()
        if output == '' and p.poll() is not None:
            break
        if output:
            res = res + output.strip()
            if print_output: logger.info(output.strip())

    rc = p.poll()

    return res


def update_time():
    # Updates time from the internet since the RPi is turned off most of the time
    # Read time from real-time clock module
    # logger.info('Reading time from RTC')
    # call_cmd_line('sudo hwclock -r')

    # Update time from internet
    logger.info('Updating time from internet before GCS sync')
    cmd_res = call_cmd_line('sudo timeout 180s ntpdate ntp.ubuntu.com')

    # Check if ntpdate was successful
    if 'adjust time server' in cmd_res:
        # Update time on real-time clock module
        logger.info('Writing updated time to RTC')
        call_cmd_line('sudo hwclock -w')



def check_internet_conn(led_driver=[], led_driver_chs=[], col_succ=[], col_fail=[], timeout=2):
    """
    Check if there is a valid internet connection by fetching the entire content of Google's homepage and print the number of bytes.
    """
    try:
        # Use the requests library to handle the connection and automatically follow redirects
        response = requests.get('http://www.google.com', timeout=timeout)
        
        # Check if the request was successful (status code 200)
        if response.status_code == 200:
            content_length = len(response.content)  # Get the number of bytes in the response content
            logger.debug("Successfully fetched Google's homepage. Content size: %s bytes.", content_length)
            if led_driver:
                set_led(led_driver, led_driver_chs, col_succ)
            return True
        else:
            logger.debug("Failed to fetch Google's homepage. Status code: %s", response.status_code)
    except Exception as e:
        logger.debug("An error occurred: %s", e)
        if led_driver:
            set_led(led_driver, led_driver_chs, col_fail)
        return False


def wait_for_internet_conn(n_tries, led_driver, led_driver_chs, col_succ, col_fail, timeout=2, verbose=False):
    """
    Repeatedly check and wait for a valid internet conntection
    """

    is_conn = False

    logger.info('Waiting for internet connection...')

    for n_try in range(n_tries):
        # Try to connect to the internet
        is_conn = check_internet_conn(timeout=timeout)

        # If connected break out
        if is_conn:
            break

        # Otherwise sleep for a second and try again
        else:
            if verbose:
                logger.info('No internet connection on try {}/{}'.format(n_try+1, n_tries))
            time.sleep(1)

    if is_conn:
        logger.info('Connected to the Internet')
        set_led(led_driver, led_driver_chs, col_succ)
    else:
        logger.info('No connection to internet after {} tries'.format(n_tries))
        set_led(led_driver, led_driver_chs, col_fail)

    return is_conn


def add_network_profile(name, apn, username, password):
    """ Add a new GSM connection profile to NetworkManager if there isn't already one with the same apn, username and password. """
   
    try:
        # List existing GSM connections and grab their UUIDs
        result = subprocess.run(['nmcli', '-t', '-f', 'TYPE,UUID', 'connection', 'show'], stdout=subprocess.PIPE, check=True)
        connections_output = result.stdout.decode().strip()
        gsm_uuids = [line.split(':')[1] for line in connections_output.split('\n') if line.startswith('gsm')]
 
        # Check each GSM connection for the specified APN, username, and password
        exists = False
        for uuid in gsm_uuids:
            # Fetch details of each GSM connection
            details_result = subprocess.run(['nmcli', '--show-secrets', '-t', 'connection', 'show', uuid], stdout=subprocess.PIPE, check=True)
            details_output = details_result.stdout.decode()
        
            # Build dictionary of the connection details
            details = dict(line.split(':', 1) for line in details_output.splitlines() if ':' in line)
            if details.get('gsm.apn') == apn and details.get('gsm.username') == username and details.get('gsm.password') == password:
                exists = True
                logger.info("Skipping: connection with these details already exists.")
                break
    
        # If the connection does not exist, add it using the provided name
        if not exists:
            add_command = ['nmcli', 'connection', 'add', 'type', 'gsm', 'con-name', name, 'gsm.apn', apn] 
            if username is not None and username != "":
                add_command.append('gsm.username')
                add_command.append(username)
            if password is not None and password != "":
                add_command.append('gsm.password')
                add_command.append(password)
        
            if subprocess.run(add_command, check=True):
                logger.info("New connection added with name: %s", name)
                
    except subprocess.CalledProcessError as e:
        logger.info("Failed to add new connection: %s", e)


def copy_sd_card_config(sd_mount_loc, config_fname):

    """
    Checks the boot sector on the SD card for any recorder config files -
    if there are any, copy them to the relevant directories
    """

    sd_config_path = os.path.join(sd_mount_loc, config_fname)
    local_config_path = config_fname

    try:
        # Try to load the config file on the SD card as JSON to validate it works
        config = json.load(open(sd_config_path))
    except Exception as e:
        logger.info('Couldn\'t parse {} as valid JSON'.format(sd_config_path))
        raise e

    # Check it's not just the same as the one we're already using
    if os.path.exists(local_config_path) and filecmp.cmp(sd_config_path, local_config_path):
        logger.info('SD card config file ({}) matches existing config ({})'.format(sd_config_path, local_config_path))
        return

    # Copy the SD config file and reboot
    # TODO: Indicate with LEDs / buzzer a new config has been found
    logger.info('Copied config from SD to local')
    shutil.copyfile(sd_config_path, local_config_path)

    # Try to configure modem, but it's not required so escape any errors
    try:
        # Load the mobile network settings from the config file
        config = json.load(open(local_config_path))
        modem_config = config['mobile_network']
        m_uname = modem_config['username']
        m_pwd = modem_config['password']
        m_host = modem_config['hostname']
        m_conname = m_host.replace('.','') + config['device']['config_id']

        m_uname = m_uname.strip()
        m_pwd = m_pwd.strip()

        # Add the profile to the network manager
        logger.info('Adding network connection profile from config file')
        add_network_profile(m_conname, m_host, m_uname, m_pwd)

    except Exception as e:
        logger.info('Couldn\'t add network manager profile from config file: {}'.format(str(e)))


def mount_ext_sd(sd_mount_loc, dev_file_str='mmcblk1p'):

    """
    Tries to mount the external SD card, and if not possible flashes an error
    code on the LEDs
    """

    # Check if SD card already mounted
    if os.path.exists(sd_mount_loc) and os.path.ismount(sd_mount_loc):
        logger.info('Device already mounted to {}. Assuming SD card, but warning - might not be!'.format(sd_mount_loc))
        return

    # Make sure sd_mount_loc is an empty directory
    if os.path.exists(sd_mount_loc): shutil.rmtree(sd_mount_loc)
    os.makedirs(sd_mount_loc)

    # List potential devices that could be the SD card
    potential_dev_fs = [f for f in os.listdir('/dev') if dev_file_str in f]

    for dev_f in potential_dev_fs:
        # Try to mount each partition in turn
        logger.info('Trying to mount device {} to {}'.format(dev_f, sd_mount_loc))
        call_cmd_line('sudo mount -orw /dev/{} {}'.format(dev_f, sd_mount_loc))

        # Check if device mounted successfully
        if os.path.ismount(sd_mount_loc):
            logger.info('Successfully mounted {} to {}'.format(dev_f, sd_mount_loc))
            break

    # If unable to mount SD then raise an exception
    if not os.path.ismount(sd_mount_loc):
        logger.critical('ERROR: Could not mount external SD card to {}'.format(sd_mount_loc))
        raise Exception('Could not mount external SD card to {}'.format(sd_mount_loc))


def check_sd_not_corrupt(sd_mnt_dir):

    """
    Check the SD card allows writing data to each of the subdirectories, as
    sometimes slightly corrupt cards will allow reads and writes to some locations
    but not all. If corrupt, this function will raise an Exception
    """

    # Write and delete a dummy files to each subdirectory of the SD card to (quickly) check it's not corrupt
    for (dirpath, dirnames, filenames) in os.walk(sd_mnt_dir):
        for subd in dirnames:
            subdir_path = os.path.join(dirpath, subd)

            # Ignore system generated directories
            if 'System Volume information' in subdir_path: continue

            # Create and delete an empty text file
            dummy_f_path = os.path.join(subdir_path, 'test_f.txt')
            f = open(dummy_f_path, 'a')
            f.close()
            os.remove(dummy_f_path)

    logger.info('check_sd_not_corrupt passed with no issues - SD should be OK')

    return True


def merge_dirs(root_src_dir, root_dst_dir, delete_src=True):

    """
    Merge two directories including all subdirectories, optionally delete root_src_dir
    """

    for src_dir, dirs, files in os.walk(root_src_dir):
        dst_dir = src_dir.replace(root_src_dir, root_dst_dir, 1)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        for file_ in files:
            src_file = os.path.join(src_dir, file_)
            dst_file = os.path.join(dst_dir, file_)
            if os.path.exists(dst_file):
                os.remove(dst_file)
            shutil.copy(src_file, dst_dir)

    if delete_src:
        shutil.rmtree(root_src_dir, ignore_errors=True)

def discover_serial():

    """
    Function to return the Raspberry Pi serial from /proc/cpuinfo

    Returns:
        A string containing the serial number or an error placeholder
    """

    # parse /proc/cpuinfo
    cpu_serial = None
    try:
        f = open('/proc/cpuinfo', 'r')
        for line in f:
            if line[0:6] == 'Serial':
                cpu_serial = line.split(':')[1].strip()
        f.close()
        # No serial line found?
        if cpu_serial is None:
            raise IOError
    except IOError:
        cpu_serial = "ERROR000000001"

    cpu_serial = "RPiID-{}".format(cpu_serial)

    return cpu_serial


def get_sys_uptime():
    """
    Get system uptime in seconds
    """

    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])

    return uptime_seconds


def clean_dirs(working_dir, upload_dir, data_dir):

    """
    Function to tidy up the directory structure, any files left in the working
    directory and any directories in upload emptied by server mirroring

    Once tidied, then make new directories if needed

    Args
        working_dir: Path to the working directory
        upload_dir: Path to the upload directory
        data_dir: Path to the data directory
    """

    ### CLEAN EMPTY DIRECTORIES

    if os.path.exists(working_dir):
        logger.info('Cleaning up working directory')
        shutil.rmtree(working_dir, ignore_errors=True)

    if os.path.exists(upload_dir):
        # Remove empty directories in the upload directory, from bottom up
        for subdir, dirs, files in os.walk(upload_dir, topdown=False):
            if not os.listdir(subdir):
                logger.info('Removing empty upload directory: {}'.format(subdir))
                shutil.rmtree(subdir, ignore_errors=True)


    ### MAKE NEW DIRECTORIES (if needed)

    # Check for / create working directory (where temporary files will be stored)
    if os.path.exists(working_dir) and os.path.isdir(working_dir):
        logger.info('Using {} as working directory'.format(working_dir))
    else:
        os.makedirs(working_dir)
        logger.info('Created {} as working directory'.format(working_dir))

    # Check for / create upload directory (root which will be used to upload files from)
    if os.path.exists(upload_dir) and os.path.isdir(upload_dir):
        logger.info('Using {} as upload directory'.format(upload_dir))
    else:
        os.makedirs(upload_dir)
        logger.info('Created {} as upload directory'.format(upload_dir))

    # Check for / create data directory (where final data files will be stored) - must be under upload_dir
    if os.path.exists(data_dir) and os.path.isdir(data_dir):
        logger.info('Using {} as data directory'.format(data_dir))
    else:
        os.makedirs(data_dir)
        logger.info('Created {} as data directory'.format(data_dir))


def wait_for_connection(n_tries, timeout=2, verbose=False):
    """
    Repeatedly check and wait for a valid internet conntection
    """

    is_conn = False

    logging.info('Waiting for internet connection...')

    for n_try in range(n_tries):
        # Try to connect to the internet
        is_conn = check_internet_conn(timeout=timeout)

        # If connected break out
        if is_conn:
            break

        # Otherwise sleep for a second and try again
        else:
            if verbose:
                logging.info('No internet connection on try {}/{}'.format(n_try+1, n_tries))
            time.sleep(1)

    if is_conn:
        logging.info('Connected to the Internet')
  
    else:
        logging.info('No connection to internet after {} tries'.format(n_tries))

    return is_conn