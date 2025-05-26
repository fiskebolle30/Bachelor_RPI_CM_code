"""
These functions are taken from the bugg, since the products have similar goals https://github.com/bugg-resources/buggd/blob/main/src/buggd/apps/buggd/utils.py
However, not all the functions from the bugg were necessary in this product

"""

import subprocess
import os
import logging
import shutil
import json
import RPi.GPIO as GPIO
import time
import requests
import soundfile as sf
import datetime as dt

from google.cloud import storage
from drivers.modem import Modem
from .logs import Log

# Create a logger for this module and set its level
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# How many times to try for an internet connection before starting recording
connection_retries = 30

#GPIO pin to signal shutdown
Shutdown_GPIO_pin = 17

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

def check_internet_conn(timeout=2):
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
        else:
            logger.debug("Failed to fetch Google's homepage. Status code: %s", response.status_code)
    except Exception as e:
        logger.debug("An error occurred: %s", e)
        return False
    
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

#Not currently used, but could be used for telemetry file in the future
def get_sys_uptime():
    """
    Get system uptime in seconds
    """

    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])

    return uptime_seconds

"""
Framework from BUGG, but heavily modified
"""

#Sync to cloud
def server_sync(cloud_dir, credentials_path, modem):
    modem.power_on()
    GLOBAL_is_connected = wait_for_connection(connection_retries)

    if GLOBAL_is_connected:
        update_time()

        logger.info('Started upload to gc cloud dir {} at {}'.format(dt.datetime.utcnow(), cloud_dir))
        Log.rotate_log()

        try:
            #Detect mounted USB 
            usb_dirs = ['/mnt/x'] #Can add more common paths if unsure where it is mounted
            usb_dir = next((d for d in usb_dirs if os.patg.isdir(d)), None)

            if not usb_dir:
                logger.error('No USB detected')
                return
            
            #Create archive dir on USB
            archive_dir = os.path.join(usb_dir, 'uploaded')
            os.makedirs(archive_dir, exist_ok=True)

            #Get credentials from credentials json file
            client = storage.client.from_service_account_json(credentials_path)

            #Find the right GCS bucket
            device_conf = json.load(open(credentials_path))['device']
            gcs_bucket_name = device_conf['gcs_bucket_name']
            bucket = client.bucket(gcs_bucket_name)

            # Loop through local files, uploading them to the server
            for root, _, files in os.walk(usb_dir):
                for local_f in files:
                    local_path = os.path.join(root, local_f)

                    #Skip files in archive dir
                    if archive_dir in local_path:
                        continue

                    try:
                        #Create remote path relative to USB and join it with the cloud dir
                        remote_path = os.path.relpath(local_path, usb_dir)
                        remote_path = os.path.join(cloud_dir, remote_path)
                        logger.info('Uploading {} to {}'.format(local_path, remote_path))

                        #Upload files
                        upload_f = bucket.blob(remote_path)
                        upload_f.upload_from_filename(filename=local_path)

                        #Create archive path
                        relative_path = os.path.relpath(local_path, usb_dirs)
                        archived_path = os.path.join(archive_dir, relative_path)
                        os.makedirs(os.path.dirname(archived_path), exist_ok=True)

                        #Move to archive instead of deleting
                        shutil.move(local_path, archived_path)
                        logger.info('Moved {} to archive'.format(local_path))

                        # If the file did not upload successfully an Exception will be thrown
                        # by upload_from_filename, so if we're here it's safe to delete the local file
                        logger.info('Upload complete. Deleting local file at {}'.format(local_path))
                        os.remove(local_path)   

                    except Exception as e:
                        logger.info('Exception caught in gcs_server_sync: {}'.format(str(e)))
                        continue

                    #Delete files after they're successfully sent
                    try:
                        if os.path.exists(archive_dir):
                            shutil.rmtree(archive_dir)
                            logger.info('Succesfully deleted archive_dir')
                        
                    except Exception as e:
                        logger.info('Exception caught in archive cleanup in gcs_server_sync: {}'.format(str(e)))

        except Exception as e:
            logger.info('Exception caught in gcs_server_sync: {}'.format(str(e)))
                        
    else: 
        logger.info('No internet connection available, not uploading')

    logger.info('Diabling modem and RPi until next upload slot')
    modem.power_off()

"""
Functions fully written, not from BUGG
"""

#Compress files
def wavtoflac(inputfile):
    #Handling the name changing
    inputname, _ = os.path.splitext(inputfile)
    outputname = inputname + ".flac"

    data, samplerate = sf.read(inputfile) #Read WAV file
    sf.write(outputname, data, samplerate, subtype='FLAC') #Copies the file into a FLAC file

    #Delete wav file after compression
    if os.path.exists(outputname):  #Ensure the FLAC file was successfully created
        os.remove(inputfile)        #and delete the wav file if it was
        print(f"Deleted wav file: {inputfile}")

    return outputname

def convert_directory(dir):
    #Puts all the wav files in a list, keeping their metadata
    wav_files = []
    for file in os.listdir(dir):
        if file.lower().endswith('.wav'):
            path = os.path.join(dir, file)

            #Ensures it's a file and not a directory
            if os.path.isfile(path):
                lastedit = os.path.getmtime(path)
                wav_files.append((lastedit, path))
    
    #Returns if there is no wav files in directory
    if not wav_files:
        print("No wav files")
        return
    
    #Sorts wav files by last modification time in ascending order
    wav_files.sort()

    #Creates new list without the latest file
    files_to_convert = [path for (lastedit, path) in wav_files[:-1]]

    #Converts the files to flac
    for inputfile in files_to_convert:
        outputfile = wavtoflac(inputfile)
        print(f"Converted: {inputfile} to {outputfile}")

def shut_down():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(Shutdown_GPIO_pin, GPIO.OUT)

    #Set shutdown pin to high
    GPIO.output(Shutdown_GPIO_pin, GPIO.HIGH)
    time.sleep(1) #Delay long enough for MCU to detect pin being high

    #Cleanup/reset GPIO Pins to default state
    GPIO.cleanup()

    #Turn off modem
    Modem.power_off()

    #Initiate safe shutdown
    subprocess.run(["sudo", "shutdown", "-h", "now"])
