import os
import sys
import time
import datetime as dt
import json
import logging
import soundfile as sf

from google.cloud import storage
from drivers.modem import Modem
from .logs import Log
from .utils import check_internet_conn, update_time, wait_for_connection

#config file
config_name = "Audiomoth.config"
credentials_name = "credentials.json"

sd_mount_loc = 'mnt/x/'

GLOBAL_is_connected = False

#Logging
log = Log() #Make log object global
logger = log.logger
logger.setLevel(logging.INFO)

# How many times to try for an internet connection before starting recording
connection_retries = 30


#Compress files
def wavtoflac(inputfile):
    #Handling the name changing
    inputname, _ = os.path.splitext(inputfile)
    outputname = inputname + ".flac"

    data, samplerate = sf.read(inputfile) #Read WAV file
    sf.write(outputname, data, samplerate, subtype='FLAC')

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


#Sync to cloud
def server_sync(cloud_dir, credentials_path, modem):
    modem.power_on()
    GLOBAL_is_connected = wait_for_connection(connection_retries)

    if GLOBAL_is_connected:
        update_time()

        logger.info('Started upload to gc cloud dir {} at {}'.format(dt.datetime.utcnow(), cloud_dir))
        log.rotate_log()

        try:
            #Get credentials from credentials json file
            client = storage.client.from_service_account_json(credentials_path)

            #Find the right GCS bucket
            device_conf = json.load(open(credentials_path))['device']
            gcs_bucket_name = device_conf['gcs_bucket_name']
            bucket = client.bucket(gcs_bucket_name)

            # Loop through local files, uploading them to the server
            #TODO - edit to read from usb stick
            for root, subdirs, files in os.walk(upload_dir):
                for local_f in files:
                    local_path = os.path.join(root, local_f)
                    remote_path = local_path[len(upload_dir)+1:]
                    logger.info('Uploading {} to {}'.format(local_path, remote_path))
                    upload_f = bucket.blob(remote_path)
                    upload_f.upload_from_filename(filename=local_path)

                    # If the file did not upload successfully an Exception will be thrown
                    # by upload_from_filename, so if we're here it's safe to delete the local file
                    logger.info('Upload complete. Deleting local file at {}'.format(local_path))
                    os.remove(local_path)        

        except Exception as e:
            logger.info('Exception caught in gcs_server_sync: {}'.format(str(e)))
            
    else: 
        logger.info('No internet connection available, not uploading')

    logger.info('Diabling modem and RPi until next upload slot')
    modem.power_off()

def shut_down():
    todo

def main():
    start_time = time.strf('%Y%m%d_%H%M')
    
    logging.getLogger().setLevel(logging.INFO)
    logger.info('RPi starting at %s' % start_time)

    modem = Modem()
    wav_directory = "pathtowavfiles"

    try:
       convert_directory(wav_directory) 
       server_sync()
    
    except Exception as e:
        type, val, tb = sys.exc_info()
        logging.error('Caught exception on main record() function: %s', e)


if __name__ == "__main__":
    main()
