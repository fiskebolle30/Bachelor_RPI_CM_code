import time
import logging

from .logs import Log
from .utils import convert_directory, server_sync, shut_down
from drivers.modem import Modem

#config file
config_name = "configs/Audiomoth.config"
credentials_name = "configs/credentials.json"

sd_mount_loc = 'mnt/x/'

GLOBAL_is_connected = False

#Logging
log = Log() #Make log object global
logger = log.logger
logger.setLevel(logging.INFO)

def main():
    modem = Modem()
    start_time = time.strf('%Y%m%d_%H%M')
    
    logging.getLogger().setLevel(logging.INFO)
    logger.info('RPi starting at %s' % start_time)

    wav_directory = "pathtowavfiles"

    try:
       convert_directory(wav_directory) 
       server_sync(cloud_dir="project-name", credentials_path=credentials_name, modem=modem)
       time.sleep(1) #Small delay to make sure its ready to power down
       shut_down()
    
    except Exception as e:
        logging.error('Caught exception on main record() function: %s', e)
        Modem.power_off()

if __name__ == "__main__":
    main()